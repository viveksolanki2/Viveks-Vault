#!/usr/bin/env python3
"""Daily job scan.

Polls public ATS endpoints for the companies listed in config.json, screens
postings against the criteria in that same file, and writes each new match to
jobs/queue/<date>/ as a markdown file containing the full job description.

Stdlib only. No third-party packages, no auth, no logged-in scraping.

Usage:
    python3 jobs/scan.py              # scan and write the queue
    python3 jobs/scan.py --dry-run    # print matches, write nothing
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
SEEN_PATH = os.path.join(ROOT, "seen.json")
QUEUE_DIR = os.path.join(ROOT, "queue")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TIMEOUT = 45


# ---------------------------------------------------------------- http


def fetch(url, data=None, headers=None):
    """GET, or POST when data is given. Returns parsed JSON or None."""
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as e:
        print(f"    fetch failed: {url} -> {e}", file=sys.stderr)
        return None


def strip_html(s):
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>", "\n", s)
    s = re.sub(r"(?i)<li>", "- ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# ---------------------------------------------------------------- sources
# Each source function yields dicts: id, title, location, url, body, company.


def from_greenhouse(src):
    token = src["token"]
    data = fetch(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    if not data:
        return
    for j in data.get("jobs", []):
        yield {
            "id": f"greenhouse:{token}:{j.get('id')}",
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "body": strip_html(j.get("content", "")),
            "company": src["company"],
        }


def from_ashby(src):
    token = src["token"]
    data = fetch(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true")
    if not data:
        return
    for j in data.get("jobs", []):
        yield {
            "id": f"ashby:{token}:{j.get('id')}",
            "title": j.get("title", ""),
            "location": j.get("location", "") or "",
            "url": j.get("jobUrl", ""),
            "body": strip_html(j.get("descriptionHtml", "")),
            "company": src["company"],
        }


def from_lever(src):
    token = src["token"]
    data = fetch(f"https://api.lever.co/v0/postings/{token}?mode=json")
    if not data:
        return
    for j in data:
        cat = j.get("categories") or {}
        yield {
            "id": f"lever:{token}:{j.get('id')}",
            "title": j.get("text", ""),
            "location": cat.get("location", "") or "",
            "url": j.get("hostedUrl", ""),
            "body": strip_html(j.get("descriptionPlain") or j.get("description", "")),
            "company": src["company"],
        }


def from_workday(src):
    host, tenant, site = src.get("host"), src.get("tenant"), src.get("site")
    if not (host and tenant and site):
        print(f"    skipping {src['company']}: workday host/tenant/site not filled in", file=sys.stderr)
        return
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    offset, seen_here = 0, 0
    while offset < 400:
        page = fetch(f"{base}/jobs", data={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""})
        if not page:
            return
        posts = page.get("jobPostings", [])
        if not posts:
            return
        for p in posts:
            path = p.get("externalPath", "")
            detail = fetch(f"{base}{path}") if path else None
            body = ""
            if detail:
                body = strip_html((detail.get("jobPostingInfo") or {}).get("jobDescription", ""))
            yield {
                "id": f"workday:{tenant}:{p.get('bulletFields', [path])[0]}",
                "title": p.get("title", ""),
                "location": p.get("locationsText", "") or "",
                "url": f"https://{host}/en-US/{site}{path}",
                "body": body,
                "company": src["company"],
            }
            seen_here += 1
        offset += 20
        if seen_here >= page.get("total", 0):
            return


SOURCE_FUNCS = {
    "greenhouse": from_greenhouse,
    "ashby": from_ashby,
    "lever": from_lever,
    "workday": from_workday,
}


# ---------------------------------------------------------------- screening

YEARS_RE = re.compile(r"(\d+)\s*(?:\+|\-\s*\d+)?\s*(?:or more\s*)?year", re.I)


def min_years_required(body):
    """Smallest 'N years' figure in the posting. None when unstated."""
    hits = [int(m.group(1)) for m in YEARS_RE.finditer(body or "") if int(m.group(1)) <= 25]
    return min(hits) if hits else None


def norm(s):
    """Lowercase and pad so substring tests behave like whole-word tests.

    'Engineering Finance Associate' must not match the term 'engineer', but
    'Software Engineer, Backend' must. Collapsing punctuation to spaces and
    wrapping in spaces gives that for free.
    """
    return " " + re.sub(r"[^a-z0-9&+]+", " ", (s or "").lower()).strip() + " "


def hit(text, terms):
    """First term of `terms` present in `text` as a whole word, else None."""
    t = norm(text)
    for term in terms:
        if norm(term) in t:
            return term
    return None


def screen(job, crit):
    """Return (passed, reason). reason explains a rejection."""
    title, loc, body = job["title"], job["location"], job["body"]

    if not hit(title, crit["title_include"]):
        return False, "title not in target roles"
    bad = hit(title, crit["title_exclude"])
    if bad:
        return False, f"title excluded on '{bad.strip()}'"

    if loc:
        bad = hit(loc, crit["location_exclude"])
        if bad:
            return False, f"location excluded on '{bad}'"
        if not hit(loc, crit["location_include"]):
            return False, f"location '{loc}' outside target area"

    bad = hit(body, crit["body_exclude"])
    if bad:
        return False, f"description excluded on '{bad}'"

    yrs = min_years_required(body)
    if yrs is not None and yrs > crit["max_years_experience"]:
        return False, f"requires {yrs}+ years"

    return True, ""


# ---------------------------------------------------------------- output


def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (s or "").lower())).strip("-")[:60]


def write_match(job, day_dir, yrs):
    name = f"{slugify(job['company'])}--{slugify(job['title'])}.md"
    path = os.path.join(day_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {job['title']}\n\n")
        f.write(f"- **Company:** {job['company']}\n")
        f.write(f"- **Location:** {job['location'] or 'not stated'}\n")
        f.write(f"- **Experience asked:** {str(yrs) + '+ years' if yrs is not None else 'not stated'}\n")
        f.write(f"- **Apply:** {job['url']}\n")
        f.write(f"- **Found:** {dt.date.today().isoformat()}\n")
        f.write(f"- **Status:** needs resume\n\n")
        f.write("---\n\n## Job description\n\n")
        f.write(job["body"] or "_No description returned by the board._\n")
    return path


# ---------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print matches, write nothing")
    args = ap.parse_args()

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    crit = cfg["criteria"]

    seen = {}
    if os.path.exists(SEEN_PATH):
        with open(SEEN_PATH, encoding="utf-8") as f:
            seen = json.load(f)

    today = dt.date.today().isoformat()
    day_dir = os.path.join(QUEUE_DIR, today)
    if not args.dry_run:
        os.makedirs(day_dir, exist_ok=True)

    total, matches, fresh = 0, 0, []

    for src in cfg["sources"]:
        fn = SOURCE_FUNCS.get(src["type"])
        if not fn:
            print(f"  unknown source type {src['type']}", file=sys.stderr)
            continue
        print(f"  scanning {src['company']} ({src['type']})...", file=sys.stderr)
        for job in fn(src):
            total += 1
            ok, reason = screen(job, crit)
            if not ok:
                continue
            matches += 1
            if job["id"] in seen:
                continue
            yrs = min_years_required(job["body"])
            fresh.append((job, yrs))
            if not args.dry_run:
                write_match(job, day_dir, yrs)
                seen[job["id"]] = {"title": job["title"], "company": job["company"], "found": today}

    if not args.dry_run:
        with open(SEEN_PATH, "w", encoding="utf-8") as f:
            json.dump(seen, f, indent=2, sort_keys=True)
        if not os.listdir(day_dir):
            os.rmdir(day_dir)

    print(f"\nScanned {total} postings. {matches} passed screening. {len(fresh)} are new.\n")
    for job, yrs in fresh:
        y = f"{yrs}+ yrs" if yrs is not None else "yrs n/s"
        print(f"  [{job['company']}] {job['title']}")
        print(f"      {job['location']} | {y}")
        print(f"      {job['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

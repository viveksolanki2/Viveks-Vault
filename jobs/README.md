# Daily job pipeline

Runs at 9:00 AM Pacific every day, unattended.

## What it does

1. `scan.py` polls the public ATS endpoints listed in `config.json`
2. Screens every posting against the criteria in that same file
3. Writes each new match to `queue/<date>/<company>--<role>.md` with the full JD
4. Generates a tailored resume per match from `master-profile.md`
5. Commits and pushes

## What it does not do

**It does not submit applications.** Every match lands in the queue with an apply
link. You click through and submit.

Three reasons, all real:

- Application portals need an account, email verification, CAPTCHAs, and file
  uploads. A headless container with no browser session and no access to your
  inbox cannot complete them.
- Submissions are irreversible and go out under your name. A bad auto-tailored
  resume becomes a permanent record with that employer.
- LinkedIn Easy Apply is a logged-in flow. The scanner reads LinkedIn's public
  guest listings, but submitting still needs your session.

The pipeline removes the search and the writing, which is most of the work. The
click is yours.

## Files

| Path | What it is |
| --- | --- |
| `config.json` | Companies to poll and the screening criteria |
| `master-profile.md` | Source of truth for resume content, including banned phrasing |
| `scan.py` | The scanner. Stdlib only, no dependencies |
| `seen.json` | Posting IDs already surfaced, so you never see one twice |
| `queue/<date>/` | New matches, one markdown file per role |

## Running it by hand

```bash
python3 jobs/scan.py --dry-run   # print matches, write nothing
python3 jobs/scan.py             # write the queue and update seen.json
```

## LinkedIn

The primary source. It uses `linkedin.com/jobs-guest/jobs/api/...`, the endpoint
LinkedIn serves to signed-out visitors. No account, no session cookie, no
authenticated scraping, so nothing here can get your account restricted.

Tunables in `config.json`:

| Key | Meaning |
| --- | --- |
| `geo_id` | LinkedIn's location ID. `103575230` is the LA metro from your saved search |
| `keywords` | One search per phrase. More phrases means wider coverage |
| `posted_within` | `r86400` for last 24h, `r604800` for last 7 days |
| `pages` | 25 results per page, per keyword |
| `pause_seconds` | Delay between requests. Do not drop below 1.0 |

Descriptions cost one extra request each, so they are only fetched for postings
that already cleared the title and location screen. That keeps request volume
proportional to real matches rather than to everything LinkedIn returns.

Your `geo_id` is LA-centric, so searches pull in Riverside, Redlands, and
Colton. The location filter drops them. If you want the search itself narrowed
to Orange County, send me an OC job-search URL and I will swap the ID.

## Adding a company

Greenhouse, Ashby, and Lever need only a board token, which is the slug in the
company's job board URL:

```json
{ "company": "Acme", "type": "greenhouse", "token": "acme", "verified": true }
```

Workday needs three values, all visible in the careers page URL
`https://<host>/en-US/<site>` where the subdomain is usually the tenant:

```json
{
  "company": "Acme",
  "type": "workday",
  "host": "acme.wd1.myworkdayjobs.com",
  "tenant": "acme",
  "site": "Acme_Careers"
}
```

The Workday entries in `config.json` with an empty `site` are waiting on this.
Open each company's careers page, copy the URL, and paste it into a session.

## Tuning the filters

`title_include` and `title_exclude` match as whole words after punctuation is
collapsed, so `engineer` will not fire on "Engineering Finance Associate". Keep
wrong-function exclusions specific for that reason.

If the queue fills with noise, add the offending phrase to `title_exclude`. If a
role you wanted got dropped, check whether an exclusion swallowed it before
widening `title_include`.

## Dreamwork MCP

`.mcp.json` registers `@dreamworkhq/mcp`, a job-search MCP server, as a
project-scoped server. It loads on the next Claude Code start in this repo.

It runs in **free guest mode** with no key and no account. That covers
`browse_listings`, `get_listing`, `get_platform_context`, and
`get_upgrade_link`, so listings are readable immediately.

The other 64 tools need a free account and an API key. Sign in at
dreamworkhq.com, open Profile then MCP, generate a key, and add it to
`.mcp.json`:

```json
{
  "mcpServers": {
    "dreamwork": {
      "command": "npx",
      "args": ["-y", "@dreamworkhq/mcp"],
      "env": { "DREAMWORK_API_KEY": "sk_..." }
    }
  }
}
```

A key carries four scopes you pick when generating it: read, write, apply,
send mail. All four start selected. Uncheck what you do not want an agent
doing. A key can do less than you can, never more.

**This is the piece the local pipeline cannot do.** Its `apply` tool submits
to employers under your name, and `set_autopilot` authorizes recurring real
applications. That works because Dreamwork holds your authorization directly
and you scope it yourself, rather than a scheduled job borrowing your
LinkedIn session. If you want applying automated, this is the route, and
enabling it is your decision to make with explicit consent, not something
to switch on from a cron job.

Note the overlap: this server does its own matching, resume tailoring, and
pipeline tracking. Running it alongside `scan.py` means two systems doing
the same job. Pick one as the source of truth rather than reconciling both.

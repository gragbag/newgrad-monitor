# New Grad Job Monitor

Checks a mix of sources every 2 hours and writes every new-grad posting found
to `jobs.json`, which the board (`index.html`) reads to render a filterable,
sortable list you can check off as you apply. Runs entirely on GitHub Actions —
free, no server to maintain.

## Sources

- **Community trackers** (curated new-grad lists): vanshb03/New-Grad-2027,
  speedyapply/2027-SWE-College-Jobs, zapplyjobs/New-Grad-Software-Engineering-Jobs-2027
- **Direct company ATS APIs** — these hit the company's own job board API, so
  postings show up here the moment they're live, not whenever a repo maintainer
  gets to adding them:
  - Greenhouse (`GREENHOUSE_COMPANIES` in monitor.py)
  - Lever (`LEVER_COMPANIES`)
  - Ashby (`ASHBY_COMPANIES`)
- **RemoteOK** — public remote-jobs API
- **We Work Remotely** — RSS feed of remote programming jobs
- **USAJobs** — US federal government postings (only runs if you set an API key, see below)

Since the ATS/aggregator sources list *all* open roles, not just new-grad ones,
`monitor.py` filters them using `NEW_GRAD_PATTERNS` (regexes for things like
"new grad", "class of 2026", "campus hire", "early career", "entry level",
"software engineer I") with `EXCLUDE_PATTERNS` ("intern", "co-op", "senior",
"staff", "engineer II/III", "3+ years", etc.) applied on top. Matching uses
`\b` word-boundary regexes rather than plain substrings, specifically so
"software engineer I" doesn't also match inside "software engineer II" — this
is inherently imperfect since titling conventions vary by company, so it's
worth tuning both lists to taste. The three GitHub tracker repos don't need
this since they're already curated to new-grad roles.

## How it works

- `monitor.py` fetches all configured sources and diffs each posting's link
  against `jobs.json` (the full history of everything ever seen).
- A posting that disappears from a source's current listing (filled, pulled,
  etc.) is marked `"status": "closed"` rather than deleted, so a job you
  already applied to stays visible on your board with a closed badge instead
  of quietly vanishing. If it comes back, it's reopened automatically.
- Closed postings you never checked off as applied are pruned entirely after
  `CLOSED_RETENTION_DAYS` (45 by default, in `monitor.py`) so `jobs.json`
  doesn't grow forever. Anything present in `checkmarks.json` (i.e. you
  applied) is kept regardless of age.
- The **first run** just records a baseline silently (so you don't get spammed with
  hundreds of existing postings), and doesn't close anything on that run either.
- A single company's ATS returning an error (404, migrated to a different ATS,
  timeout, etc.) is skipped silently for that run and its existing postings are
  left untouched — a fetch failure never closes out a source's postings, only
  a successful fetch that no longer lists them does.

## Setup (10 minutes)

1. **Create a new GitHub repo** (can be private) and push these files to it:
   ```
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<repo-name>.git
   git push -u origin main
   ```

2. **Enable the workflow**: it's already set up in `.github/workflows/monitor.yml` to run
   every 2 hours (cron: `0 */2 * * *`). You can also trigger it manually from the
   "Actions" tab in your repo → "New Grad Job Monitor" → "Run workflow", which is a good
   way to test it immediately without waiting for the schedule.
3. **Enable GitHub Pages and connect the board** — see "The board (index.html)" below.

## Customizing

- **Change frequency**: edit the `cron` line in `.github/workflows/monitor.yml`.
  (GitHub Actions free tier gives you 2,000 min/month, and this job takes well under
  a minute to run — even hourly is fine.)
- **Add/remove companies**: edit `GREENHOUSE_COMPANIES`, `LEVER_COMPANIES`, and
  `ASHBY_COMPANIES` near the top of `monitor.py`. To find a company's slug, visit
  their careers page and look at the URL: `boards.greenhouse.io/<slug>`,
  `jobs.lever.co/<slug>`, or `jobs.ashbyhq.com/<slug>`. Not every company is on
  one of these three — some (Google, Amazon, Microsoft, Apple, Meta, etc.) run their
  own custom career sites with no public API, which is exactly what the GitHub
  tracker repos are useful for catching.
- **Tune the new-grad filter**: edit `NEW_GRAD_PATTERNS` / `EXCLUDE_PATTERNS`
  in `monitor.py` (each is a list of regex fragments, ORed together). If you're
  getting too few results, loosen the include list; too many irrelevant ones,
  tighten it or add exclude terms.
- **Tune how long closed postings stick around**: edit `CLOSED_RETENTION_DAYS`
  in `monitor.py` (default 45). Postings you've applied to are never pruned,
  regardless of this setting.
- **Enable USAJobs**: register for a free key at
  https://developer.usajobs.gov/APIRequest/Index, then add two more repo secrets:
  `USAJOBS_API_KEY` and `USAJOBS_EMAIL` (the email you registered with — USAJobs
  requires it as the API User-Agent). The source is skipped automatically if these
  aren't set.
- **Add more GitHub tracker repos**: append to `GITHUB_TABLE_SOURCES` with any other
  raw.githubusercontent.com markdown file that has a table with a Company column and
  an apply link — the parser handles both `<a href="...">` and markdown `[text](url)`
  link styles.

## The board (index.html)

`index.html` is a static frontend — dark, dense, monospace — that reads `jobs.json`
and lets you filter by source/status/listing, search, sort, and check off postings
you've applied to. No build step, no framework.

The **Listing** filter controls closed postings (roles `monitor.py` no longer
finds in their source): "Open (+ applied)" (the default) hides them unless
you've already applied, "All" shows everything, "Closed only" isolates them.
A closed posting still visible in a view carries a red "closed" badge.

**Setup:**

1. **Enable GitHub Pages**: repo Settings → Pages → Source: "Deploy from a branch" →
   Branch: `main` / `(root)`. Your board will be live at
   `https://<your-username>.github.io/<repo-name>/` within a minute or two.
2. **Create a fine-grained personal access token** (for syncing checkmarks):
   GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens
   → New token. Scope it to **only this repository**, with **Contents: Read and write**
   permission and nothing else. Copy the token — you won't see it again.
3. **Open the board**, click "GitHub sync settings" in the left rail, and enter your
   repo owner, repo name, branch (`main`), and the token. It's saved in your browser's
   localStorage and only sent directly to GitHub's API — never to any third party.
4. Check off jobs as you apply. Changes auto-save to `checkmarks.json` in the repo
   ~2 seconds after your last click (batched, so rapid-fire checking doesn't spam commits).

**Notes on the token**: because it's stored in browser localStorage and used
client-side, treat it like a password — don't open the board on a shared/public
computer while logged in, and revoke it from GitHub's settings any time. Scoping it
to Contents-only on a single repo (not full repo access, not other repos) limits the
blast radius if it ever leaked.

**Multi-device**: since checkmarks live in the repo via the GitHub API, checking a job
off on your phone shows up on your laptop next time it loads (each device needs its
own token entered once via the settings panel — same repo, same file).

## Notes

- No scraping of company sites, no automated form-filling or submission — everything
  here reads from public, documented APIs (Greenhouse, Lever, Ashby, RemoteOK, USAJobs)
  or RSS feeds. You still review and apply yourself; this just saves the manual daily
  check across a dozen-plus sources.
- The new-grad keyword filter on the ATS/aggregator sources is a heuristic, not perfect —
  expect occasional misses or false positives, and adjust the keyword lists as you notice
  patterns for the companies you care about most.
- If a source errors out or restructures its data, check the Actions run logs — failures
  are logged per-source and won't take down the rest of the run.

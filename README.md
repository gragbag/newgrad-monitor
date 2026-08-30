# New Grad Job Monitor

Checks a mix of sources every 2 hours and posts **only newly added** new-grad
postings to a Discord channel via webhook. Runs entirely on GitHub Actions —
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
`monitor.py` filters them using `NEW_GRAD_TITLE_KEYWORDS` (things like "new grad",
"early career", "entry level", "software engineer I") with `EXCLUDE_KEYWORDS`
("intern", "senior", "staff", etc.) applied on top. This is inherently imperfect —
titling conventions vary by company — so it's worth tuning both lists to taste.
The three GitHub tracker repos don't need this since they're already curated to
new-grad roles.

## How it works

- `monitor.py` fetches all configured sources, compares each posting's link
  against `state.json` (the "already seen" list).
- New postings get sent to your Discord webhook as embeds; `state.json` is updated
  and committed back to the repo by the workflow so the next run knows what's already
  been reported.
- The **first run** just records a baseline silently (so you don't get spammed with
  hundreds of existing postings) — `state.json` in this repo is already pre-seeded
  with today's postings from the GitHub trackers, so your very first scheduled run
  will only report genuinely new ones. (The ATS/aggregator sources will establish
  their own baseline on their first live run.)
- A single company's ATS returning an error (404, migrated to a different ATS, etc.)
  is skipped silently for that run — it won't break the other sources.

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

2. **Create a Discord webhook**:
   - In Discord, go to the channel you want alerts in → Edit Channel → Integrations → Webhooks → New Webhook.
   - Copy the webhook URL.

3. **Add the webhook URL as a repo secret**:
   - In your GitHub repo: Settings → Secrets and variables → Actions → New repository secret.
   - Name: `DISCORD_WEBHOOK_URL`
   - Value: (paste the webhook URL)

4. **Enable the workflow**: it's already set up in `.github/workflows/monitor.yml` to run
   every 2 hours (cron: `0 */2 * * *`). You can also trigger it manually from the
   "Actions" tab in your repo → "New Grad Job Monitor" → "Run workflow", which is a good
   way to test it immediately without waiting for the schedule.

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
- **Tune the new-grad filter**: edit `NEW_GRAD_TITLE_KEYWORDS` / `EXCLUDE_KEYWORDS`
  in `monitor.py`. If you're getting too few results, loosen the include list; too
  many irrelevant ones, tighten it or add exclude terms.
- **Enable USAJobs**: register for a free key at
  https://developer.usajobs.gov/APIRequest/Index, then add two more repo secrets:
  `USAJOBS_API_KEY` and `USAJOBS_EMAIL` (the email you registered with — USAJobs
  requires it as the API User-Agent). The source is skipped automatically if these
  aren't set.
- **Add more GitHub tracker repos**: append to `GITHUB_TABLE_SOURCES` with any other
  raw.githubusercontent.com markdown file that has a table with a Company column and
  an apply link — the parser handles both `<a href="...">` and markdown `[text](url)`
  link styles.

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

# New Grad Job Monitor

Checks a few community-maintained new-grad job trackers every 2 hours and posts
**only newly added** postings to a Discord channel via webhook. Runs entirely
on GitHub Actions — free, no server to maintain.

## How it works

- `monitor.py` fetches the markdown job tables from these repos:
  - vanshb03/New-Grad-2027
  - speedyapply/2027-SWE-College-Jobs
  - zapplyjobs/New-Grad-Software-Engineering-Jobs-2027
- It compares each posting's link against `state.json` (the "already seen" list).
- New postings get sent to your Discord webhook as embeds; `state.json` is updated
  and committed back to the repo by the workflow so the next run knows what's already
  been reported.
- The **first run** just records a baseline silently (so you don't get spammed with
  hundreds of existing postings) — `state.json` in this repo is already pre-seeded
  with today's postings, so your very first scheduled run will only report genuinely
  new ones.

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
  (GitHub Actions free tier gives you 2,000 min/month, and this job takes seconds to run,
  so even hourly is fine.)
- **Filter by role keywords**: edit `INCLUDE_KEYWORDS` / `EXCLUDE_KEYWORDS` near the top
  of `monitor.py`. E.g. set `INCLUDE_KEYWORDS = ["backend", "software engineer"]` to only
  get roles matching those terms.
- **Add more sources**: append to the `SOURCES` list with any other raw.githubusercontent.com
  markdown file that has a table with a Company column and an apply link — the parser is
  generic and handles both `<a href="...">` and markdown `[text](url)` link styles.

## Notes

- This only reads public data from these GitHub repos — no scraping of company sites,
  no automated form-filling or submission. You still review and apply yourself; this
  just saves you from manually re-checking the lists every day.
- If a source repo renames its file or restructures its table, that one source may
  silently return 0 results — check the Actions run logs occasionally.

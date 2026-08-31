#!/usr/bin/env python3
"""
New Grad Job Monitor
---------------------
Pulls new-grad job postings from a variety of source *types*:
  - github_table   : community trackers (markdown tables) on GitHub
  - greenhouse     : direct company ATS API (boards-api.greenhouse.io)
  - lever          : direct company ATS API (api.lever.co)
  - ashby          : direct company ATS API (api.ashbyhq.com)
  - remoteok       : remoteok.com public JSON API
  - wwr_rss        : weworkremotely.com RSS feed
  - usajobs        : USAJobs.gov API (requires a free API key)

Diffs against previously-seen postings (state.json) and pushes new ones to Discord.
"""

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests

STATE_FILE = "state.json"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
USAJOBS_API_KEY = os.environ.get("USAJOBS_API_KEY", "")
USAJOBS_EMAIL = os.environ.get("USAJOBS_EMAIL", "")  # USAJobs wants your email as the User-Agent

HEADERS = {"User-Agent": "newgrad-job-monitor/1.0 (personal use)"}

# ---------------------------------------------------------------------------
# CONFIG — edit these to tune what you get
# ---------------------------------------------------------------------------

# Direct-ATS companies to check. Find a company's slug from its careers URL:
#   Greenhouse: boards.greenhouse.io/<slug>       -> greenhouse
#   Lever:      jobs.lever.co/<slug>               -> lever
#   Ashby:      jobs.ashbyhq.com/<slug>            -> ashby
# Slugs occasionally change or a company migrates ATS — a 404 for one company
# is skipped silently (see fetch_greenhouse/lever/ashby below).
GREENHOUSE_COMPANIES = [
    "stripe", "doordash", "coinbase", "robinhood", "brex", "plaid", "discord",
    "figma", "notion", "airtable", "twilio", "cloudflare", "databricks",
    "gitlab", "dropbox", "pinterest", "reddit", "instacart", "lyft", "asana",
    "samsara", "confluent", "snowflake", "gusto", "affirm",
]
LEVER_COMPANIES = ["box", "eventbrite", "attentive"]
ASHBY_COMPANIES = ["ramp", "linear", "openai", "anthropic", "vanta", "mercury", "retool"]

# Keyword signals used to filter the ATS/aggregator feeds (which list ALL roles,
# not just new-grad ones) down to new-grad-relevant postings. This is inherently
# imperfect — company title conventions vary — so tune this list to taste.
NEW_GRAD_TITLE_KEYWORDS = [
    "new grad", "university grad", "college grad", "early career",
    "entry level", "entry-level", "associate software engineer",
    "software engineer i", "software engineer 1", "swe i", "swe 1",
    "graduate software", "recent graduate",
]

# Applies on top of the new-grad keyword match above, to the same role text.
EXCLUDE_KEYWORDS = ["intern", "senior", "staff", "principal", "manager", "director"]

# GitHub tracker repos (existing behavior) — general markdown-table sources
GITHUB_TABLE_SOURCES = [
    {
        "name": "New-Grad-2027 (vanshb03)",
        "url": "https://raw.githubusercontent.com/vanshb03/New-Grad-2027/dev/README.md",
    },
    {
        "name": "2027-SWE-College-Jobs (speedyapply)",
        "url": "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/NEW_GRAD_USA.md",
    },
    {
        "name": "New-Grad-Jobs-2027 (zapplyjobs)",
        "url": "https://raw.githubusercontent.com/zapplyjobs/New-Grad-Software-Engineering-Jobs-2027/main/README.md",
    },
]

# USAJobs search params (only used if USAJOBS_API_KEY is set)
USAJOBS_KEYWORDS = ["software engineer", "computer scientist"]

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

LINK_RE = re.compile(r'href="([^"]+)"|\]\((https?://[^)\s]+)\)')
BOLD_STRIP_RE = re.compile(r"\*\*|<[^>]+>")


def clean_cell(cell: str) -> str:
    cell = BOLD_STRIP_RE.sub("", cell)
    cell = cell.replace("</br>", ", ").replace("<br>", ", ")
    return cell.strip()


def normalize_link(url: str) -> str:
    """Strip tracking query params so re-shared links dedupe correctly."""
    try:
        parsed = urlparse(url)
        q = [(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_")]
        return urlunparse(parsed._replace(query=urlencode(q)))
    except Exception:
        return url


def matches_new_grad(role: str) -> bool:
    role_lower = role.lower()
    if any(kw in role_lower for kw in EXCLUDE_KEYWORDS):
        return False
    return any(kw in role_lower for kw in NEW_GRAD_TITLE_KEYWORDS)


# ---------------------------------------------------------------------------
# Source fetchers — each returns a list of {company, role, location, link}
# ---------------------------------------------------------------------------

def fetch_github_table(source):
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"[warn] {source['name']}: fetch failed: {e}", file=sys.stderr)
        return []

    jobs = []
    for line in resp.text.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        link = None
        for c in cells:
            m = LINK_RE.search(c)
            if m:
                link = m.group(1) or m.group(2)
                break
        if not link:
            continue
        company = clean_cell(cells[0])
        role = clean_cell(cells[1]) if len(cells) > 1 else ""
        location = clean_cell(cells[2]) if len(cells) > 2 else ""
        if not company or company.lower() == "company":
            continue
        if "intern" in role.lower():
            continue  # these trackers are curated new-grad lists; just drop internships
        jobs.append({
            "company": company, "role": role, "location": location,
            "link": normalize_link(link),
        })
    return jobs


def fetch_greenhouse(slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[warn] greenhouse/{slug}: fetch failed: {e}", file=sys.stderr)
        return []

    jobs = []
    for j in data.get("jobs", []):
        role = j.get("title", "")
        if not matches_new_grad(role):
            continue
        jobs.append({
            "company": slug.capitalize(),
            "role": role,
            "location": (j.get("location") or {}).get("name", ""),
            "link": normalize_link(j.get("absolute_url", "")),
        })
    return jobs


def fetch_lever(slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[warn] lever/{slug}: fetch failed: {e}", file=sys.stderr)
        return []

    jobs = []
    for j in data:
        role = j.get("text", "")
        if not matches_new_grad(role):
            continue
        categories = j.get("categories", {}) or {}
        jobs.append({
            "company": slug.capitalize(),
            "role": role,
            "location": categories.get("location", ""),
            "link": normalize_link(j.get("hostedUrl", "")),
        })
    return jobs


def fetch_ashby(slug):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[warn] ashby/{slug}: fetch failed: {e}", file=sys.stderr)
        return []

    jobs = []
    for j in data.get("jobs", []):
        role = j.get("title", "")
        if not matches_new_grad(role):
            continue
        jobs.append({
            "company": slug.capitalize(),
            "role": role,
            "location": j.get("location", ""),
            "link": normalize_link(j.get("jobUrl", "") or j.get("applyUrl", "")),
        })
    return jobs


def fetch_remoteok():
    url = "https://remoteok.com/api"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[warn] remoteok: fetch failed: {e}", file=sys.stderr)
        return []

    jobs = []
    for j in data:
        if not isinstance(j, dict) or "position" not in j:
            continue  # first element is a metadata/legal blob, not a job
        role = j.get("position", "")
        if not matches_new_grad(role):
            continue
        jobs.append({
            "company": j.get("company", ""),
            "role": role,
            "location": j.get("location", "Remote"),
            "link": normalize_link(j.get("url", "")),
        })
    return jobs


def fetch_wwr_rss():
    url = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        print(f"[warn] weworkremotely: fetch failed: {e}", file=sys.stderr)
        return []

    jobs = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not matches_new_grad(title):
            continue
        # WWR titles are usually "Company: Role"
        if ":" in title:
            company, role = title.split(":", 1)
        else:
            company, role = "", title
        jobs.append({
            "company": company.strip(), "role": role.strip(),
            "location": "Remote", "link": normalize_link(link),
        })
    return jobs


def fetch_usajobs():
    if not USAJOBS_API_KEY:
        return []  # skipped — see README for how to get a free key
    jobs = []
    for kw in USAJOBS_KEYWORDS:
        url = "https://data.usajobs.gov/api/search"
        headers = {
            "Host": "data.usajobs.gov",
            "User-Agent": USAJOBS_EMAIL or "newgrad-job-monitor",
            "Authorization-Key": USAJOBS_API_KEY,
        }
        params = {"Keyword": kw, "WhoMayApply": "public", "ResultsPerPage": 100}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[warn] usajobs ({kw}): fetch failed: {e}", file=sys.stderr)
            continue
        for item in data.get("SearchResult", {}).get("SearchResultItems", []):
            d = item.get("MatchedObjectDescriptor", {})
            role = d.get("PositionTitle", "")
            if not matches_new_grad(role):
                continue
            jobs.append({
                "company": d.get("OrganizationName", "US Government"),
                "role": role,
                "location": d.get("PositionLocationDisplay", ""),
                "link": normalize_link(d.get("PositionURI", "")),
            })
    return jobs


# ---------------------------------------------------------------------------
# State + Discord
# ---------------------------------------------------------------------------

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"seen_links": []}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def post_to_discord(new_jobs, source_name):
    if not DISCORD_WEBHOOK_URL:
        print("[warn] DISCORD_WEBHOOK_URL not set, skipping Discord post")
        return
    for i in range(0, len(new_jobs), 10):
        batch = new_jobs[i:i + 10]
        embeds = [{
            "title": f"{job['company']} — {job['role']}"[:256],
            "url": job["link"],
            "description": job["location"] or "Location not listed",
        } for job in batch]
        payload = {
            "content": f"**{len(new_jobs)} new posting(s)** from *{source_name}*" if i == 0 else None,
            "embeds": embeds,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        if resp.status_code >= 300:
            print(f"[warn] discord post failed: {resp.status_code} {resp.text}", file=sys.stderr)
        time.sleep(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_sources():
    """Returns list of (name, fetch_fn) pairs."""
    sources = []
    for s in GITHUB_TABLE_SOURCES:
        sources.append((s["name"], lambda s=s: fetch_github_table(s)))
    for slug in GREENHOUSE_COMPANIES:
        sources.append((f"Greenhouse/{slug}", lambda slug=slug: fetch_greenhouse(slug)))
    for slug in LEVER_COMPANIES:
        sources.append((f"Lever/{slug}", lambda slug=slug: fetch_lever(slug)))
    for slug in ASHBY_COMPANIES:
        sources.append((f"Ashby/{slug}", lambda slug=slug: fetch_ashby(slug)))
    sources.append(("RemoteOK", fetch_remoteok))
    sources.append(("We Work Remotely", fetch_wwr_rss))
    if USAJOBS_API_KEY:
        sources.append(("USAJobs", fetch_usajobs))
    return sources


def main():
    state = load_state()
    seen = set(state.get("seen_links", []))
    is_first_run = len(seen) == 0

    all_new = []
    for name, fetch_fn in build_sources():
        jobs = fetch_fn()
        new_jobs = [j for j in jobs if j["link"] and j["link"] not in seen]
        for j in new_jobs:
            seen.add(j["link"])

        if is_first_run:
            print(f"[init] baseline: recorded {len(new_jobs)} existing postings from {name}")
            continue

        if new_jobs:
            print(f"[info] {len(new_jobs)} new posting(s) from {name}")
            post_to_discord(new_jobs, name)
            all_new.extend(new_jobs)
        else:
            print(f"[info] no new postings from {name}")

    state["seen_links"] = sorted(seen)
    save_state(state)

    if is_first_run:
        print("Baseline established. Future runs will report only new postings.")
    else:
        print(f"Done. {len(all_new)} total new posting(s) this run.")


if __name__ == "__main__":
    main()

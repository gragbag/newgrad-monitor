#!/usr/bin/env python3
"""
New Grad Job Monitor
---------------------
Fetches new-grad job listings from a set of community-maintained GitHub repos,
diffs against previously-seen postings, and pushes new ones to a Discord webhook.

State is stored in state.json (committed back to the repo by the GitHub Action)
so the diff persists across runs.
"""

import json
import os
import re
import sys
import time
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests

STATE_FILE = "state.json"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# Each source is a raw.githubusercontent.com markdown file containing a
# "| Company | Role | Location | ... | <a href=...>Apply</a> | ... |" style table.
SOURCES = [
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

# Optional filters. Leave empty to get everything.
# INCLUDE_KEYWORDS: if non-empty, role text must contain at least one (case-insensitive)
# EXCLUDE_KEYWORDS: role text must NOT contain any of these
INCLUDE_KEYWORDS = []  # e.g. ["software engineer", "backend", "swe"]
EXCLUDE_KEYWORDS = ["intern"]  # skip internship rows, keep new-grad/full-time only

ROW_RE = re.compile(r"^\|(.+)\|\s*$", re.MULTILINE)
# Matches either an HTML href="..." link or a markdown [text](url) link
LINK_RE = re.compile(r'href="([^"]+)"|\]\((https?://[^)\s]+)\)')
BOLD_STRIP_RE = re.compile(r"\*\*|<[^>]+>")  # strip markdown bold + any stray html tags


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


def parse_table(markdown: str):
    """Yield dicts of {company, role, location, link} from any markdown table
    that has a Company-like first column and an <a href> apply link somewhere in the row."""
    jobs = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        # find the cell containing an apply link
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
        jobs.append({
            "company": company,
            "role": role,
            "location": location,
            "link": normalize_link(link),
        })
    return jobs


def passes_filters(job) -> bool:
    role_lower = job["role"].lower()
    if EXCLUDE_KEYWORDS and any(kw.lower() in role_lower for kw in EXCLUDE_KEYWORDS):
        return False
    if INCLUDE_KEYWORDS and not any(kw.lower() in role_lower for kw in INCLUDE_KEYWORDS):
        return False
    return True


def fetch_source(source):
    try:
        resp = requests.get(source["url"], timeout=20)
        resp.raise_for_status()
        return parse_table(resp.text)
    except Exception as e:
        print(f"[warn] failed to fetch {source['name']}: {e}", file=sys.stderr)
        return []


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
    # Discord embeds: batch in groups of 10 (embed limit per message)
    for i in range(0, len(new_jobs), 10):
        batch = new_jobs[i:i + 10]
        embeds = []
        for job in batch:
            embeds.append({
                "title": f"{job['company']} — {job['role']}"[:256],
                "url": job["link"],
                "description": job["location"] or "Location not listed",
            })
        payload = {
            "content": f"**{len(new_jobs)} new posting(s)** from *{source_name}*" if i == 0 else None,
            "embeds": embeds,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        if resp.status_code >= 300:
            print(f"[warn] discord post failed: {resp.status_code} {resp.text}", file=sys.stderr)
        time.sleep(1)  # be polite to Discord rate limits


def main():
    state = load_state()
    seen = set(state.get("seen_links", []))
    is_first_run = len(seen) == 0

    all_new = []
    for source in SOURCES:
        jobs = fetch_source(source)
        jobs = [j for j in jobs if passes_filters(j)]
        new_jobs = [j for j in jobs if j["link"] not in seen]

        for j in new_jobs:
            seen.add(j["link"])

        if is_first_run:
            # Don't spam Discord with hundreds of postings on the very first run —
            # just establish the baseline silently.
            print(f"[init] baseline: recorded {len(new_jobs)} existing postings from {source['name']}")
            continue

        if new_jobs:
            print(f"[info] {len(new_jobs)} new posting(s) from {source['name']}")
            post_to_discord(new_jobs, source["name"])
            all_new.extend(new_jobs)
        else:
            print(f"[info] no new postings from {source['name']}")

    state["seen_links"] = sorted(seen)
    save_state(state)

    if is_first_run:
        print("Baseline established. Future runs will report only new postings.")
    else:
        print(f"Done. {len(all_new)} total new posting(s) this run.")


if __name__ == "__main__":
    main()

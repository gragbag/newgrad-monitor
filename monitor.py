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

Writes jobs.json: a full, browsable snapshot of every posting ever seen, with
company/role/location/source/category/status/first_seen/last_seen/closed_at —
this is the file the frontend (index.html) reads to render the board. Postings
that drop out of a source's current listing are marked "closed" (not deleted)
so applied-to jobs don't vanish from your board; closed postings you never
applied to are pruned after CLOSED_RETENTION_DAYS.
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests

JOBS_FILE = "jobs.json"  # full browsable dataset, read by index.html
CHECKMARKS_FILE = "checkmarks.json"  # written by index.html; read here so applied jobs are never pruned
CLOSED_RETENTION_DAYS = 45  # how long a closed, never-applied posting stays visible before being dropped
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

# Regex signals used to filter the ATS/aggregator feeds (which list ALL roles,
# not just new-grad ones) down to new-grad-relevant postings. This is inherently
# imperfect — company title conventions vary — so tune these to taste.
#
# Matched with \b word boundaries (not substrings) so e.g. "software engineer i"
# does NOT match inside "software engineer ii" — the naive substring check this
# replaced had exactly that bug, silently letting mid-level roles through.
NEW_GRAD_PATTERNS = [
    r"new\s*grad", r"university\s*grad", r"college\s*grad", r"recent\s*grad",
    r"class\s+of\s+20(2[4-9]|3[0-9])",       # "Class of 2026", etc.
    r"campus\s+(hire|hiring|recruit)",
    r"early[\s-]?career", r"entry[\s-]?level",
    r"associate\s+software\s+engineer",
    r"junior\s+(software\s+)?(engineer|developer)",
    r"graduate\s+software",
    r"software\s+(engineer|developer)\s*,?\s*[i1]\b",   # "Software Engineer I" / "... 1"
    r"\bswe\s*[i1]\b",
]

# Applies on top of the new-grad match above, to the same role text — rules out
# internships/co-ops (not full-time new-grad roles) and anything mid+ level.
EXCLUDE_PATTERNS = [
    r"\bintern(ship)?\b", r"\bco[\s-]?op\b",
    r"\bsenior\b", r"\bsr\.?\b", r"\bstaff\b", r"\bprincipal\b", r"\blead\b",
    r"\bmanager\b", r"\bdirector\b", r"\bvp\b", r"\bvice\s+president\b", r"\bhead\s+of\b",
    r"\bmid\b", r"\bexperienced\b",         # "\bmid\b" also covers "mid-level" / "mid level"
    r"(engineer|developer|scientist|analyst)\s*,?\s*(ii|iii|iv|v)\b",  # "... II/III/IV/V"
    r"(engineer|developer|scientist|analyst)\s*,?\s*[2-9]\b",          # "... 2", "... 3", ...
    r"\b[2-9]\+?\s*years?\b",               # "3+ years" mentioned in the title
]

NEW_GRAD_RE = re.compile("|".join(NEW_GRAD_PATTERNS), re.IGNORECASE)
EXCLUDE_RE = re.compile("|".join(EXCLUDE_PATTERNS), re.IGNORECASE)

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
    if EXCLUDE_RE.search(role):
        return False
    return bool(NEW_GRAD_RE.search(role))


# ---------------------------------------------------------------------------
# Source fetchers — each returns a list of {company, role, location, link}
# ---------------------------------------------------------------------------

def fetch_github_table(source):
    """Returns a list of jobs, or None if the fetch itself failed (as opposed
    to succeeding with zero matches) — callers use None to skip stale-closing
    postings for this source on a transient error rather than closing all of them."""
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"[warn] {source['name']}: fetch failed: {e}", file=sys.stderr)
        return None

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
        # These trackers are curated new-grad lists, so we don't require a
        # NEW_GRAD_RE match (many legit rows are just "Software Engineer" with
        # no "new grad" wording) — but mid/senior/staff/leveled roles do slip
        # through some of these lists, so still run the exclude filter.
        if EXCLUDE_RE.search(role):
            continue
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
            # slug is wrong or the company migrated ATS — a standing condition, not
            # "zero jobs right now", so don't let this close out its prior postings
            print(f"[warn] greenhouse/{slug}: 404 (bad slug or migrated ATS)", file=sys.stderr)
            return None
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[warn] greenhouse/{slug}: fetch failed: {e}", file=sys.stderr)
        return None

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
            print(f"[warn] lever/{slug}: 404 (bad slug or migrated ATS)", file=sys.stderr)
            return None
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[warn] lever/{slug}: fetch failed: {e}", file=sys.stderr)
        return None

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
            print(f"[warn] ashby/{slug}: 404 (bad slug or migrated ATS)", file=sys.stderr)
            return None
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[warn] ashby/{slug}: fetch failed: {e}", file=sys.stderr)
        return None

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
        return None

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
        return None

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
            # bail out entirely rather than closing postings based on partial results
            return None
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
# State
# ---------------------------------------------------------------------------

def load_jobs():
    """Returns dict of {link: job_record}. job_record has company, role,
    location, link, source, source_type, category, status ("open"/"closed"),
    closed_at, first_seen, last_seen (ISO8601 UTC)."""
    if os.path.exists(JOBS_FILE):
        with open(JOBS_FILE, "r") as f:
            data = json.load(f)
            return {j["link"]: j for j in data.get("jobs", [])}
    return {}


def load_checkmarks():
    """Returns the set of links marked applied in checkmarks.json (written by
    index.html), so pruning never deletes a job the user has applied to."""
    if os.path.exists(CHECKMARKS_FILE):
        try:
            with open(CHECKMARKS_FILE, "r") as f:
                return set(json.load(f).keys())
        except Exception:
            return set()
    return set()


def save_jobs(jobs_by_link):
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(jobs_by_link),
        "jobs": sorted(jobs_by_link.values(), key=lambda j: j["first_seen"], reverse=True),
    }
    with open(JOBS_FILE, "w") as f:
        json.dump(payload, f, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_sources():
    """Returns list of (name, source_type, category, fetch_fn) tuples.
    source_type/category are metadata tags carried into jobs.json for the
    frontend's filters — not used for fetching logic itself."""
    sources = []
    for s in GITHUB_TABLE_SOURCES:
        sources.append((s["name"], "tracker", "New Grad", lambda s=s: fetch_github_table(s)))
    for slug in GREENHOUSE_COMPANIES:
        sources.append((f"Greenhouse/{slug}", "greenhouse", "New Grad", lambda slug=slug: fetch_greenhouse(slug)))
    for slug in LEVER_COMPANIES:
        sources.append((f"Lever/{slug}", "lever", "New Grad", lambda slug=slug: fetch_lever(slug)))
    for slug in ASHBY_COMPANIES:
        sources.append((f"Ashby/{slug}", "ashby", "New Grad", lambda slug=slug: fetch_ashby(slug)))
    sources.append(("RemoteOK", "remoteok", "New Grad", fetch_remoteok))
    sources.append(("We Work Remotely", "wwr", "New Grad", fetch_wwr_rss))
    if USAJOBS_API_KEY:
        sources.append(("USAJobs", "usajobs", "New Grad", fetch_usajobs))
    return sources


def main():
    jobs_by_link = load_jobs()
    applied_links = load_checkmarks()
    is_first_run = len(jobs_by_link) == 0
    now_iso = datetime.now(timezone.utc).isoformat()

    total_new = total_closed = total_reopened = 0

    for name, source_type, category, fetch_fn in build_sources():
        fetched = fetch_fn()

        if fetched is None:
            print(f"[warn] {name}: fetch failed, leaving its existing postings untouched")
            continue

        current_links = {j["link"] for j in fetched if j["link"]}

        new_count = reopened_count = 0
        for j in fetched:
            if not j["link"]:
                continue
            existing = jobs_by_link.get(j["link"])
            if existing is None:
                jobs_by_link[j["link"]] = {
                    **j,
                    "source": name,
                    "source_type": source_type,
                    "category": category,
                    "status": "open",
                    "closed_at": None,
                    "first_seen": now_iso,
                    "last_seen": now_iso,
                }
                new_count += 1
            else:
                existing["last_seen"] = now_iso
                was_closed = existing.get("status") == "closed"
                if existing.get("status") != "open":
                    # covers both reopening a closed posting and backfilling
                    # "status" on records written before this field existed
                    existing["status"] = "open"
                    existing["closed_at"] = None
                    if was_closed:
                        reopened_count += 1

        closed_count = 0
        if not is_first_run:
            for job in jobs_by_link.values():
                if (job.get("source") == name and job.get("status", "open") == "open"
                        and job["link"] not in current_links):
                    job["status"] = "closed"
                    job["closed_at"] = now_iso
                    closed_count += 1

        total_new += new_count
        total_closed += closed_count
        total_reopened += reopened_count

        if is_first_run:
            print(f"[init] baseline: recorded {len(current_links)} existing postings from {name}")
        elif new_count or closed_count or reopened_count:
            print(f"[info] {name}: {new_count} new, {closed_count} closed, {reopened_count} reopened")
        else:
            print(f"[info] {name}: no changes")

    # Drop postings that have been closed for a while and were never applied to,
    # so jobs.json doesn't grow forever. Anything in checkmarks.json is kept regardless.
    cutoff_ts = datetime.now(timezone.utc).timestamp() - CLOSED_RETENTION_DAYS * 86400
    stale_links = [
        link for link, job in jobs_by_link.items()
        if job.get("status") == "closed"
        and job.get("closed_at")
        and datetime.fromisoformat(job["closed_at"]).timestamp() < cutoff_ts
        and link not in applied_links
    ]
    for link in stale_links:
        del jobs_by_link[link]

    save_jobs(jobs_by_link)

    if is_first_run:
        print("Baseline established. Future runs will report new postings, closures, and reopenings.")
    else:
        print(f"Done. {total_new} new, {total_closed} newly closed, {total_reopened} reopened, "
              f"{len(stale_links)} pruned (closed >{CLOSED_RETENTION_DAYS}d, never applied). "
              f"{len(jobs_by_link)} total tracked.")


if __name__ == "__main__":
    main()

# ============================================================
#  job_discovery.py
#  Scrapes LinkedIn + Indeed for the NEWEST H1B-sponsoring
#  entry-level SWE roles and returns a deduplicated list.
# ============================================================

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from jobspy import scrape_jobs

import config

# ── H1B sponsorship signals ──────────────────────────────────
# If any of these appear in a job description, we treat the
# role as likely willing to sponsor H1B.
H1B_KEYWORDS = [
    r"\bh[-\s]?1b\b",
    r"\bh1b\b",
    r"visa sponsorship",
    r"sponsor.*visa",
    r"visa.*sponsor",
    r"work authorization",
    r"will sponsor",
    r"sponsorship available",
    r"open to sponsoring",
]

# Top H1B-sponsoring companies (USCIS-confirmed, FY2023 data).
# Used as a secondary signal when description doesn't mention H1B.
KNOWN_H1B_SPONSORS: set[str] = {
    "amazon", "google", "microsoft", "meta", "apple", "salesforce",
    "oracle", "ibm", "intel", "qualcomm", "cisco", "nvidia",
    "deloitte", "cognizant", "infosys", "tata consultancy", "tcs",
    "wipro", "accenture", "capgemini", "hcl", "tech mahindra",
    "ernst & young", "ey", "pwc", "kpmg",
    "jpmorgan", "jp morgan", "goldman sachs", "morgan stanley",
    "bloomberg", "two sigma", "jane street", "citadel",
    "stripe", "airbnb", "lyft", "uber", "doordash", "instacart",
    "databricks", "snowflake", "palantir", "splunk", "workday",
    "servicenow", "crowdstrike", "palo alto networks",
    "linkedin", "twitter", "x corp", "pinterest", "snap",
    "adobe", "vmware", "broadcom", "amd", "arm",
    "netflix", "spotify", "twitch", "roblox", "epic games",
    "dropbox", "box", "atlassian", "github", "gitlab",
    "zoom", "slack", "okta", "datadog", "new relic",
    "mongodb", "elastic", "hashicorp", "confluent",
    "samsung", "lg", "sony", "hitachi", "fujitsu", "ntt",
    "rakuten", "mercari", "line", "bytedance", "tiktok",
    "dell", "hp", "lenovo", "asus",
    "bosch", "siemens", "ericsson", "nokia", "sap",
    "lockheed martin", "boeing", "raytheon", "northrop grumman",
    "general dynamics", "l3harris",
    "ge", "general electric", "honeywell", "3m",
    "johnson & johnson", "pfizer", "genentech", "roche",
    "united health", "humana", "cigna",
    "charles schwab", "fidelity", "vanguard", "blackrock",
    "visa", "mastercard", "paypal", "square", "block",
    "walmart", "target", "cvs", "walgreens",
    "at&t", "verizon", "t-mobile",
}


def _matches_h1b_keyword(text: str) -> bool:
    """Return True if any H1B sponsorship keyword appears in text."""
    if not text:
        return False
    text_lower = text.lower()
    return any(re.search(pat, text_lower) for pat in H1B_KEYWORDS)


def _is_known_sponsor(company: str) -> bool:
    """Return True if the company is a known H1B sponsor."""
    if not company:
        return False
    company_lower = company.lower().strip()
    return any(sponsor in company_lower for sponsor in KNOWN_H1B_SPONSORS)


def _score_job(row: pd.Series) -> int:
    """
    0 = not H1B-friendly (skip)
    1 = known sponsor (possible)
    2 = explicitly mentions H1B in description (strong signal)
    """
    desc = str(row.get("description", ""))
    title = str(row.get("title", ""))
    company = str(row.get("company", ""))

    if _matches_h1b_keyword(desc) or _matches_h1b_keyword(title):
        return 2
    if _is_known_sponsor(company):
        return 1
    return 0


def _is_entry_level(title: str) -> bool:
    """
    Keep roles that look entry-level / new-grad.
    Reject 'Senior', 'Staff', 'Principal', 'Lead', 'Manager', etc.
    """
    title_lower = title.lower()
    exclude = [
        "senior", "sr.", "sr ", "staff", "principal", "lead",
        "manager", "director", "architect", "vp ", "head of",
        "distinguished", "fellow",
    ]
    return not any(kw in title_lower for kw in exclude)


def fetch_fresh_jobs(hours_old: int = 24, max_results: int = 50) -> list[dict]:
    """
    Scrape LinkedIn + Indeed for the newest H1B-sponsoring entry-level
    SWE jobs posted within `hours_old` hours.

    Returns a list of dicts sorted newest-first, deduplicated by
    (company, title).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_old)
    all_jobs: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for term in config.JOB_SEARCH_TERMS:
        print(f"  🔍 Searching: '{term}' ...")
        try:
            df = scrape_jobs(
                site_name=["linkedin", "indeed"],
                search_term=term,
                location="United States",
                results_wanted=25,          # per site per term
                hours_old=hours_old,        # only fresh postings
                country_indeed="USA",
                linkedin_fetch_description=True,  # need desc for H1B keywords
                verbose=0,
            )
        except Exception as exc:
            print(f"    ⚠ scrape error for '{term}': {exc}")
            continue

        if df is None or df.empty:
            continue

        for _, row in df.iterrows():
            title = str(row.get("title", "")).strip()
            company = str(row.get("company", "")).strip()

            # Deduplicate
            key = (company.lower(), title.lower())
            if key in seen:
                continue
            seen.add(key)

            # Filter: entry-level only
            if not _is_entry_level(title):
                continue

            # Filter: H1B signal
            score = _score_job(row)
            if score == 0:
                continue

            # Parse date
            date_posted = row.get("date_posted")
            if isinstance(date_posted, str):
                try:
                    date_posted = datetime.fromisoformat(date_posted)
                except ValueError:
                    date_posted = None

            # Make timezone-aware for comparison
            if date_posted and date_posted.tzinfo is None:
                date_posted = date_posted.replace(tzinfo=timezone.utc)

            # Skip if older than cutoff (belt-and-suspenders check)
            if date_posted and date_posted < cutoff:
                continue

            # Extract company domain from job_url for Apollo lookup
            job_url = str(row.get("job_url", ""))
            company_url = str(row.get("company_url", ""))
            domain = _extract_domain(company_url or job_url)

            all_jobs.append({
                "title": title,
                "company": company,
                "company_domain": domain,
                "location": str(row.get("location", "")).strip(),
                "job_url": job_url,
                "date_posted": date_posted.isoformat() if date_posted else "unknown",
                "description_snippet": str(row.get("description", ""))[:500],
                "h1b_signal": score,   # 2=explicit mention, 1=known sponsor
                "source": str(row.get("site", "")),
            })

        # Small pause between search terms to be polite to scrapers
        time.sleep(2)

    # Sort newest first (unknown dates go last)
    def sort_key(j: dict):
        d = j["date_posted"]
        return d if d != "unknown" else "0000"

    all_jobs.sort(key=sort_key, reverse=True)

    # Cap at max_results
    result = all_jobs[:max_results]
    print(f"\n✅ Found {len(result)} H1B-eligible jobs (from {len(all_jobs)} total matches)")
    return result


def _extract_domain(url: str) -> str:
    """Best-effort domain extraction from a URL."""
    if not url:
        return ""
    # Strip protocol
    domain = re.sub(r"^https?://", "", url)
    # Take first segment
    domain = domain.split("/")[0]
    # Remove www.
    domain = re.sub(r"^www\.", "", domain)
    return domain.lower().strip()


# ── Quick test ───────────────────────────────────────────────
if __name__ == "__main__":
    jobs = fetch_fresh_jobs(hours_old=config.JOB_MAX_AGE_HOURS, max_results=5)
    for i, j in enumerate(jobs, 1):
        print(f"\n[{i}] {j['title']} @ {j['company']}")
        print(f"     Posted : {j['date_posted']}")
        print(f"     H1B    : {'EXPLICIT' if j['h1b_signal'] == 2 else 'known sponsor'}")
        print(f"     Domain : {j['company_domain']}")
        print(f"     URL    : {j['job_url']}")

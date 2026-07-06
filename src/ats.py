# ============================================================
#  ats.py
#  Unified reader for the three public ATS job-board APIs:
#  Greenhouse, Lever, and Ashby. Each company hosts on ONE of
#  them; none offers a global cross-company search, so we probe
#  a company slug against each provider.
#
#  Every provider is normalized to the same job dict:
#    {company, ats, title, location, url, updated_at, job_id}
#
#  Public interface:
#    fetch(provider, slug)                 -> list[normalized job]
#    board_name(provider, slug)            -> str | None   (for verification)
#    is_junior_swe(title) / is_us(location)
# ============================================================

from __future__ import annotations

import datetime as dt
import re

import requests

TIMEOUT = 15

# ── Title / location filters (strict, explicit-junior) ────────
# Must contain an actual SOFTWARE role keyword. Bare "engineer i" is NOT here
# on purpose — it matched mechanical / propulsion / civil / hardware "Engineer I"
# roles (e.g. Relativity Space, LG). We require a software-specific title.
_POSITIVE = [
    "software engineer", "software developer", "software development engineer",
    "swe", "backend engineer", "back end engineer", "back-end engineer",
    "frontend engineer", "front end engineer", "front-end engineer",
    "full stack engineer", "fullstack engineer", "full-stack engineer",
    "data engineer", "machine learning engineer", "ml engineer",
    "platform engineer", "infrastructure engineer", "devops engineer",
    "site reliability engineer", "mobile engineer", "ios engineer",
    "android engineer", "web developer", "member of technical staff",
    "applications developer", "programmer",
]
_JUNIOR = ["new grad", "new-grad", "university grad", "early career",
           "early-career", "entry level", "entry-level", "associate",
           " i ", " i,", " i("]
_SENIOR = ["senior", "staff", "principal", " lead", "lead ", "manager",
           "director", " ii", " iii", " iv", " 2", " 3", " 4", "sr.", "sr ",
           " l3", " l4", " l5", " l6", "distinguished", "architect", "head of"]
# Extra safety: reject non-software disciplines even if a keyword sneaks in.
_NONSOFTWARE = ["technical support", "support engineer", "sales engineer",
                "field service", "hardware", "mechanical", "electrical",
                "civil", "structural", "propulsion", "aerospace", "aerodynamic",
                "facilities", "validation", "manufacturing", "in training",
                "chemical", "biomedical", "optical", "materials",
                # talent-pool / pipeline postings that aren't real openings
                "talent community", "talent network", "talent pool",
                "expression of interest", "general application", "future opportunities"]


def is_explicit_junior(title: str) -> bool:
    """True only if the title explicitly signals new-grad / entry-level / SWE I."""
    t = f" {title.lower().strip()} "
    return any(j in t for j in _JUNIOR) or t.rstrip().endswith(" i")


def is_junior_swe(title: str) -> bool:
    """An entry-to-early-career SWE role: a software title that is NOT senior.

    Loosened for ~1 YOE — includes plain 'Software Engineer' (no level word) and
    explicit new-grad/entry/I roles, while still excluding Senior/Staff/Principal/
    Lead/Manager and II/III+ levels.
    """
    t = f" {title.lower().strip()} "
    if not any(p in t for p in _POSITIVE):
        return False
    if any(s in t for s in _SENIOR):
        return False
    if any(n in t for n in _NONSOFTWARE):
        return False
    return True


def is_us(location: str) -> bool:
    loc = (location or "").lower()
    if not loc:
        return True
    non_us = ["china", "shanghai", "beijing", "shenzhen", "india", "bangalore",
              "bengaluru", "hyderabad", "pune", "gurgaon", "london", "uk",
              "united kingdom", "scotland", "edinburgh", "glasgow", "manchester",
              "wales", "cardiff", "belfast", "cork",
              "canada", "toronto", "vancouver", "montreal",
              "mexico", "brazil", "sao paulo", "ireland", "dublin", "germany",
              "berlin", "munich", "portugal", "lisbon", "singapore", "japan",
              "tokyo", "australia", "sydney", "france", "paris", "netherlands",
              "amsterdam", "poland", "warsaw", "spain", "madrid", "korea",
              "seoul", "hwaseong", "taiwan", "hsinchu", "taipei", "hong kong",
              "philippines", "manila", "vietnam", "malaysia", "thailand",
              "indonesia", "israel", "tel aviv", "sweden", "denmark", "norway",
              "finland", "italy", "switzerland", "zurich", "austria", "belgium",
              "czech", "prague", "romania", "ukraine", "turkey", "uae", "dubai",
              "saudi", "qatar", "argentina", "chile", "colombia", "costa rica",
              "new zealand", "emea", "apac"]
    return not any(x in loc for x in non_us)


# ── Provider: Greenhouse ──────────────────────────────────────
def _greenhouse(slug: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    r = requests.get(url, timeout=TIMEOUT)
    if r.status_code != 200:
        return []
    out = []
    for j in r.json().get("jobs", []) or []:
        out.append({
            "company": slug, "ats": "greenhouse", "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "updated_at": (j.get("updated_at") or "")[:10],
            "job_id": j.get("id"),
        })
    return out


def _greenhouse_name(slug: str) -> str | None:
    try:
        r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}",
                         timeout=TIMEOUT)
        return (r.json() or {}).get("name") if r.status_code == 200 else None
    except Exception:
        return None


# ── Provider: Lever ───────────────────────────────────────────
def _lever(slug: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = requests.get(url, timeout=TIMEOUT)
    if r.status_code != 200:
        return []
    data = r.json()
    if not isinstance(data, list):
        return []
    out = []
    for p in data:
        cat = p.get("categories") or {}
        created = p.get("createdAt")
        iso = ""
        if created:
            iso = dt.datetime.fromtimestamp(created / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
        out.append({
            "company": slug, "ats": "lever", "title": p.get("text", ""),
            "location": cat.get("location", ""),
            "url": p.get("hostedUrl", ""),
            "updated_at": iso,
            "job_id": p.get("id"),
            # Lever returns the full description in the list — capture it free.
            "description": p.get("descriptionPlain") or p.get("description", ""),
        })
    return out


# ── Provider: Ashby ───────────────────────────────────────────
_ASHBY_GQL = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams"
_ASHBY_GQL_QUERY = (
    "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {"
    " jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) {"
    " jobPostings { id title locationName employmentType } } }"
)


def _ashby_frontend(slug: str) -> list[dict]:
    """Fallback for Ashby orgs whose public posting-api is disabled (e.g. Whatnot).
    Uses the same GraphQL endpoint the careers page itself calls."""
    r = requests.post(_ASHBY_GQL, timeout=TIMEOUT, headers={"Content-Type": "application/json"},
                      json={"operationName": "ApiJobBoardWithTeams",
                            "variables": {"organizationHostedJobsPageName": slug},
                            "query": _ASHBY_GQL_QUERY})
    if r.status_code != 200:
        return []
    board = ((r.json() or {}).get("data") or {}).get("jobBoard") or {}
    out = []
    for j in board.get("jobPostings", []) or []:
        out.append({
            "company": slug, "ats": "ashby", "title": j.get("title", ""),
            "location": j.get("locationName", ""),
            "url": f"https://jobs.ashbyhq.com/{slug}/{j.get('id','')}",
            "updated_at": "",  # not exposed here; first-seen tracking covers freshness
            "job_id": j.get("id"),
        })
    return out


def _ashby(slug: str) -> list[dict]:
    # 1) public posting API (has dates); 2) frontend GraphQL fallback if disabled
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=TIMEOUT)
    if r.status_code == 200:
        data = r.json() or {}
        out = []
        for j in data.get("jobs", []) or []:
            pub = j.get("publishedAt") or j.get("updatedAt") or ""
            out.append({
                "company": slug, "ats": "ashby", "title": j.get("title", ""),
                "location": j.get("location", "") or j.get("locationName", ""),
                "url": j.get("jobUrl", "") or j.get("applyUrl", ""),
                "updated_at": str(pub)[:10],
                "job_id": j.get("id"),
            })
        if out:
            return out
    return _ashby_frontend(slug)


# ── Provider: Workday ─────────────────────────────────────────
# Workday has no name search and every company has a unique
# tenant/pod/site, so the "slug" here is a composite "tenant|pod|site".
# Endpoint: https://{tenant}.{pod}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
def _workday(slug: str) -> list[dict]:
    try:
        tenant, pod, site = slug.split("|")
    except ValueError:
        return []
    base = f"https://{tenant}.{pod}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    out, offset = [], 0
    # Narrow with searchText and page a few times (Workday caps limit at 20).
    while offset < 200:
        try:
            r = requests.post(api, timeout=TIMEOUT,
                              headers={"Content-Type": "application/json", "Accept": "application/json"},
                              json={"limit": 20, "offset": offset, "searchText": "software engineer",
                                    "appliedFacets": {}})
            if r.status_code != 200:
                break
            data = r.json() or {}
        except Exception:
            break
        posts = data.get("jobPostings", []) or []
        if not posts:
            break
        for p in posts:
            ext = p.get("externalPath", "")
            out.append({
                "company": slug, "ats": "workday", "title": p.get("title", ""),
                "location": p.get("locationsText", ""),
                "url": f"{base}/en-US/{site}{ext}",
                "updated_at": "",  # only relative "Posted N days ago"; first-seen covers it
                "job_id": ext,
            })
        offset += 20
        if offset >= (data.get("total") or 0):
            break
    return out


_PROVIDERS = {"greenhouse": _greenhouse, "lever": _lever,
              "ashby": _ashby, "workday": _workday}


def fetch(provider: str, slug: str) -> list[dict]:
    """Normalized jobs for one company on one provider. [] on any error."""
    try:
        return _PROVIDERS[provider](slug)
    except Exception:
        return []


def board_name(provider: str, slug: str) -> str | None:
    """Company name for verification. Only Greenhouse exposes one."""
    if provider == "greenhouse":
        return _greenhouse_name(slug)
    return None  # Lever/Ashby have no name endpoint — verified by slug strength


# ── Job description (on-demand; for sponsorship check + fit ranking) ──
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    import html
    return html.unescape(_TAG_RE.sub(" ", s or "")).replace("\xa0", " ")


_ASHBY_POST_QUERY = (
    "query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {"
    " jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName,"
    " jobPostingId: $jobPostingId) { descriptionHtml } }"
)


def description(job: dict) -> str:
    """Best-effort full JD text for one job. '' if unavailable."""
    prov, slug, jid = job.get("ats"), job.get("company"), job.get("job_id")
    try:
        if prov == "lever":
            if job.get("description"):
                return job["description"]  # captured in the list fetch
            r = requests.get(f"https://api.lever.co/v0/postings/{slug}/{jid}?mode=json",
                             timeout=TIMEOUT)
            if r.status_code == 200:
                p = r.json() or {}
                parts = [_strip_html(p.get("descriptionPlain") or p.get("description", ""))]
                for lst in p.get("lists", []) or []:
                    parts.append((lst.get("text", "") + " " + _strip_html(lst.get("content", ""))).strip())
                return "\n".join(x for x in parts if x)
        if prov == "greenhouse":
            r = requests.get(
                f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}", timeout=TIMEOUT)
            if r.status_code == 200:
                return _strip_html((r.json() or {}).get("content", ""))
        if prov == "ashby":
            r = requests.post(
                "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting",
                timeout=TIMEOUT, headers={"Content-Type": "application/json"},
                json={"operationName": "ApiJobPosting",
                      "variables": {"organizationHostedJobsPageName": slug, "jobPostingId": jid},
                      "query": _ASHBY_POST_QUERY})
            if r.status_code == 200:
                jp = ((r.json() or {}).get("data") or {}).get("jobPosting") or {}
                return _strip_html(jp.get("descriptionHtml", ""))
        if prov == "workday":
            tenant, pod, site = slug.split("|")
            r = requests.get(
                f"https://{tenant}.{pod}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{jid}",
                timeout=TIMEOUT, headers={"Accept": "application/json"})
            if r.status_code == 200:
                info = (r.json() or {}).get("jobPostingInfo") or {}
                return _strip_html(info.get("jobDescription", ""))
    except Exception:
        pass
    return ""

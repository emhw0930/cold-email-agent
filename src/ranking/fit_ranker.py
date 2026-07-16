# ============================================================
#  fit_ranker.py
#  Two quality gates for the daily job feed:
#    1. Sponsorship gate — drop postings whose JD text explicitly
#       says no visa sponsorship (a company can be an H-1B sponsor
#       yet still say "no sponsorship" on a specific role).
#    2. Fit ranking — ask Gemini (free tier) to score each role
#       0-100 against the user's resume, with a one-line reason,
#       and sort by it. Falls back to a free keyword scorer.
#
#  Both need the JD text, fetched on demand via ats.description().
#  Ranking is LLM-cost-bearing, so it's applied to the (small)
#  daily set, not the whole ~900-role pool.
#
#  Public:
#    enrich(jobs, limit=40, drop_no_sponsorship=True) -> list[dict]
# ============================================================

from __future__ import annotations

import concurrent.futures as cf
import json
import re
from functools import lru_cache
from pathlib import Path

from src.jobs import ats
from src.core import config
from src.core import gemini
from src.core import h1b_db

# ── Sponsorship gate ─────────────────────────────────────────
# Phrases that mean "we will NOT sponsor". Kept specific to avoid
# false positives (e.g. "sponsorship available" must NOT match).
_NO_SPONSOR = [
    r"not\s+(?:able|eligible)\s+to\s+sponsor",
    r"unable\s+to\s+sponsor",
    r"do(?:es)?\s+not\s+(?:offer|provide)\s+(?:visa\s+)?sponsorship",
    r"will\s+not\s+sponsor",
    r"no\s+(?:visa\s+)?sponsorship",
    r"without\s+(?:the\s+need\s+for\s+)?(?:current\s+or\s+future\s+)?sponsorship",
    r"not\s+provide\s+sponsorship\s+(?:now|for)",
    r"sponsorship\s+is\s+not\s+available",
    r"cannot\s+sponsor",
    r"are\s+unable\s+to\s+provide\s+visa",
    r"must\s+be\s+(?:legally\s+)?authorized\s+to\s+work\s+.*without\s+sponsorship",
]
_NO_SPONSOR_RE = re.compile("|".join(_NO_SPONSOR), re.I)


def says_no_sponsorship(text: str) -> bool:
    return bool(text) and bool(_NO_SPONSOR_RE.search(text))


# ── Resume text ──────────────────────────────────────────────
@lru_cache(maxsize=1)
def resume_text() -> str:
    """Pull the resume text once. Prefer assets/resume.txt, else the PDF,
    else fall back to the profile bio from config."""
    txt_path = Path(config.PROJECT_ROOT) / "assets" / "resume.txt"
    if txt_path.exists():
        return txt_path.read_text(errors="ignore")[:6000]
    pdf_path = Path(config.RESUME_PATH)
    if pdf_path.exists():
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(pdf_path))
            return "\n".join((p.extract_text() or "") for p in reader.pages)[:6000]
        except Exception:
            pass
    return config.YOUR_BIO


# ── Free keyword fit score (always available, no API) ────────
# The LLM scorers below are quality-first but quota/cost-bound. This
# deterministic scorer runs on every role instantly so the site's
# "Best fit" sort is always fully populated. Titles carry most of the
# signal; JD text (when available) sharpens the tech-stack overlap.

# Tech terms the résumé demonstrates strength in (kept broad; matched
# case-insensitively as substrings against the résumé + each posting).
_SKILLS = [
    "python", "java", "c++", "javascript", "typescript", "react", "node",
    "fastapi", "spring", "flask", "django", "langgraph", "langchain",
    "llm", "ai", "ml", "machine learning", "gcp", "google cloud", "aws",
    "cloud run", "bigquery", "bigtable", "kubernetes", "docker", "sql",
    "nosql", "distributed", "backend", "full stack", "fullstack", "frontend",
    "api", "microservice", "socket", "data", "platform", "web",
]
# Résumé's strongest themes → a title hit on any of these boosts fit.
_STRENGTHS = ["software engineer", "software developer", "backend", "full stack",
              "fullstack", "full-stack", "ai", "ml", "machine learning", "data",
              "platform", "cloud", "distributed", "web", "api"]
# Off-profile domains → a title hit here means a poor fit for this candidate.
_OFFFIT = ["embedded", "firmware", "hardware", "fpga", "asic", "mainframe",
           "nonstop", "cobol", "kernel", "driver", "device", "rf ", "analog",
           "mechanical", "electrical", "clearance", "ceph", "packet forwarding"]
_JUNIOR = ["new grad", "new-grad", "newgrad", "university", "early career",
           "early-career", "entry level", "entry-level", "associate", "graduate"]


@lru_cache(maxsize=1)
def _resume_skills() -> frozenset[str]:
    r = resume_text().lower()
    return frozenset(s for s in _SKILLS if s in r)


# The candidate is early-career (~1-2 yrs). A role demanding materially more
# experience is a poor fit even if the title looks junior — but the "N+ years"
# requirement lives in the JD body, so this only fires when a JD is provided.
def _years_required(jd: str) -> int | None:
    """Largest 'years of experience' the JD demands, if it states one. Matches
    '5+ years', '3-5 years', 'at least 5 years', 'minimum of 5 years', etc., but
    only when 'experience' appears nearby (avoids 'founded 5 years ago')."""
    if not jd:
        return None
    low = jd.lower()
    best = None
    for m in re.finditer(r'(\d{1,2})\s*(?:\+|\-\s*\d{1,2}|to\s*\d{1,2}|–\s*\d{1,2})?\s*'
                         r'(?:\+\s*)?(?:years?|yrs?)', low):
        window = low[max(0, m.start() - 30):m.end() + 60]
        if "experien" not in window:
            continue
        n = int(m.group(1))
        if 1 <= n <= 15:
            best = n if best is None else max(best, n)
    return best


def keyword_fit(title: str, company: str = "", jd: str = "") -> tuple[int, str]:
    """Deterministic 0-100 résumé-fit score from title (+JD if given). Free.

    When a JD is supplied, an over-seniority penalty is applied for roles that
    demand more years of experience than an early-career candidate has."""
    t = f" {title.lower()} "
    text = f"{title} {company} {jd}".lower()
    score, hits = 52, []  # base for a junior SWE role that already passed filters

    if any(j in t for j in _JUNIOR) or t.rstrip().endswith(" i "):
        score += 16; hits.append("new-grad")
    strengths = [s for s in _STRENGTHS if s in t]
    score += min(len(strengths), 3) * 8
    if strengths:
        hits.append(strengths[0])

    # tech-stack overlap between résumé and the posting (title + JD)
    overlap = [s for s in _resume_skills() if s in text and s not in ("data", "web")]
    score += min(len(overlap), 4) * 4
    if overlap and not hits:
        hits.append(overlap[0])

    if any(o in t for o in _OFFFIT):
        score -= 24; hits.append("off-stack")

    # years-of-experience gate (JD only): 3y −6, 4y −14, 5y+ −24
    req = _years_required(jd)
    if req is not None and req >= 3:
        score -= 6 if req == 3 else 14 if req == 4 else 24
        hits.append(f"{req}+ yrs req")

    score = max(5, min(96, score))
    reason = "keyword: " + (", ".join(dict.fromkeys(hits)) if hits else "generic SWE role")
    return score, reason[:80]


def ensure_scored(job: dict) -> dict:
    """Guarantee job has a fit_score. Keeps an existing (LLM) score; otherwise
    fills a free keyword score. Mutates and returns the job."""
    if not (isinstance(job.get("fit_score"), int) and job["fit_score"] >= 0):
        s, reason = keyword_fit(job.get("title", ""), job.get("company", ""))
        job["fit_score"], job["fit_reason"] = s, reason
    return job


# ── Fit scoring via Gemini (free tier) ───────────────────────
def _prompt(title: str, company: str, jd: str, resume: str) -> str:
    return (
        "You are screening software-engineering jobs for a candidate. Score how good a "
        "FIT this role is for THIS candidate from 0 (poor) to 100 (excellent), considering "
        "seniority match, tech-stack overlap, and role type. Be discerning — most roles "
        "should land 30-75; reserve 85+ for strong matches.\n\n"
        f"CANDIDATE RESUME:\n{resume[:4000]}\n\n"
        f"JOB: {title} at {company}\n"
        f"JOB DESCRIPTION:\n{(jd or '(no description available)')[:4000]}\n\n"
        'Respond ONLY with compact JSON: {"score": <int>, "reason": "<max 12 words>"}'
    )


def _parse_score(text: str) -> tuple[int, str]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in model reply: {text[:60]!r}")
    data = json.loads(m.group(0))
    if "score" not in data:
        raise ValueError("model reply JSON lacks 'score'")
    score = int(data["score"])
    return max(0, min(100, score)), str(data.get("reason", ""))[:80]


def _score_one(title: str, company: str, jd: str, resume: str) -> tuple[int, str]:
    """Return (score 0-100, one-line reason).

    Scores via Gemini's free tier. On any failure (no key, daily quota
    exhausted, parse error) fall back to the free deterministic keyword
    scorer so every role is still scored — always at $0.
    """
    try:
        text = gemini.generate(_prompt(title, company, jd, resume),
                               max_output_tokens=500)
        return _parse_score(text)
    except Exception:
        return keyword_fit(title, company, jd)


# ── Persisted daily scoring (JD-based, new postings only) ────
# Scores live in the seen_jobs table so they survive across runs. Only postings
# first seen TODAY get their full JD fetched and scored (years-of-experience
# aware); everything older keeps the score it already had — no re-fetch.
_SEEN_MIN_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS seen_jobs (job_key TEXT PRIMARY KEY, "
    "first_seen TEXT, title TEXT, company TEXT, ats TEXT, url TEXT)")


def _ensure_score_cols(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(seen_jobs)")}
    if "fit_score" not in cols:
        conn.execute("ALTER TABLE seen_jobs ADD COLUMN fit_score INTEGER")
    if "fit_reason" not in cols:
        conn.execute("ALTER TABLE seen_jobs ADD COLUMN fit_reason TEXT")
    if "no_sponsor" not in cols:
        conn.execute("ALTER TABLE seen_jobs ADD COLUMN no_sponsor INTEGER DEFAULT 0")


def _seen_key(j: dict) -> str:
    return f"{j['ats']}:{j.get('job_id')}"


def score_daily(jobs: list[dict], db_path: str | None = None) -> list[dict]:
    """Attach fit_score / fit_reason / no_sponsorship to every job, scoring only
    the day's NEW postings from their full JD and persisting the result.

    - Jobs first seen TODAY (is_new): fetch the full description, run the
      sponsorship gate, score fit (Gemini when quota allows, else the free
      years-of-experience-aware keyword scorer), and STORE it in seen_jobs.
    - Jobs seen on an earlier day: reuse the stored score, no re-fetch.
    - Jobs seen before but never scored (predate this feature): free title-only
      score as a fallback (not stored, so they stay put).

    Used by the daily workflow so BOTH the website and the email show the same
    JD-based scores without re-scoring the whole ~800-role pool every day.
    """
    db_path = db_path or h1b_db.DB_PATH
    resume = resume_text()
    conn = h1b_db.connect(db_path)
    conn.execute(_SEEN_MIN_SCHEMA)
    _ensure_score_cols(conn)
    stored = {r["job_key"]: (r["fit_score"], r["fit_reason"], r["no_sponsor"])
              for r in conn.execute(
                  "SELECT job_key, fit_score, fit_reason, no_sponsor FROM seen_jobs")}

    # new postings that still need a JD-based score → fetch their JDs in parallel
    todo = [j for j in jobs
            if j.get("is_new") and stored.get(_seen_key(j), (None,))[0] is None]
    if todo:
        print(f"  Scoring {len(todo)} new posting(s) from full JD …")

        def _fetch(j):
            j["_jd"] = ats.description(j) or ""
            return j
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(_fetch, todo))

    updates = []
    for j in jobs:
        k = _seen_key(j)
        s = stored.get(k)
        if s and s[0] is not None:                       # seen before → reuse
            j["fit_score"], j["fit_reason"] = int(s[0]), s[1] or ""
            j["no_sponsorship"] = bool(s[2])
        elif j.get("is_new"):                             # new today → JD score
            jd = j.get("_jd", "")
            j["no_sponsorship"] = says_no_sponsorship(jd)
            sc, reason = _score_one(j["title"], j["company"], jd, resume)
            j["fit_score"], j["fit_reason"] = sc, reason
            updates.append((k, j.get("first_seen", ""), j["title"], j["company"],
                            j["ats"], j.get("url", ""), sc, reason,
                            1 if j["no_sponsorship"] else 0))
        else:                                             # pre-existing, unscored
            j["fit_score"], j["fit_reason"] = keyword_fit(j["title"], j["company"])
            j["no_sponsorship"] = False
        j.pop("_jd", None)

    if updates:
        # upsert: the row usually already exists (_annotate_seen created it), but
        # insert it if not so score_daily is correct even called on its own.
        conn.executemany(
            "INSERT INTO seen_jobs "
            "(job_key, first_seen, title, company, ats, url, fit_score, fit_reason, no_sponsor) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(job_key) DO UPDATE SET fit_score=excluded.fit_score, "
            "fit_reason=excluded.fit_reason, no_sponsor=excluded.no_sponsor",
            updates)
        conn.commit()
    conn.close()
    return jobs


def enrich(jobs: list[dict], limit: int = 40, drop_no_sponsorship: bool = True) -> list[dict]:
    """Fetch JD text, drop no-sponsorship roles, score fit, return sorted by score.

    Only the first `limit` jobs (already ordered new-grad-first) are processed to
    bound LLM cost. Unprocessed extras are appended after, unscored.
    """
    head, tail = jobs[:limit], jobs[limit:]
    resume = resume_text()

    # 1) fetch descriptions in parallel
    def _desc(j):
        j["_jd"] = ats.description(j)
        return j
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        head = list(ex.map(_desc, head))

    # 2) sponsorship gate
    kept = []
    for j in head:
        if drop_no_sponsorship and says_no_sponsorship(j.get("_jd", "")):
            j["dropped_no_sponsorship"] = True
            continue
        kept.append(j)

    # 3) score fit in parallel
    def _score(j):
        s, reason = _score_one(j["title"], j["company"], j.get("_jd", ""), resume)
        j["fit_score"], j["fit_reason"] = s, reason
        return j
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        kept = list(ex.map(_score, kept))

    kept.sort(key=lambda j: j.get("fit_score", 0), reverse=True)
    for j in kept:
        j.pop("_jd", None)
    return kept + tail


# ── Quick test ───────────────────────────────────────────────
if __name__ == "__main__":
    from src.jobs import h1b_greenhouse as hg
    jobs = hg.daily_fresh_swe(us_only=True, limit=8)
    ranked = enrich(jobs, limit=8)
    print(f"Ranked {len(ranked)} roles:\n")
    for j in ranked:
        if "fit_score" in j:
            print(f"  {j['fit_score']:>3}  [{j['company']}] {j['title']} — {j['fit_reason']}")

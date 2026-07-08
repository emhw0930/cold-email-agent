# ============================================================
#  fit_ranker.py
#  Two quality gates for the daily job feed:
#    1. Sponsorship gate — drop postings whose JD text explicitly
#       says no visa sponsorship (a company can be an H-1B sponsor
#       yet still say "no sponsorship" on a specific role).
#    2. Fit ranking — ask Claude to score each role 0-100 against
#       the user's resume, with a one-line reason, and sort by it.
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
import threading
import time
from functools import lru_cache
from pathlib import Path

import anthropic
import requests

import ats
import config

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


# ── Fit scoring via Claude ───────────────────────────────────
_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


_SCORE_MODEL = "claude-haiku-4-5"  # cheap + fast; scoring is a simple judgement


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


# ── Gemini free-tier throttle + model fallback ───────────────
# Free-tier quotas are PER MODEL and much smaller than advertised
# (empirically ~150-160 requests/day for flash-lite). We space calls
# ~4.5s apart for the RPM limit, and keep a fallback chain of models:
# when one model's DAILY quota is exhausted (429 with a PerDay quotaId),
# it's taken out of rotation for the rest of the run and the next
# model's separate daily bucket is used instead.
_GEMINI_MODELS = list(dict.fromkeys([config.GEMINI_MODEL, "gemini-2.5-flash"]))
_gemini_dead: set[str] = set()   # models whose daily quota is gone (this run)
_gemini_lock = threading.Lock()
_gemini_next_ok = 0.0
_GEMINI_SPACING = 4.5  # seconds between calls


def _gemini_throttle() -> None:
    global _gemini_next_ok
    with _gemini_lock:
        wait = _gemini_next_ok - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _gemini_next_ok = time.monotonic() + _GEMINI_SPACING


def _quota_is_daily(resp) -> bool:
    try:
        for d in resp.json().get("error", {}).get("details", []):
            for v in d.get("violations", []):
                if "PerDay" in v.get("quotaId", ""):
                    return True
    except Exception:
        pass
    return False


def _score_one_gemini(title: str, company: str, jd: str, resume: str) -> tuple[int, str]:
    """Score via Google Gemini (free tier), falling through the model chain."""
    body = {
        "contents": [{"parts": [{"text": _prompt(title, company, jd, resume)}]}],
        # thinkingBudget 0 disables 2.5-family "thinking", which otherwise
        # silently consumes maxOutputTokens and truncates the JSON reply
        "generationConfig": {"maxOutputTokens": 500, "temperature": 0.1,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }
    for model in _GEMINI_MODELS:
        if model in _gemini_dead:
            continue
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in (1, 2):
            _gemini_throttle()
            r = requests.post(url, json=body, timeout=45,
                              headers={"x-goog-api-key": config.GEMINI_API_KEY,
                                       "Content-Type": "application/json"})
            if r.status_code == 429:
                if _quota_is_daily(r):
                    _gemini_dead.add(model)   # dead for the day — next model
                    print(f"  ⚠ {model}: daily quota exhausted, switching model")
                    break
                if attempt == 1:
                    time.sleep(25)  # per-minute window; back off and retry once
                    continue
                break  # persistent RPM trouble — try the next model
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return _parse_score(text)
    raise RuntimeError("gemini quota exhausted on all models")


def _score_one_claude(title: str, company: str, jd: str, resume: str) -> tuple[int, str]:
    resp = _get_client().messages.create(
        model=_SCORE_MODEL, max_tokens=80,
        messages=[{"role": "user", "content": _prompt(title, company, jd, resume)}],
    )
    return _parse_score(resp.content[0].text.strip())


def _score_one(title: str, company: str, jd: str, resume: str) -> tuple[int, str]:
    """Return (score 0-100, one-line reason).

    Provider: Gemini free tier when GEMINI_API_KEY is set, else Claude.
    A Gemini failure returns -1 rather than silently spending Claude credits.
    """
    try:
        if config.GEMINI_API_KEY:
            return _score_one_gemini(title, company, jd, resume)
        return _score_one_claude(title, company, jd, resume)
    except Exception as e:
        return -1, f"score error: {e}"[:80]


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
    import h1b_greenhouse as hg
    jobs = hg.daily_fresh_swe(us_only=True, limit=8)
    ranked = enrich(jobs, limit=8)
    print(f"Ranked {len(ranked)} roles:\n")
    for j in ranked:
        if "fit_score" in j:
            print(f"  {j['fit_score']:>3}  [{j['company']}] {j['title']} — {j['fit_reason']}")

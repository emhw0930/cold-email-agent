# ============================================================
#  gemini.py
#  Thin client for Google's Gemini API (free tier). Shared by the
#  fit ranker (bulk role scoring) and the cold-email generator, so
#  the whole project runs on one free LLM — no paid API.
#
#  Handles the three things every Gemini call in this repo needs:
#    - a model fallback chain: when one model's DAILY free-tier quota
#      is spent, roll to the next model's separate daily bucket
#    - RPM throttling: calls spaced a few seconds apart
#    - thinkingBudget 0: 2.5-family "thinking" otherwise silently eats
#      the output-token budget and truncates the reply
# ============================================================

from __future__ import annotations

import threading
import time

import requests

import config

# Free-tier quotas are PER MODEL and much smaller than advertised, so we
# keep a fallback chain: the configured model first, then flash as backup.
_MODELS = list(dict.fromkeys([config.GEMINI_MODEL, "gemini-2.5-flash"]))
_dead: set[str] = set()          # models whose daily quota is gone (this run)
_lock = threading.Lock()
_next_ok = 0.0
_SPACING = 4.5                    # seconds between calls (RPM limit)


class GeminiUnavailable(RuntimeError):
    """No configured model can serve the request — either GEMINI_API_KEY is
    unset or every model's free-tier daily quota is exhausted. Callers should
    fall back (keyword fit score / template email) rather than crash."""


def _throttle() -> None:
    global _next_ok
    with _lock:
        wait = _next_ok - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _next_ok = time.monotonic() + _SPACING


def _quota_is_daily(resp) -> bool:
    try:
        for d in resp.json().get("error", {}).get("details", []):
            for v in d.get("violations", []):
                if "PerDay" in v.get("quotaId", ""):
                    return True
    except Exception:
        pass
    return False


def generate(prompt: str, *, system: str = "", max_output_tokens: int = 500,
             temperature: float = 0.1) -> str:
    """Return the text of a single Gemini completion.

    Rolls through the model fallback chain on daily-quota 429s and retries
    once on a per-minute 429. Raises GeminiUnavailable when nothing can serve
    the request.
    """
    if not config.GEMINI_API_KEY:
        raise GeminiUnavailable("GEMINI_API_KEY not set")

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_output_tokens,
            "temperature": temperature,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    for model in _MODELS:
        if model in _dead:
            continue
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in (1, 2):
            _throttle()
            r = requests.post(url, json=body, timeout=45,
                              headers={"x-goog-api-key": config.GEMINI_API_KEY,
                                       "Content-Type": "application/json"})
            if r.status_code == 429:
                if _quota_is_daily(r):
                    _dead.add(model)   # dead for the day — next model
                    print(f"  ⚠ {model}: daily quota exhausted, switching model")
                    break
                if attempt == 1:
                    time.sleep(25)  # per-minute window; back off and retry once
                    continue
                break  # persistent RPM trouble — try the next model
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    raise GeminiUnavailable("gemini quota exhausted on all models")

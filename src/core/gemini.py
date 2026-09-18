# ============================================================
#  gemini.py
#  Thin LLM client. Shared by the fit ranker (bulk role scoring) and
#  the cold-email generator.
#
#  Text generation (generate()) prefers Claude when ANTHROPIC_API_KEY is
#  set (paid), and otherwise uses Google's Gemini free tier; callers fall
#  back to a plain template when neither is available. Embeddings (embed(),
#  for RAG) are Gemini-only.
#
#  The Gemini path handles the three things every free-tier call needs:
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

from src.core import config

# Free-tier quotas are PER MODEL and much smaller than advertised, so we
# keep a fallback chain: the configured model first, then flash as backup.
_MODELS = list(dict.fromkeys([config.GEMINI_MODEL, "gemini-2.5-flash"]))
# Embedding model — free tier. Used by resume_kb for RAG retrieval. We request
# 768 dimensions (the model defaults to 3072) — plenty for a tiny résumé corpus
# and a third the storage.
_EMBED_MODEL = "gemini-embedding-001"
_EMBED_DIMS = 768
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


_anthropic_client = None


def _get_anthropic_client():
    """Lazily build the Anthropic SDK client (imported only when a key is set)."""
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic  # optional dependency — only needed on the Claude path
        _anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _anthropic_client


def _anthropic_generate(prompt: str, *, system: str, max_output_tokens: int) -> str:
    """A single Claude completion via the Anthropic Messages API."""
    kwargs = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": max_output_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    resp = _get_anthropic_client().messages.create(**kwargs)
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def generate(prompt: str, *, system: str = "", max_output_tokens: int = 500,
             temperature: float = 0.1) -> str:
    """Return the text of a single LLM completion.

    Uses Claude when ANTHROPIC_API_KEY is set (falling back to Gemini on any
    error), otherwise Gemini's free tier — rolling through its model fallback
    chain on daily-quota 429s. Raises GeminiUnavailable when nothing can serve
    the request, so callers can fall back to a template.
    """
    # Prefer Claude when configured; degrade to Gemini, then (via the raised
    # GeminiUnavailable) to the caller's template.
    if config.ANTHROPIC_API_KEY:
        try:
            return _anthropic_generate(prompt, system=system,
                                       max_output_tokens=max_output_tokens)
        except Exception as e:  # auth/network/quota all degrade the same way
            if not config.GEMINI_API_KEY:
                raise GeminiUnavailable(f"Anthropic request failed: {e}") from e
            print(f"  ⚠ Anthropic call failed ({e}); falling back to Gemini")

    if not config.GEMINI_API_KEY:
        raise GeminiUnavailable(
            "no LLM configured — set ANTHROPIC_API_KEY or GEMINI_API_KEY")

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


def embed(texts: str | list[str], *, task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    """Return an embedding vector (list of floats) for each input text.

    Uses the free-tier text-embedding-004 model. `task_type` should be
    RETRIEVAL_DOCUMENT when embedding stored corpus chunks and RETRIEVAL_QUERY
    when embedding a search query — Gemini tunes the two ends of the retrieval
    asymmetrically, which improves match quality.

    A single string returns a 1-element list. Raises GeminiUnavailable when the
    key is unset or the embedding quota is spent, so callers can fall back to
    keyword-only retrieval rather than crash.
    """
    if not config.GEMINI_API_KEY:
        raise GeminiUnavailable("GEMINI_API_KEY not set")
    if isinstance(texts, str):
        texts = [texts]
    if not texts:
        return []
    # gemini-embedding-001 serves one text per call (no synchronous batch), so
    # we loop. The corpus is embedded once (and only when a bullet changes), and
    # queries are single, so the call count stays tiny.
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{_EMBED_MODEL}:embedContent")
    out: list[list[float]] = []
    for t in texts:
        body = {"model": f"models/{_EMBED_MODEL}",
                "content": {"parts": [{"text": t}]},
                "taskType": task_type,
                "outputDimensionality": _EMBED_DIMS}
        _throttle()
        r = requests.post(url, json=body, timeout=45,
                          headers={"x-goog-api-key": config.GEMINI_API_KEY,
                                   "Content-Type": "application/json"})
        if r.status_code == 429:
            raise GeminiUnavailable("embedding quota exhausted")
        r.raise_for_status()
        out.append(r.json()["embedding"]["values"])
    return out

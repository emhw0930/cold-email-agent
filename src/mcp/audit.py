# ============================================================
#  audit.py — action log for the MCP server
#
#  Records EVERY tool call (reads, drafts, sends, dry-runs) to a private SQLite
#  DB so you can answer "what did my agent do?". One decorator (`audited`) wraps
#  a tool function; it captures name, arguments, success, duration, a short
#  result summary, and any error — then never lets a logging failure break the
#  actual tool call.
#
#  Private by design: arguments can include recipient addresses and email bodies
#  (send_batch), so data/agent_log.db is gitignored.
# ============================================================

from __future__ import annotations

import datetime as dt
import functools
import inspect
import json
import sqlite3
import time
from pathlib import Path

from src.core import config

LOG_DB = str(Path(config.PROJECT_ROOT) / "data" / "agent_log.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_actions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          TEXT    NOT NULL,   -- UTC ISO-8601, second precision
  tool        TEXT    NOT NULL,   -- tool/function name
  args        TEXT,               -- JSON of named arguments (values truncated)
  ok          INTEGER NOT NULL,   -- 1 success, 0 raised
  duration_ms INTEGER,
  result      TEXT,               -- short summary of the return value
  error       TEXT                -- "ExcType: message" when ok=0
);
CREATE INDEX IF NOT EXISTS idx_actions_ts ON agent_actions(ts);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(LOG_DB)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def _short(v, n: int = 400):
    """Truncate long strings/containers so the log stays compact."""
    if isinstance(v, str):
        return v if len(v) <= n else v[:n] + f"…(+{len(v) - n} chars)"
    if isinstance(v, (list, tuple)):
        return f"<{type(v).__name__} of {len(v)}>"
    if isinstance(v, dict):
        return {k: _short(x, 120) for k, x in list(v.items())[:8]}
    return v


def _summarize_args(sig: inspect.Signature | None, args, kwargs) -> str:
    """Map positional+keyword args to their parameter names and JSON-encode,
    truncating long values. Falls back to a positional dump if binding fails."""
    try:
        if sig is not None:
            bound = sig.bind_partial(*args, **kwargs)
            data = {k: _short(v) for k, v in bound.arguments.items()}
        else:
            data = {"args": [_short(a) for a in args], **{k: _short(v) for k, v in kwargs.items()}}
        return json.dumps(data, default=str)[:2000]
    except Exception:
        return json.dumps({"unbindable": True})


def _summarize_result(r) -> str:
    if r is None:
        return ""
    if isinstance(r, list):
        return f"{len(r)} item(s)"
    if isinstance(r, dict):
        # surface the most telling keys without dumping bodies
        keys = ("found", "sent", "resent", "matched_name", "status", "count",
                "best_guess", "requires", "error")
        picked = {k: r[k] for k in keys if k in r}
        return json.dumps(picked or {"keys": list(r.keys())[:8]}, default=str)[:400]
    return _short(str(r), 200)


def _record(tool: str, args_json: str, ok: bool, ms: float,
            result_summary: str, err: str | None) -> None:
    c = _conn()
    c.execute(
        "INSERT INTO agent_actions (ts, tool, args, ok, duration_ms, result, error) "
        "VALUES (?,?,?,?,?,?,?)",
        (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
         tool, args_json, 1 if ok else 0, int(ms), result_summary, err))
    c.commit()
    c.close()


def audited(fn):
    """Wrap a tool so every call is logged. Uses functools.wraps so FastMCP still
    sees the original signature/annotations/docstring (schema is unchanged)."""
    sig = None
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        pass

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        ok, err, result = True, None, None
        try:
            result = fn(*args, **kwargs)
            return result
        except Exception as e:                       # log the failure, then re-raise
            ok, err = False, f"{type(e).__name__}: {e}"
            raise
        finally:
            try:
                _record(fn.__name__, _summarize_args(sig, args, kwargs), ok,
                        (time.perf_counter() - start) * 1000,
                        _summarize_result(result), err)
            except Exception:
                pass   # logging must NEVER break the tool

    return wrapper


def read_actions(tool: str = "", ok_only: bool = False,
                 limit: int = 50) -> list[dict]:
    """Return recent logged actions, newest first, optionally filtered by a
    tool-name substring and/or success. [] if nothing has been logged yet."""
    import os
    if not os.path.exists(LOG_DB):
        return []
    c = _conn()
    try:
        rows = c.execute(
            "SELECT ts, tool, args, ok, duration_ms, result, error "
            "FROM agent_actions ORDER BY id DESC").fetchall()
    finally:
        c.close()
    t = tool.lower().strip()
    out = []
    for r in rows:
        if t and t not in (r["tool"] or "").lower():
            continue
        if ok_only and not r["ok"]:
            continue
        out.append({
            "ts": r["ts"], "tool": r["tool"], "ok": bool(r["ok"]),
            "duration_ms": r["duration_ms"], "args": r["args"],
            "result": r["result"], "error": r["error"],
        })
        if len(out) >= limit:
            break
    return out

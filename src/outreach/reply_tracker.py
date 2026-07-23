# ============================================================
#  reply_tracker.py
#  Close the OTHER half of the loop: detect human REPLIES to cold emails.
#
#  bounce_retry.py answers "did it get delivered?"; this answers "did they
#  respond, and how?". It scans the Gmail inbox (IMAP), matches inbound mail
#  to the send log (data/outreach_state.db), skips bounces + auto-replies, and
#  classifies each real reply as interview / rejected / replied.
#
#  A separate `reply_status` column keeps this independent of delivery status
#  (a row can be status='sent' AND reply_status='rejected').
#
#  Public interface:
#    ensure_schema()                       — add reply columns (idempotent)
#    scan_replies(since_days, apply=True)  — scan inbox, classify, record
#    needs_followup(days)                  — delivered + no_reply + aged
#    stats()                               — the response funnel per company
#
#  Reading the inbox needs GMAIL_APP_PASSWORD (IMAP enabled in Gmail).
# ============================================================

from __future__ import annotations

import datetime as dt
import email
import imaplib
import re
from email.header import decode_header, make_header
from email.utils import parseaddr

from src.core import config
from src.outreach.bounce_retry import STATE_DB, _conn

# ── classification keyword sets (rule-based, $0) ─────────────
_AUTO_HINTS = ("out of office", "automatic reply", "auto-reply", "autoreply",
               "away from my desk", "currently out of", "on vacation",
               "on annual leave", "will be out", "ooo")
_REJECT_HINTS = ("unfortunately", "not moving forward", "decided not to",
                 "will not be moving", "not be moving forward", "other candidates",
                 "position has been filled", "regret to inform", "not be proceeding",
                 "won't be moving", "not to move forward", "no longer under consideration",
                 "pursue other candidates", "not selected")
_INTERVIEW_HINTS = ("schedule", "availability", "available", "phone screen",
                    "interview", "set up a call", "hop on a call", "book a time",
                    "calendly", "times that work", "next steps", "connect for a call",
                    "speak with you", "chat about", "give you a call", "quick call")


def classify_reply(subject: str, body: str, auto_flag: bool = False) -> str:
    """Rule-based label for a reply. auto -> reject -> interview -> replied."""
    text = f"{subject}\n{body}".lower()
    if auto_flag or any(h in text for h in _AUTO_HINTS):
        return "auto_reply"
    if any(h in text for h in _REJECT_HINTS):
        return "rejected"
    if any(h in text for h in _INTERVIEW_HINTS):
        return "interview"
    return "replied"


# ── schema migration (idempotent) ────────────────────────────
def ensure_schema() -> None:
    c = _conn()
    cols = {r[1] for r in c.execute("PRAGMA table_info(outreach_sends)")}
    for col, ddl in (("reply_status", "TEXT DEFAULT 'no_reply'"),
                     ("last_reply_at", "TEXT"),
                     ("reply_snippet", "TEXT")):
        if col not in cols:
            c.execute(f"ALTER TABLE outreach_sends ADD COLUMN {col} {ddl}")
    c.commit()
    c.close()


# ── helpers ──────────────────────────────────────────────────
def _hdr(v: str) -> str:
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def _extract_text(msg) -> str:
    """Decoded text/plain body of an email.message (handles multipart, base64,
    quoted-printable; falls back to stripped HTML)."""
    def _decode(part):
        try:
            payload = part.get_payload(decode=True)
            return payload.decode(part.get_content_charset() or "utf-8", "ignore") if payload else ""
        except Exception:
            return ""
    if msg.is_multipart():
        for part in msg.walk():
            if (part.get_content_type() == "text/plain"
                    and "attachment" not in str(part.get("Content-Disposition", ""))):
                t = _decode(part)
                if t.strip():
                    return t
        for part in msg.walk():                       # fallback: first HTML part
            if part.get_content_type() == "text/html":
                return re.sub(r"<[^>]+>", " ", _decode(part))
        return ""
    t = _decode(msg)
    if msg.get_content_type() == "text/html":
        t = re.sub(r"<[^>]+>", " ", t)
    return t


def _clean_snippet(body: str, n: int = 240) -> str:
    """First meaningful lines of a reply: drop quoted text + signatures."""
    lines = []
    for ln in (body or "").splitlines():
        s = ln.strip()
        if s.startswith(">"):                    # quoted original
            break
        if re.match(r"^On .+wrote:$", s):        # gmail quote header
            break
        if s.lower().startswith(("from:", "sent:", "to:", "subject:")):
            break
        if s:
            lines.append(s)
        if sum(len(x) for x in lines) > n:
            break
    return re.sub(r"\s+", " ", " ".join(lines))[:n]


def _sent_index() -> tuple[dict, list]:
    """Build address->row and (subject, row) indexes from the send log."""
    c = _conn()
    rows = c.execute("SELECT id, name, company, current_email, tried, subject "
                     "FROM outreach_sends").fetchall()
    c.close()
    by_addr: dict[str, dict] = {}
    by_subject: list[tuple[str, dict]] = []
    for r in rows:
        info = {"id": r["id"], "name": r["name"], "company": r["company"]}
        addrs = set(a.strip().lower() for a in (r["tried"] or "").split(",") if a.strip())
        if r["current_email"]:
            addrs.add(r["current_email"].strip().lower())
        for a in addrs:
            by_addr[a] = info
        if r["subject"]:
            by_subject.append((r["subject"].strip().lower(), info))
    return by_addr, by_subject


# ── inbox scan ───────────────────────────────────────────────
def fetch_replies(since_days: int = 14) -> list[dict]:
    """Scan INBOX for messages that match a sent address (or a reply to one of
    our subjects), excluding bounces. Returns one dict per matched message:
    {row_id, name, company, from, subject, date, snippet, reply_status}.
    """
    if not config.GMAIL_APP_PASSWORD:
        raise RuntimeError("GMAIL_APP_PASSWORD not set — cannot read inbox via IMAP.")
    by_addr, by_subject = _sent_index()
    since = (dt.date.today() - dt.timedelta(days=since_days)).strftime("%d-%b-%Y")

    M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    M.login(config.SENDER_EMAIL, config.GMAIL_APP_PASSWORD)
    M.select("INBOX")
    _, data = M.search(None, "SINCE", since)
    uids = data[0].split()

    out: list[dict] = []
    for uid in uids:
        _, hd = M.fetch(uid, "(BODY.PEEK[HEADER.FIELDS "
                             "(FROM SUBJECT DATE AUTO-SUBMITTED X-AUTOREPLY)])")
        if not hd or not hd[0]:
            continue
        raw = hd[0][1].decode("utf-8", "ignore")
        from_raw = re.search(r"(?im)^From:\s*(.+)$", raw)
        subj_raw = re.search(r"(?im)^Subject:\s*(.+)$", raw)
        date_raw = re.search(r"(?im)^Date:\s*(.+)$", raw)
        from_addr = parseaddr(_hdr(from_raw.group(1)) if from_raw else "")[1].lower()
        subject = _hdr(subj_raw.group(1)) if subj_raw else ""
        date_s = date_raw.group(1).strip() if date_raw else ""
        auto_flag = bool(re.search(r"(?im)^(Auto-Submitted:\s*auto|X-Autoreply:)", raw))

        if not from_addr:
            continue
        if any(x in from_addr for x in ("mailer-daemon", "postmaster", "googlemail")):
            continue                              # bounces handled elsewhere
        if from_addr == (config.SENDER_EMAIL or "").lower():
            continue

        info = by_addr.get(from_addr)
        if not info:                              # subject fallback (reply from alias)
            sl = subject.lower()
            for subj, row in by_subject:
                if subj and subj in sl:
                    info = row
                    break
        if not info:
            continue

        # matched — pull the FULL message and parse it (proper MIME decode)
        _, bd = M.fetch(uid, "(BODY.PEEK[])")
        body = ""
        if bd and bd[0]:
            msg = email.message_from_bytes(bd[0][1])
            body = _extract_text(msg)
            auto_flag = auto_flag or "auto" in msg.get("Auto-Submitted", "").lower() \
                or bool(msg.get("X-Autoreply")) \
                or msg.get("Precedence", "").lower() in ("auto_reply", "bulk", "junk")
        snippet = _clean_snippet(body)
        out.append({
            "row_id": info["id"], "name": info["name"], "company": info["company"],
            "from": from_addr, "subject": subject, "date": date_s,
            "snippet": snippet,
            "reply_status": classify_reply(subject, body, auto_flag),
        })
    M.logout()
    return out


def record_reply(row_id: int, reply_status: str, snippet: str) -> None:
    c = _conn()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    c.execute("UPDATE outreach_sends SET reply_status=?, last_reply_at=?, "
              "reply_snippet=? WHERE id=?", (reply_status, now, snippet[:240], row_id))
    c.commit()
    c.close()


def scan_replies(since_days: int = 14, apply: bool = True) -> list[dict]:
    """Full pass: ensure schema, scan inbox, dedupe per person (real reply beats
    auto-reply; latest wins), optionally persist reply_status. Returns the
    per-person summary."""
    ensure_schema()
    msgs = fetch_replies(since_days)

    # collapse to one record per person (row_id)
    best: dict[int, dict] = {}
    for m in msgs:
        cur = best.get(m["row_id"])
        if cur is None:
            best[m["row_id"]] = m
            continue
        # prefer a real reply over an auto_reply; otherwise keep the later one
        cur_auto = cur["reply_status"] == "auto_reply"
        new_auto = m["reply_status"] == "auto_reply"
        if cur_auto and not new_auto:
            best[m["row_id"]] = m
        elif cur_auto == new_auto and m["date"] >= cur["date"]:
            best[m["row_id"]] = m

    results = sorted(best.values(), key=lambda r: r["date"], reverse=True)
    if apply:
        for r in results:
            record_reply(r["row_id"], r["reply_status"], r["snippet"])
    return results


# ── follow-up candidates + funnel ────────────────────────────
def needs_followup(days: int = 6) -> list[dict]:
    """Delivered contacts with no reply, aged >= `days`, not bounced/exhausted."""
    ensure_schema()
    c = _conn()
    rows = c.execute(
        "SELECT name, company, current_email, subject, updated_at, attempts "
        "FROM outreach_sends "
        "WHERE reply_status='no_reply' AND status IN ('sent','retried') "
        "ORDER BY updated_at").fetchall()
    c.close()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    out = []
    for r in rows:
        try:
            sent_at = dt.datetime.fromisoformat(r["updated_at"])
        except Exception:
            continue
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=dt.timezone.utc)
        if sent_at <= cutoff:
            out.append({"name": r["name"], "company": r["company"],
                        "email": r["current_email"], "subject": r["subject"],
                        "sent_at": r["updated_at"], "days_ago": (cutoff.now(dt.timezone.utc) - sent_at).days})
    return out


def stats() -> dict:
    """Response funnel overall and per company."""
    ensure_schema()
    c = _conn()
    rows = c.execute("SELECT company, status, reply_status FROM outreach_sends").fetchall()
    c.close()

    def blank():
        return {"sent": 0, "bounced": 0, "replied": 0, "interview": 0,
                "rejected": 0, "auto_reply": 0, "no_reply": 0}
    overall = blank()
    per: dict[str, dict] = {}
    for r in rows:
        comp = r["company"] or "(unknown)"
        b = per.setdefault(comp, blank())
        for d in (overall, b):
            d["sent"] += 1
            if r["status"] == "exhausted":
                d["bounced"] += 1
            rs = r["reply_status"] or "no_reply"
            if rs in d:
                d[rs] += 1
    def rate(d):
        real = d["replied"] + d["interview"] + d["rejected"]
        d["response_rate"] = round(100 * real / d["sent"], 1) if d["sent"] else 0.0
        return d
    return {"overall": rate(overall),
            "by_company": {k: rate(v) for k, v in sorted(per.items())}}

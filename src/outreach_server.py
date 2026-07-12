#!/usr/bin/env python3
# ============================================================
#  outreach_server.py
#  Local web server behind the "Email recruiters" button in the
#  daily digest. Clicking a job opens a REVIEW page:
#    - an AI-drafted outreach email for that role
#    - a recruiter builder (paste names + pick the company's email
#      pattern -> it constructs the addresses)
#    - a "Send all" button that sends (resume attached) + logs.
#
#  Nothing is ever sent without you clicking Send on the review
#  page — this preserves the always-review rule.
#
#  Runs locally (http://127.0.0.1:8770). Works from the Mac it
#  runs on; not from a phone (localhost isn't reachable there).
#
#  Start:  python src/outreach_server.py
# ============================================================

from __future__ import annotations

import html
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ats
import config
import gemini
import gmail_sender
import sheets_logger

PORT = 8770
GREET = "FIRSTNAME"  # placeholder replaced per-recipient at send time


def _signature() -> str:
    parts = [f"Best,\n{config.YOUR_NAME}"]
    if config.YOUR_PHONE:
        parts.append(config.YOUR_PHONE)
    emails = " | ".join(e for e in [config.YOUR_EMAIL_PRIMARY, config.YOUR_EMAIL_ALT] if e)
    parts.append(emails)
    if config.YOUR_LINKEDIN:
        parts.append(config.YOUR_LINKEDIN)
    return "\n".join(parts)


def _draft(company: str, title: str) -> tuple[str, str]:
    """AI-draft a subject + body (with FIRSTNAME greeting placeholder)."""
    prompt = (
        f"Write a concise (~110 word) cold outreach email to a recruiter.\n"
        f"Candidate: {config.YOUR_NAME} — {config.YOUR_BIO}. Lead with UC Berkeley CS and "
        f"current experience; tie skills to the role. Applying to: {title} at {company}.\n"
        f"Start the body EXACTLY with 'Hi {GREET},' and do NOT include a signature.\n"
        f"NEVER mention H1B, visa status, work authorization, or sponsorship.\n"
        f'Return compact JSON: {{"subject": "...", "body": "..."}} — subject under 90 chars, '
        f"professional, no buzzwords, no 'I hope this finds you well'."
    )
    try:
        import json, re
        text = gemini.generate(prompt, max_output_tokens=800, temperature=0.6)
        m = re.search(r"\{.*\}", text, re.S)
        d = json.loads(m.group(0))
        return d.get("subject", f"{title} — {config.YOUR_NAME}"), d.get("body", "")
    except Exception as e:
        # Gemini unavailable (no key / quota) or bad JSON — plain fallback draft.
        return (f"{title} at {company} — {config.YOUR_NAME}",
                f"Hi {GREET},\n\nI'm reaching out about the {title} role at {company}. "
                f"I'm {config.YOUR_BIO}, and the role looks like a strong match for my "
                f"experience. Would you be open to a quick call to see if I'd be a good "
                f"fit?\n\n")


def _guess_domain(company: str) -> str:
    tok = company.split("|")[0]                # workday composite -> tenant
    tok = "".join(c for c in tok if c.isalnum())
    return f"{tok}.com"


# ── HTML ─────────────────────────────────────────────────────
def _review_page(company: str, title: str) -> str:
    subject, body = _draft(company, title)
    domain = _guess_domain(company)
    sig = _signature()
    esc = html.escape
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Outreach — {esc(company)}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#202124}}
 h1{{font-size:20px;margin:0 0 2px}} .sub{{color:#5f6368;font-size:13px;margin-bottom:18px}}
 label{{font-weight:600;font-size:13px;display:block;margin:14px 0 4px}}
 input,select,textarea{{width:100%;padding:8px;border:1px solid #dadce0;border-radius:8px;font:inherit;box-sizing:border-box}}
 textarea{{min-height:150px}} .row{{display:flex;gap:10px}} .row>div{{flex:1}}
 .btn{{background:#1a73e8;color:#fff;border:0;border-radius:8px;padding:11px 18px;font-weight:600;font-size:15px;cursor:pointer;margin-top:16px}}
 .btn2{{background:#f1f3f4;color:#202124;border:0;border-radius:8px;padding:8px 14px;font-weight:600;cursor:pointer}}
 .note{{background:#fef7e0;color:#7a5900;font-size:12px;padding:10px 12px;border-radius:8px;margin-top:12px}}
</style></head><body>
<h1>Reach recruiters — {esc(company)}</h1>
<div class="sub">Role: {esc(title)} · resume attached automatically · review, then Send</div>

<form method="POST" action="/send">
 <input type="hidden" name="company" value="{esc(company)}">
 <input type="hidden" name="title" value="{esc(title)}">

 <label>Build recruiter addresses</label>
 <div class="row">
  <div><input id="domain" placeholder="company domain" value="{esc(domain)}"></div>
  <div><select id="pattern">
     <option value="first.last">first.last@</option>
     <option value="flast">flast@</option>
     <option value="firstlast">firstlast@</option>
     <option value="first_last">first_last@</option>
     <option value="first">first@</option>
  </select></div>
 </div>
 <textarea id="names" placeholder="Paste recruiter names, one per line:&#10;Jane Smith&#10;Alex Lee"></textarea>
 <button type="button" class="btn2" onclick="build()">Build addresses ↓</button>

 <label>Recipients (one per line: <code>Name &lt;email&gt;</code>)</label>
 <textarea name="recipients" id="recipients" placeholder="Jane Smith <jane.smith@company.com>"></textarea>

 <label>Subject</label>
 <input name="subject" value="{esc(subject)}">

 <label>Body (<code>{GREET}</code> is replaced with each recipient's first name)</label>
 <textarea name="body" style="min-height:220px">{esc(body)}

{esc(sig)}</textarea>

 <div class="note">⚠ Pattern-guessed addresses can bounce. Prefer verified emails. Sends real
   email to real people — this is the only review step.</div>
 <button type="submit" class="btn">Send all with my resume →</button>
</form>

<script>
function build(){{
 var domain=document.getElementById('domain').value.trim().replace(/^@/,'');
 var pat=document.getElementById('pattern').value;
 var lines=document.getElementById('names').value.split('\\n').map(s=>s.trim()).filter(Boolean);
 var out=lines.map(function(n){{
   var p=n.toLowerCase().replace(/[^a-z ]/g,'').split(/\\s+/);
   if(p.length<1) return '';
   var f=p[0], l=p[p.length-1]||'';
   var local={{'first.last':f+'.'+l,'flast':f[0]+l,'firstlast':f+l,'first_last':f+'_'+l,'first':f}}[pat];
   return n+' <'+local+'@'+domain+'>';
 }}).filter(Boolean);
 var box=document.getElementById('recipients');
 box.value=(box.value?box.value+'\\n':'')+out.join('\\n');
}}
</script>
</body></html>"""


def _parse_recipients(raw: str) -> list[dict]:
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "<" in line and ">" in line:
            name = line[:line.index("<")].strip()
            email = line[line.index("<") + 1:line.index(">")].strip()
        else:
            name, email = "", line
        if "@" not in email:
            continue
        first = (name.split()[0] if name else email.split("@")[0].split(".")[0]).strip()
        out.append({"name": name or first, "first_name": first, "email": email})
    return out


def _send_page(company: str, title: str, recipients: list[dict], subject: str, body: str) -> str:
    job = {"company": company, "title": title, "job_url": "", "h1b_signal": 1}
    outreach_list, results = [], []
    for r in recipients:
        personal = body.replace(GREET, r["first_name"])
        outreach_list.append({"to_email": r["email"], "to_name": r["name"],
                              "subject": subject, "body": personal})
    sent = gmail_sender.send_batch(outreach_list, dry_run=False)
    for r, s in zip(recipients, sent):
        ok = s.get("sent")
        if ok:
            try:
                sheets_logger.log_outreach(job, r, {"subject": subject, "body": ""}, status="Sent")
            except Exception:
                pass
        results.append((r, ok))
    rows = "".join(
        f"<li>{'✅' if ok else '❌'} {html.escape(r['name'])} &lt;{html.escape(r['email'])}&gt;</li>"
        for r, ok in results)
    n_ok = sum(1 for _, ok in results if ok)
    return (f"<!doctype html><html><body style='font-family:sans-serif;max-width:640px;margin:40px auto'>"
            f"<h2>Sent {n_ok}/{len(results)} for {html.escape(company)}</h2><ul>{rows}</ul>"
            f"<p style='color:#5f6368'>Logged to your sheet. Watch for bounces on guessed addresses.</p>"
            f"<a href='javascript:window.close()'>Close</a></body></html>")


def _jd_page(company: str, title: str, ats_name: str, job_id: str) -> str:
    """Fetch the full job description and auto-copy it to the clipboard."""
    job = {"ats": ats_name, "company": company, "job_id": job_id}
    jd = ats.description(job).strip()
    display = company.split("|")[0]
    header = f"{title} — {display}"
    payload = (f"{header}\n\n{jd}" if jd
               else f"{header}\n\n(No description text was available from this board.)")
    esc = html.escape
    # localhost is a secure context, so navigator.clipboard works.
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>JD copied</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#202124}}
 #status{{font-size:15px;font-weight:600;padding:10px 14px;border-radius:8px;background:#e6f4ea;color:#137333;display:inline-block}}
 pre{{white-space:pre-wrap;background:#f8f9fa;border:1px solid #eaecef;border-radius:10px;padding:16px;font:13px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin-top:14px}}
 button{{background:#1a73e8;color:#fff;border:0;border-radius:8px;padding:10px 16px;font-weight:600;cursor:pointer;margin-top:12px}}
</style></head><body>
<div><span id="status">Copying…</span></div>
<h2 style="margin:14px 0 0">{esc(header)}</h2>
<button onclick="copyJD()">Copy again</button>
<pre id="jd">{esc(payload)}</pre>
<script>
 var TEXT = document.getElementById('jd').textContent;
 function copyJD(){{
   navigator.clipboard.writeText(TEXT).then(function(){{
     document.getElementById('status').textContent = '✓ Job description copied to clipboard';
   }}).catch(function(){{
     document.getElementById('status').textContent = 'Select the text below and copy manually (⌘C)';
     document.getElementById('status').style.background='#fef7e0';
     document.getElementById('status').style.color='#7a5900';
   }});
 }}
 copyJD();
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _html(self, body: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass  # quiet

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/health":
            return self._html("ok")
        if u.path == "/jd":
            return self._html(_jd_page(
                (q.get("company", [""])[0]).strip(),
                (q.get("title", [""])[0]).strip(),
                (q.get("ats", [""])[0]).strip(),
                (q.get("job_id", [""])[0]).strip()))
        if u.path == "/prepare":
            company = (q.get("company", [""])[0]).strip()
            title = (q.get("title", [""])[0]).strip()
            if not company:
                return self._html("<p>Missing company.</p>", 400)
            return self._html(_review_page(company, title))
        self._html("<p>Not found</p>", 404)

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/send":
            return self._html("<p>Not found</p>", 404)
        length = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        company = form.get("company", [""])[0]
        title = form.get("title", [""])[0]
        subject = form.get("subject", [""])[0]
        body = form.get("body", [""])[0]
        recipients = _parse_recipients(form.get("recipients", [""])[0])
        if not recipients:
            return self._html("<p>No valid recipients. <a href='javascript:history.back()'>Back</a></p>", 400)
        self._html(_send_page(company, title, recipients, subject, body))


def main():
    print(f"Outreach server on http://127.0.0.1:{PORT}  (Ctrl-C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()

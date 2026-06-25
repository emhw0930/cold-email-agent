#!/usr/bin/env python3
# ============================================================
#  graph_workflow.py — LangGraph outreach pipeline
#
#  Models the outreach flow as a stateful graph:
#
#    find_recruiters → generate_emails → [human review/interrupt] → send_and_log
#
#  - LLM email generation uses Claude via langchain-anthropic (ChatAnthropic)
#  - The human-approval step is a real LangGraph interrupt() (human-in-the-loop)
#  - Existing tools (Prospeo, Gmail, Sheets) are wrapped as graph nodes
#
#  Usage:
#    python src/graph_workflow.py --company twilio.com --title "Software Engineer (L1)" \
#        --jd jd.txt --max 5            # runs to the review interrupt, prints drafts
#    python src/graph_workflow.py ... --send   # auto-approves and sends
# ============================================================

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.types import Command, interrupt

import config
from prospeo_lookup import _search_recruiters, _reveal_email
from gmail_sender import send_email
from sheets_logger import already_emailed, log_outreach


# ── Graph state ───────────────────────────────────────────────
class OutreachState(TypedDict, total=False):
    company_domain: str
    company_name: str
    title: str
    jd_text: str
    max_people: int
    send: bool
    recruiters: list   # [{name, first_name, job_title, email}]
    drafts: list       # [{name, email, subject, body}]
    results: list      # [{name, email, status}]


# ── LLM (Claude via LangChain) ────────────────────────────────
_SYSTEM = """You write concise cold outreach emails from a job seeker to a recruiter.
Rules:
- 100-120 words, body only (no subject line in the body)
- Greet the recruiter by first name
- Name the exact job title + company in the first sentence
- One or two sentences tying the candidate's background to the role
- Professional, specific, confident. No buzzwords, no "I hope this finds you well"
- End with the signature block EXACTLY as provided (name, phone, emails, LinkedIn)
- Output ONLY the email body."""

_USER = """Write a cold email for:
Recruiter first name: {first_name}
Job title: {title}
Company: {company}
Job description (excerpt): {jd}

Candidate:
Name: {name}
Bio: {bio}

Signature block to end with exactly:
{name}
{phone}
{emails}
{linkedin}"""

_llm = ChatAnthropic(
    model=getattr(config, "EMAIL_MODEL", "claude-haiku-4-5"),
    api_key=config.ANTHROPIC_API_KEY,
    max_tokens=400,
)
_chain = ChatPromptTemplate.from_messages([("system", _SYSTEM), ("user", _USER)]) | _llm | StrOutputParser()


def _emails_line() -> str:
    if config.YOUR_EMAIL_ALT:
        return f"{config.YOUR_EMAIL_PRIMARY} | {config.YOUR_EMAIL_ALT}"
    return config.YOUR_EMAIL_PRIMARY


# ── Nodes ─────────────────────────────────────────────────────
def find_recruiters(state: OutreachState) -> dict:
    domain = state["company_domain"]
    print(f"\n▶ [node] find_recruiters — {domain}")
    people = _search_recruiters(domain, us_only=True)
    out = []
    for p in people:
        if len(out) >= state.get("max_people", 5):
            break
        email = _reveal_email(p, domain)
        if email:
            p["email"] = email
            out.append(p)
            print(f"   ✅ {p['name']} <{email}>")
        time.sleep(0.4)
    return {"recruiters": out, "company_name": domain.split(".")[0].title()}


def generate_emails(state: OutreachState) -> dict:
    print(f"\n▶ [node] generate_emails — {len(state.get('recruiters', []))} recruiter(s)")
    drafts = []
    subject = f"{state['title']} — {config.YOUR_NAME}"
    for r in state.get("recruiters", []):
        body = _chain.invoke({
            "first_name": r.get("first_name") or r.get("name", "there"),
            "title": state["title"],
            "company": state["company_name"],
            "jd": state["jd_text"][:600],
            "name": config.YOUR_NAME,
            "bio": config.YOUR_BIO,
            "phone": config.YOUR_PHONE,
            "emails": _emails_line(),
            "linkedin": config.YOUR_LINKEDIN,
        })
        drafts.append({"name": r["name"], "email": r["email"], "subject": subject, "body": body.strip()})
    return {"drafts": drafts}


def human_review(state: OutreachState) -> Command:
    """Human-in-the-loop: pause and surface drafts for approval."""
    payload = [{"to": d["name"], "email": d["email"], "subject": d["subject"], "body": d["body"]}
               for d in state.get("drafts", [])]
    # interrupt() pauses the graph; the caller resumes with "approve" or "reject"
    decision = interrupt({"action": "review_drafts", "drafts": payload})
    return Command(goto="send_and_log" if decision == "approve" else END)


def send_and_log(state: OutreachState) -> dict:
    print(f"\n▶ [node] send_and_log")
    results = []
    job_base = {"company": state["company_name"], "job_url": "", "date_posted": "", "h1b_signal": 1}
    for d in state.get("drafts", []):
        if already_emailed(d["email"]):
            print(f"   ⏭ already emailed {d['email']}")
            results.append({"name": d["name"], "email": d["email"], "status": "Skipped"})
            continue
        ok = send_email(d["email"], d["name"], d["subject"], d["body"], dry_run=False)
        job = {**job_base, "title": f"{state['title']} ({d['name']})"}
        try:
            log_outreach(job, {"name": d["name"], "title": "Recruiter", "email": d["email"]},
                         {"subject": d["subject"]}, status="Sent" if ok else "Failed")
        except Exception as e:
            print(f"   ⚠ log failed: {e}")
        results.append({"name": d["name"], "email": d["email"], "status": "Sent" if ok else "Failed"})
        print(f"   {d['name']}: {'SENT' if ok else 'FAILED'}")
        time.sleep(config.EMAIL_SEND_DELAY_SECONDS)
    return {"results": results}


# ── Build the graph ───────────────────────────────────────────
def build_graph():
    g = StateGraph(OutreachState)
    g.add_node("find_recruiters", find_recruiters)
    g.add_node("generate_emails", generate_emails)
    g.add_node("human_review", human_review)
    g.add_node("send_and_log", send_and_log)
    g.add_edge(START, "find_recruiters")
    g.add_edge("find_recruiters", "generate_emails")
    g.add_edge("generate_emails", "human_review")
    g.add_edge("send_and_log", END)
    return g.compile(checkpointer=MemorySaver())


# ── CLI runner ────────────────────────────────────────────────
def run(company: str, title: str, jd_text: str, max_people: int, send: bool) -> None:
    graph = build_graph()
    cfg = {"configurable": {"thread_id": f"{company}-{int(time.time())}"}}
    state = {"company_domain": company, "title": title, "jd_text": jd_text,
             "max_people": max_people, "send": send}

    # Run until the human_review interrupt
    result = graph.invoke(state, cfg)
    interrupts = result.get("__interrupt__")
    if not interrupts:
        print("\n(no drafts produced — nothing to review)")
        return

    drafts = interrupts[0].value["drafts"]
    print(f"\n{'═'*60}\n  REVIEW — {len(drafts)} draft(s)\n{'═'*60}")
    for d in drafts:
        print(f"\nTo: {d['to']} <{d['email']}>\nSubject: {d['subject']}\n\n{d['body']}\n{'─'*60}")

    if not send:
        print("\nPreview only. Re-run with --send to approve and send.")
        return

    print("\n▶ Auto-approving (--send) ...")
    final = graph.invoke(Command(resume="approve"), cfg)
    sent = sum(1 for r in final.get("results", []) if r["status"] == "Sent")
    print(f"\n{'═'*60}\n  Sent: {sent} / {len(drafts)}\n{'═'*60}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LangGraph outreach workflow")
    p.add_argument("--company", required=True, help="Company domain, e.g. twilio.com")
    p.add_argument("--title", required=True, help="Job title")
    p.add_argument("--jd", help="Path to JD text file")
    p.add_argument("--max", type=int, default=5)
    p.add_argument("--send", action="store_true", help="Approve + send (default: preview)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    jd = Path(args.jd).read_text() if args.jd and Path(args.jd).exists() else ""
    run(args.company, args.title, jd, args.max, args.send)

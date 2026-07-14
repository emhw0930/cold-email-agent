# ============================================================
#  email_generator.py
#  Uses Gemini (free tier) to write a short, specific cold email
#  per job. Target: ~120-150 words. Personalized to company + role.
#  If Gemini is unavailable (no key / daily quota spent), falls back
#  to a plain template so outreach never hard-fails.
# ============================================================

from __future__ import annotations

import config
import gemini

_SYSTEM_PROMPT = """You write concise, professional cold outreach emails from a job seeker \
to a corporate recruiter. Follow proven cold-email best practices: a recruiter should \
understand in five seconds who this is, which role it's about, and why they should reply.

Write the body in this order:
1. Greeting using the recruiter's first name.
2. One sentence stating exactly why you're writing — name the SPECIFIC role (and requisition \
   ID if provided) and the company — so it reads as a deliberate, one-to-one message.
3. One or two sentences of concrete value: the candidate's most relevant experience, project, \
   or result for THIS role, drawn from the background provided. Be specific and factual; name \
   real technologies or outcomes rather than adjectives.
4. Exactly ONE clear, low-friction ask (e.g. "Would you be the right person to talk to about \
   this role?" or "Would you be open to a brief call?"). Never more than one ask.
5. A sign-off using the exact signature lines provided, one per line.

Hard rules:
- 90-130 words in the body. Short, scannable paragraphs (1-3 sentences each).
- Tone: professional, confident, and respectful of the reader's time — never casual, \
  groveling, or salesy.
- Mention briefly that the résumé is attached.
- NEVER mention H1B, visa status, work authorization, or sponsorship.
- No buzzwords or clichés ("passionate", "synergy", "leverage", "rockstar", "guru", \
  "I hope this email finds you well") and no spammy words ("free", "guarantee", "act now").
- Output ONLY the email body — no subject line, no preamble, no markdown, no notes."""


def generate_subject(job: dict) -> str:
    """A short, specific subject line: the role + candidate name (no H1B/visa mention)."""
    return f"{job['title']} — {config.YOUR_NAME}"


def _signature() -> str:
    """The exact contact lines to close every email with, one per line."""
    lines = [config.YOUR_NAME]
    if config.YOUR_PHONE:
        lines.append(config.YOUR_PHONE)
    emails = config.YOUR_EMAIL_PRIMARY
    if config.YOUR_EMAIL_ALT:
        emails += f" | {config.YOUR_EMAIL_ALT}"
    lines.append(emails)
    if config.YOUR_LINKEDIN:
        lines.append(config.YOUR_LINKEDIN)
    return "\n".join(lines)


def _template_body(job: dict, recruiter: dict) -> str:
    """Deterministic fallback email when Gemini is unavailable. Lower-touch
    than the generated version but still specific and sendable. Never
    mentions H1B / visa / sponsorship."""
    greeting = recruiter.get("first_name") or recruiter.get("name") or "there"
    return (
        f"Hi {greeting},\n\n"
        f"I'm writing about the {job['title']} role at {job['company']}. "
        f"I'm {config.YOUR_BIO}, and I believe my background is a close match for what "
        f"the role calls for.\n\n"
        f"My résumé is attached. Would you be the right person to speak with about this "
        f"role, or could you point me to whoever is?\n\n"
        f"Thanks for your time,\n{_signature()}"
    )


def generate_email_body(job: dict, recruiter: dict) -> str:
    """
    Call Gemini to write a personalized cold email body. Falls back to a
    plain template if Gemini is unavailable.

    job keys: title, company, description_snippet
    recruiter keys: first_name, name, title, email
    """
    req = job.get("req") or job.get("job_id") or ""
    user_prompt = f"""Write a cold outreach email for this situation.

Recipient: {recruiter.get('first_name') or recruiter.get('name', 'Recruiter')} ({recruiter['title']} at {job['company']})
Job title: {job['title']}
Company: {job['company']}
Requisition/ID (mention only if non-empty): {req}
Job posting snippet: {job.get('description_snippet', '')[:300]}

Candidate background — use this ONLY to write the value sentence(s); do NOT put it in the signature:
{config.YOUR_BIO}

Signature — end with a short sign-off ("Best," or "Thanks for your time,") then EXACTLY these lines, one per line, verbatim, with nothing after them:
{_signature()}"""

    try:
        body = gemini.generate(user_prompt, system=_SYSTEM_PROMPT,
                               max_output_tokens=800, temperature=0.6)
        return body.strip()
    except gemini.GeminiUnavailable:
        print("  ⚠ Gemini unavailable — using template email")
        return _template_body(job, recruiter)


def generate_outreach(job: dict, recruiter: dict) -> dict:
    """
    Returns dict with keys: subject, body, to_email, to_name
    """
    print(f"  ✍️  Generating email for {job['title']} @ {job['company']} ...")
    subject = generate_subject(job)
    body = generate_email_body(job, recruiter)

    return {
        "subject": subject,
        "body": body,
        "to_email": recruiter["email"],
        "to_name": recruiter["name"],
    }


# ── Quick test ───────────────────────────────────────────────
if __name__ == "__main__":
    test_job = {
        "title": "Software Engineer I",
        "company": "Stripe",
        "description_snippet": (
            "We are looking for a software engineer to join our payments team. "
            "We sponsor H1B visas for qualified candidates."
        ),
        "h1b_signal": 2,
    }
    test_recruiter = {
        "first_name": "Sarah",
        "name": "Sarah Johnson",
        "title": "Technical Recruiter",
        "email": "sarah.johnson@stripe.com",
    }

    result = generate_outreach(test_job, test_recruiter)
    print("\n── SUBJECT ────────────────────────────────")
    print(result["subject"])
    print("\n── BODY ───────────────────────────────────")
    print(result["body"])

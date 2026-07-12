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

_SYSTEM_PROMPT = """You write cold outreach emails from a job seeker to a corporate recruiter.

Rules:
- 120-150 words max (body only, no subject line in body)
- Open with the recruiter's first name greeting
- Mention the EXACT job title and company name in the first sentence
- Two concrete sentences about the candidate's background (use the bio provided)
- End with a clear ask: "Would you be open to a quick call?" or similar
- Tone: confident, professional, not groveling
- NEVER mention H1B, visa status, work authorization, or sponsorship anywhere in the email
- NO filler phrases like "I hope this email finds you well"
- NO buzzwords like "passionate", "synergy", "leverage"
- Sign off with name, phone, email(s), and LinkedIn (exactly as provided)
- Do NOT include the subject line in the body text
- Output ONLY the email body, nothing else"""


def generate_subject(job: dict) -> str:
    """Generate a concise, specific subject line (no H1B/visa mention)."""
    title = job["title"]
    company = job["company"]
    return f"Interested in the {title} role at {company}"


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
        f"I'm reaching out about the {job['title']} role at {job['company']}. "
        f"For background, I'm {config.YOUR_BIO}, and the role looks like a strong "
        f"match for my experience.\n\n"
        f"Would you be open to a quick call to see if I'd be a good fit?\n\n"
        f"Best,\n{_signature()}"
    )


def generate_email_body(job: dict, recruiter: dict) -> str:
    """
    Call Gemini to write a personalized cold email body. Falls back to a
    plain template if Gemini is unavailable.

    job keys: title, company, description_snippet
    recruiter keys: first_name, name, title, email
    """
    user_prompt = f"""Write a cold email for this situation:

Recipient: {recruiter.get('first_name') or recruiter.get('name', 'Recruiter')} ({recruiter['title']} at {job['company']})
Job Title: {job['title']}
Company: {job['company']}
Job posting snippet: {job.get('description_snippet', '')[:300]}

Sender info (use these EXACT contact lines in the signature, one per line):
Name: {config.YOUR_NAME}
Bio: {config.YOUR_BIO}
Phone: {config.YOUR_PHONE}
Email: {config.YOUR_EMAIL_PRIMARY}{(' | ' + config.YOUR_EMAIL_ALT) if config.YOUR_EMAIL_ALT else ''}
LinkedIn: {config.YOUR_LINKEDIN}"""

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

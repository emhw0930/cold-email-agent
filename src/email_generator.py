# ============================================================
#  email_generator.py
#  Uses Claude to write a short, specific cold email per job.
#  Target: ~120-150 words. Personalized to company + role.
# ============================================================

from __future__ import annotations

import anthropic

import config

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


_SYSTEM_PROMPT = """You write cold outreach emails from a job seeker to a corporate recruiter.

Rules:
- 120-150 words max (body only, no subject line in body)
- Open with the recruiter's first name greeting
- Mention the EXACT job title and company name in the first sentence
- One sentence on H1B sponsorship (acknowledge they sponsor or ask if they do — match the h1b_signal)
- One concrete sentence about the candidate's background (use the bio provided)
- End with a clear ask: "Would you be open to a quick call?" or similar
- Tone: confident, professional, not groveling
- NO filler phrases like "I hope this email finds you well"
- NO buzzwords like "passionate", "synergy", "leverage"
- Sign off with name, phone, email(s), and LinkedIn (exactly as provided)
- Do NOT include the subject line in the body text
- Output ONLY the email body, nothing else"""


def generate_subject(job: dict) -> str:
    """Generate a concise, specific subject line."""
    title = job["title"]
    company = job["company"]
    return f"Interested in {title} role at {company} — H1B Sponsorship"


def generate_email_body(job: dict, recruiter: dict) -> str:
    """
    Call Claude to write a personalized cold email body.

    job keys: title, company, description_snippet, h1b_signal
    recruiter keys: first_name, name, title, email
    """
    h1b_context = (
        "The company explicitly mentions H1B sponsorship in the job posting."
        if job.get("h1b_signal") == 2
        else "The company is a known H1B sponsor based on USCIS records."
    )

    user_prompt = f"""Write a cold email for this situation:

Recipient: {recruiter.get('first_name') or recruiter.get('name', 'Recruiter')} ({recruiter['title']} at {job['company']})
Job Title: {job['title']}
Company: {job['company']}
H1B context: {h1b_context}
Job posting snippet: {job.get('description_snippet', '')[:300]}

Sender info (use these EXACT contact lines in the signature, one per line):
Name: {config.YOUR_NAME}
Bio: {config.YOUR_BIO}
Phone: {config.YOUR_PHONE}
Email: {config.YOUR_EMAIL_PRIMARY}{(' | ' + config.YOUR_EMAIL_ALT) if config.YOUR_EMAIL_ALT else ''}
LinkedIn: {config.YOUR_LINKEDIN}"""

    response = _get_client().messages.create(
        model="claude-haiku-4-5-20251001",   # fast + cheap for bulk generation
        max_tokens=400,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return response.content[0].text.strip()


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

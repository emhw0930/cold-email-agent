#!/usr/bin/env python3
# ============================================================
#  main.py — H1B Job Application Agent
#
#  Usage:
#    python main.py              # live run
#    python main.py --dry-run    # preview emails, no sends
#    python main.py --hours 48   # look back 48 hours instead of 24
#    python main.py --max 5      # process at most 5 jobs
# ============================================================

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import config
from job_discovery import fetch_fresh_jobs
from prospeo_lookup import batch_find_recruiters
from email_generator import generate_outreach
from gmail_sender import send_email
from sheets_logger import already_contacted, log_outreach


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="H1B Job Application Agent")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview emails without sending")
    p.add_argument("--hours", type=int, default=config.JOB_MAX_AGE_HOURS,
                   help=f"How many hours back to search (default: {config.JOB_MAX_AGE_HOURS})")
    p.add_argument("--max", type=int, default=config.MAX_JOBS_PER_RUN,
                   help=f"Max jobs to process (default: {config.MAX_JOBS_PER_RUN})")
    return p.parse_args()


def run(dry_run: bool = False, hours: int = 24, max_jobs: int = 10) -> None:
    start_time = datetime.now(timezone.utc)
    print(f"\n{'═'*60}")
    print(f"  H1B Job Application Agent — {start_time.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Mode: {'DRY RUN (no emails sent)' if dry_run else 'LIVE'}")
    print(f"  Looking back: {hours}h  |  Max jobs: {max_jobs}")
    print(f"{'═'*60}\n")

    # ── Step 1: Discover fresh H1B-sponsoring jobs ────────────
    print("▶ Step 1: Discovering fresh H1B-sponsoring jobs...")
    jobs = fetch_fresh_jobs(hours_old=hours, max_results=max_jobs * 3)

    if not jobs:
        print("\n⚠ No new H1B-eligible jobs found. Try increasing --hours.")
        return

    # ── Step 2: Deduplicate against Sheets ───────────────────
    print("\n▶ Step 2: Deduplicating against existing applications...")
    new_jobs = []
    for job in jobs:
        if already_contacted(job["company"], job["title"]):
            print(f"  ⏭ Already applied: {job['title']} @ {job['company']}")
        else:
            new_jobs.append(job)
        if len(new_jobs) >= max_jobs:
            break

    if not new_jobs:
        print("\n✅ All found jobs already applied to. Nothing to do.")
        return

    print(f"\n  → {len(new_jobs)} new jobs to process")

    # ── Step 3: Find recruiter emails via Apollo ──────────────
    print("\n▶ Step 3: Finding recruiter emails via Apollo.io...")
    enriched_jobs = batch_find_recruiters(new_jobs)

    if not enriched_jobs:
        print("\n⚠ No recruiter emails found. Check your Apollo API key.")
        return

    print(f"\n  → {len(enriched_jobs)} jobs with recruiter emails")

    # ── Step 4: Generate + send emails ───────────────────────
    print(f"\n▶ Step 4: Generating and {'previewing' if dry_run else 'sending'} emails...")

    sent_count = 0
    failed_count = 0

    for i, job in enumerate(enriched_jobs, 1):
        recruiter = job["recruiter"]
        print(f"\n[{i}/{len(enriched_jobs)}] {job['title']} @ {job['company']}")
        print(f"     Recruiter : {recruiter['name']} <{recruiter['email']}>")

        # Generate personalized email
        try:
            outreach = generate_outreach(job, recruiter)
        except Exception as e:
            print(f"  ❌ Email generation failed: {e}")
            failed_count += 1
            continue

        # Send (or preview)
        success = send_email(
            to_email=outreach["to_email"],
            to_name=outreach["to_name"],
            subject=outreach["subject"],
            body=outreach["body"],
            dry_run=dry_run,
        )

        # Log to Google Sheets
        status = "Sent" if (success and not dry_run) else ("Dry Run" if dry_run else "Failed")
        try:
            log_outreach(job, recruiter, outreach, status=status)
        except Exception as e:
            print(f"  ⚠ Sheets logging failed: {e}")

        if success:
            sent_count += 1
        else:
            failed_count += 1

    # ── Summary ───────────────────────────────────────────────
    elapsed = (datetime.now(timezone.utc) - start_time).seconds
    print(f"\n{'═'*60}")
    print(f"  Run complete in {elapsed}s")
    print(f"  {'Previewed' if dry_run else 'Sent'}  : {sent_count}")
    print(f"  Failed : {failed_count}")
    print(f"  Skipped (already applied / no email): "
          f"{len(jobs) - len(enriched_jobs) + failed_count}")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    args = parse_args()

    # Override config DRY_RUN with CLI flag
    dry_run = args.dry_run or config.DRY_RUN

    try:
        run(dry_run=dry_run, hours=args.hours, max_jobs=args.max)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        sys.exit(0)

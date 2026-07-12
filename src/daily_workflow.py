#!/usr/bin/env python3
# ============================================================
#  daily_workflow.py
#  The one daily entry point (GitHub Actions or launchd/cron):
#
#    1. Fetch all current roles (Workday + Greenhouse/Lever/Ashby)
#    2. Score once (score_daily): postings first seen TODAY are scored
#       from their full JD (years-of-experience aware) and persisted;
#       older roles reuse their stored score — no daily re-scan of the
#       whole ~800-role pool. Both outputs below share these scores.
#    3. Email the TOP 10 unemailed roles by fit (+ sponsorship gate),
#       mark them so they don't repeat
#    4. Regenerate the public website with ALL roles + their scores
#
#  The email step and the site step are isolated: a failure in one
#  never blocks the other (scoring runs first so both can use it, but
#  the site is written regardless of email outcome).
#
#  Usage:
#    python src/daily_workflow.py --to you@example.com
#    python src/daily_workflow.py --to you@example.com --dry-run
# ============================================================

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import h1b_greenhouse as hg
import fit_ranker
import daily_job_email
import jobs_site
import config


def rebuild_site(jobs: list[dict]) -> bool:
    """Regenerate docs/index.html with every current role. Returns success."""
    try:
        site_jobs = []
        for j in jobs:
            item = {
                "company": (j.get("company") or "").split("|")[0],
                "title": j.get("title", ""),
                "location": j.get("location", "") or "Location N/A",
                "url": j.get("url", ""),
                "source": j.get("ats", ""),
                "new_grad": bool(j.get("new_grad")),
                "is_new": bool(j.get("is_new")),
                "first_seen": j.get("first_seen", "") or j.get("updated_at", ""),
            }
            # every role gets a fit score: the LLM score from the ranking step
            # if it has one, else a free keyword score (fully populates "Best fit")
            if isinstance(j.get("fit_score"), int) and j["fit_score"] >= 0:
                item["fit_score"], item["fit_reason"] = j["fit_score"], j.get("fit_reason", "")
            else:
                item["fit_score"], item["fit_reason"] = fit_ranker.keyword_fit(
                    item["title"], item["company"])
            site_jobs.append(item)
        html = jobs_site.build(site_jobs, dt.date.today().strftime("%B %-d, %Y"))
        out = Path(config.PROJECT_ROOT) / "docs" / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print(f"  ✅ Site: wrote {len(site_jobs)} roles to {out} ({out.stat().st_size // 1024} KB)")
        return True
    except Exception as e:
        print(f"  ❌ Site regeneration failed: {e}", file=sys.stderr)
        return False


def email_top10(jobs: list[dict], to_email: str, dry_run: bool) -> bool:
    """Email the 10 best-fit unemailed roles by their persisted score, and mark
    them. Scores come from score_daily (JD-based for new postings); the
    sponsorship gate drops roles whose JD says 'no sponsorship'. Returns success."""
    try:
        fresh = hg.filter_unemailed(jobs)
        print(f"  {len(jobs) - len(fresh)} roles already emailed before — skipped; "
              f"{len(fresh)} candidates")
        eligible = [j for j in fresh if not j.get("no_sponsorship")]
        eligible.sort(key=lambda j: j.get("fit_score", 0), reverse=True)
        top10 = eligible[:10]
        if not top10:
            print("  Nothing new to email today.")
            return True

        print(f"  Top {len(top10)} by fit:")
        for j in top10:
            print(f"    {j.get('fit_score', '?'):>3}  [{j['ats']}:{j['company']}]  "
                  f"{j['title']} — {j.get('location', '')}")

        if dry_run:
            print("  (dry-run — no email sent, nothing marked as emailed)")
            return True

        daily_job_email.send_digest(to_email, top10)
        hg.mark_emailed(top10, to_email=to_email)
        print(f"  ✅ Email: sent {len(top10)} best-fit roles to {to_email}")
        return True
    except Exception as e:
        print(f"  ❌ Email step failed: {e}", file=sys.stderr)
        return False


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Daily run: rebuild the site with all roles + email the top-10 by fit")
    ap.add_argument("--to", required=True, help="recipient email for the top-10 digest")
    ap.add_argument("--dry-run", action="store_true",
                    help="write the site but don't send email or mark roles")
    args = ap.parse_args()

    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] daily workflow starting")
    try:
        jobs = hg.daily_fresh_swe(us_only=True, include_aggregator=False,
                                  include_custom=False)
    except RuntimeError as e:
        print(f"ERROR fetching roles: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  Fetched {len(jobs)} roles from the boards")

    # Score once, shared by both outputs: NEW postings today are scored from
    # their full JD (years-of-experience aware) and persisted; older roles reuse
    # their stored score. The site build and the email both read these.
    jobs = fit_ranker.score_daily(jobs)

    email_ok = email_top10(jobs, args.to, args.dry_run)
    site_ok = rebuild_site(jobs)

    if site_ok and email_ok:
        print("Done: site rebuilt + digest handled.")
    else:
        print("Done with errors (see above).", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

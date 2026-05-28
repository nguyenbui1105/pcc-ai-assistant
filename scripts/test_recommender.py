"""Quick smoke test for the course registration agent.

Usage:
    python scripts/test_recommender.py              # uses saved GRAD Plan cookies
    python scripts/test_recommender.py --mock       # uses mock degree audit (no cookies)
"""
import sys
import argparse
sys.path.insert(0, ".")

from loguru import logger

parser = argparse.ArgumentParser()
parser.add_argument("--mock", action="store_true", help="Use mock degree audit instead of real cookies")
parser.add_argument("--term", default="summer2026", help="Term to check (default: summer2026)")
parser.add_argument("--subject", help="Test single subject HTML parsing only (e.g. BI)")
args = parser.parse_args()

# ── Test single subject parsing ────────────────────────────────────────────
if args.subject:
    from src.schedule.fetcher import fetch_subjects
    from src.schedule.parser import parse_schedule_html

    logger.info(f"Fetching schedule for {args.subject}/{args.term}...")
    html_map = fetch_subjects([args.subject], term=args.term)
    html = html_map.get(args.subject, "")
    if not html:
        print(f"ERROR: no HTML returned for {args.subject}")
        sys.exit(1)

    print(f"\n--- Raw HTML excerpt (first 2000 chars) ---")
    print(html[:2000])
    print("\n--- Parsed sections ---")
    sections = parse_schedule_html(html, args.subject)
    print(f"Total sections: {len(sections)}")
    for s in sections[:10]:
        print(f"  {s.summary()}")
    sys.exit(0)

# ── Full recommender test ──────────────────────────────────────────────────
if args.mock:
    from src.mypcc.parser import DegreeAudit, DegreeRequirement

    audit = DegreeAudit(
        degree="Associate of Arts Oregon Transfer (AAOT)",
        gpa="4.00",
        credits_applied=69,
        credits_required=90,
        status="INCOMPLETE",
        requirements=[
            DegreeRequirement("Arts and Letters", "incomplete", "1 course needed"),
            DegreeRequirement("Social Sciences", "incomplete", "1 course from 2+ disciplines"),
            DegreeRequirement("Lab Requirement 1", "incomplete", ""),
            DegreeRequirement("Lab Requirement 2", "incomplete", ""),
            DegreeRequirement("Lab Requirement 3", "incomplete", ""),
        ],
    )
    logger.info("Using mock degree audit")
else:
    from src.mypcc.fetcher import fetch_degree_audit
    from src.mypcc.parser import parse_degree_audit

    _GRADPLAN_COOKIES = "./data/gradplan_cookies.json"
    logger.info("Loading GRAD Plan from cookies...")
    try:
        text = fetch_degree_audit(_GRADPLAN_COOKIES)
        audit = parse_degree_audit(text)
        logger.success(f"Loaded: {audit.degree} — {audit.credits_applied}/{audit.credits_required} credits")
    except Exception as e:
        logger.error(f"Failed to load GRAD Plan: {e}")
        logger.info("Tip: run with --mock to use a mock audit")
        sys.exit(1)

# Run the recommender
from src.agent.course_recommender import recommend_courses

print("\n" + "=" * 60)
result = recommend_courses(audit, term=args.term)
print(result)
print("=" * 60)

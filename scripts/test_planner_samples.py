"""Sample outputs for schedule planner — domestic vs international, various histories."""
import sys, asyncio
sys.path.insert(0, ".")
from loguru import logger
logger.remove()

from src.mypcc.parser import DegreeAudit, DegreeRequirement
from src.agent.schedule_planner import plan_schedule_async

SEP = "=" * 70

async def run(label, audit, prefs, is_international=False):
    print(f"\n{SEP}")
    print(f"SCENARIO: {label}")
    print(f"Prefs   : {prefs}")
    print(f"Intl    : {'YES (F-1)' if is_international else 'No'}")
    print(f"Completed: {audit.completed_courses}")
    print(SEP)
    result = await plan_schedule_async(
        audit, prefs, term="summer2026", is_international=is_international
    )
    print(result)

async def main():
    # ── Scenario 1: DOMESTIC, WR121 done, online, avoid Tue/Thu ─────────────
    audit1 = DegreeAudit(
        degree="Associate of Arts Oregon Transfer (AAOT)",
        gpa="3.80", credits_applied=75, credits_required=90, status="INCOMPLETE",
        requirements=[
            DegreeRequirement("Arts and Letters", "incomplete", "1 course needed"),
            DegreeRequirement("Social Sciences", "incomplete", "1 course needed"),
            DegreeRequirement("Lab Requirement 1", "incomplete", ""),
        ],
        completed_courses=["WR121", "COMM111"],
    )
    await run(
        "Domestic — WR121+COMM111 done, 9cr online, avoid Tue/Thu",
        audit1,
        "9 credits, online only, not available Tuesday and Thursday",
        is_international=False,
    )

    # ── Scenario 2: INTERNATIONAL, WR121 done, prefer morning ───────────────
    audit2 = DegreeAudit(
        degree="Associate of Arts Oregon Transfer (AAOT)",
        gpa="3.90", credits_applied=70, credits_required=90, status="INCOMPLETE",
        requirements=[
            DegreeRequirement("Arts and Letters", "incomplete", "1 course needed"),
            DegreeRequirement("Social Sciences", "incomplete", "1 course needed"),
            DegreeRequirement("Lab Requirement 1", "incomplete", ""),
            DegreeRequirement("Lab Requirement 2", "incomplete", ""),
        ],
        completed_courses=["WR121", "COMM111"],
    )
    await run(
        "International F-1 — WR121+COMM111 done, nothing before 9am, avoid Friday",
        audit2,
        "12 credits, nothing before 9am, not available Friday",
        is_international=True,
    )

    # ── Scenario 3: INTERNATIONAL, tries to ask for online only ─────────────
    audit3 = DegreeAudit(
        degree="Associate of Arts Oregon Transfer (AAOT)",
        gpa="3.50", credits_applied=65, credits_required=90, status="INCOMPLETE",
        requirements=[
            DegreeRequirement("Social Sciences", "incomplete", "2 courses needed"),
            DegreeRequirement("Lab Requirement 1", "incomplete", ""),
            DegreeRequirement("Lab Requirement 2", "incomplete", ""),
        ],
        completed_courses=["WR115", "WR121", "WR122", "COMM111"],
    )
    await run(
        "International F-1 — requests online only (should be overridden to any+in-person priority)",
        audit3,
        "12 credits, online only",
        is_international=True,
    )

    # ── Scenario 4: Near graduation, domestic ───────────────────────────────
    audit4 = DegreeAudit(
        degree="Associate of Arts Oregon Transfer (AAOT)",
        gpa="4.00", credits_applied=82, credits_required=90, status="INCOMPLETE",
        requirements=[
            DegreeRequirement("Lab Requirement 3", "incomplete", ""),
            DegreeRequirement("Social Sciences", "incomplete", "1 more course"),
        ],
        completed_courses=["WR115","WR121","WR122","COMM111","MTH065","MTH095",
                           "MTH111","MTH112","SOC204","PSY101","BI101","CH104"],
    )
    await run(
        "Near graduation (82/90 cr) — 1 lab + 1 social science left, 6cr online",
        audit4,
        "6 credits, online only",
        is_international=False,
    )

asyncio.run(main())

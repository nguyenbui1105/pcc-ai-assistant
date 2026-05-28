"""Test script for schedule planner with completed course filtering."""
import sys, asyncio
sys.path.insert(0, ".")
from loguru import logger
logger.remove()

from src.schedule.prereq_map import get_superseded_courses, get_missing_prereqs
from src.mypcc.parser import DegreeAudit, DegreeRequirement, _course_id_to_code
from src.agent.schedule_planner import plan_schedule_async

# ── Unit tests ───────────────────────────────────────────────────────────────
s1 = get_superseded_courses(["WR121"])
assert s1 == {"WR115"}, f"expected {{WR115}}, got {s1}"
print("WR121 supersedes WR115: OK")

s3 = get_superseded_courses(["MTH112"])
assert "MTH111" in s3 and "MTH95" in s3 and "MTH65" in s3
print(f"MTH112 transitive supersede {sorted(s3)}: OK")

s4 = get_superseded_courses(["WR122"])
assert "WR121" in s4 and "WR115" in s4
print(f"WR122 supersedes {sorted(s4)}: OK")

missing = get_missing_prereqs("WR122", ["WR121"])
assert missing == [], f"Expected [], got {missing}"
print("WR122 prereqs met when WR121 done: OK")

assert _course_id_to_code("WR-121-12345") == "WR121"
assert _course_id_to_code("COMM-111-30400") == "COMM111"
assert _course_id_to_code("invalid") == ""
print("_course_id_to_code: OK")

print()

# ── Integration test ─────────────────────────────────────────────────────────
print("=== Planner: WR121 + COMM111 already done ===")
audit = DegreeAudit(
    degree="Associate of Arts Oregon Transfer (AAOT)",
    gpa="4.00",
    credits_applied=73,
    credits_required=90,
    status="INCOMPLETE",
    requirements=[
        DegreeRequirement("Arts and Letters", "incomplete", "1 course needed"),
        DegreeRequirement("Social Sciences", "incomplete", "1 course needed"),
        DegreeRequirement("Lab Requirement 1", "incomplete", ""),
    ],
    completed_courses=["WR121", "COMM111"],
)

result = asyncio.run(
    plan_schedule_async(audit, "9 credits, online only", term="summer2026")
)
print(result)
print()

for excluded in ["WR115", "WR121", "COMM111"]:
    status = "FAIL" if excluded in result else "OK"
    label = "still appears (BUG)" if excluded in result else "correctly excluded"
    print(f"  [{status}] {excluded}: {label}")

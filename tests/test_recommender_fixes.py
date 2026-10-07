"""Regression tests for the course recommender and the academic planning layer.

The live schedule is replaced by an in-memory catalog (no network): every listed
course gets one open in-person section.
"""
import pytest

import src.agent.course_recommender as cr
from src.academic.academic_state import AcademicState
from src.academic.course_knowledge import COURSE_KB, get_course_metadata
from src.academic.student_model import StudentProfile, infer_profile
from src.academic.utility_scorer import is_high_priority_gap
from src.agent.session import create_demo_session
from src.mypcc.parser import CourseInfo, DegreeAudit, DegreeRequirement
from src.schedule.gap_analyzer import REQ_LABELS
from src.schedule.parser import CourseInfo as Listing
from src.schedule.parser import CourseSection

AAOT = "Associate of Arts Oregon Transfer (AAOT)"


def _audit(
    *reqs: DegreeRequirement,
    completed: list[str] | None = None,
    credits: tuple[int, int] = (45, 90),
    status: str = "INCOMPLETE",
) -> DegreeAudit:
    return DegreeAudit(
        degree=AAOT,
        gpa="3.50",
        credits_applied=credits[0],
        credits_required=credits[1],
        status=status,
        requirements=list(reqs),
        completed_courses=list(completed or []),
    )


@pytest.fixture
def catalog(monkeypatch):
    """Serve `catalog[subject] = [(code, title), ...]` instead of pcc.edu.

    Returns the dict to fill and the list of course codes whose detail pages
    were fetched.
    """
    courses: dict[str, list[tuple[str, str]]] = {}
    fetched: list[str] = []

    async def fake_listings(subjects, term=None):
        return {s: s for s in subjects}

    def fake_parse_listing(html, subject):
        return [
            Listing(code, title, ["40001"], f"https://www.pcc.edu/schedule/test/{code}/")
            for code, title in courses.get(subject, [])
        ]

    async def fake_details(urls):
        return {u: u for u in urls}

    def fake_parse_detail(html, course_code="", title=""):
        fetched.append(course_code)
        return [CourseSection(
            course_code=course_code, title=title, crn="40001", seats=1,
            section_type="In-person", days="Monday and Wednesday", time="9am-10:50am",
            credits="4",
        )]

    monkeypatch.setattr(cr, "fetch_listings_async", fake_listings)
    monkeypatch.setattr(cr, "parse_listing_html", fake_parse_listing)
    monkeypatch.setattr(cr, "fetch_details_async", fake_details)
    monkeypatch.setattr(cr, "parse_detail_html", fake_parse_detail)
    return courses, fetched


def _block(out: str, label: str) -> str:
    """The requirement block whose header starts with **label**."""
    blocks = out.split("\n\n")
    return next((b for b in blocks if b.startswith(f"**{label}**")), "")


def _shown(block: str) -> list[str]:
    """Course codes listed in a requirement block."""
    return [ln.split()[0].strip("*") for ln in block.splitlines() if ln.startswith("  **")]


_MTH = [
    ("MTH95", "Intermediate Algebra"),
    ("MTH111", "College Algebra"),
    ("MTH112", "Elementary Functions"),
    ("MTH211", "Foundations of Elementary Mathematics I"),
    ("MTH251", "Calculus I"),
]


# ── Finding 16: recommendations respect prerequisites ─────────────────────────

@pytest.mark.anyio
async def test_recommender_drops_courses_with_missing_prereqs(catalog):
    courses, _ = catalog
    courses["MTH"] = _MTH
    audit = _audit(
        DegreeRequirement("College Mathematics", "incomplete", "MTH111 or higher"),
        completed=["MTH95", "MTH111", "CS161", "CS162"],   # MTH111 in progress
    )
    out = await cr.recommend_courses_async(audit, term="fall2026")
    shown = _shown(_block(out, "College Mathematics"))
    assert "MTH112" in shown
    assert "MTH211" not in shown     # needs MTH112
    assert "MTH251" not in shown     # needs MTH112


@pytest.mark.anyio
async def test_demo_lab_blocks_skip_courses_needing_calculus(catalog):
    courses, _ = catalog
    courses["MTH"] = _MTH
    courses["PHY"] = [("PHY201", "General Physics"), ("PHY121", "Elementary Astronomy I")]
    courses["CH"] = [("CH221", "General Chemistry I")]
    session = create_demo_session()
    out = await cr.recommend_courses_async(
        session.degree_audit, is_f1=True, academic_state=session.academic_state
    )
    lab = _shown(_block(out, REQ_LABELS["Lab Requirement 2"]))
    assert "PHY121" in lab
    assert "PHY201" not in lab       # needs MTH251
    assert "CH221" not in lab        # needs MTH112
    # MTH111 is enrolled, so the demo audit marks College Mathematics in-progress:
    # it's listed as in progress instead of getting a recommendation block
    assert _block(out, "College Mathematics") == ""
    assert "In progress this term" in out


def test_fetch_selection_skips_ineligible_and_completed_courses():
    profile = StudentProfile(major_interest="CS/STEM", stem_affinity=0.6, cs_affinity=0.4)
    courses = [Listing(code, title) for code, title in _MTH]
    picked = cr._select_courses_for_fetch(
        courses, profile, 2, completed={"MTH95", "MTH111"}
    )
    assert [c.course_code for c in picked] == ["MTH112"]


@pytest.mark.anyio
async def test_eligible_next_course_is_fetched(catalog):
    courses, fetched = catalog
    # Higher-scoring courses the student has done or can't take yet used to
    # fill the 5-page detail budget and push MTH112 out
    courses["MTH"] = _MTH + [
        ("MTH212", "Foundations of Elementary Mathematics II"),
        ("MTH213", "Foundations of Elementary Mathematics III"),
        ("MTH252", "Calculus II"),
        ("MTH253", "Calculus III"),
    ]
    audit = _audit(
        DegreeRequirement("College Mathematics", "incomplete"),
        completed=["MTH95", "MTH111"],
    )
    await cr.recommend_courses_async(audit, term="fall2026")
    assert "MTH112" in fetched
    assert not {"MTH95", "MTH111", "MTH211", "MTH251"} & set(fetched)


@pytest.mark.anyio
async def test_block_names_missing_prereqs_when_nothing_is_eligible(catalog):
    courses, _ = catalog
    courses["MTH"] = [("MTH251", "Calculus I")]
    audit = _audit(DegreeRequirement("College Mathematics", "incomplete"), completed=["MTH95"])
    out = await cr.recommend_courses_async(audit, term="fall2026")
    block = _block(out, "College Mathematics")
    assert _shown(block) == []
    assert "MTH251 (needs MTH112)" in block


# ── Finding 17: Oregon common course numbering (Z suffix) ────────────────────

@pytest.mark.parametrize("z_code, base", [
    ("WR121Z", "WR121"), ("WR122Z", "WR122"), ("WR227Z", "WR227"),
    ("MTH111Z", "MTH111"), ("MTH112Z", "MTH112"), ("MTH251Z", "MTH251"),
])
def test_z_courses_use_the_unsuffixed_kb_entry(z_code, base):
    meta = get_course_metadata(z_code)
    assert not meta.is_filler
    assert meta == COURSE_KB[base]


def test_z_course_without_kb_entry_is_not_a_support_lab():
    assert not get_course_metadata("MTH105Z").is_filler
    assert "support_lab" not in get_course_metadata("WR 122Z").tags


@pytest.mark.parametrize("code", ["WR121L", "WR115X", "PE100X", "MTH65H", "WR115"])
def test_real_support_labs_still_detected(code):
    assert "support_lab" in get_course_metadata(code).tags


def test_pre_college_courses_are_low_value():
    # Numbered below 100: doesn't count toward the AAOT
    assert get_course_metadata("MTH20").is_filler
    assert get_course_metadata("MTH020").is_filler


def test_mth211_is_not_a_cs_course_and_calculus_is():
    assert not get_course_metadata("MTH211").is_cs
    assert get_course_metadata("MTH251").is_cs


@pytest.mark.anyio
async def test_composition_recommends_z_numbered_courses(catalog):
    courses, _ = catalog
    courses["WR"] = [
        ("WR121Z", "Composition I"), ("WR122Z", "Composition II"),
        ("WR227Z", "Technical Writing"), ("WR241", "Creative Writing: Fiction"),
    ]
    audit = _audit(
        DegreeRequirement("Composition", "incomplete", "WR 122Z or WR 227Z"),
        completed=["WR121Z", "SOC204"],
    )
    out = await cr.recommend_courses_async(audit, term="fall2026")
    shown = _shown(_block(out, REQ_LABELS["Composition"]))
    assert shown[0] == "WR122Z"
    assert "WR121Z" not in shown


# ── Finding 18: no false congratulations ─────────────────────────────────────

def _all_mapped_complete() -> list[DegreeRequirement]:
    return [DegreeRequirement(name, "complete") for name in REQ_LABELS]


@pytest.mark.anyio
async def test_no_congratulations_when_credits_short():
    audit = _audit(
        *_all_mapped_complete(),
        DegreeRequirement("AAOT Credit Total", "incomplete", "15 Credits"),
        credits=(75, 90),
    )
    out = await cr.recommend_courses_async(audit, term="fall2026")
    assert "congratulations" not in out.lower()
    assert "75/90" in out
    assert "AAOT Credit Total — 15 Credits" in out


@pytest.mark.anyio
async def test_no_congratulations_when_audit_incomplete_even_with_credits():
    audit = _audit(*_all_mapped_complete(), credits=(90, 90), status="INCOMPLETE")
    out = await cr.recommend_courses_async(audit, term="fall2026")
    assert "congratulations" not in out.lower()


@pytest.mark.anyio
async def test_congratulations_when_audit_complete():
    audit = _audit(*_all_mapped_complete(), credits=(92, 90), status="COMPLETE")
    out = await cr.recommend_courses_async(audit, term="fall2026")
    assert "congratulations" in out.lower()


# ── Finding 31: GRAD Plan in-progress requirements get no recommendations ────

@pytest.mark.anyio
@pytest.mark.parametrize("use_state", [False, True])
async def test_in_progress_requirement_gets_no_recommendations(catalog, use_state):
    courses, _ = catalog
    courses["WR"] = [("WR122", "English Composition"), ("WR241", "Creative Writing: Fiction")]
    courses["ENG"] = [("ENG104", "Introduction to Literature")]
    audit = _audit(
        DegreeRequirement("Composition", "in-progress"),
        DegreeRequirement("Arts and Letters", "incomplete", "Any AL-coded course"),
        completed=["WR121", "WR122"],
    )
    state = (
        AcademicState.from_session(
            audit, current_courses=[CourseInfo("English Composition", "WR-122-41001", "x")]
        )
        if use_state else None
    )
    out = await cr.recommend_courses_async(audit, term="fall2026", academic_state=state)
    assert _block(out, REQ_LABELS["Composition"]) == ""
    assert _block(out, REQ_LABELS["Arts and Letters"])
    assert "In progress this term" in out and REQ_LABELS["Composition"] in out


@pytest.mark.anyio
async def test_only_in_progress_requirements_left_is_not_a_completed_degree():
    audit = _audit(
        *[r for r in _all_mapped_complete() if r.name != "Composition"],
        DegreeRequirement("Composition", "in-progress"),
        credits=(86, 90),
    )
    out = await cr.recommend_courses_async(audit, term="fall2026")
    assert "congratulations" not in out.lower()
    assert "**In progress this term:** Composition" in out


# ── Findings 20 / 32: AcademicState in-progress attribution ──────────────────

def test_enrolled_courses_match_exact_subjects_only():
    state = AcademicState.from_session(
        _audit(
            DegreeRequirement("Communication", "in-progress"),
            DegreeRequirement("Lab Requirement 2", "in-progress"),
            DegreeRequirement("Social Sciences", "incomplete"),
            completed=["ESOL250"],
        ),
        current_courses=[
            CourseInfo("Academic Reading", "ESOL-252-41600", "x"),
            CourseInfo("Spanish 101", "SPAN-101-41700", "x"),
        ],
    )
    reqs = {r.name: r for r in state.requirements}
    assert reqs["Communication"].courses_ip == []        # SPAN is not SP
    assert reqs["Lab Requirement 2"].courses_ip == []    # ESOL is not ES
    assert reqs["Lab Requirement 2"].courses_done == []
    assert reqs["Social Sciences"].status == "incomplete"


def test_incomplete_requirement_not_upgraded_by_enrolled_course():
    state = AcademicState.from_session(
        _audit(
            DegreeRequirement("Composition", "in-progress"),
            DegreeRequirement("Arts and Letters", "incomplete"),
            DegreeRequirement("Lab Requirement 2", "incomplete"),
            DegreeRequirement("Lab Requirement 3", "incomplete"),
        ),
        current_courses=[
            CourseInfo("English Composition", "WR-122-12345", "x"),
            CourseInfo("Intro Chem", "CH-104-41301", "y"),
        ],
    )
    reqs = {r.name: r for r in state.requirements}
    assert reqs["Composition"].status == "in_progress"
    assert reqs["Composition"].courses_ip == ["WR122"]
    for name in ("Arts and Letters", "Lab Requirement 2", "Lab Requirement 3"):
        assert reqs[name].status == "incomplete"
        assert reqs[name].courses_ip == []
    assert state.in_progress == frozenset({"WR122", "CH104"})


def test_in_progress_course_credited_to_one_requirement():
    state = AcademicState.from_session(
        _audit(
            DegreeRequirement("Arts and Letters", "in-progress"),
            DegreeRequirement("Composition", "in-progress"),
        ),
        current_courses=[
            CourseInfo("English Composition", "WR-122-12345", "x"),
            CourseInfo("Intro to Literature", "ENG-104-12346", "y"),
        ],
    )
    reqs = {r.name: r for r in state.requirements}
    assert reqs["Composition"].courses_ip == ["WR122"]
    assert reqs["Arts and Letters"].courses_ip == ["ENG104"]


def test_demo_context_does_not_credit_wr122_to_arts_and_letters():
    context = create_demo_session().academic_state.to_llm_context()
    al = next(ln for ln in context.splitlines() if "Arts and Letters" in ln)
    assert "✗ Arts and Letters" in al and "WR122" not in al
    comp = next(ln for ln in context.splitlines() if "Composition" in ln)
    assert "⏳ Composition" in comp and "IP: WR122" in comp


# ── Finding 21: HIGH PRIORITY by requirement name ────────────────────────────

_STEM = StudentProfile(major_interest="CS/STEM", stem_affinity=0.6, cs_affinity=0.4)


@pytest.mark.parametrize("name", ["Social Sciences", "Arts and Letters", "Health and Wellness"])
def test_non_stem_requirements_are_not_high_priority(name):
    assert not is_high_priority_gap(name, _STEM)
    assert not is_high_priority_gap(REQ_LABELS[name], _STEM)


@pytest.mark.parametrize("name", [
    "College Mathematics", "Lab Requirement 1", "Lab Requirement 2", "Lab Requirement 3",
    "Science/Math/Computer Science",
])
def test_math_and_labs_are_high_priority_for_stem(name):
    assert is_high_priority_gap(name, _STEM)
    assert is_high_priority_gap(REQ_LABELS[name], _STEM)   # planner passes labels


@pytest.mark.anyio
async def test_social_sciences_block_has_no_priority_badge(catalog):
    courses, _ = catalog
    courses["PSY"] = [("PSY101", "Psychology and Human Relations")]
    audit = _audit(
        DegreeRequirement("Social Sciences", "incomplete"),
        completed=["CS161A", "CS162", "MTH111", "MTH112", "PHY201"],
    )
    out = await cr.recommend_courses_async(audit, term="fall2026")
    header = _block(out, "Social Sciences").splitlines()[0]
    assert "HIGH PRIORITY" not in header


# ── Finding 23: CS/STEM needs real CS/CIS courses ────────────────────────────

def test_humanities_transcript_is_not_cs_stem():
    completed = [
        "WR121", "WR122", "COMM111", "COMM218", "MTH105", "HE250", "PE185", "ART115",
        "ENG104", "ENG106", "PHL201", "HUM100", "MUS105", "SOC204", "SOC206",
        "BI101", "BI102", "CH100",
    ]
    profile = infer_profile(_audit(completed=completed))
    assert profile.major_interest != "CS/STEM"
    assert not profile.is_stem_oriented
    assert not profile.is_cs_oriented


def test_college_math_alone_is_not_cs():
    profile = infer_profile(_audit(completed=["WR121", "SOC204", "MTH111", "MTH112"]))
    assert profile.major_interest != "CS/STEM"
    assert not profile.is_cs_oriented


def test_math_and_physics_without_cs_is_stem_not_cs():
    profile = infer_profile(_audit(completed=["MTH111", "MTH112", "MTH251", "PHY201", "WR121"]))
    assert profile.major_interest != "CS/STEM"
    assert profile.is_stem_oriented
    assert not profile.is_cs_oriented


def test_cs_courses_still_make_a_cs_stem_profile():
    profile = infer_profile(_audit(completed=["WR121", "CS161A", "CS162", "MTH111"]))
    assert profile.major_interest == "CS/STEM"
    assert profile.is_cs_oriented

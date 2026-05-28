"""Parse raw MyPCC page text into structured personal context."""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CourseInfo:
    title: str
    course_id: str
    instructor: str


@dataclass
class ScheduledCourse:
    title: str
    subject: str
    course_number: str
    crn: str
    days: str       # e.g. "Tuesday, Thursday"
    start_time: str # e.g. "9:00 AM"
    end_time: str   # e.g. "11:20 AM"
    term: str


_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def parse_banss_events(events: list[dict]) -> list[ScheduledCourse]:
    """Group Banner calendar events by CRN and extract weekly schedule."""
    crn_events: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        crn_events[str(event["crn"])].append(event)

    courses: list[ScheduledCourse] = []
    for crn, evts in crn_events.items():
        first = evts[0]
        # Collect unique days of week across all occurrences
        days_seen: list[str] = []
        for evt in sorted(evts, key=lambda e: e["start"]):
            dt = datetime.fromisoformat(evt["start"])
            day = _DAY_NAMES[dt.weekday()]
            if day not in days_seen:
                days_seen.append(day)

        start_dt = datetime.fromisoformat(first["start"])
        end_dt = datetime.fromisoformat(first["end"])

        courses.append(ScheduledCourse(
            title=first["title"],
            subject=first["subject"],
            course_number=first["courseNumber"],
            crn=crn,
            days=", ".join(days_seen),
            start_time=start_dt.strftime("%I:%M %p").lstrip("0"),
            end_time=end_dt.strftime("%I:%M %p").lstrip("0"),
            term=first["term"],
        ))
    return courses


@dataclass
class DegreeRequirement:
    name: str
    status: str  # "complete", "incomplete", "in-progress"
    still_needed: str = ""


_GRAD_COURSE_RE = re.compile(r"\b([A-Z]{2,4})\s+(\d{3}[A-Z]?)\b")
_MYPCC_COURSE_ID_RE = re.compile(r"([A-Z]{2,4})-(\d{3}[A-Z]?)-\d{5}")


def _course_id_to_code(course_id: str) -> str:
    """Convert 'WR-121-12345' → 'WR121'."""
    m = _MYPCC_COURSE_ID_RE.match(course_id)
    return (m.group(1) + m.group(2)) if m else ""


@dataclass
class DegreeAudit:
    degree: str = ""
    gpa: str = ""
    credits_applied: int = 0
    credits_required: int = 0
    status: str = ""
    requirements: list[DegreeRequirement] = field(default_factory=list)
    completed_courses: list[str] = field(default_factory=list)  # e.g. ["WR121", "COMM111"]

    def to_context_string(self) -> str:
        if not self.degree:
            return ""
        lines = [
            "\n=== DEGREE AUDIT (GRAD PLAN) ===",
            f"Degree: {self.degree} — {self.status}",
            f"Overall GPA: {self.gpa}",
            f"Credits: {self.credits_applied} applied / {self.credits_required} required"
            f" (need {max(0, self.credits_required - self.credits_applied)} more)",
        ]
        if self.requirements:
            lines.append("\nRequirement status:")
            for r in self.requirements:
                icon = "✓" if r.status == "complete" else ("⏳" if r.status == "in-progress" else "✗")
                line = f"  {icon} {r.name}: {r.status.upper()}"
                if r.still_needed:
                    line += f" — Still needed: {r.still_needed}"
                lines.append(line)
        lines.append("=== END DEGREE AUDIT ===")
        return "\n".join(lines)


_INTL_SIGNALS = re.compile(
    r"\b(f-?1|i-?20|sevis|international student|visa status|iss office|"
    r"international\.pcc\.edu|study abroad|non.?immigrant)\b",
    re.IGNORECASE,
)


@dataclass
class PersonalContext:
    student_name: str = ""
    degree: str = ""
    advisor: str = ""
    current_term: str = ""
    courses: list[CourseInfo] = field(default_factory=list)
    total_charges: str = ""
    payments_received: str = ""
    amount_due: str = ""
    upcoming_dates: list[str] = field(default_factory=list)
    announcements: list[str] = field(default_factory=list)
    degree_audit: DegreeAudit = field(default_factory=DegreeAudit)
    is_international: bool = False

    def has_meaningful_data(self) -> bool:
        """True if at least some real personal data was parsed (not just empty session)."""
        return bool(
            self.student_name or self.courses or self.total_charges
            or self.upcoming_dates or self.degree_audit.degree
        )

    def to_context_string(self) -> str:
        if not self.has_meaningful_data():
            return ""

        lines: list[str] = ["=== MY PCC PERSONAL INFORMATION ==="]

        if self.student_name:
            lines.append(f"Student: {self.student_name}")
        if self.degree:
            lines.append(f"Degree program: {self.degree}")
        if self.advisor:
            lines.append(f"Academic advisor: {self.advisor}")

        if self.courses:
            lines.append(f"\nCurrent courses ({self.current_term}):")
            for c in self.courses:
                lines.append(f"  - {c.title} ({c.course_id}) — Instructor: {c.instructor}")
        else:
            lines.append("\n[Note: Current course enrollment not available — MyPCC session may be expired. Run `python scripts/setup_session.py` to refresh.]")

        if self.total_charges:
            lines.append(f"\nFinancial account ({self.current_term}):")
            lines.append(f"  Total charges: {self.total_charges}")
            lines.append(f"  Payments received: {self.payments_received}")
            lines.append(f"  Amount due: {self.amount_due}")

        if self.upcoming_dates:
            lines.append("\nUpcoming important dates:")
            for d in self.upcoming_dates:
                lines.append(f"  - {d}")

        if self.announcements:
            lines.append("\nAnnouncements:")
            for a in self.announcements:
                lines.append(f"  - {a}")

        audit_str = self.degree_audit.to_context_string()
        if audit_str:
            lines.append(audit_str)

        lines.append("=== END PERSONAL INFORMATION ===")
        return "\n".join(lines)


def parse(pages: dict[str, str]) -> PersonalContext:
    ctx = PersonalContext()

    if "dashboard" in pages:
        _parse_dashboard(pages["dashboard"], ctx)
    if "courses" in pages:
        _parse_courses(pages["courses"], ctx)
    if "financial" in pages:
        _parse_financial(pages["financial"], ctx)
    if "student_guide" in pages:
        _parse_student_guide(pages["student_guide"], ctx)

    # Detect international status from any page
    all_text = " ".join(pages.values())
    ctx.is_international = bool(_INTL_SIGNALS.search(all_text))

    return ctx


def parse_degree_audit(text: str) -> DegreeAudit:
    audit = DegreeAudit()
    if not text:
        return audit

    # Degree name and status
    m = re.search(r"(Associate of Arts[^\n]+?)\s+(INCOMPLETE|COMPLETE|IN-PROGRESS)", text)
    if m:
        audit.degree = m.group(1).strip()
        audit.status = m.group(2)

    # GPA
    m = re.search(r"Overall GPA\s+([\d.]+)", text)
    if m:
        audit.gpa = m.group(1)

    # Credits
    m = re.search(r"Credits required:\s*(\d+)Credits applied:\s*(\d+)", text)
    if m:
        audit.credits_required = int(m.group(1))
        audit.credits_applied = int(m.group(2))

    # Extract completed course codes from GRAD Plan text
    # Strategy: scan each line; if a line contains a known course-code pattern
    # AND is NOT in a "Still needed" section, collect it.
    in_still_needed = False
    seen_codes: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if "Still needed" in stripped:
            in_still_needed = True
        elif stripped and any(
            kw in stripped for kw in ("Requirement is complete", "Not complete", "IN-PROGRESS",
                                      "INCOMPLETE", "COMPLETE", "Credits required", "Credits applied",
                                      "Overall GPA", "Associate of")
        ):
            in_still_needed = False

        if not in_still_needed:
            for m in _GRAD_COURSE_RE.finditer(stripped):
                code = m.group(1) + m.group(2)
                if code not in seen_codes:
                    seen_codes.add(code)
                    audit.completed_courses.append(code)

    # Parse each requirement block
    req_pattern = re.compile(
        r"([\w &/\-]+?)\s+(Requirement is complete|Not complete|When the in-progress classes are completed this requirement should be complete|IN-PROGRESS|INCOMPLETE|COMPLETE)",
        re.IGNORECASE,
    )

    # Key requirements to track (filter noise)
    tracked = {
        "Health and Wellness", "College Mathematics", "Communication", "Composition",
        "Cultural Literacy", "Arts and Letters", "Social Sciences",
        "Lab Requirement 1", "Lab Requirement 2", "Lab Requirement 3",
        "Science/Math/Computer Science", "Residency Requirement",
        "AAOT Credit Total", "Oregon Transfer Requirements",
        "Foundational Requirements", "Discipline Studies Requirements",
    }

    seen: set[str] = set()
    lines = text.splitlines()

    for i, line in enumerate(lines):
        line = line.strip()
        for req_name in tracked:
            if req_name in line and req_name not in seen:
                seen.add(req_name)
                if "Requirement is complete" in line or "complete" in line.lower() and "not complete" not in line.lower() and "incomplete" not in line.lower():
                    if "in-progress" in line.lower() or "when the in-progress" in line.lower():
                        status = "in-progress"
                    elif "not complete" in line.lower():
                        status = "incomplete"
                    else:
                        status = "complete"
                elif "not complete" in line.lower():
                    status = "incomplete"
                elif "in-progress" in line.lower() or "when the in-progress" in line.lower():
                    status = "in-progress"
                else:
                    continue

                # Look ahead for "Still needed:" text
                still_needed = ""
                for j in range(i + 1, min(i + 8, len(lines))):
                    nxt = lines[j].strip()
                    if "Still needed:" in nxt:
                        for k in range(j + 1, min(j + 4, len(lines))):
                            detail = lines[k].strip()
                            if detail and len(detail) > 5:
                                still_needed = detail
                                break
                        break
                    if nxt and any(t in nxt for t in tracked) and nxt != line:
                        break

                audit.requirements.append(DegreeRequirement(
                    name=req_name, status=status, still_needed=still_needed
                ))

    return audit


def _parse_dashboard(text: str, ctx: PersonalContext) -> None:
    m = re.search(r"Welcome\s+(\w+)", text)
    if m:
        ctx.student_name = m.group(1)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    try:
        degree_idx = next(i for i, l in enumerate(lines) if "My degree and major" in l)
        degree_parts: list[str] = []
        for l in lines[degree_idx + 1:degree_idx + 6]:
            if l and not l.startswith("update") and not l.startswith("check") and len(l) > 3:
                degree_parts.append(l)
            if len(degree_parts) == 2:
                break
        ctx.degree = " — ".join(degree_parts)
    except StopIteration:
        pass

    try:
        advisor_idx = next(i for i, l in enumerate(lines) if "My advisor" in l)
        for l in lines[advisor_idx + 1:advisor_idx + 6]:
            if re.match(r"[A-Z][a-z]+ [a-z]* ?[A-Z]", l) and "advisor" not in l.lower():
                ctx.advisor = l
                break
    except StopIteration:
        pass

    try:
        ann_idx = next(i for i, l in enumerate(lines) if "My announcements" in l)
        stop_terms = {"Quick links", "Show previously", "MyPCC resources"}
        for l in lines[ann_idx + 1:ann_idx + 10]:
            if any(t in l for t in stop_terms):
                break
            if l and len(l) > 10:
                ctx.announcements.append(l)
    except StopIteration:
        pass


def _parse_courses(text: str, ctx: PersonalContext) -> None:
    m = re.search(r"(Spring|Winter|Fall|Summer)\s+(\d{4})", text)
    if m:
        ctx.current_term = m.group(0)

    course_id_re = re.compile(r"([A-Z]{2,4}-\d{3}-\d{5})")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        mid = course_id_re.search(line)
        if not mid:
            continue
        parts = line.split("\t")
        title = parts[0].strip() if parts else ""
        course_id = mid.group(1)
        instructor = ""
        for j in range(i + 1, min(i + 4, len(lines))):
            candidate = lines[j].strip()
            if candidate and re.match(r"[A-Z][a-z]", candidate) and not course_id_re.search(candidate):
                instructor = candidate
                break
        if title and course_id:
            ctx.courses.append(CourseInfo(title=title, course_id=course_id, instructor=instructor))


def _parse_financial(text: str, ctx: PersonalContext) -> None:
    m = re.search(r"My account:\s*(.+)", text)
    if m and not ctx.current_term:
        ctx.current_term = m.group(1).strip()

    def _extract_dollar(label: str) -> str:
        pattern = re.compile(rf"{label}[:\s]*\$?([\d,]+\.\d{{2}})", re.IGNORECASE)
        match = pattern.search(text)
        return f"${match.group(1)}" if match else ""

    ctx.total_charges = _extract_dollar("Total charges")
    ctx.payments_received = _extract_dollar("Payments received")
    ctx.amount_due = _extract_dollar("Amount due")


def _parse_student_guide(text: str, ctx: PersonalContext) -> None:
    date_pattern = re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+\s+.{10,60}")
    for match in date_pattern.finditer(text):
        line = match.group(0).strip()
        if len(line) < 80:
            ctx.upcoming_dates.append(line)
        if len(ctx.upcoming_dates) >= 10:
            break

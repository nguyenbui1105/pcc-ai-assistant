"""Parse PCC schedule HTML — subject listing and course detail pages."""
import re
from dataclasses import dataclass, field

from loguru import logger

_CRN_DIGIT_RE = re.compile(r"^\d{5}$")
# Matches PCC format "from 9:30 - to 11:50am" or standard "9:30am - 11:50am"
_TIME_RANGE_RE = re.compile(
    r"(?:from\s+)?(\d{1,2}(?::\d{2})?)\s*(?:am|pm)?\s*[-–]\s*(?:to\s+)?(\d{1,2}(?::\d{2})?)\s*(am|pm)",
    re.I,
)
_TIME_RE = re.compile(
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)(?:\s*[-–]\s*\d{1,2}(?::\d{2})?\s*(?:am|pm))?",
    re.I,
)
_FEES_RE = re.compile(r"\$[\d,.]+")


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class CourseInfo:
    """A course entry from the subject listing page."""
    course_code: str           # "BI231"
    title: str                 # "Human Anatomy & Physiology I"
    crns: list[str] = field(default_factory=list)
    detail_url: str = ""


@dataclass
class CourseSection:
    """A single class section from a course detail page."""
    course_code: str = ""
    title: str = ""
    crn: str = ""
    seats: int = 0             # boolean flag from HTML: 1=open, 0=full
    seats_available: int = -1  # real count from capacity API (-1 = not fetched)
    seats_total: int = -1
    waitlist_available: int = -1
    waitlist_total: int = -1
    section_type: str = ""     # "In-person lecture", "Online", "Hybrid"
    location: str = ""
    days: str = ""             # "Saturday", "Monday and Wednesday"
    time: str = ""             # "8am-4:50pm"
    date_range: str = ""       # "Jun 27 – Aug 22"
    instructor: str = ""
    fees: str = ""
    credits: str = ""

    @property
    def is_open(self) -> bool:
        if self.seats_available >= 0:
            return self.seats_available > 0
        return self.seats > 0

    def summary(self) -> str:
        parts: list[str] = [f"CRN {self.crn}"]
        if self.section_type:
            parts.append(self.section_type)
        if self.location:
            parts.append(self.location)
        schedule = " ".join(filter(None, [self.days, self.time]))
        if schedule:
            parts.append(schedule)
        if self.date_range:
            parts.append(self.date_range)
        if self.instructor:
            parts.append(self.instructor)
        if self.seats_available >= 0 and self.seats_total > 0:
            parts.append(f"{self.seats_available}/{self.seats_total} open")
            if self.waitlist_total > 0:
                parts.append(f"waitlist {self.waitlist_available}/{self.waitlist_total}")
        else:
            parts.append("Open" if self.seats > 0 else "Full")
        if self.fees:
            parts.append(f"fees {self.fees}")
        return " | ".join(parts)


# ── Listing page parser ───────────────────────────────────────────────────────

def parse_listing_html(html_text: str, subject: str) -> list[CourseInfo]:
    """Extract course list from a subject listing page."""
    if not html_text:
        return []
    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(html_text, "html.parser")
        return _listing_bs4(soup, subject)
    except ImportError:
        logger.warning("beautifulsoup4 not available — listing parser unavailable")
        return _listing_regex(html_text, subject)


def _listing_bs4(soup, subject: str) -> list[CourseInfo]:  # type: ignore[no-untyped-def]
    subj_lower = subject.lower()
    # Pattern: /schedule/{term}/{subject}/{subject}{num}/?crn=...
    href_re = re.compile(
        rf'/schedule/[^/]+/{re.escape(subj_lower)}/{re.escape(subj_lower)}(\d+[a-z]?)/',
        re.I,
    )
    courses: list[CourseInfo] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        m = href_re.search(href)
        if not m:
            continue
        course_num = m.group(1).upper()
        course_code = f"{subject.upper()}{course_num}"
        if course_code in seen:
            continue
        seen.add(course_code)

        span = a.find("span")
        title = span.get_text(strip=True) if span else a.get_text(strip=True)

        crn_m = re.search(r"[?&]crn=([\d,]+)", href)
        crns = crn_m.group(1).split(",") if crn_m else []

        full_url = href if href.startswith("http") else f"https://www.pcc.edu{href}"
        courses.append(CourseInfo(
            course_code=course_code,
            title=title,
            crns=crns,
            detail_url=full_url,
        ))

    logger.debug(f"Listing parser: {len(courses)} courses for {subject}")
    return courses


def _listing_regex(html_text: str, subject: str) -> list[CourseInfo]:
    """Regex fallback for listing parsing."""
    subj = re.escape(subject.lower())
    pattern = re.compile(
        rf'href="([^"]+/schedule/[^"]+/{subj}/{subj}(\d+[a-z]?)/[^"]*)"[^>]*>.*?<span>([^<]+)</span>',
        re.I | re.S,
    )
    courses: list[CourseInfo] = []
    for m in pattern.finditer(html_text):
        href, num, title = m.group(1), m.group(2), m.group(3)
        crn_m = re.search(r"crn=([\d,]+)", href)
        crns = crn_m.group(1).split(",") if crn_m else []
        url = href if href.startswith("http") else f"https://www.pcc.edu{href}"
        courses.append(CourseInfo(
            course_code=f"{subject.upper()}{num.upper()}",
            title=title.strip(),
            crns=crns,
            detail_url=url,
        ))
    return courses


# ── Detail page parser ────────────────────────────────────────────────────────

def parse_detail_html(
    html_text: str,
    course_code: str = "",
    title: str = "",
) -> list[CourseSection]:
    """Extract section rows from a course detail page."""
    if not html_text:
        return []
    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(html_text, "html.parser")
        return _detail_bs4(soup, course_code, title)
    except ImportError:
        logger.warning("beautifulsoup4 not available — detail parser unavailable")
        return []


def _detail_bs4(soup, course_code: str, title: str) -> list[CourseSection]:  # type: ignore[no-untyped-def]
    sections: list[CourseSection] = []

    # Course title from page heading if not provided
    if not title:
        h1 = soup.find("h1") or soup.find("h2")
        if h1:
            title = h1.get_text(" ", strip=True)

    # Credits from <p class="credits"><strong>Credits: N</strong></p>
    page_credits = ""
    credits_p = soup.find("p", class_="credits")
    if credits_p:
        credits_text = credits_p.get_text()
        if "variable" in credits_text.lower():
            page_credits = ""  # variable-credit courses: don't lock in a number
        else:
            m = re.search(r"Credits?:\s*(\d+(?:\.\d+)?)", credits_text, re.I)
            if m:
                page_credits = m.group(1)

    # data-rows with a numeric 5-digit data-crn (skip continuation rows with data-crn="and")
    for row in soup.find_all("tr", attrs={"data-crn": _CRN_DIGIT_RE}):
        crn = row["data-crn"]
        try:
            seats = int(row.get("data-seats", 0))
        except ValueError:
            seats = 0
        fees_raw = row.get("data-fees", "")
        fees = f"${fees_raw}" if fees_raw and fees_raw != "0.00" else ""

        cells = row.find_all(["th", "td"])
        section_type = ""
        location = ""
        days = ""
        time_str = ""
        date_range = ""
        instructor = ""

        # Cell layout (from observed HTML):
        # [0] th: CRN  [1] td: type  [2] td: location  [3] td: days+time
        # [4] td: dates  [5] td: seats(JS)  [6] td: faculty  [7] td: more+fees
        for i, cell in enumerate(cells):
            if i == 0:
                pass  # CRN already from data attr
            elif i == 1:
                raw_type = cell.get_text(" ", strip=True)
                # Normalise: "In-person lecture" → "In-person", keep "Online (no scheduled meetings)"
                if raw_type.lower().startswith("online"):
                    section_type = "Online"
                elif raw_type.lower().startswith("hybrid"):
                    section_type = "Hybrid"
                elif raw_type.lower().startswith("in-person") or raw_type.lower().startswith("in person"):
                    section_type = "In-person"
                else:
                    section_type = raw_type
            elif i == 2:
                raw_loc = cell.get_text(" ", strip=True)
                # Skip placeholder location for online sections
                if raw_loc.startswith("—") or "not applicable" in raw_loc.lower():
                    location = ""
                else:
                    location = raw_loc
            elif i == 3:
                days, time_str = _parse_days_time(cell)
            elif i == 4:
                # Use aria-hidden spans for clean short-form date (avoids visually-hidden verbose text)
                aria_spans = cell.find_all("span", attrs={"aria-hidden": "true"})
                if aria_spans:
                    date_range = " ".join(s.get_text(strip=True) for s in aria_spans).replace("&ndash;", "–").strip()
                else:
                    date_range = _clean_date(cell.get_text(" ", strip=True))
            elif i == 5:
                pass  # seats via data-seats attribute
            elif i == 6:
                a = cell.find("a")
                instructor = a.get_text(strip=True) if a else cell.get_text(" ", strip=True)
            elif i == 7:
                # Fall back fees from cell text if not in data-fees
                if not fees:
                    m = _FEES_RE.search(cell.get_text())
                    if m:
                        fees = m.group(0)

        sections.append(CourseSection(
            course_code=course_code,
            title=title,
            crn=crn,
            seats=seats,
            section_type=section_type,
            location=location,
            days=days,
            time=time_str,
            date_range=date_range,
            instructor=instructor,
            fees=fees,
            credits=page_credits,
        ))

    logger.debug(f"Detail parser: {len(sections)} sections for {course_code}")
    return sections


def _parse_days_time(cell) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """Extract (days, time) from a schedule cell."""
    days = ""
    time_str = ""

    weekly = cell.find("div", class_="weekly")
    if weekly:
        title = weekly.get("title", "")
        days = re.sub(r"^[Cc]lass on\s+", "", title).strip()

    cell_text = cell.get_text(" ", strip=True)
    # Try "from 9:30 - to 11:50am" style first (PCC's primary format)
    m = _TIME_RANGE_RE.search(cell_text)
    if m:
        start, end, ampm = m.group(1), m.group(2), m.group(3).lower()
        time_str = f"{start}{ampm}–{end}{ampm}"
    else:
        m2 = _TIME_RE.search(cell_text)
        if m2:
            time_str = m2.group(0).strip()

    return days, time_str


def _clean_date(text: str) -> str:
    """Collapse whitespace and normalize en-dashes in date range text."""
    text = re.sub(r"\s+", " ", text)
    text = text.replace("&ndash;", "–").replace(" – ", "–").strip()
    # Remove screen-reader-only repetition like "From June 27 through August 22, 2026"
    # by keeping only the short form (e.g. "Jun 27–Aug 22")
    m = re.search(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+\s*[–-]\s*"
        r"(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+)?\d+",
        text, re.I,
    )
    return m.group(0).strip() if m else text

"""Fetch authenticated MyPCC and GRAD Plan pages via Playwright + saved cookies."""
import asyncio
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.mypcc.parser import CourseInfo

from playwright.async_api import async_playwright
from loguru import logger

from src.schedule.fetcher import TERM_LABELS, TERM_NAMES

# Reverse map: "Summer 2026" → "summer2026"
_TERM_LABEL_TO_KEY: dict[str, str] = {v: k for k, v in TERM_LABELS.items()}


class SessionExpiredError(Exception):
    """Raised when a fetched page has too little content — session/cookie has expired."""

COOKIES_PATH = "./data/mypcc_cookies.json"
GRADPLAN_COOKIES_PATH = "./data/gradplan_cookies.json"
BANSS_COOKIES_PATH = "./data/banss_cookies.json"
GRADPLAN_URL = "https://gradplan.pcc.edu/RespDashboard/worksheets/WEB31"
BANSS_SCHEDULE_URL = "https://banss.pcc.edu/StudentRegistrationSsb/ssb/registrationHistory/registrationHistory"
BANSS_EVENTS_ENDPOINT = "getRegistrationEvents"

MYPCC_PAGES = {
    "dashboard":     "https://my.pcc.edu/",
    "courses":       "https://my.pcc.edu/web/home-community/my-courses",
    "financial":     "https://my.pcc.edu/web/home-community/paying-for-college",
    "student_guide": "https://my.pcc.edu/web/home-community/student-guide",
}


def _load_cookies(path: str) -> list[dict]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cookies = []
    for c in raw:
        cookie: dict = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ".pcc.edu"),
            "path": c.get("path", "/"),
        }
        if "expirationDate" in c:
            cookie["expires"] = int(c["expirationDate"])
        elif "expires" in c and c["expires"] != -1:
            cookie["expires"] = int(c["expires"])
        if "secure" in c:
            cookie["secure"] = c["secure"]
        if "httpOnly" in c:
            cookie["httpOnly"] = c["httpOnly"]
        cookies.append(cookie)
    return cookies


def _clean_text(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _fetch_pages_async(cookies_path: str, pages: dict[str, str]) -> dict[str, str]:
    cookies = _load_cookies(cookies_path)
    results: dict[str, str] = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(cookies)

        for name, url in pages.items():
            logger.info(f"Fetching: {name} ({url})")
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
                text = _clean_text(await page.inner_text("body"))
                if len(text) > 200:
                    results[name] = text
                    logger.success(f"Got {len(text)} chars from {name}")
                else:
                    logger.warning(f"{name}: content too short ({len(text)} chars) — re-run setup_session.py")
            except Exception as e:
                logger.error(f"{name}: {e}")
            finally:
                await page.close()

        await browser.close()
    return results


async def _fetch_degree_audit_async(cookies_path: str) -> str:
    cookies = _load_cookies(cookies_path)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(cookies)
        page = await context.new_page()
        try:
            logger.info(f"Fetching: degree_audit ({GRADPLAN_URL})")
            await page.goto(GRADPLAN_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_function(
                "document.body.innerText.trim().length > 500", timeout=15000
            )
            text = _clean_text(await page.evaluate("document.body.innerText"))
            if len(text) > 200:
                logger.success(f"Got {len(text)} chars from degree_audit")
                return text
            logger.warning("degree_audit: content too short — session likely expired")
            raise SessionExpiredError(
                "GRAD Plan content too short — JWT may be expired. "
                "Re-run `scripts/setup_session.py` to refresh."
            )
        except SessionExpiredError:
            raise
        except Exception as e:
            logger.error(f"degree_audit: {e}")
        finally:
            await page.close()
            await browser.close()
    return ""


async def fetch_banss_schedule_async(
    cookies_path: str = BANSS_COOKIES_PATH,
    term_code: str | None = None,
) -> list[dict]:
    """Fetch current class schedule events from Banner SSB by intercepting XHR.

    If term_code is given (e.g. '202602'), selects that term from the dropdown
    so events reflect the correct semester instead of the last-viewed default.
    """
    cookies = _load_cookies(cookies_path)
    captured: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(cookies)
        page = await context.new_page()

        async def _on_response(response):
            if BANSS_EVENTS_ENDPOINT in response.url and response.status == 200:
                try:
                    data = await response.json()
                    if isinstance(data, list):
                        captured.extend(data)
                        logger.success(f"Captured {len(data)} schedule events from Banner")
                except Exception as e:
                    logger.warning(f"Could not parse banss response: {e}")

        page.on("response", _on_response)

        try:
            logger.info(f"Fetching Banner schedule: {BANSS_SCHEDULE_URL}")
            await page.goto(BANSS_SCHEDULE_URL, wait_until="networkidle", timeout=30000)

            if term_code:
                # Select the target term — clears stale events from the default term.
                # Banner SSB uses a hidden <select> driven by a custom widget; we set
                # the value via JS and fire a change event to trigger the data reload.
                try:
                    changed = await page.evaluate(f"""
                        (() => {{
                            const sel = document.getElementById('lookupFilter')
                                     || document.querySelector('select[name="lookupFilter"]')
                                     || document.querySelector('select');
                            if (!sel) return false;
                            sel.value = '{term_code}';
                            sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            return sel.value === '{term_code}';
                        }})()
                    """)
                    if changed:
                        captured.clear()
                        await page.wait_for_load_state("networkidle", timeout=15000)
                        logger.info(f"Selected term {term_code} via JS ({len(captured)} events after)")
                    else:
                        logger.warning(f"Term select not found or value not set for {term_code}")
                except Exception as e:
                    logger.warning(f"Could not select term {term_code}: {e}")
        except Exception as e:
            logger.error(f"banss_schedule: {e}")
        finally:
            await page.close()
            await browser.close()

    return captured


async def fetch_enrolled_times_async(
    courses: list["CourseInfo"], term_name: str
) -> dict[str, str]:
    """Fetch meeting days/times for enrolled courses from the public PCC schedule.

    Returns a dict mapping CRN → formatted schedule string, e.g. "Tu/Th 9:30–11:50 AM".
    """
    import httpx
    from src.schedule.parser import parse_detail_html

    term_key = _TERM_LABEL_TO_KEY.get(term_name, "")
    if not term_key:
        logger.warning(f"fetch_enrolled_times_async: unknown term label {term_name!r} — skipping")
        return {}
    term_slug = TERM_NAMES.get(term_key, "")
    base = "https://www.pcc.edu/schedule"
    results: dict[str, str] = {}

    async with httpx.AsyncClient(timeout=10, follow_redirects=True,
                                  headers={"User-Agent": "Mozilla/5.0"}) as client:
        for course in courses:
            parts = course.course_id.split("-")
            if len(parts) < 3:
                continue
            subj, num, crn = parts[0].lower(), parts[1].lower(), parts[2]
            url = f"{base}/{term_slug}/{subj}/{subj}{num}/?crn={crn}"
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                course_code = f"{parts[0]}{parts[1]}".upper()
                sections = parse_detail_html(resp.text, course_code, course.title)
                match = next((s for s in sections if s.crn == crn), None)
                if match and match.time:
                    days = match.days or ""
                    results[crn] = f"{days} {match.time}".strip()
                    logger.info(f"Schedule for {course.course_id}: {results[crn]}")
            except Exception as e:
                logger.warning(f"Could not fetch schedule for {course.course_id}: {e}")

    return results


async def fetch_personal_context_async(cookies_path: str = COOKIES_PATH) -> dict[str, str]:
    return await _fetch_pages_async(cookies_path, MYPCC_PAGES)


async def fetch_degree_audit_async(cookies_path: str = GRADPLAN_COOKIES_PATH) -> str:
    return await _fetch_degree_audit_async(cookies_path)


def fetch_personal_context(cookies_path: str = COOKIES_PATH) -> dict[str, str]:
    return asyncio.run(_fetch_pages_async(cookies_path, MYPCC_PAGES))


def fetch_degree_audit(cookies_path: str = GRADPLAN_COOKIES_PATH) -> str:
    return asyncio.run(_fetch_degree_audit_async(cookies_path))

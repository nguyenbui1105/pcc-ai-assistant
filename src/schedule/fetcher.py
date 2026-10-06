"""Fetch PCC class schedule pages — listing and course detail."""
import asyncio
import re
from datetime import datetime

import httpx
from loguru import logger

# PCC term codes are <calendar year><quarter>: 01 winter, 02 spring, 03 summer,
# 04 fall — e.g. fall2026 → "202604", spring2027 → "202702".
_SEASON_SUFFIX: dict[str, str] = {"winter": "01", "spring": "02", "summer": "03", "fall": "04"}


def _build_term_tables(center_year: int) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Build (codes, url slugs, labels) for every quarter from last year through next year."""
    codes: dict[str, str] = {}
    names: dict[str, str] = {}
    labels: dict[str, str] = {}
    for year in range(center_year - 1, center_year + 2):
        for season, suffix in _SEASON_SUFFIX.items():
            key = f"{season}{year}"
            codes[key] = f"{year}{suffix}"
            names[key] = season
            labels[key] = f"{season.capitalize()} {year}"
    return codes, names, labels


# Term key ("fall2026") → PCC term code / URL slug ("fall") / display label ("Fall 2026").
TERM_CODES, TERM_NAMES, TERM_LABELS = _build_term_tables(datetime.now().year)

_API_BASE = "https://www.pcc.edu/schedule/default.cfm"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _listing_url(subject: str, term: str) -> str:
    code = TERM_CODES[resolve_term(term)]
    return (
        f"{_API_BASE}?fa=doadvquery&thisTerm={code}"
        f"&SubjectCode={subject.upper()}&dltype=*&queryTextType=AND"
    )


async def _get(client: httpx.AsyncClient, url: str, label: str = "") -> str:
    try:
        resp = await client.get(url, headers=_HEADERS)
        resp.raise_for_status()
        logger.debug(f"Fetched {label or url} ({len(resp.text)} chars)")
        return resp.text
    except Exception as e:
        logger.error(f"Failed fetching {label or url}: {e}")
        return ""


async def fetch_listings_async(
    subjects: list[str], term: str | None = None
) -> dict[str, str]:
    """Fetch subject listing pages for multiple subjects concurrently."""
    term = resolve_term(term)
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        results = await asyncio.gather(
            *[_get(client, _listing_url(s, term), f"listing:{s}/{term}") for s in subjects],
            return_exceptions=True,
        )
    return {
        subj: (html if isinstance(html, str) else "")
        for subj, html in zip(subjects, results)
    }


async def fetch_details_async(urls: list[str]) -> dict[str, str]:
    """Fetch multiple course detail pages concurrently."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        results = await asyncio.gather(
            *[_get(client, url, f"detail:{url.split('/')[-3]}") for url in urls],
            return_exceptions=True,
        )
    return {
        url: (html if isinstance(html, str) else "")
        for url, html in zip(urls, results)
    }


_CAPACITY_URL = "https://www.pcc.edu/schedule/capacity/"
_CAPACITY_HEADERS = {
    **_HEADERS,
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.pcc.edu",
    "Referer": "https://www.pcc.edu/schedule/",
}


async def fetch_capacity_async(
    crns: list[str], term: str | None = None
) -> dict[str, dict]:
    """Fetch real seat counts for a list of CRNs.

    Returns dict mapping CRN → {"seat": [available, total], "wait": [available, total]}.
    """
    if not crns:
        return {}
    term_code = TERM_CODES[resolve_term(term)]
    crn_list = ",".join(crns)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.post(
                _CAPACITY_URL,
                data={"term": term_code, "crn": crn_list},
                headers=_CAPACITY_HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.debug(f"Capacity API: {len(data)} CRNs returned")
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.error(f"Capacity API failed: {e}")
        return {}


def get_current_term() -> str:
    """Return the default term key based on today's date.

    Jan–Mar → spring<year>, Apr–Jul → summer<year>, Aug–Dec → fall<year>.
    """
    now = datetime.now()
    if now.month <= 3:
        return f"spring{now.year}"
    if now.month <= 7:
        return f"summer{now.year}"
    return f"fall{now.year}"


def resolve_term(term: str | None = None) -> str:
    """Normalize a term ("Fall 2026", "fall-2026" → "fall2026"); empty → current term.

    Raises ValueError for a term outside TERM_CODES so callers never silently
    fetch a different term than the one asked for.
    """
    if not term:
        return get_current_term()
    key = re.sub(r"[\s_-]", "", term).lower()
    if key not in TERM_CODES:
        raise ValueError(f"Unknown term {term!r}. Valid terms: {', '.join(TERM_CODES)}")
    return key


def fetch_listings(subjects: list[str], term: str | None = None) -> dict[str, str]:
    return asyncio.run(fetch_listings_async(subjects, term))


def fetch_details(urls: list[str]) -> dict[str, str]:
    return asyncio.run(fetch_details_async(urls))


def fetch_capacity(crns: list[str], term: str | None = None) -> dict[str, dict]:
    return asyncio.run(fetch_capacity_async(crns, term))

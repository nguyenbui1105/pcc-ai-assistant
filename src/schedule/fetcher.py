"""Fetch PCC class schedule pages — listing and course detail."""
import asyncio
from dataclasses import dataclass, field

import httpx
from loguru import logger

TERM_CODES: dict[str, str] = {
    "spring2026": "202602",
    "summer2026": "202603",
    "fall2026":   "202604",
    "spring2027": "202702",
}

TERM_NAMES: dict[str, str] = {
    "spring2026": "spring",
    "summer2026": "summer",
    "fall2026":   "fall",
    "spring2027": "spring",
}

TERM_LABELS: dict[str, str] = {
    "spring2026": "Spring 2026",
    "summer2026": "Summer 2026",
    "fall2026":   "Fall 2026",
    "spring2027": "Spring 2027",
}

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
    code = TERM_CODES.get(term, "202603")
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
    subjects: list[str], term: str = "summer2026"
) -> dict[str, str]:
    """Fetch subject listing pages for multiple subjects concurrently."""
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
    crns: list[str], term: str = "summer2026"
) -> dict[str, dict]:
    """Fetch real seat counts for a list of CRNs.

    Returns dict mapping CRN → {"seat": [available, total], "wait": [available, total]}.
    """
    if not crns:
        return {}
    term_code = TERM_CODES.get(term, "202603")
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


def fetch_listings(subjects: list[str], term: str = "summer2026") -> dict[str, str]:
    return asyncio.run(fetch_listings_async(subjects, term))


def fetch_details(urls: list[str]) -> dict[str, str]:
    return asyncio.run(fetch_details_async(urls))


def fetch_capacity(crns: list[str], term: str = "summer2026") -> dict[str, dict]:
    return asyncio.run(fetch_capacity_async(crns, term))

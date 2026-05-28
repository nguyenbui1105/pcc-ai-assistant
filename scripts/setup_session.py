"""
One-time setup: open a real browser, log in, then save cookies automatically.
No Cookie-Editor extension needed.

Usage:
    python scripts/setup_session.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

from playwright.async_api import async_playwright
from loguru import logger

MYPCC_COOKIES_PATH = Path("./data/mypcc_cookies.json")
GRADPLAN_COOKIES_PATH = Path("./data/gradplan_cookies.json")
BANSS_COOKIES_PATH = Path("./data/banss_cookies.json")
MYPCC_URL = "https://my.pcc.edu/"
GRADPLAN_URL = "https://gradplan.pcc.edu/RespDashboard/worksheets/WEB31"
BANSS_URL = "https://banss.pcc.edu/StudentRegistrationSsb/ssb/registrationHistory/registrationHistory"


def _print_banner(msg: str) -> None:
    print(f"\n{'='*60}\n  {msg}\n{'='*60}\n", flush=True)


async def _wait_for_text(page, text: str, label: str, timeout_s: int = 180) -> bool:
    _print_banner(f"Please log into {label} in the browser window.\nWaiting up to {timeout_s // 60} minutes...")
    for elapsed in range(0, timeout_s, 2):
        try:
            content = await page.evaluate("document.body ? document.body.innerText : ''")
            if text in content:
                logger.success(f"Logged into {label} successfully")
                return True
        except Exception:
            pass
        await asyncio.sleep(2)
        remaining = timeout_s - elapsed
        if remaining % 30 == 0 and remaining > 0 and remaining < timeout_s:
            print(f"  Still waiting... {remaining}s remaining", flush=True)
    logger.error(f"Timed out waiting for {label} login")
    return False


def _extract_cookies(storage_state: dict, domain_filter: str) -> list[dict]:
    """Extract cookies matching a domain from Playwright storage state."""
    return [
        c for c in storage_state.get("cookies", [])
        if domain_filter in c.get("domain", "")
    ]


async def setup() -> None:
    MYPCC_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)

    _print_banner("PCC RAG — One-time session setup\nA browser window will open. Log in when prompted.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(viewport={"width": 1280, "height": 800})

        # ── Step 1: MyPCC ──────────────────────────────────────────────────
        page1 = await context.new_page()
        await page1.goto(MYPCC_URL, wait_until="domcontentloaded", timeout=30000)
        ok1 = await _wait_for_text(page1, "Welcome", "MyPCC")

        if not ok1:
            print("MyPCC login failed. Re-run the script.", flush=True)
            await browser.close()
            return

        # Save MyPCC cookies right after login
        state = await context.storage_state()
        mypcc_cookies = _extract_cookies(state, "pcc.edu")
        MYPCC_COOKIES_PATH.write_text(json.dumps(mypcc_cookies, indent=2), encoding="utf-8")
        logger.info(f"Saved {len(mypcc_cookies)} cookies → {MYPCC_COOKIES_PATH}")

        # ── Step 2: GRAD Plan ──────────────────────────────────────────────
        page2 = await context.new_page()
        await page2.goto(GRADPLAN_URL, wait_until="domcontentloaded", timeout=30000)
        ok2 = await _wait_for_text(page2, "GRAD PLAN", "GRAD Plan")

        if ok2:
            state2 = await context.storage_state()
            gradplan_cookies = _extract_cookies(state2, "gradplan.pcc.edu")
            # Also keep shared .pcc.edu cookies
            shared = _extract_cookies(state2, ".pcc.edu")
            all_grad_cookies = {c["name"]: c for c in shared}
            all_grad_cookies.update({c["name"]: c for c in gradplan_cookies})
            GRADPLAN_COOKIES_PATH.write_text(
                json.dumps(list(all_grad_cookies.values()), indent=2), encoding="utf-8"
            )
            logger.info(f"Saved {len(all_grad_cookies)} cookies → {GRADPLAN_COOKIES_PATH}")
        else:
            print("GRAD Plan login timed out. MyPCC cookies still saved.", flush=True)

        # ── Step 3: Banner SSB (schedule times) ───────────────────────────
        page3 = await context.new_page()
        await page3.goto(BANSS_URL, wait_until="networkidle", timeout=30000)
        ok3 = await _wait_for_text(page3, "Registration", "Banner SSB", timeout_s=60)

        if ok3:
            state3 = await context.storage_state()
            banss_cookies = _extract_cookies(state3, "banss.pcc.edu")
            BANSS_COOKIES_PATH.write_text(
                json.dumps(banss_cookies, indent=2), encoding="utf-8"
            )
            logger.info(f"Saved {len(banss_cookies)} cookies → {BANSS_COOKIES_PATH}")
        else:
            print("Banner SSB timed out. Schedule times will be unavailable.", flush=True)

        await browser.close()

    _print_banner(
        "Cookies saved! No more manual exports.\n\n"
        "  Start the chatbot:\n"
        "    chainlit run ui/app.py\n\n"
        "  Re-run this script when cookies expire (~7 days)."
    )


if __name__ == "__main__":
    asyncio.run(setup())

"""Entry point: crawl PCC website and save raw pages to data/raw/."""
import asyncio
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")
sys.path.insert(0, ".")

from loguru import logger
from src.crawler.config import CrawlConfig
from src.crawler.scraper import PCCCrawler


async def main() -> None:
    config = CrawlConfig()
    crawler = PCCCrawler(config)
    await crawler.run()


if __name__ == "__main__":
    logger.info("Starting PCC crawler (student-relevant pages only)...")
    asyncio.run(main())

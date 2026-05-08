#!/usr/bin/env python3
"""
Download NSE Bhavcopy for a date range and load into ClickHouse.
Usage: python scripts/backfill.py --from 2024-01-01 --to 2024-12-31
"""
import argparse
import asyncio
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    parser = argparse.ArgumentParser(description="NSE Bhavcopy backfill to ClickHouse")
    parser.add_argument("--from", dest="from_date", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--to", 
                        dest="to_date", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--symbols", help="Path to yaml with symbol list (optional)")
    args = parser.parse_args()

    from core.logger import configure_logging
    configure_logging("INFO")

    try:
        from tqdm import tqdm
    except ImportError:
        tqdm = None

    from data.storage.clickhouse_writer import ClickHouseWriter
    from data.backfill.nse_bhavcopy import NSEBhavcopyDownloader
    from core.config import get_instruments_config
    from core.clock import MarketClock

    writer = ClickHouseWriter(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", 9000)),
        database=os.getenv("CLICKHOUSE_DB", "trading"),
        user=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
    )
    await writer.connect()

    cfg = get_instruments_config()
    clock = MarketClock(holidays=cfg.holidays)
    downloader = NSEBhavcopyDownloader()

    start = date.fromisoformat(args.from_date)
    end = date.fromisoformat(args.to_date)

    all_dates = []
    d = start
    while d <= end:
        if clock.is_trading_day(d):
            all_dates.append(d)
        d += timedelta(days=1)

    print(f"Processing {len(all_dates)} trading days from {start} to {end}")

    loaded = 0
    skipped = 0
    failed = 0

    iterator = tqdm(all_dates, desc="Backfilling") if tqdm else all_dates
    for d in iterator:
        already_loaded = await downloader.is_date_loaded(d, writer)
        if already_loaded:
            skipped += 1
            continue

        df = downloader.download_bhavcopy(d)
        if df is None:
            failed += 1
            continue

        rows = await downloader.load_to_clickhouse(df, writer)
        loaded += 1
        if tqdm is None:
            print(f"  {d}: loaded {rows} rows")

    print(f"\nDone. Loaded: {loaded}, Skipped (already exists): {skipped}, Failed: {failed}")


if __name__ == "__main__":
    asyncio.run(main())

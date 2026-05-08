import asyncio
import io
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Optional
import polars as pl
import requests
import structlog

logger = structlog.get_logger()

BHAVCOPY_URL = "https://nsearchives.nseindia.com/content/historical/EQUITIES/{year}/{month}/cm{date}bhav.csv.zip"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "*/*",
    "Connection": "keep-alive",
}


class NSEBhavcopyDownloader:
    def __init__(self, executor: Optional[ThreadPoolExecutor] = None) -> None:
        self._executor = executor or ThreadPoolExecutor(max_workers=2)

    def download_bhavcopy(self, d: date) -> Optional[pl.DataFrame]:
        url = BHAVCOPY_URL.format(
            year=d.strftime("%Y"),
            month=d.strftime("%b").upper(),
            date=d.strftime("%d%b%Y").upper(),
        )
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=HEADERS, timeout=30)
                if resp.status_code == 404:
                    logger.info("bhavcopy_not_found", date=str(d))
                    return None
                resp.raise_for_status()

                with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                    csv_name = z.namelist()[0]
                    with z.open(csv_name) as f:
                        df = pl.read_csv(f)

                df = df.select([
                    pl.col("SYMBOL").alias("symbol"),
                    pl.col("OPEN").cast(pl.Float64).alias("open"),
                    pl.col("HIGH").cast(pl.Float64).alias("high"),
                    pl.col("LOW").cast(pl.Float64).alias("low"),
                    pl.col("CLOSE").cast(pl.Float64).alias("close"),
                    pl.col("TOTTRDQTY").cast(pl.Int64).alias("volume"),
                    pl.lit(str(d)).alias("date"),
                ])
                logger.info("bhavcopy_downloaded", date=str(d), rows=len(df))
                return df

            except Exception as e:
                logger.warning("bhavcopy_download_failed", attempt=attempt, date=str(d), error=str(e))
                if attempt < 2:
                    time.sleep(2 ** attempt)
        return None

    async def load_to_clickhouse(self, df: pl.DataFrame, writer) -> int:
        rows = []
        from datetime import datetime
        import pytz
        IST = pytz.timezone("Asia/Kolkata")

        for row in df.iter_rows(named=True):
            close = row["close"]
            rows.append({
                "timestamp": IST.localize(datetime.strptime(row["date"], "%Y-%m-%d").replace(hour=15, minute=30)),
                "symbol": row["symbol"],
                "exchange": "NSE",
                "last_price": close,
                "bid": close * 0.9995,
                "ask": close * 1.0005,
                "bid_qty": 0,
                "ask_qty": 0,
                "volume": row["volume"],
                "oi": 0,
            })

        for row in rows:
            await writer.write_tick(row)
        await writer._flush_ticks()
        return len(rows)

    async def is_date_loaded(self, d: date, writer) -> bool:
        try:
            result = await writer.query(
                f"SELECT count() as cnt FROM trading.ticks WHERE toDate(timestamp) = '{d}' LIMIT 1"
            )
            return result[0]["cnt"] > 0 if result else False
        except Exception:
            return False

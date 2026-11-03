"""穏当なリクエスト間隔、タイムアウト、再試行とサイズ上限を備えたHTTP取得。"""
from __future__ import annotations
import time
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

MAX_BYTES = 6_000_000
USER_AGENT = "Shiryoshitsu/0.1 (+https://github.com/erizomu/shiryoshitsu; bibliographic-metadata-only)"


class FetchError(Exception):
    pass


def _retry_delay(value: str, attempt: int, minimum_delay: float = 0) -> float:
    delay = max(float(2 ** (attempt + 1)), minimum_delay)
    if value:
        try:
            requested = float(value) if value.isdecimal() else (
                parsedate_to_datetime(value) - datetime.now(timezone.utc)
            ).total_seconds()
            delay = max(delay, requested)
        except (ValueError, TypeError, OverflowError):
            pass
    # 長時間待機は次回実行に委ねる。指定待機時間より早く再要求しない。
    if delay > 60:
        raise FetchError("再試行には60秒超の待機が必要です。時間をおいて再実行してください")
    return delay


def get_xml(url: str, *, timeout: float = 15.0, retries: int = 2, minimum_delay: float = 0) -> bytes:
    if not math.isfinite(timeout) or timeout <= 0 or not isinstance(retries, int) or not 0 <= retries <= 5:
        raise ValueError("timeoutは有限の正数、retriesは0〜5の整数で指定してください")
    if not math.isfinite(minimum_delay) or minimum_delay < 0:
        raise ValueError("minimum_delayは有限の0以上で指定してください")
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml"})
            with urlopen(request, timeout=timeout) as response:
                data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise FetchError("レスポンスが6MBを超えました")
                return data
        except HTTPError as exc:
            try:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == retries:
                    raise FetchError(f"HTTP {exc.code} (API側の状態・利用条件を確認してください)") from exc
                delay = _retry_delay(exc.headers.get("Retry-After", "") if exc.headers else "", attempt, minimum_delay)
            finally:
                exc.close()
            time.sleep(delay)
        except (URLError, TimeoutError, OSError, HTTPException) as exc:
            if attempt == retries:
                raise FetchError(f"ネットワーク接続に失敗しました: {type(exc).__name__}") from exc
            time.sleep(_retry_delay("", attempt, minimum_delay))
    raise AssertionError("unreachable")

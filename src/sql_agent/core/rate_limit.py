from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque

from sql_agent.core.errors import RateLimitError


class SlidingWindowRateLimiter:
    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max_per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        window = 60.0
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= self.max_per_minute:
                raise RateLimitError("Rate limit exceeded. Try again shortly.")
            q.append(now)


class ConcurrencyLimiter:
    def __init__(self, max_concurrent: int) -> None:
        self._sem = threading.BoundedSemaphore(max_concurrent)

    def acquire(self) -> bool:
        return self._sem.acquire(blocking=False)

    def release(self) -> None:
        self._sem.release()


_SQL_FENCE = re.compile(r"```(?:sql)?\s*([\s\S]*?)```", re.I)


def extract_sql_text(raw: str) -> str:
    text = (raw or "").strip()
    fenced = _SQL_FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    lower = text.lower()
    marker = "sql:"
    if marker in lower:
        text = text[lower.rfind(marker) + len(marker) :].strip()
    for token in ("```sql", "```"):
        text = text.replace(token, "")
    return text.strip().rstrip(";")

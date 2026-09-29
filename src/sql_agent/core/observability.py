from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        stream=sys.stdout,
        force=True,
    )


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def log_event(event: str, **fields: Any) -> None:
    payload = {"event": event, **{k: v for k, v in fields.items() if v is not None}}
    logging.getLogger("sql_agent").info(json.dumps(payload, default=str))


@contextmanager
def timed(event: str, request_id: str, **fields: Any) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        log_event(
            event,
            request_id=request_id,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
            **fields,
        )

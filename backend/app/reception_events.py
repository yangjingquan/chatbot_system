"""In-process reception event hub for admin WebSocket subscribers.

Single-backend / single-process only: subscribers live in this process memory.
Multiple uvicorn workers or multiple backend containers will NOT share events;
use one backend replica for local/Docker trial, or add a shared bus later.
"""
from __future__ import annotations
import asyncio
import json
import threading
from typing import Any

_lock = threading.Lock()
# tenant_id -> set of asyncio.Queue
_subscribers: dict[str, set[asyncio.Queue]] = {}


def subscribe(tenant_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    with _lock:
        _subscribers.setdefault(tenant_id, set()).add(queue)
    return queue


def unsubscribe(tenant_id: str, queue: asyncio.Queue) -> None:
    with _lock:
        group = _subscribers.get(tenant_id)
        if not group:
            return
        group.discard(queue)
        if not group:
            _subscribers.pop(tenant_id, None)


def publish(tenant_id: str, event_type: str, **payload: Any) -> int:
    """Fan-out a reception event to all live subscribers of the tenant. Returns delivered count."""
    message = {'type': event_type, **payload}
    with _lock:
        targets = list(_subscribers.get(tenant_id, ()))
    delivered = 0
    for queue in targets:
        try:
            queue.put_nowait(message)
            delivered += 1
        except asyncio.QueueFull:
            # Drop oldest then retry once so a slow client does not stall publishers.
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(message)
                delivered += 1
            except asyncio.QueueFull:
                pass
    return delivered


def subscriber_count(tenant_id: str | None = None) -> int:
    with _lock:
        if tenant_id is None:
            return sum(len(v) for v in _subscribers.values())
        return len(_subscribers.get(tenant_id, ()))


def encode(message: dict) -> str:
    return json.dumps(message, ensure_ascii=False, default=str)

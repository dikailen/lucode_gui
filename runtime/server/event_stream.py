from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import contextmanager
from typing import Iterator

from runtime.server.schemas import RunEvent


class RunEventStream:
    """Versioned in-memory run event stream for the stage-one Runtime Server."""

    def __init__(self) -> None:
        self._events: dict[str, list[RunEvent]] = defaultdict(list)
        self._subscribers: dict[str, list[asyncio.Queue[RunEvent]]] = defaultdict(list)

    def emit(self, *, run_id: str, session_id: str, event_type: str, payload: dict | None = None) -> RunEvent:
        events = self._events[str(run_id)]
        event = RunEvent(
            run_id=str(run_id),
            session_id=str(session_id),
            seq=len(events) + 1,
            type=str(event_type),
            payload=dict(payload or {}),
        )
        events.append(event)
        for queue in list(self._subscribers.get(str(run_id), [])):
            queue.put_nowait(event)
        return event

    def snapshot(self, run_id: str, *, after_seq: int = 0) -> list[RunEvent]:
        cursor = max(0, int(after_seq or 0))
        return [event for event in self._events.get(str(run_id), []) if event.seq > cursor]

    def subscriber_count(self, run_id: str) -> int:
        return len(self._subscribers.get(str(run_id), []))

    @contextmanager
    def subscribe(self, run_id: str) -> Iterator[asyncio.Queue[RunEvent]]:
        queue: asyncio.Queue[RunEvent] = asyncio.Queue()
        subscribers = self._subscribers[str(run_id)]
        subscribers.append(queue)
        try:
            yield queue
        finally:
            try:
                subscribers.remove(queue)
            except ValueError:
                pass

"""Server-Sent Events helpers."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi.responses import StreamingResponse


def sse_format(data: Any, event: str | None = None, id_: int | str | None = None) -> str:
    lines = []
    if id_ is not None:
        lines.append(f"id: {id_}")
    if event:
        lines.append(f"event: {event}")
    payload = json.dumps(data, ensure_ascii=False)
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def sse_response(gen: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

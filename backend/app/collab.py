"""Live collaboration: presence (cursors, selection) and edit broadcast over WebSocket.

Edits still go through the normal HTTP endpoints. After an edit the server
sends the same map patch it returned to the author to every other client of
the document, numbered with the document revision so clients apply patches in
order. Presence messages are relayed as they come.

HTTP handlers run in a thread pool, so publishing hops onto the event loop
with call_soon_threadsafe.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
from dataclasses import dataclass, field
from typing import Any

COLORS = ["#2563EB", "#DB2777", "#16A34A", "#EA580C", "#7C3AED", "#0891B2", "#CA8A04", "#DC2626"]


@dataclass
class Peer:
    id: str
    name: str
    color: str
    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop
    presence: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "color": self.color, **self.presence}


class Hub:
    def __init__(self) -> None:
        self._docs: dict[str, dict[str, Peer]] = {}
        self._lock = threading.Lock()
        self._counter = itertools.count(1)

    def next_color(self) -> str:
        return COLORS[next(self._counter) % len(COLORS)]

    def join(self, doc_id: str, peer: Peer) -> list[dict[str, Any]]:
        with self._lock:
            peers = self._docs.setdefault(doc_id, {})
            others = [p.public() for p in peers.values()]
            peers[peer.id] = peer
        self.publish(doc_id, {"type": "join", "peer": peer.public()}, exclude=peer.id)
        return others

    def leave(self, doc_id: str, peer_id: str) -> None:
        with self._lock:
            peers = self._docs.get(doc_id, {})
            peers.pop(peer_id, None)
            if not peers:
                self._docs.pop(doc_id, None)
        self.publish(doc_id, {"type": "leave", "id": peer_id})

    def peers(self, doc_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [p.public() for p in self._docs.get(doc_id, {}).values()]

    def update_presence(self, doc_id: str, peer_id: str, presence: dict[str, Any]) -> None:
        with self._lock:
            peer = self._docs.get(doc_id, {}).get(peer_id)
            if peer is None:
                return
            name = presence.pop("name", None)
            if name:
                peer.name = name
            peer.presence.update(presence)
            msg = {"type": "presence", "peer": peer.public()}
        self.publish(doc_id, msg, exclude=peer_id)

    def publish(self, doc_id: str, message: dict[str, Any], exclude: str | None = None) -> None:
        """Safe to call from any thread."""
        with self._lock:
            targets = [p for pid, p in self._docs.get(doc_id, {}).items() if pid != exclude]
        for p in targets:
            try:
                p.loop.call_soon_threadsafe(p.queue.put_nowait, message)
            except RuntimeError:
                pass  # loop closed: the socket is going away


HUB = Hub()


def clean_presence(data: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields peers need, with sane types."""
    out: dict[str, Any] = {}
    cursor = data.get("cursor")
    if cursor is None or (isinstance(cursor, list) and len(cursor) == 2 and all(isinstance(v, (int, float)) for v in cursor)):
        out["cursor"] = cursor
    sel = data.get("selection")
    if sel is None or (isinstance(sel, dict) and sel.get("kind") in ("way", "node") and isinstance(sel.get("id"), int)):
        out["selection"] = sel
    view = data.get("view")
    if isinstance(view, dict) and isinstance(view.get("zoom"), (int, float)) and isinstance(view.get("center"), list)             and len(view["center"]) == 2 and all(isinstance(v, (int, float)) for v in view["center"]):
        out["view"] = {"center": view["center"], "zoom": view["zoom"]}  # camera, for "follow this editor"
    if isinstance(data.get("tool"), str):
        out["tool"] = data["tool"][:20]
    if isinstance(data.get("name"), str) and data["name"].strip():
        out["name"] = data["name"].strip()[:32]
    return out

"""In-memory visitor sessions: one agent conversation per browser, cleaned up when idle.

The browser keeps its own id in localStorage and sends it with every request. Losing the server's
copy (a restart, or a long pause) only means the next question starts a fresh conversation.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from grocery_agent import prefs
from grocery_agent.agent import Agent

IDLE_SECONDS = 3 * 3600
MAX_SESSIONS = 500


@dataclass
class Session:
    agent: Agent
    prefs: prefs.MemoryStore = field(default_factory=prefs.MemoryStore)
    import_cost_usd: float = 0.0
    last_seen: float = field(default_factory=time.monotonic)
    busy: threading.Lock = field(default_factory=threading.Lock)

    @property
    def spent_usd(self) -> float:
        return self.agent.cost + self.import_cost_usd


class Sessions:
    def __init__(self, make_agent, idle_seconds: float = IDLE_SECONDS, max_sessions: int = MAX_SESSIONS):
        self._make_agent = make_agent
        self._idle = idle_seconds
        self._max = max_sessions
        self._items: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Session:
        """The session for this id, created if new. Also drops idle and surplus sessions."""
        now = time.monotonic()
        with self._lock:
            for sid in [s for s, v in self._items.items() if now - v.last_seen > self._idle]:
                del self._items[sid]
            session = self._items.pop(session_id, None) or Session(agent=self._make_agent())
            session.last_seen = now
            self._items[session_id] = session  # most recently used last
            while len(self._items) > self._max:
                self._items.popitem(last=False)
            return session

    def peek(self, session_id: str) -> Session | None:
        with self._lock:
            return self._items.get(session_id)

    def reset(self, session_id: str) -> None:
        """Start a new conversation; money spent in this session still counts."""
        with self._lock:
            old = self._items.get(session_id)
            if old is not None:
                spent = old.spent_usd
                fresh = Session(agent=self._make_agent(), prefs=old.prefs, import_cost_usd=spent)
                self._items[session_id] = fresh

    def __len__(self) -> int:
        return len(self._items)

import copy
import time
import uuid
from dataclasses import dataclass, field

from . import config
from .data import MOCK_ORDERS


@dataclass
class Session:
    id: str
    orders: dict
    messages: list = field(default_factory=list)      # LLM-facing history (no system prompt)
    transcript: list = field(default_factory=list)    # [{role, text, ts}]
    tool_log: list = field(default_factory=list)      # [{name, args, result}]
    started_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    turns: int = 0

    def over_limit(self) -> bool:
        return self.turns >= config.MAX_TURNS or (time.time() - self.started_at) > config.MAX_CALL_SECONDS

    def log(self, role: str, text: str):
        self.transcript.append({"role": role, "text": text, "ts": round(time.time(), 1)})

    def history(self) -> list:
        """Trimmed history that never starts on an orphaned tool/assistant message."""
        msgs = self.messages
        if len(msgs) <= config.MAX_HISTORY_MESSAGES:
            return list(msgs)
        cut = len(msgs) - config.MAX_HISTORY_MESSAGES
        while cut < len(msgs) and msgs[cut]["role"] != "user":
            cut += 1
        return list(msgs[cut:])


class SessionStore:
    MAX_SESSIONS = 200
    TTL_SECONDS = 30 * 60

    def __init__(self):
        self._s: dict[str, Session] = {}

    def create(self) -> Session:
        self._cleanup()
        s = Session(id=uuid.uuid4().hex, orders=copy.deepcopy(MOCK_ORDERS))
        self._s[s.id] = s
        return s

    def get(self, sid: str) -> Session | None:
        s = self._s.get(sid)
        if s:
            s.last_active = time.time()
        return s

    def _cleanup(self):
        now = time.time()
        for sid in [k for k, v in self._s.items() if now - v.last_active > self.TTL_SECONDS]:
            del self._s[sid]
        while len(self._s) >= self.MAX_SESSIONS:
            del self._s[min(self._s, key=lambda k: self._s[k].last_active)]


store = SessionStore()

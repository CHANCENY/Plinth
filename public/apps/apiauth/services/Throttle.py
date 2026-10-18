"""
Throttle
----------
Slows down guessing. Failed attempts are counted per key (the callers use client IP + the identity being
tried, so one attacker can't lock a real user out of everywhere, and one identity can't be hammered from one place);
once a key has `limit` failures inside `window` seconds it's blocked until the oldest of them ages out, and a
success clears the count.

In memory, per process: it resets when the server restarts and isn't shared between several server processes.
That's honest protection for the single-process dev server this framework ships with; behind a multi-process
deployment put rate limiting in the proxy (nginx limit_req, a WAF) as well. The client address is the TCP peer,
so behind a reverse proxy every request appears to come from the proxy.
"""

import threading
import time
from typing import Dict, List

_MAX_KEYS = 20000  # bound memory if someone sprays random identities


class Throttle:
    def __init__(self):
        self._failures: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str, limit: int, window: int) -> int:
        """Seconds until this key may try again - 0 if it isn't blocked."""
        if limit <= 0:
            return 0
        with self._lock:
            recent = self._recent(key, window)
            if len(recent) < limit:
                return 0
            return max(int(recent[0] + window - time.time()) + 1, 1)

    def fail(self, key: str, window: int) -> None:
        with self._lock:
            if len(self._failures) >= _MAX_KEYS:
                self._sweep(window)
            recent = self._recent(key, window)
            recent.append(time.time())
            self._failures[key] = recent

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def reset_all(self) -> None:
        with self._lock:
            self._failures.clear()

    def _recent(self, key: str, window: int) -> List[float]:
        cutoff = time.time() - window
        return [t for t in self._failures.get(key, []) if t > cutoff]

    def _sweep(self, window: int) -> None:
        cutoff = time.time() - window
        for key in [k for k, times in self._failures.items() if not times or times[-1] <= cutoff]:
            del self._failures[key]


throttle = Throttle()  # one per process - state has to outlive a single request


class NonceCache:
    """
    Remembers the nonces of signed requests for a while so a captured request can't be replayed inside the
    timestamp tolerance window. In memory and per process, like Throttle - see its note.
    """

    def __init__(self):
        self._seen: Dict[str, float] = {}
        self._lock = threading.Lock()

    def use(self, nonce_key: str, ttl: int) -> bool:
        """Records the nonce and returns True the first time; False if it was already used (a replay)."""
        now = time.time()
        with self._lock:
            if len(self._seen) >= _MAX_KEYS:
                self._seen = {k: expires for k, expires in self._seen.items() if expires > now}
            if self._seen.get(nonce_key, 0) > now:
                return False
            self._seen[nonce_key] = now + ttl
            return True

    def reset_all(self) -> None:
        with self._lock:
            self._seen.clear()


nonces = NonceCache()

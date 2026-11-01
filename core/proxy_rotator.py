"""
proxy_rotator.py — Proxy pool management with round-robin rotation,
smart banning, and connectivity testing.
"""

import time
import threading
import logging
from typing import Optional, List, Dict
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)


@dataclass
class ProxyInfo:
    """Tracks a single proxy's state."""
    url: str
    protocol: str = "http"       # http | socks5
    fail_count: int = 0
    success_count: int = 0
    banned_until: float = 0.0    # epoch timestamp
    last_used: float = 0.0
    latency_ms: float = 0.0

    @property
    def is_banned(self) -> bool:
        return time.time() < self.banned_until

    def record_success(self, latency_ms: float = 0.0):
        self.success_count += 1
        self.fail_count = 0
        self.latency_ms = latency_ms
        self.last_used = time.time()

    def record_failure(self, ban_seconds: int = 60):
        self.fail_count += 1
        self.last_used = time.time()
        if self.fail_count >= 3:
            self.banned_until = time.time() + ban_seconds
            log.warning("Proxy banned for %ds: %s", ban_seconds, self.url)


class ProxyRotator:
    """Thread-safe proxy rotator with smart rotation."""

    def __init__(self):
        self._proxies: List[ProxyInfo] = []
        self._index: int = 0
        self._lock = threading.Lock()
        self._enabled: bool = False

    @property
    def enabled(self) -> bool:
        return self._enabled and len(self._proxies) > 0

    @property
    def count(self) -> int:
        return len(self._proxies)

    @property
    def active_count(self) -> int:
        return sum(1 for p in self._proxies if not p.is_banned)

    def load_proxies(self, proxy_list: List[str]):
        """Load proxies from a list of URLs.

        Formats supported:
            http://host:port
            socks5://user:pass@host:port
            host:port  (assumes http)
        """
        with self._lock:
            self._proxies.clear()
            for raw in proxy_list:
                raw = raw.strip()
                if not raw or raw.startswith('#'):
                    continue
                if '://' not in raw:
                    raw = f"http://{raw}"
                protocol = "socks5" if raw.startswith("socks5") else "http"
                self._proxies.append(ProxyInfo(url=raw, protocol=protocol))
            self._index = 0
            self._enabled = len(self._proxies) > 0
            log.info("Loaded %d proxies", len(self._proxies))

    def load_from_file(self, filepath: str):
        """Load proxies from a text file (one per line)."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            self.load_proxies(lines)
        except IOError as e:
            log.error("Failed to load proxy file: %s", e)

    def get_next(self) -> Optional[str]:
        """Get the next available proxy URL (round-robin, skipping banned)."""
        if not self._enabled:
            return None

        with self._lock:
            n = len(self._proxies)
            for _ in range(n):
                proxy = self._proxies[self._index % n]
                self._index = (self._index + 1) % n
                if not proxy.is_banned:
                    proxy.last_used = time.time()
                    return proxy.url
            # All banned — unban the one with earliest ban
            earliest = min(self._proxies, key=lambda p: p.banned_until)
            earliest.banned_until = 0
            earliest.fail_count = 0
            return earliest.url

    def report_success(self, proxy_url: str, latency_ms: float = 0.0):
        """Report a successful request through a proxy."""
        with self._lock:
            for p in self._proxies:
                if p.url == proxy_url:
                    p.record_success(latency_ms)
                    break

    def report_failure(self, proxy_url: str):
        """Report a failed request through a proxy."""
        with self._lock:
            for p in self._proxies:
                if p.url == proxy_url:
                    p.record_failure()
                    break

    def test_proxy(self, proxy_url: str, timeout: float = 10.0) -> Dict:
        """Test a single proxy's connectivity.

        Returns:
            Dict with keys: ok, latency_ms, error
        """
        try:
            start = time.time()
            with httpx.Client(
                proxy=proxy_url,
                timeout=timeout,
                verify=False,
            ) as client:
                resp = client.get("https://api.elevenlabs.io/v1/voices")
                latency = (time.time() - start) * 1000
                return {
                    "ok": resp.status_code == 200,
                    "latency_ms": round(latency, 1),
                    "status": resp.status_code,
                    "error": None,
                }
        except Exception as e:
            return {
                "ok": False,
                "latency_ms": 0,
                "status": 0,
                "error": str(e),
            }

    def test_all(self) -> List[Dict]:
        """Test all proxies and return results."""
        results = []
        for p in self._proxies:
            result = self.test_proxy(p.url)
            result["proxy"] = p.url
            if result["ok"]:
                p.record_success(result["latency_ms"])
            else:
                p.record_failure()
            results.append(result)
        return results

    def get_stats(self) -> List[Dict]:
        """Get stats for all proxies."""
        return [
            {
                "url": p.url,
                "protocol": p.protocol,
                "success": p.success_count,
                "fails": p.fail_count,
                "banned": p.is_banned,
                "latency_ms": p.latency_ms,
            }
            for p in self._proxies
        ]

    def clear(self):
        """Remove all proxies."""
        with self._lock:
            self._proxies.clear()
            self._enabled = False
            self._index = 0

    def set_enabled(self, enabled: bool):
        """Toggle proxy usage."""
        self._enabled = enabled and len(self._proxies) > 0

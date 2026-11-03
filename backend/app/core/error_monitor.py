"""In-process rolling error-rate monitor.

Prometheus + Alertmanager (infra/prometheus) is the primary detection mechanism. This
monitor is a zero-dependency safety net for deployments without a metrics stack: it logs
a structured `error_rate_threshold_breached` event (easy to alert on in any log
platform) and optionally POSTs to a webhook (e.g. Slack).
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass

import httpx

from app.core.metrics import ERROR_RATE

log = logging.getLogger("app.error_monitor")


@dataclass
class MonitorConfig:
    window_seconds: int = 300
    min_requests: int = 20
    threshold_5xx: float = 0.05
    threshold_4xx: float = 0.30
    cooldown_seconds: int = 600
    webhook_url: str | None = None


class ErrorRateMonitor:
    def __init__(self, cfg: MonitorConfig, clock=time.monotonic):
        self.cfg = cfg
        self._clock = clock
        self._events: deque[tuple[float, int]] = deque()
        self._counts = {2: 0, 3: 0, 4: 0, 5: 0}
        self._last_alert: dict[str, float] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0][0] > self.cfg.window_seconds:
            _, cls = self._events.popleft()
            self._counts[cls] -= 1

    def record(self, status_code: int) -> list[str]:
        """Record a response; returns the list of alerts fired (for tests)."""
        cls = min(max(status_code // 100, 2), 5)
        now = self._clock()
        with self._lock:
            self._events.append((now, cls))
            self._counts[cls] += 1
            self._prune(now)
            total = len(self._events)
            ratios = {
                "4xx": self._counts[4] / total if total else 0.0,
                "5xx": self._counts[5] / total if total else 0.0,
            }
            fired = []
            if total >= self.cfg.min_requests:
                for name, threshold in (
                    ("5xx", self.cfg.threshold_5xx),
                    ("4xx", self.cfg.threshold_4xx),
                ):
                    last = self._last_alert.get(name)
                    cooled = last is None or now - last > self.cfg.cooldown_seconds
                    if ratios[name] > threshold and cooled:
                        self._last_alert[name] = now
                        fired.append(name)
        for name, value in ratios.items():
            ERROR_RATE.labels(status_class=name).set(value)
        for name in fired:
            self._alert(name, ratios[name], total)
        return fired

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            self._prune(self._clock())
            total = len(self._events)
            return {
                "window_seconds": self.cfg.window_seconds,
                "requests": total,
                "ratio_4xx": round(self._counts[4] / total, 4) if total else 0.0,
                "ratio_5xx": round(self._counts[5] / total, 4) if total else 0.0,
            }

    def _alert(self, status_class: str, ratio: float, total: int) -> None:
        payload = {
            "alert": f"high_{status_class}_rate",
            "ratio": round(ratio, 4),
            "requests_in_window": total,
            "window_seconds": self.cfg.window_seconds,
        }
        log.error("error_rate_threshold_breached", extra=payload)
        if self.cfg.webhook_url:
            threading.Thread(target=self._post, args=(payload,), daemon=True).start()

    def _post(self, payload: dict[str, object]) -> None:
        text = (
            f":rotating_light: {payload['alert']}: {payload['ratio']:.1%} of "
            f"{payload['requests_in_window']} requests in last {payload['window_seconds']}s"
        )
        try:
            httpx.post(self.cfg.webhook_url or "", json={"text": text, **payload}, timeout=5)
        except httpx.HTTPError:
            log.warning("alert_webhook_failed", exc_info=True)

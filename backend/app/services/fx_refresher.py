"""Background FX refresh.

Runs inside each API process. A Postgres transaction-scoped advisory lock plus a
freshness check make it safe with N replicas: at most one instance calls the provider
per interval, the others see a fresh snapshot and skip.
"""

import logging
import threading
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.fx.providers import FxProvider, FxProviderError
from app.services import fx_service

log = logging.getLogger(__name__)
ADVISORY_LOCK_KEY = 0x0F_C0FFEE


class FxRefresher:
    def __init__(
        self, provider: FxProvider, session_factory: sessionmaker[Session], interval_seconds: int
    ):
        self.provider = provider
        self.session_factory = session_factory
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self, force: bool = False) -> bool:
        """Returns True if a new snapshot was stored."""
        with self.session_factory() as db:
            got = db.execute(
                text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY}
            ).scalar()
            if not got:
                return False
            latest = fx_service.latest_snapshot(db)
            if latest and not force:
                age = (datetime.now(UTC) - latest.fetched_at).total_seconds()
                if age < self.interval * 0.9:
                    db.rollback()
                    return False
            fx_service.refresh_rates(db, self.provider)  # commits -> releases lock
            return True

    def _loop(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                self.run_once()
                failures = 0
                delay: float = min(self.interval, 300)
            except FxProviderError:
                failures += 1
                delay = min(30 * 2**failures, self.interval)
            except Exception:
                failures += 1
                log.exception("fx_refresher_error")
                delay = min(30 * 2**failures, self.interval)
            self._stop.wait(delay)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="fx-refresher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

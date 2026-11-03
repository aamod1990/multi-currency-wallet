"""Background FX refresh.

Runs inside each API process. A short-lived lease document in MongoDB plus a
freshness check make it safe with N replicas: at most one instance calls the
provider per interval, the others see a fresh snapshot (or a held lease) and skip.
"""

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import timedelta

from pymongo.errors import DuplicateKeyError

from app.db.models import now
from app.db.mongo import LOCKS, Db
from app.fx.providers import FxProvider, FxProviderError
from app.services import fx_service

log = logging.getLogger(__name__)
LEASE_ID = "fx-refresh"
LEASE_SECONDS = 120


def acquire_lease(db: Db, owner: str, seconds: int = LEASE_SECONDS) -> bool:
    t = now()
    try:
        db[LOCKS].find_one_and_update(
            {"_id": LEASE_ID, "expires_at": {"$lt": t}},
            {"$set": {"owner": owner, "expires_at": t + timedelta(seconds=seconds)}},
            upsert=True,
        )
        return True
    except DuplicateKeyError:
        return False  # lease exists and has not expired


def release_lease(db: Db, owner: str) -> None:
    db[LOCKS].delete_one({"_id": LEASE_ID, "owner": owner})


class FxRefresher:
    def __init__(self, provider: FxProvider, db_factory: Callable[[], Db], interval_seconds: int):
        self.provider = provider
        self.db_factory = db_factory
        self.interval = interval_seconds
        self.owner = uuid.uuid4().hex
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self, force: bool = False) -> bool:
        """Returns True if a new snapshot was stored."""
        db = self.db_factory()
        if not acquire_lease(db, self.owner):
            return False
        try:
            latest = fx_service.latest_snapshot(db)
            if latest and not force:
                age = (now() - latest.fetched_at).total_seconds()
                if age < self.interval * 0.9:
                    return False
            fx_service.refresh_rates(db, self.provider)
            return True
        finally:
            release_lease(db, self.owner)

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

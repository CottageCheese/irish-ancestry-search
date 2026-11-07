"""Tiny JSON resume cache keyed on search parameters. 30-day TTL."""
import json
import time
from dataclasses import asdict
from pathlib import Path

from genealogy_core.models import Record

from . import settings


class SearchCache:
    """Repeat-run cache so we don't re-hit the same URL. Values are Record
    lists plus a timestamp. Entries older than CACHE_TTL_DAYS are ignored.
    """

    def __init__(self, path: Path, enabled: bool):
        self.path = path
        self.enabled = enabled
        self.data: dict[str, dict] = {}
        self.hits = 0
        self.misses = 0
        if enabled and path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    @staticmethod
    def key(*parts: str) -> str:
        return "|".join((p or "").strip().lower() for p in parts)

    def get(self, key: str) -> list[Record] | None:
        if not self.enabled:
            return None
        entry = self.data.get(key)
        if not entry:
            self.misses += 1
            return None
        ts = entry.get("ts", 0)
        age_days = (time.time() - ts) / 86400
        if age_days > settings.CACHE_TTL_DAYS:
            self.misses += 1
            return None
        self.hits += 1
        return [Record(**r) for r in entry.get("records", [])]

    def put(self, key: str, records: list[Record]) -> None:
        if not self.enabled:
            return
        self.data[key] = {"ts": time.time(), "records": [asdict(r) for r in records]}

    def save(self) -> None:
        if not self.enabled:
            return
        try:
            self.path.write_text(
                json.dumps(self.data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

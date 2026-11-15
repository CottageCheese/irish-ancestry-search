"""User confirm/reject sidecar for records.

Confirm/reject state is kept out of results.csv (which is overwritten each
run) and lives in `confirmations.json` alongside it. The key is a short
hash of the diff-key so two scraper runs that re-produce the same record
map to the same confirmation entry.
"""
import hashlib
import json
from pathlib import Path
from typing import Iterable

from .diff import _record_diff_key
from .models import Record

VALID_STATUSES = {"confirmed", "saved", "rejected"}


def record_key(r: Record) -> str:
    """Stable 12-char hash of the diff key. Short enough for HTML attrs,
    long enough to not collide in a per-ancestor result set.
    """
    tup = _record_diff_key(r)
    blob = json.dumps(tup, ensure_ascii=False, sort_keys=False).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:12]


def load_confirmations(path: Path) -> dict[str, dict]:
    """Load the sidecar. Missing file / corrupt JSON → empty dict."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_confirmations(path: Path, data: dict[str, dict]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def set_status(data: dict[str, dict], key: str, status: str,
               person_label: str = "") -> dict[str, dict]:
    """Mutate and return `data`. Status == '' clears the entry."""
    if status == "":
        data.pop(key, None)
    elif status in VALID_STATUSES:
        data[key] = {"status": status, "person": person_label}
    return data


def apply_confirmations(records: Iterable[Record], data: dict[str, dict]) -> None:
    """Stamp each record.status from the sidecar. In-place."""
    for r in records:
        entry = data.get(record_key(r))
        r.status = entry["status"] if entry else ""


def filter_rejected(records: list[Record], data: dict[str, dict]) -> list[Record]:
    """Drop records the user has explicitly rejected. Preserves order."""
    rejected = {k for k, v in data.items() if v.get("status") == "rejected"}
    if not rejected:
        return records
    return [r for r in records if record_key(r) not in rejected]

"""Shared mutable state for the web app — single source of truth."""
from __future__ import annotations

import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
UPLOAD_CSV = HERE / "ancestors.csv"
CONFIRMS_FILE = HERE / "confirmations.json"

CONFIRMS_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()
PEOPLE_LOCK = threading.Lock()

RUN: dict = {
    "state": "idle",      # idle | running | done | error
    "current": 0,
    "total": 0,
    "label": "",
    "last_site": "",
    "live_records": 0,
    "started": None,
    "finished": None,
    "error": "",
    "new_count": 0,
    "record_count": 0,
    "pause_host": "",
    "pause_seconds": 0.0,
    "pause_until": 0.0,
    "server_now": 0.0,
    "step": "",
    "current_query": "",
    "person_name": "",
    "cancel_requested": False,
    "stopped": False,
}

CSV_COLUMNS = [
    "surname", "forename", "surname_variants", "birth_year_approx",
    "year_range", "county", "townland", "townland_variants",
    "father", "mother", "spouse", "notes", "sex",
]

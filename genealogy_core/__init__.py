"""Pure-logic core of the genealogy search tool. No Playwright dependency.

Split out of the main script so scoring, soundex, nicknames, kin matching,
and diff logic can be unit-tested without launching a browser.
"""
from .config import RUNTIME_FLAGS
from .models import Person, Record, confidence_tier
from .soundex import soundex, expand_surnames
from .nicknames import NICKNAMES, expand_forename
from .scoring import score_record
from .kin import kin_cross_check, annotate_records
from .diff import (
    RECORD_ID_RE,
    load_previous_keys,
    mark_new_records,
)
from .render import (
    render_results_html,
    render_person_page,
    render_index,
    render_person_table,
    summarise_person,
    slugify,
)

__all__ = [
    "RUNTIME_FLAGS",
    "Person",
    "Record",
    "confidence_tier",
    "soundex",
    "expand_surnames",
    "NICKNAMES",
    "expand_forename",
    "score_record",
    "kin_cross_check",
    "annotate_records",
    "RECORD_ID_RE",
    "load_previous_keys",
    "mark_new_records",
    "render_results_html",
    "render_person_page",
    "render_index",
    "render_person_table",
    "summarise_person",
    "slugify",
]

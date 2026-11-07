"""Playwright-backed scrapers for the Irish genealogy sites.

Depends on playwright. Separated from genealogy_core so pure logic
(scoring, nicknames, rendering) can be imported and tested without a
browser dependency.
"""
from .settings import (
    DELAY_MIN_SECONDS,
    DELAY_MAX_SECONDS,
    PAGE_TIMEOUT_MS,
    MAX_RESULTS_PER_SEARCH,
    MAX_CONSECUTIVE_FAILURES,
    CACHE_TTL_DAYS,
    USER_AGENT,
)
from .sites import SITES, CENSUS_YEARS, IG_SEARCH_MATRIX, NAI_LINK_BASE, NAI_READY_JS
from .browser import (
    build_url,
    encode,
    safe_goto,
    safe_text,
    polite_pause,
    looks_like_captcha,
    wait_for_user_after_captcha,
)
from .cache import SearchCache
from .nai import search_nai_census, parse_nai_row
from .ig import search_irishgenealogy_civil
from .griffiths import search_griffiths
from .runner import SEARCH_FUNCTIONS, dedupe_records, run_search, sort_by_score

__all__ = [
    "DELAY_MIN_SECONDS", "DELAY_MAX_SECONDS", "PAGE_TIMEOUT_MS",
    "MAX_RESULTS_PER_SEARCH", "MAX_CONSECUTIVE_FAILURES", "CACHE_TTL_DAYS",
    "USER_AGENT",
    "SITES", "CENSUS_YEARS", "IG_SEARCH_MATRIX", "NAI_LINK_BASE", "NAI_READY_JS",
    "build_url", "encode", "safe_goto", "safe_text", "polite_pause",
    "looks_like_captcha", "wait_for_user_after_captcha",
    "SearchCache",
    "search_nai_census", "parse_nai_row",
    "search_irishgenealogy_civil",
    "search_griffiths",
    "SEARCH_FUNCTIONS", "dedupe_records", "run_search", "sort_by_score",
]

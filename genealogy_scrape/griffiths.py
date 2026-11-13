"""Griffith's Valuation: manual-link only (Cloudflare blocks automation).

build_griffiths_record is pure — no browser, no Playwright. Unit-testable.
"""
import logging

from playwright.sync_api import Page

from genealogy_core.models import Person, Record

from .browser import build_url
from .cache import SearchCache
from .sites import SITES

GRIFFITHS_NOTE = (
    "Cloudflare blocks automated scraping of this site. "
    "Click the link to run this search in your own browser."
)


def build_griffiths_record(person: Person, surname: str, url: str,
                           site_label: str) -> Record:
    """One manual-search Record for a surname variant."""
    return Record(
        person_searched=person.label(),
        site=site_label,
        record_type="Griffith's Valuation (manual search)",
        name_found=f"{person.forename} {surname}".strip(),
        age_or_year="~1847-64",
        place=person.county,
        other_details=GRIFFITHS_NOTE,
        link=url,
    )


def search_griffiths(page: Page, person: Person, logger: logging.Logger,
                     state: dict, cache: SearchCache,
                     use_nicknames: bool) -> list[Record]:
    """askaboutireland.ie sits behind Cloudflare's challenge which blocks
    automated browsers. Bypassing would be rude and brittle, so we don't.
    Emit a manual-search row per surname instead — the user clicks the
    link and the challenge resolves in their normal browser session.
    """
    cfg = SITES["griffiths"]
    records: list[Record] = []
    for surname in person.all_surnames():
        url = build_url(
            cfg["search_url"],
            surname=surname,
            forename=person.forename,
            county=person.county,
        )
        logger.info("[Griffith] manual search link for %s %s -> %s",
                    surname, person.forename, url)
        records.append(build_griffiths_record(person, surname, url, cfg["name"]))
    return records

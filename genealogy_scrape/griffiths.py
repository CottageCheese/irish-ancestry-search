"""Griffith's Valuation: manual-link only (Cloudflare blocks automation)."""
import logging

from playwright.sync_api import Page

from genealogy_core.models import Person, Record

from .browser import build_url
from .cache import SearchCache
from .sites import SITES


def search_griffiths(page: Page, person: Person, logger: logging.Logger,
                     state: dict, cache: SearchCache,
                     use_nicknames: bool, deep: bool) -> list[Record]:
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
        records.append(
            Record(
                person_searched=person.label(),
                site=cfg["name"],
                record_type="Griffith's Valuation (manual search)",
                name_found=f"{person.forename} {surname}".strip(),
                age_or_year="~1847-64",
                place=person.county,
                other_details=(
                    "Cloudflare blocks automated scraping of this site. "
                    "Click the link to run this search in your own browser."
                ),
                link=url,
            )
        )
    return records

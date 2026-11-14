"""Per-site config: URL templates, selectors, captcha hints.

Non-programmers can edit strings here when a site's HTML changes without
touching any search logic.
"""

SITES: dict[str, dict] = {
    "nai_census": {
        "name": "National Archives of Ireland census",
        # Selector for the household table on a census individual detail page.
        # Each <tr> inside this table is one co-resident.
        "household_table_row": "table tr",
        # Verified live 2026-11: SPA that reads URL-fragment params and
        # fetches JSON client-side. 1901 + 1911 share this endpoint.
        "search_url": (
            "https://nationalarchives.ie/collections/search-the-census/search-results/"
            "#census_year={census_year}"
            "&surname__icontains={surname}"
            "&firstname__icontains={forename}"
            "&county={county}"
            "&townland__icontains={townland}"
            "&limit={limit}"
        ),
        # 1926 census lives on its own SPA, same fragment scheme, no
        # census_year filter (page is 1926-only). Verified 2026-11.
        "search_url_1926": (
            "https://nationalarchives.ie/collections/search-the-1926-census/search-results/"
            "#surname__icontains={surname}"
            "&firstname__icontains={forename}"
            "&county={county}"
            "&townland__icontains={townland}"
            "&limit={limit}"
        ),
        "results_row": "table tr",
        "captcha_hint": "captcha",
    },
    "irishgenealogy_civil": {
        "name": "IrishGenealogy.ie",
        # event_flag: event-birth / event-marriage / event-death (civil)
        # or event-baptism / event-marriage / event-burial (church).
        "search_url": (
            "https://www.irishgenealogy.ie/search"
            "?firstname={forename}"
            "&lastname={surname}"
            "&yearStart={year_from}"
            "&yearEnd={year_to}"
            "&{event_flag}=on"
            "&church-or-civil={mode}"
            "&location={county}"
            "&exact-matches-only=off"
            "&per_page=100"
        ),
        "results_container": "#results",
        "results_row": "#results li",
        "record_link": "a[href*='record_id']",
        "captcha_hint": "captcha",
    },
    "griffiths": {
        "name": "Griffith's Valuation",
        "search_url": (
            "https://www.askaboutireland.ie/griffith-valuation/index.xml"
            "?action=doNameSearch"
            "&familyname={surname}"
            "&firstname={forename}"
            "&county={county}"
            "&barony=&parish=&union="
        ),
        "results_table": "table.searchResults, table.results, table",
        "results_row": "table tr",
        "record_link": "a[href*='placeID'], a[href*='detail']",
        "captcha_hint": "captcha",
    },
}

CENSUS_YEARS = ["1901", "1911", "1926"]

IG_SEARCH_MATRIX = [
    ("civil",  "event-birth",    "Civil Birth"),
    ("civil",  "event-marriage", "Civil Marriage"),
    ("civil",  "event-death",    "Civil Death"),
    ("church", "event-baptism",  "Church Baptism"),
    ("church", "event-marriage", "Church Marriage"),
    ("church", "event-burial",   "Church Burial"),
]

NAI_LINK_BASE = "https://nationalarchives.ie"

# JS predicate: true once a table row has real text OR the page explicitly
# says "no results". OR-ing no-results lets empty searches return fast
# instead of timing out after 30s.
NAI_READY_JS = (
    "() => {"
    " const body = (document.body && document.body.innerText || '').toLowerCase();"
    " if (body.includes('no results found')) return true;"
    " const rs = document.querySelectorAll('table tr');"
    " for (const r of rs) {"
    "   if ((r.innerText || '').trim().length > 10) return true;"
    " }"
    " return false;"
    "}"
)

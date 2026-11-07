"""Pure HTML rendering. Every function returns a string; callers handle I/O.

Split out so the renderer can be snapshot-tested without touching disk or
threading `RUNTIME_FLAGS` through fixtures. Dark-mode CSS and the inline
filter/toggle script live here too.
"""
import html
import re

from .models import Record, confidence_tier

_BASE_CSS = (
    "body{font-family:Georgia,serif;max-width:960px;margin:2em auto;padding:0 1em;color:#222}"
    "h1{border-bottom:2px solid #444}"
    "h2{margin-top:2em;background:#f4efe6;padding:.4em .6em;border-left:4px solid #8b6f47}"
    ".summary{margin:.3em 0 1em;color:#444;font-style:italic}"
    ".score{text-align:right;font-variant-numeric:tabular-nums}"
    ".kin{background:#fef6d8}"
    "tr.top td{background:#e8f5e8}"
    ".tier{display:inline-block;padding:1px 6px;border-radius:3px;font-size:.85em;font-weight:bold;color:#fff}"
    ".tier-Likely{background:#2a7d2a}"
    ".tier-Possible{background:#b48a1a}"
    ".tier-Weak{background:#888}"
    ".tier-Manual{background:#bbb;color:#333}"
    ".new{display:inline-block;margin-left:.3em;padding:1px 6px;border-radius:3px;"
    "background:#c33;color:#fff;font-size:.8em;font-weight:bold}"
    "tr.weak td{opacity:.6}"
    "details{margin:1em 0}"
    "details summary{cursor:pointer;color:#555;font-style:italic;padding:.3em 0}"
    "table{border-collapse:collapse;width:100%;margin-bottom:1em;font-size:.95em}"
    "th,td{border:1px solid #ccc;padding:.4em .6em;text-align:left;vertical-align:top}"
    "th{background:#eee}"
    "a{color:#0b5fa4;text-decoration:none}a:hover{text-decoration:underline}"
    ".empty{color:#777;font-style:italic}"
    "ul.toc li{margin:.3em 0}"
)

_DARK_CSS = (
    "body.dark{background:#1a1a1a;color:#ddd}"
    "body.dark h1{border-bottom-color:#888}"
    "body.dark h2{background:#2a241c;border-left-color:#c7a46b;color:#eee}"
    "body.dark .summary{color:#aaa}"
    "body.dark .kin{background:#4a3f10}"
    "body.dark tr.top td{background:#1e3b1e}"
    "body.dark th{background:#2a2a2a;color:#eee}"
    "body.dark th,body.dark td{border-color:#444}"
    "body.dark a{color:#6bb6ff}"
    "body.dark .empty,body.dark details summary{color:#aaa}"
    "body.dark .tier-Manual{background:#555;color:#ddd}"
    "body.dark input.filter{background:#222;color:#eee;border-color:#555}"
    ".controls{margin:.6em 0;display:flex;gap:.6em;align-items:center}"
    "input.filter{padding:.4em .6em;font-size:1em;width:260px;border:1px solid #ccc;border-radius:3px}"
    "button.dark-toggle{padding:.3em .8em;cursor:pointer;border:1px solid #888;background:#fff;border-radius:3px}"
    "body.dark button.dark-toggle{background:#333;color:#eee;border-color:#666}"
    "img.thumb{max-width:60px;max-height:60px;border:1px solid #888}"
)

_HTML_SCRIPT = (
    "<script>"
    "(function(){try{if(localStorage.getItem('dark')==='1')document.body.classList.add('dark');}catch(e){}"
    "var btn=document.getElementById('dm');if(btn)btn.onclick=function(){"
    "document.body.classList.toggle('dark');"
    "try{localStorage.setItem('dark',document.body.classList.contains('dark')?'1':'0');}catch(e){}"
    "};"
    "var inp=document.getElementById('filter');if(inp)inp.oninput=function(){"
    "var q=this.value.toLowerCase();"
    "document.querySelectorAll('table tbody tr').forEach(function(tr){"
    "tr.style.display=tr.innerText.toLowerCase().indexOf(q)>=0?'':'none';});"
    "};})();"
    "</script>"
)


def _esc(s: str) -> str:
    return html.escape(s or "")


def _html_head(title: str) -> list[str]:
    return [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{_esc(title)}</title>",
        "<style>",
        _BASE_CSS,
        _DARK_CSS,
        "</style></head><body>",
    ]


def _html_controls() -> str:
    return ("<div class='controls'>"
            "<input class='filter' id='filter' placeholder='Filter rows...' type='search'>"
            "<button class='dark-toggle' id='dm' type='button'>Toggle dark mode</button>"
            "</div>")


def slugify(name: str) -> str:
    """Filesystem-safe slug used for per-person HTML filenames."""
    s = (name or "unknown").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "unknown"


def summarise_person(recs: list[Record]) -> str:
    """One-line hit-count summary across record types for a single person."""
    counts: dict[str, int] = {}
    manual = 0
    for r in recs:
        if "manual" in r.record_type.lower():
            manual += 1
            continue
        if r.record_type.startswith("Census"):
            counts["census hits"] = counts.get("census hits", 0) + 1
        elif "Civil Birth" in r.record_type:
            counts["civil births"] = counts.get("civil births", 0) + 1
        elif "Civil Marriage" in r.record_type:
            counts["civil marriages"] = counts.get("civil marriages", 0) + 1
        elif "Civil Death" in r.record_type:
            counts["civil deaths"] = counts.get("civil deaths", 0) + 1
        elif "Church" in r.record_type:
            counts["church records"] = counts.get("church records", 0) + 1
        else:
            counts["other"] = counts.get("other", 0) + 1
    parts = [f"{v} {k}" for k, v in counts.items()]
    if manual:
        parts.append(f"{manual} manual link(s)")
    return ", ".join(parts) if parts else "no records"


def render_person_table(recs: list[Record], collapse_weak: bool = True) -> str:
    """Render a single person's record table (strong + optional collapsed weak)."""
    if not recs:
        return "<p class='empty'>No records.</p>"
    max_score = max((r.score for r in recs), default=0)
    strong = [r for r in recs if confidence_tier(r.score) != "Weak"]
    weak = [r for r in recs if confidence_tier(r.score) == "Weak"]

    def rows(rr: list[Record]) -> str:
        if not rr:
            return ""
        buf = [
            "<table><thead><tr>"
            "<th>Tier</th><th>Score</th><th>Site</th><th>Record type</th><th>Name found</th>"
            "<th>Age / year</th><th>Place</th><th>Other details</th>"
            "<th>Kin match</th><th>Scan</th><th>Notes</th><th>Link</th>"
            "</tr></thead><tbody>"
        ]
        for r in rr:
            tier = confidence_tier(r.score)
            classes: list[str] = []
            if r.score and r.score == max_score and max_score > 1:
                classes.append("top")
            if tier == "Weak":
                classes.append("weak")
            row_cls = f" class='{' '.join(classes)}'" if classes else ""
            kin_cls = " class='kin'" if r.kin_hits else ""
            new_badge = "<span class='new'>NEW</span>" if r.is_new else ""
            thumb_cell = (
                f"<a href='{_esc(r.thumbnail)}' target='_blank'>"
                f"<img class='thumb' src='{_esc(r.thumbnail)}' alt=''></a>"
                if r.thumbnail else ""
            )
            buf.append(
                f"<tr{row_cls}>"
                f"<td><span class='tier tier-{tier}'>{tier}</span>{new_badge}</td>"
                f"<td class='score'>{r.score}</td>"
                f"<td>{_esc(r.site)}</td>"
                f"<td>{_esc(r.record_type)}</td>"
                f"<td>{_esc(r.name_found)}</td>"
                f"<td>{_esc(r.age_or_year)}</td>"
                f"<td>{_esc(r.place)}</td>"
                f"<td>{_esc(r.other_details)}</td>"
                f"<td{kin_cls}>{_esc(r.kin_hits)}</td>"
                f"<td>{thumb_cell}</td>"
                f"<td>{_esc(r.user_notes)}</td>"
                f"<td><a href='{_esc(r.link)}' target='_blank'>open</a></td>"
                "</tr>"
            )
        buf.append("</tbody></table>")
        return "".join(buf)

    out = rows(strong)
    if weak:
        if collapse_weak:
            out += (f"<details><summary>Show {len(weak)} weak-tier record(s)</summary>"
                    + rows(weak) + "</details>")
        else:
            out += rows(weak)
    return out


def render_results_html(records: list[Record], stamp: str,
                        collapse_weak: bool = True) -> str:
    """Render the single-page results view: all people inline."""
    grouped: dict[str, list[Record]] = {}
    for r in records:
        grouped.setdefault(r.person_searched, []).append(r)

    parts = _html_head("Irish genealogy search results")
    parts.append(f"<h1>Irish genealogy search results</h1><p>Generated {_esc(stamp)}.</p>")
    parts.append(_html_controls())

    if not grouped:
        parts.append("<p class='empty'>No records found.</p>")

    for person, recs in grouped.items():
        parts.append(f"<h2>{_esc(person)}</h2>")
        parts.append(f"<p class='summary'>{_esc(summarise_person(recs))}.</p>")
        parts.append(render_person_table(recs, collapse_weak=collapse_weak))

    parts.append(_HTML_SCRIPT)
    parts.append("</body></html>")
    return "\n".join(parts)


def render_person_page(name: str, recs: list[Record], stamp: str,
                       back_href: str, collapse_weak: bool = True) -> str:
    """Render one person's page for the per-person output layout."""
    parts = _html_head(f"{name} - genealogy results")
    parts.append(f"<p><a href='{_esc(back_href)}'>&larr; back to index</a></p>")
    parts.append(f"<h1>{_esc(name)}</h1>")
    parts.append(f"<p>Generated {_esc(stamp)}.</p>")
    parts.append(_html_controls())
    parts.append(f"<p class='summary'>{_esc(summarise_person(recs))}.</p>")
    parts.append(render_person_table(recs, collapse_weak=collapse_weak))
    parts.append(_HTML_SCRIPT)
    parts.append("</body></html>")
    return "\n".join(parts)


def render_index(records: list[Record], rel_map: dict[str, str], stamp: str) -> str:
    """Render the TOC index that links out to per-person pages."""
    grouped: dict[str, list[Record]] = {}
    for r in records:
        grouped.setdefault(r.person_searched, []).append(r)
    parts = _html_head("Irish genealogy search results")
    parts.append("<h1>Irish genealogy search results</h1>")
    parts.append(f"<p>Generated {_esc(stamp)}. Click a name for their records.</p>")
    parts.append(_html_controls())
    parts.append("<ul class='toc'>")
    for name, recs in grouped.items():
        tiers = {"Likely": 0, "Possible": 0, "Weak": 0, "Manual": 0}
        for r in recs:
            tiers[confidence_tier(r.score)] += 1
        badges = " ".join(
            f"<span class='tier tier-{k}'>{k}:{v}</span>"
            for k, v in tiers.items() if v
        )
        href = rel_map.get(name, "#")
        parts.append(
            f"<li><a href='{_esc(href)}'>{_esc(name)}</a> &mdash; {len(recs)} record(s) {badges}</li>"
        )
    parts.append("</ul>")
    parts.append(_HTML_SCRIPT)
    parts.append("</body></html>")
    return "\n".join(parts)

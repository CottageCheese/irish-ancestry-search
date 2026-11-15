# Irish Ancestry Search

A friendly desktop app that hunts for your Irish ancestors across three
**free** Irish family-history websites at once. Type a name, pick a county,
and watch it search census records, civil registration, church records, and
Griffith's Valuation — then tap through the matches it finds, keep the ones
that are yours, and build up a little family book over time.

Designed for newcomers to family history: no genealogy jargon, plain-English
explanations on every record, star ratings on how good each match looks, and
a gentle visual interface anyone in the family can use.

## What it does

**Search**

- Hunts three free Irish sites in one go:
  - **National Archives census** — 1901, 1911, and 1926 (fully automated)
  - **IrishGenealogy.ie** — civil *and* church records across six event
    types (Birth / Marriage / Death / Baptism / Marriage / Burial)
  - **Griffith's Valuation** — generates a one-click search link (the site
    sits behind Cloudflare, so automation isn't allowed)
- **Smart name matching** — handles spelling variants automatically.
  William also searches Bill, Will, Liam, Billy. Furey also catches Fury
  and Fewery. Soundex catches wonky spellings the registrar invented on
  the day.
- **Townland matching** — exact, variant, *and* soundex, so the parish
  priest's creative spelling doesn't lose you a match.
- **Household grouping** — when a census match is found, it also fetches
  the other people living at the same address, so Patrick appearing in
  the same household as John strengthens John's match.

**Score & explain**

- Every match gets a star rating and a confidence tier (Likely / Possible
  / Weak). Click **"Why this score?"** on any card to see every bonus and
  penalty broken down in plain English.
- Known-wrong-sex records auto-reject (a male ancestor search won't keep
  Bridgets).
- Records outside the plausible year window auto-reject.
- The feedback loop learns: when you confirm a record, future searches
  rank similar records higher automatically.

**Curate**

- Three buttons on every record: **👍 That's them** (adds to your family),
  **🔖 Keep** (interesting but not yours — goes to Bookmarks), **👎 Not
  them**.
- **Our Family** page shows every confirmed ancestor as a card, grouped by
  Irish historical era (Famine years, Land War, Independence, etc.).
- **Per-person pages** with a map of where they lived, a life timeline,
  and record-type badges.
- **Bookmarks** page for interesting records that aren't your line.

**Export & share**

- Results written to CSV (spreadsheet), HTML (pretty web page), JSON (for
  developers), and GEDCOM (import into Ancestry, MyHeritage, FamilySearch,
  etc.).
- Confirmed-only GEDCOM so you only export the family you've verified.

**Easy on the sites**

- 5–10 second random pause between every search.
- Stops and asks you to solve any CAPTCHA by hand — no bypass attempts.
- Caches every search so re-runs don't hammer the same pages.
- Automatically skips any site that fails to load twice in a row.

> This is a **personal research tool**. Please respect each site's terms of
> use, keep the delays, and don't run it in a tight loop.

---

## Architecture at a glance

```mermaid
flowchart TD
  CLI[genealogy_search.py<br/>CLI + I/O]
  WEB[web.py<br/>Flask UI]
  SCRAPE[genealogy_scrape<br/>Playwright scrapers,<br/>cache, runner]
  CORE[genealogy_core<br/>pure logic:<br/>models, scoring,<br/>soundex, kin, render]

  CLI --> SCRAPE
  CLI --> CORE
  WEB --> SCRAPE
  WEB --> CORE
  SCRAPE --> CORE
```

`genealogy_core` has no browser or I/O. `genealogy_scrape` owns everything
browser-ish. `genealogy_search.py` and `web.py` are thin orchestrators.

**`web.py`** is the browser-based interface. It runs a small Flask server at
`http://127.0.0.1:8000`. From there you can type a name, choose filters, and
watch a live loading screen while the search runs — no terminal needed. Results
appear as cards you can confirm, reject, and export.

---

## What you need

* A computer running **Windows 10/11** or **macOS**.
* **Python 3.10 or newer**.
* About 20 minutes the first time you set it up.

You don't need to know any programming.

---

## Step-by-step setup

### 1. Install Python

**Windows**

1. Go to <https://www.python.org/downloads/windows/>.
2. Download the latest "Windows installer (64-bit)".
3. Run the installer. **Tick the box that says "Add python.exe to PATH"** before
   clicking Install Now.
4. When it finishes, open **PowerShell** (press `Win` key, type `powershell`,
   press Enter).
5. Type `python --version` and press Enter. You should see something like
   `Python 3.12.3`.

**Mac**

1. Go to <https://www.python.org/downloads/macos/>.
2. Download and run the macOS installer.
3. Open the **Terminal** app (press `Cmd+Space`, type `terminal`, press Enter).
4. Type `python3 --version` and press Enter. You should see `Python 3.12.x`.

> On Mac, use `python3` and `pip3` everywhere this README says `python` and `pip`.

### 2. Open a terminal in the project folder

**Windows (PowerShell)**

```powershell
cd irish-ancestry-search
```

**Mac (Terminal)**

```bash
cd irish-ancestry-search
```

(Adjust the path to wherever you cloned or saved the folder.)

### 3. Install dependencies

Copy these lines into your terminal, one at a time, and press Enter after each:

```bash
python -m pip install playwright flask
python -m playwright install chromium
```

The first line installs the Python packages. The second downloads a copy of the
Chromium browser (about 150 MB) that the script will drive.

> **Why `python -m` ?** It runs the tool through Python itself, so it works even
> if the `playwright` or `pip` command isn't on your system PATH. If you see
> `'playwright' is not recognized` or `'pip' is not recognized`, use the
> `python -m` form above. On Mac use `python3 -m ...`.

### 4. Edit `ancestors.csv`

`ancestors.csv` is gitignored (it may contain real family data). Copy the
sample to get started:

```bash
cp ancestors.sample.csv ancestors.csv    # Mac/Linux
copy ancestors.sample.csv ancestors.csv  # Windows
```

Open `ancestors.csv` in Excel, Numbers, or any text editor. You will see a
header row and example rows:

```csv
surname,forename,surname_variants,birth_year_approx,year_range,county,townland,townland_variants,father,mother,spouse,notes,sex
Furey,John,Fury;Fewery,1875,5,Galway,Ballinasloe,,Patrick,Mary,,Possibly born near Ballinasloe,M
Kelly,Mary,Kelley;O'Kelly,1880,7,Mayo,,,,,,Married in Castlebar area,F
```

Columns:

| Column | Meaning | Example |
|---|---|---|
| `surname` | Main spelling | `Furey` |
| `forename` | First name (leave blank if unknown) | `John` |
| `surname_variants` | Other spellings, separated by `;` | `Fury;Fewery` |
| `birth_year_approx` | Roughly when they were born | `1875` |
| `year_range` | How many years either side of that to search | `5` |
| `county` | Irish county | `Galway` |
| `townland` | Townland / parish (optional) | `Ballinasloe` |
| `townland_variants` | Other spellings of townland, separated by `;` | `Ballybeg;Ballybeag` |
| `father` | Father's first name (optional, helps kin matching) | `Patrick` |
| `mother` | Mother's first name (optional) | `Mary` |
| `spouse` | Spouse's first name (optional) | `Anne` |
| `sex` | `M`, `F`, or leave blank | `M` |
| `notes` | Anything for your own reference | `Born near Ballinasloe` |

Replace the two example rows with your own ancestors. Save the file as **CSV
(Comma delimited)** - *not* `.xlsx`.

### 5. Run it

**Option A — web interface (recommended)**

```bash
python web.py
```

Then open **http://127.0.0.1:8000** in your browser. Type a name, choose
filters, and click **Search**. A live loading screen shows each step as it
runs. Results appear as cards you can confirm, reject, and download.

**Option B — command line**

```bash
python genealogy_search.py
```

A Chromium browser window will open and the script will start searching. You
can watch it work. When it finishes:

* **`results.csv`** - a spreadsheet of everything found.
* **`results.html`** - open this in your browser for a tidy summary grouped by
  person, with clickable links to each record.
* **`run.log`** - a diary of every search and any errors.

### Useful command-line options

```bash
python genealogy_search.py --headless                    # no visible browser
python genealogy_search.py --input me.csv                # different input file
python genealogy_search.py --skip nai_census             # skip one site
python genealogy_search.py --skip nai_census,griffiths   # skip several
```

Valid site keys for `--skip`: `nai_census`, `irishgenealogy_civil`, `griffiths`.

The script also stops hitting any site that fails to load twice in a row,
so a down site won't eat your whole run.

---

## What happens if something goes wrong?

### A CAPTCHA appears

The script will **pause** and print a message like:

```
*** A CAPTCHA or block page appeared. ***
Please solve it in the browser window, then press ENTER here to continue.
```

Solve the CAPTCHA in the browser window, come back to the terminal, press
**Enter**, and the script will carry on.

### A site times out repeatedly

If every attempt to a given site logs `ERR_CONNECTION_TIMED_OUT`:

1. Open the site in your normal browser. If **the browser also hangs**,
   the site is down or unreachable from your network - nothing the script
   can fix. Wait and retry later, or try a VPN.
2. If the browser loads the site fine but the script keeps timing out,
   your antivirus or firewall may be blocking the headless Chromium. Add
   an exception, or run with a VPN off.
3. To carry on with the other sites in the meantime, skip the broken one:

   ```bash
   python genealogy_search.py --skip nai_census
   ```

### A site's layout has changed

Websites change. If one site stops returning results, the script logs an error
for *that site* and keeps going with the other two. Check `run.log` for details.

To fix a broken site yourself:

1. Open `genealogy_scrape/sites.py` in any text editor.
2. Find the entry in `SITES` for the broken site (`nai_census`,
   `irishgenealogy_civil`, or `griffiths`).
3. Visit the site manually, do a search, and look at the URL in your browser's
   address bar. Update the `search_url` template so its `{placeholders}` match
   the real parameters.
4. If needed, update the `results_table` and `results_row` CSS selectors by
   right-clicking a result in your browser and choosing **Inspect**.

You never have to touch the search logic in other files.

### Python says "command not found"

You skipped ticking "Add python.exe to PATH" during install. Reinstall Python
and tick that box, or on Mac use `python3` instead of `python`.

### `pip install` fails or `playwright` not recognized

Use the module form, which does not depend on PATH:

```bash
python -m pip install playwright
python -m playwright install chromium
```

### Nothing found at all

* Double-check the county spelling (use the standard Irish county name, e.g.
  `Galway`, `Mayo`, `Dublin`, `Antrim`).
* Widen `year_range` to 10 or 15.
* Add more spelling variants in `surname_variants`.

---

## A note on etiquette

These three sites are free gifts to the genealogy community, run by public
bodies and charities. Please:

* Don't remove the delay between searches.
* Don't try to bypass any CAPTCHA.
* Don't run the script repeatedly for the same people - cache the results you
  already have.
* Click through and read the actual records on the site; the script only finds
  candidate matches.

Happy ancestor-hunting.

"""Scraper runtime knobs. Module globals so CLI can mutate at startup
(e.g. --max-results) and every scraper module picks up the new value.
"""

# Delay between searches (seconds). Random pick inside range for each pause.
DELAY_MIN_SECONDS = 5
DELAY_MAX_SECONDS = 10

# Playwright per-action timeout (ms).
PAGE_TIMEOUT_MS = 30_000

# Max rows scraped per search. CLI --max-results overrides at runtime.
MAX_RESULTS_PER_SEARCH = 25

# Cache entries older than this many days get re-fetched.
CACHE_TTL_DAYS = 30

# After this many consecutive load failures, mark a site dead for the run.
MAX_CONSECUTIVE_FAILURES = 2

# safe_goto retry behaviour for transient failures (timeouts, 5xx like
# Cloudflare 503). Total attempts = 1 + RETRY_ATTEMPTS. Sleep between
# attempts is RETRY_BACKOFF_BASE * 2**(attempt-1) seconds.
RETRY_ATTEMPTS = 2
RETRY_BACKOFF_BASE = 3.0

# Plain desktop Chrome UA.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def set_max_results(n: int) -> None:
    """Allow the CLI to override MAX_RESULTS_PER_SEARCH at startup. Keeps
    the override visible to every scraper that reads the module attr.
    """
    global MAX_RESULTS_PER_SEARCH
    MAX_RESULTS_PER_SEARCH = n

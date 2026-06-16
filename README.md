# PopiSiege

Orospor Labs research toolkit for measuring WordPress Contact Form 7 and
WordPress search endpoint resilience under authorized, controlled load.

PopiSiege was built to study where CDN/WAF policy stops and origin PHP work
begins. It focuses on two high-cost WordPress paths:

- Contact Form 7 REST feedback submissions
- WordPress search requests through `/?s=`

The project includes tooling for proxy pool validation, burst-based request
tests, and a technical white paper documenting the research model.

## Responsible use

Use PopiSiege only on infrastructure you own or have explicit written
authorization to test. Do not run this against third-party sites, public
targets, customer systems, or any service where you do not control the scope.

Recommended lab controls:

- Start with very low concurrency.
- Run during a maintenance window.
- Watch PHP-FPM or LiteSpeed worker usage.
- Watch WAF, CDN, access, and error logs.
- Stop immediately when availability degrades.
- Document the test window and approval.

## Repository contents

| File | Purpose |
| --- | --- |
| `popisiege.py` | Contact Form 7 feedback endpoint pressure test with rotating proxies. |
| `search_flood.py` | WordPress search endpoint pressure test using randomized search terms. |
| `proxy_tester.py` | Fetches public proxy lists, tests them, and writes working proxies. |
| `proxy_pool.py` | Shared proxy source definitions. |
| `install.sh` | Installs global `popisiege` and `search-flood` launchers. |
| `whitepaper.md` | Technical research paper and mitigation notes. |
| `requirements.txt` | Python dependencies. |

## Install

Local install:

```bash
git clone https://github.com/orospor/PopiSiege.git
cd PopiSiege
python3 -m pip install -r requirements.txt
```

Global command install:

```bash
curl -sSL https://raw.githubusercontent.com/orospor/PopiSiege/main/install.sh | sudo bash
```

This creates:

```bash
popisiege
search-flood
```

## Prepare proxies

PopiSiege expects a proxy file for rotating requests. Generate one first:

```bash
python3 proxy_tester.py
```

Useful options:

```bash
python3 proxy_tester.py --output /tmp/working_proxies.txt
python3 proxy_tester.py --workers 300 --timeout 10
```

Then pass that file to the test command:

```bash
python3 popisiege.py --proxy-file /tmp/working_proxies.txt
```

## Contact Form 7 test

`popisiege.py` submits multipart Contact Form 7 feedback requests through a
rotating proxy pool and reports availability, response time, HTTP status, cache
headers, and proxy health.

Run with the built-in lab profile:

```bash
python3 popisiege.py
```

Common options:

```bash
python3 popisiege.py --concurrency 10
python3 popisiege.py --delay 1
python3 popisiege.py --verbose
python3 popisiege.py --proxy-file /tmp/working_proxies.txt
```

To test your own authorized environment, update the `TARGETS` map in
`popisiege.py` with your domain, Contact Form 7 feedback URL, form ID, unit tag,
and safe starting threshold.

## WordPress search test

`search_flood.py` sends randomized WordPress search requests through the proxy
pool. This is useful for checking whether search pages are cached, rate limited,
or passed directly to PHP and the database.

```bash
python3 search_flood.py --target example.com --concurrency 10 --delay 1
python3 search_flood.py --target example.com --verbose
```

Global launcher:

```bash
search-flood --target example.com --concurrency 10 --delay 1
```

## Reading the output

PopiSiege reports:

- `OK`: responses counted as successful for that burst
- `Avail`: percentage of successful responses
- `Avg`: average response time
- `Status`: simple stable, degraded, or down classification
- `Proxies alive`: remaining proxies not marked dead
- `cf-cache-status`: whether Cloudflare served or bypassed cache

For defensive testing, compare the tool output with:

- CDN cache and rate-limit logs
- WAF challenge/block logs
- Web server access and error logs
- PHP-FPM or LiteSpeed worker metrics
- Database CPU and slow query logs

## Defensive findings to validate

Use the toolkit to answer practical questions:

- Does the WAF rate-limit Contact Form 7 feedback POST requests?
- Do POST requests always reach origin PHP?
- How many concurrent PHP workers are available?
- Does search traffic bypass page cache?
- Do bot rules catch browser-like automated clients?
- What response codes appear before users see timeouts?

## Mitigation checklist

- Add CDN/WAF rate limits for Contact Form 7 feedback POST paths.
- Disable unused Contact Form 7 REST endpoints where possible.
- Add server-side per-IP throttling for form submissions.
- Cap upload sizes before PHP work begins.
- Cache or restrict expensive search routes.
- Monitor PHP worker saturation and queue depth.
- Keep WordPress, plugins, WAF rules, and bot controls current.

## White paper

Read the full research write-up:

```bash
whitepaper.md
```

On GitHub:

```text
https://github.com/orospor/PopiSiege/blob/main/whitepaper.md
```

## Orospor Labs

More projects and updates: [Orospor](https://orospor.com).

## License

Use this project responsibly under the license terms published in this
repository.

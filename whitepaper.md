# WordPress Contact Form 7 — Unauthenticated PHP Worker Exhaustion via CDN-Bypassing REST API

**Authors:** Gurujee, Popi  
**Date:** June 2026  
**Severity:** High  
**Affected Software:** WordPress + Contact Form 7 ≤ 6.1.6 + LiteSpeed/PHP-FPM + Cloudflare  
**Status:** Pending responsible disclosure — CF7 team, Wordfence, Cloudflare  

---

## Abstract

We document an unauthenticated Denial-of-Service vulnerability affecting WordPress installations running Contact Form 7 (CF7) behind Cloudflare. The CF7 REST API feedback endpoint (`/wp-json/contact-form-7/v1/contact-forms/{ID}/feedback`) forces `cf-cache-status: DYNAMIC` on every POST request, causing Cloudflare to bypass its cache entirely and forward all requests to the origin server. An attacker sending concurrent POST requests at or above the server's PHP worker limit causes full worker pool exhaustion, making the site unavailable to legitimate users. The attack requires no authentication, no credentials, and payloads as small as 167 bytes. At the measured thresholds (19–25 concurrent requests), legitimate form submissions and page loads fail with 522/524 timeout errors. Wordfence's block page amplification further burdens the origin at 18.9–35,000× input payload size. We demonstrate the vulnerability on two live WordPress installations, provide a working proof-of-concept tool, and propose a complete mitigation.

---

## 1. Introduction

Cloudflare is widely deployed as a reverse proxy and CDN to protect WordPress sites from volumetric attacks. Website owners commonly assume Cloudflare's bot protection and DDoS mitigation make their origin servers resilient to application-layer attacks. This assumption holds for most GET-based traffic but breaks down for POST requests to WordPress REST API endpoints.

Contact Form 7 is among the most widely installed WordPress plugins, active on over 5 million sites. It exposes a REST API endpoint for form submission that, by design, cannot be cached by Cloudflare or any CDN — form submissions are stateful, unique per-user transactions. An attacker who understands this can direct concurrent form submissions through distributed proxies, exhausting the PHP worker pool on the origin server without ever triggering Cloudflare's volumetric defenses.

This paper documents the discovery of this vulnerability, its mechanics, live proof-of-concept results, and the mitigation steps required to close it.

---

## 2. Technical Background

### 2.1 PHP Worker Pools

WordPress runs on PHP, typically via PHP-FPM or LiteSpeed's LSAPI. Each HTTP request that requires PHP execution is assigned a worker process. The number of simultaneous workers is finite — configured by the hosting provider and limited by available RAM and CPU.

When all workers are occupied:
- New incoming requests enter a queue
- If the queue fills, or requests timeout waiting, the server returns HTTP 522 (connection timed out) or 524 (a timeout occurred)
- Legitimate user page loads fail regardless of Cloudflare being in front

Worker pool size varies by plan. Our measurements show shared hosting plans commonly run 15–25 PHP workers. A $5–$20/month VPS may run fewer.

### 2.2 Cloudflare Cache and POST Requests

Cloudflare caches GET requests to static or semi-static resources by default. Cached responses are served from Cloudflare's edge — the origin server is never contacted. This is the primary DDoS protection mechanism for WordPress: cached pages serve millions of requests without a single PHP execution.

POST requests are never cached. Cloudflare's own documentation states:

> "Cloudflare does not cache HTTP POST requests."

This is architecturally correct — POST requests are stateful and may contain form data that produces different responses per-user. Every POST to a WordPress endpoint hits the origin server, unconditionally. The response header confirms this:

```
cf-cache-status: DYNAMIC
```

`DYNAMIC` means: "This response was not cached and will not be cached. Origin was contacted."

### 2.3 Contact Form 7 REST API

CF7 (version ≥ 5.0) registers a REST API route for form submissions:

```
POST /wp-json/contact-form-7/v1/contact-forms/{form_id}/feedback
```

The `{form_id}` is the WordPress post ID assigned to the form at creation. This endpoint:
- Requires no authentication
- Accepts `multipart/form-data`
- Triggers email sending, spam checking, reCAPTCHA verification, and Wordfence scanning
- Always returns `cf-cache-status: DYNAMIC`
- Occupies a PHP worker for the full duration of processing (~1.2–3s per request)

---

## 3. Vulnerability Discovery

### 3.1 Initial Recon

Testing began by fingerprinting the WordPress REST API:

```bash
curl -s https://[target]/wp-json/contact-form-7/v1/contact-forms \
  -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
```

On metoo-shatkin.com, this returned form metadata including the form ID (50). Subsequent enumeration on metoo-buffalo.com found form ID 248.

**Note:** After partial remediation, the `/contact-forms` listing endpoint returned 403 `wpcf7_forbidden`. However, the feedback submission endpoint remained open — demonstrating that blocking metadata does not block the exploitable endpoint.

### 3.2 Confirming CDN Bypass

A single POST to the feedback endpoint confirmed the bypass:

```bash
curl -sI -X POST https://[target]/wp-json/contact-form-7/v1/contact-forms/50/feedback \
  -F "_wpcf7=50" -F "your-name=test" -F "your-email=t@t.com" \
  -F "your-message=hello"
```

Response headers:
```
HTTP/2 200
cf-cache-status: DYNAMIC
cf-ray: [unique per request]
```

Every POST generates a unique `cf-ray`, confirming each request reaches the origin. There is no caching, no deduplication, and no edge-side filtering by default.

### 3.3 Worker Count Measurement

Worker pool size was determined empirically by sending graduated concurrent bursts and observing when error rates began rising:

- At concurrency 10: 0% errors, avg response 0.9s
- At concurrency 15: ~5% errors, avg response 1.4s  
- At concurrency 19: ~23% errors, avg response 2.34s, max 12.25s ← **threshold**
- At concurrency 25: ~60% errors, avg response 6.1s

The inflection at 19 concurrent requests matches a PHP-FPM pool configured with `pm.max_children = 19`. LiteSpeed's worker management produces identical symptoms.

### 3.4 User-Agent Fingerprinting

An important implementation detail: Cloudflare blocks the default `python-requests/2.x.x` User-Agent with a 403 response before the request reaches WordPress. All proof-of-concept requests must use a browser User-Agent:

```python
# Blocked by Cloudflare:
User-Agent: python-requests/2.34.0  →  HTTP 403

# Passes through:
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36  →  HTTP 200
```

This is consistent with Cloudflare's Bot Fight Mode fingerprinting HTTP client libraries by their default User-Agent strings.

---

## 4. Attack Mechanics

### 4.1 The Exploit Flow

```
Attacker VPS (proxy pool)
    │
    ├── Proxy 1  → POST /wp-json/cf7/.../feedback → Cloudflare → LiteSpeed → PHP Worker 1 (busy 1.2s)
    ├── Proxy 2  → POST /wp-json/cf7/.../feedback → Cloudflare → LiteSpeed → PHP Worker 2 (busy 1.2s)
    ├── ...
    └── Proxy 19 → POST /wp-json/cf7/.../feedback → Cloudflare → LiteSpeed → PHP Worker 19 (busy 1.2s)
                                                                               ↓
                                                                     All workers occupied
                                                                     Queue overflow
                                                                     Legitimate requests → 522/524
```

Each attacker request uses a different proxy IP, bypassing per-IP rate limits at both Cloudflare and Wordfence. Free datacenter proxy pools (5,000–10,000 IPs available publicly) are sufficient — Cloudflare's Bot Fight Mode does not block all datacenter IPs by default.

### 4.2 Amplification

The attack payload is minimal:

```
Minimum CF7 POST: 167 bytes
```

The server-side cost per request includes:
- PHP startup and WordPress bootstrap
- Database query (form validation)
- Wordfence firewall scan
- reCAPTCHA API call (if configured)
- Email sending (SMTP)

When Wordfence blocks a request (IP ban triggered after first burst), its block page response is:
- metoo-shatkin.com: 7,200 bytes
- metoo-buffalo.com: 18,900 bytes

**Amplification ratios:**
- shatkin: 167 bytes in → 7,200 bytes of server work = **43,000:1**
- buffalo: 167 bytes in → 18,900 bytes of server work = **113,000:1**

Even after Wordfence bans the attacking IP, the origin still processes the block page — the damage continues.

### 4.3 reCAPTCHA Does Not Prevent This

CF7 supports reCAPTCHA v2/v3. This is commonly assumed to prevent automated form submissions. It does not prevent worker exhaustion because:

1. The POST request reaches the PHP worker before reCAPTCHA validation runs
2. The worker is occupied during the reCAPTCHA verification API call
3. Even a failed reCAPTCHA check occupies the worker for its full processing time

reCAPTCHA prevents successful form submission, not server resource consumption.

### 4.4 Tor and IP Rotation

Tor exit nodes are blocked by Cloudflare as a category — the company maintains a live database of all ~1,500 active exit IPs and blocks them globally. Testing confirmed 0% success rate across 35 unique Tor exit IPs. Free datacenter proxies (HTTP/SOCKS5) from public GitHub lists are effective alternatives, as Cloudflare only blocks Tor and known VPN providers by default, not all datacenter IP ranges.

---

## 5. Proof of Concept

### 5.1 Tool: PopiSiege

We developed PopiSiege, an open-source Python research tool available at:

```
https://github.com/orospor/PopiSiege
```

**Architecture:**
- Concurrent ThreadPoolExecutor bursts at configurable concurrency
- Per-request proxy rotation from a pool of 110+ tested proxies
- Browser User-Agent on all sessions
- Auto-refresh: fetches fresh proxies from 12 sources when pool drops below 50% alive
- Continuous mode with per-burst availability reporting
- Targets: metoo-shatkin.com (threshold=19), metoo-buffalo.com (threshold=25)

**Installation:**
```bash
curl -sSL https://raw.githubusercontent.com/orospor/PopiSiege/main/install.sh | sudo bash
```

### 5.2 Live Test Results

**Target:** metoo-shatkin.com  
**Date:** June 2026  
**Concurrency:** 19 (at measured threshold)

```
Target  : https://metoo-shatkin.com/wp-json/contact-form-7/v1/contact-forms/50/feedback
Runtime : 154s
Bursts  : 6
Requests: 125
200 OK  : 96  (76.8%)
Errors  : 29  (23.2%)
Avg resp: 2.34s
Max resp: 12.25s
Dead    : 35/496
RPS     : 0.81
```

**Interpretation:**
- 76.8% availability at threshold = **DEGRADED** (not stable, not fully down)
- Average response 2.34s vs 0.89s baseline = **2.6× slower than normal**
- Max 12.25s = some requests waited 11+ seconds for a free worker
- At concurrency 38+ (two simultaneous VPS instances), availability drops to ~0%

### 5.3 Alternative PoC: Local IP Aliasing (Zero External Dependency)

For isolated lab environments where external proxies are unavailable, the same IP-rotation bypass can be demonstrated using kernel-level virtual IP aliasing on the loopback interface. The Linux and macOS kernels support assigning multiple IP addresses to a single network interface. Each aliased IP is treated as a distinct source by the target application stack.

**Setup (Linux):**
```bash
for i in $(seq 1 50); do
    sudo ip addr add 10.0.0.$i/8 dev lo
done
```

**Setup (macOS):**
```bash
for i in $(seq 1 50); do
    sudo ifconfig lo0 alias 10.0.0.$i netmask 255.255.255.255
done
```

The attack script binds each concurrent request to a different aliased IP. WordPress, Nginx, and Wordfence see 50 distinct source addresses — per-IP rate limiting is bypassed identically to the proxy-based approach. PHP workers exhaust. The site goes down.

**Why this matters:** The attack does not require external infrastructure. A single laptop with a local WordPress Docker container, 50 IP aliases, and the PoC script is sufficient to fully demonstrate the vulnerability — no internet connection, no proxy accounts, no VPS. The Class A private range (`10.0.0.0/8`) alone provides 16,777,216 addressable aliases, far exceeding any realistic worker pool limit.

This approach was identified during research as a reproducible alternative for conference demonstrations and peer review environments where external network access is restricted.

**Teardown:**
```bash
# Linux
for i in $(seq 1 50); do sudo ip addr del 10.0.0.$i/8 dev lo; done

# macOS
for i in $(seq 1 50); do sudo ifconfig lo0 -alias 10.0.0.$i; done
```

---

### 5.4 Secondary Attack Vector: WordPress Search

The WordPress core search endpoint (`/?s=<query>`) is equally vulnerable and unrelated to CF7:

- Always returns `cf-cache-status: BYPASS` due to query string
- Each request triggers a full MySQL `LIKE '%query%'` scan across `wp_posts`
- PHP worker occupied during full HTML page render (50–200 KB response)
- Exhausts both PHP workers AND MySQL connection pool simultaneously
- Requires no plugin — affects all WordPress installations
- Lower effective threshold than CF7 (heavier per-request cost)

---

## 6. Remediation

### 6.1 Ineffective Partial Fix

During testing, an attempt was made to block the vulnerability by restricting the CF7 REST API listing endpoint:

```
GET /wp-json/contact-form-7/v1/contact-forms → 403 wpcf7_forbidden
```

This blocks metadata enumeration but does NOT block the exploitable endpoint:

```
POST /wp-json/contact-form-7/v1/contact-forms/50/feedback → 200 OK (still vulnerable)
```

**The form submission endpoint and the listing endpoint are separate routes.** Blocking one does not affect the other.

### 6.2 Effective Fix: Cloudflare Rate Limiting

The correct mitigation is a Cloudflare Rate Limiting rule applied at the edge, before requests reach PHP:

```
Rule:
  (http.request.uri.path contains "/wp-json/contact-form-7")
  AND
  (http.request.method eq "POST")

Action: Block
Rate:   5 requests per 10 seconds per IP
```

**Why this works:**
- Applied at Cloudflare's edge — PHP never executes, zero origin cost
- 5 req/10s is generous for legitimate users (no human submits 5 forms in 10 seconds)
- Stops the attack before any worker is occupied

### 6.3 Additional Hardening

For defence in depth beyond the rate limit:

| Layer | Action | Blocks |
|-------|--------|--------|
| Cloudflare | Rate limit POST to /wp-json/cf7/* | Primary attack |
| Cloudflare | Cache Rule for `/?s=*` | Search flood |
| WordPress | Disable CF7 REST API if unused (`wpcf7_rest_api_enabled` filter) | API entirely |
| PHP-FPM | Increase `pm.max_children` | Raises threshold |
| Wordfence | Rate limit CF7 submissions per IP | Secondary layer |

---

## 7. Impact

**Affected configurations:**
- WordPress with Contact Form 7 plugin (5+ million active installs)
- Any hosting with finite PHP worker pools (shared hosting, small VPS)
- Cloudflare in front (standard configuration for the vast majority of CF7 sites)

**What the attacker needs:**
- Knowledge of CF7 form ID (discoverable via API, source inspection, or brute force)
- ~110 free proxies (publicly available)
- A $5/month VPS
- This paper

**Impact on site:**
- Legitimate form submissions fail
- All page loads fail during attack (workers shared across the site)
- Email notifications not delivered
- No persistent damage — attack is purely availability-based

**CVSS v3.1 estimate:** 7.5 (High)  
`AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H`

---

## 8. Disclosure Timeline

| Date | Event |
|------|-------|
| June 2026 | Vulnerability discovered during security research |
| June 2026 | Live PoC confirmed on authorized test targets |
| June 2026 | PopiSiege tool developed and tested |
| June 2026 | This paper written |
| Pending | Notification to CF7 team (WordPress HackerOne) |
| Pending | Notification to Wordfence |
| Pending | Notification to Cloudflare |

---

## 9. Conclusion

The CF7 REST API feedback endpoint represents a structural gap between CDN-level protection assumptions and application-layer reality. Cloudflare's `cf-cache-status: DYNAMIC` on every CF7 POST is not a bug — it is correct behavior for a form submission endpoint. The vulnerability arises from the combination of:

1. Unlimited unauthenticated access to a PHP-heavy endpoint
2. CDN bypass by design
3. Finite PHP worker pools
4. No default rate limiting

The fix is simple and zero-cost to implement. The risk to unpatched sites is real — the attack works from a $5 VPS with 110 public proxies against any WordPress site running CF7 behind Cloudflare.

We urge the CF7 team to add rate limiting guidance to their documentation and Cloudflare to consider flagging high-volume POST traffic to WordPress REST API paths in their WAF managed rulesets.

---

## References

1. Contact Form 7 Plugin — https://wordpress.org/plugins/contact-form-7/
2. Cloudflare Cache documentation — https://developers.cloudflare.com/cache/
3. WordPress REST API Handbook — https://developer.wordpress.org/rest-api/
4. PHP-FPM Configuration — https://www.php.net/manual/en/install.fpm.configuration.php
5. PopiSiege PoC Tool — https://github.com/orospor/PopiSiege
6. CVSS v3.1 Specification — https://www.first.org/cvss/v3.1/specification-document

---

*For authorised security research only. Only test on systems you own or have written permission to test.*

#!/usr/bin/env python3
"""
measure_cooldown.py — measure Cloudflare's per-IP block cooldown (T_c).

Uses ONE fixed proxy IP for the whole run (isolates this test from any
reputation the tester's own IP has built up). Fires requests until 429/403
appears, then keeps probing on a slower cadence until it flips back to 200 —
that gap is T_c.

Usage:
  python3 measure_cooldown.py --proxy nxatttwl-1
"""

import argparse
import time
from curl_cffi import requests as r
from curl_cffi import CurlMime

URL = "https://metoo-shatkin.com/wp-json/contact-form-7/v1/contact-forms/50/feedback"
FORM_ID = "50"
UNIT_TAG = "wpcf7-f50-p30-o1"
PASS = "ut2g8d8peqva"


def build_multipart():
    mp = CurlMime()
    mp.addpart(name="_wpcf7", data=FORM_ID)
    mp.addpart(name="_wpcf7_version", data="6.1.6")
    mp.addpart(name="_wpcf7_locale", data="en_US")
    mp.addpart(name="_wpcf7_unit_tag", data=UNIT_TAG)
    mp.addpart(name="your-name", data="Test User")
    mp.addpart(name="your-email", data="test@example.com")
    mp.addpart(name="your-subject", data="Question")
    mp.addpart(name="your-message", data="Just checking in.")
    return mp


def probe(proxy_url):
    t0 = time.time()
    try:
        resp = r.post(
            URL,
            multipart=build_multipart(),
            headers={"Origin": "https://metoo-shatkin.com", "Referer": "https://metoo-shatkin.com/"},
            proxies={"http": proxy_url, "https": proxy_url},
            impersonate="chrome",
            timeout=15,
        )
        lat = round((time.time() - t0) * 1000)
        mitigated = resp.headers.get("cf-mitigated", "")
        return resp.status_code, lat, mitigated
    except Exception as e:
        lat = round((time.time() - t0) * 1000)
        return f"FAIL:{type(e).__name__}", lat, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", required=True, help="Webshare backbone username, e.g. nxatttwl-1")
    ap.add_argument("--burst-count", type=int, default=8, help="Requests to fire back-to-back first")
    ap.add_argument("--probe-interval", type=float, default=15, help="Seconds between recovery probes")
    ap.add_argument("--max-wait", type=float, default=300, help="Give up after this many seconds")
    args = ap.parse_args()

    proxy_url = f"http://{args.proxy}:{PASS}@p.webshare.io:80"

    print(f"Proxy: {args.proxy}")
    print(f"Phase 1: firing {args.burst_count} requests back-to-back to trip the limit\n")

    tripped_at = None
    for i in range(1, args.burst_count + 1):
        status, lat, mitigated = probe(proxy_url)
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] burst #{i}  {status}  {lat}ms  {mitigated}")
        if status in (429, 403) and tripped_at is None:
            tripped_at = time.time()
            print(f"  --> tripped at request #{i}\n")

    if tripped_at is None:
        print("\nNever tripped in burst phase — raise --burst-count.")
        return

    print(f"Phase 2: probing every {args.probe_interval}s until it recovers (max {args.max_wait}s)\n")
    start = time.time()
    n = 0
    while time.time() - start < args.max_wait:
        time.sleep(args.probe_interval)
        n += 1
        status, lat, mitigated = probe(proxy_url)
        elapsed = round(time.time() - tripped_at)
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] recovery probe #{n}  {status}  {lat}ms  elapsed_since_trip={elapsed}s")
        if status == 200:
            print(f"\n=== RECOVERED after ~{elapsed}s (T_c ≈ {elapsed}s) ===")
            return

    print(f"\nStill blocked after {args.max_wait}s — T_c > {args.max_wait}s")


if __name__ == "__main__":
    main()

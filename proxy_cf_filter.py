#!/usr/bin/env python3
"""
proxy_cf_filter.py — check which proxies bypass Cloudflare for metoo-shatkin.com REST API.

Uses full browser fingerprint headers to avoid CF bot detection.
Outputs clean list of CF-passing proxies to proxies_cf_clean.txt.

Run this on the Mac (where Webshare IP auth is active).
Then scp the output to the VPS.
"""

import requests
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

TARGET      = "https://metoo-shatkin.com/wp-json/wp/v2/posts?per_page=1"
TIMEOUT     = 12
WORKERS     = 20
OUTPUT_FILE = "/Users/gurujee/PopiSiege/proxies_cf_clean.txt"

# Full Chrome 124 browser fingerprint — same headers the browser sends for a fetch() call
HEADERS = {
    "User-Agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":           "application/json, text/plain, */*",
    "Accept-Language":  "en-US,en;q=0.9",
    "Accept-Encoding":  "gzip, deflate, br",
    "sec-ch-ua":        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest":   "empty",
    "sec-fetch-mode":   "cors",
    "sec-fetch-site":   "same-origin",
    "Referer":          "https://metoo-shatkin.com/",
    "Connection":       "keep-alive",
}

PROXIES_RAW = [
    "23.95.244.70:6023","31.58.16.134:6101","198.37.118.246:5705","173.239.237.50:5696",
    "142.147.244.66:6310","82.22.211.220:6028","198.37.98.200:5730","23.95.250.25:6298",
    "23.26.95.159:5641","161.123.154.151:6681","166.88.64.127:6510","91.198.95.207:8508",
    "104.233.15.120:5843","142.111.227.60:6255","154.6.87.40:6510","82.24.212.236:5542",
    "152.232.120.163:5925","154.6.129.108:5578","216.74.114.95:6378","91.198.95.41:8342",
    "154.29.232.187:6847","172.120.101.201:6380","173.211.69.158:6751","31.57.78.108:5492",
    "172.245.158.65:6018","45.115.194.139:5835","173.211.30.14:6448","154.6.114.229:7198",
    "23.236.182.212:5988","146.103.5.41:6621","198.46.246.197:6821","31.58.16.27:5994",
    "198.37.98.253:5783","136.0.120.129:6147","31.58.16.135:6102","107.175.208.148:6089",
    "154.6.116.193:6162","192.177.103.55:6548","142.147.132.138:6333","142.147.244.94:6338",
    "38.154.182.231:7499","45.41.171.134:6170","198.144.190.56:5903","104.239.80.81:5659",
    "198.46.137.76:6280","67.227.112.240:6280","38.154.233.193:5603","191.101.188.137:6891",
    "104.253.208.156:6567","166.88.195.35:5667","198.46.241.62:6597","31.59.18.131:6712",
    "154.29.233.8:5769","23.236.255.242:7018","136.0.108.12:5688","166.88.3.89:6560",
    "199.180.11.22:6759","209.127.183.17:5391","104.232.209.159:6117","172.121.139.37:5216",
    "154.6.8.43:5510","172.120.112.73:5752","136.0.108.155:5831","152.232.100.129:9223",
    "152.232.122.219:6204","198.46.246.75:6699","142.111.124.137:6157","23.27.93.237:5816",
    "23.26.71.187:5670","45.38.69.95:6020","154.6.11.191:5660","23.27.91.210:6289",
    "142.111.58.118:6696","31.58.151.197:8193","89.32.200.195:6651","198.46.161.153:5203",
    "67.227.1.179:6460","173.239.237.102:5748","107.174.4.35:6004","82.108.65.126:6265",
    "154.30.1.111:5427","192.177.87.113:5959","107.174.194.218:5660","107.172.221.232:6187",
    "198.89.123.110:6652","38.154.183.22:7790","154.29.232.146:6806","173.211.69.144:6737",
    "198.20.191.44:5114","191.101.25.169:6566","107.173.137.190:6444","67.227.37.219:5761",
    "185.202.175.225:8425","45.81.149.171:8671","172.120.119.208:5868","166.88.64.129:6512",
    "136.0.109.93:6379","161.123.93.236:5966",
]

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; W = "\033[0m";   B = "\033[1m"

results = {"PASS": [], "CF_BLOCKED": [], "CHALLENGE": [], "DEAD": []}
lock = threading.Lock()


def classify(proxy_str):
    proxy_url = f"http://{proxy_str}"
    proxies   = {"http": proxy_url, "https": proxy_url}
    try:
        r = requests.get(TARGET, headers=HEADERS, proxies=proxies,
                         timeout=TIMEOUT, allow_redirects=True)
        ct = r.headers.get("content-type", "")

        if r.status_code in (200, 401, 404):
            # Reached WordPress — Cloudflare let it through
            return "PASS", r.status_code

        if r.status_code == 403:
            if "application/json" in ct:
                # CF returned a JSON error (rate-limit or specific rule) — still bypassed JS challenge
                return "PASS", 403
            body = r.text[:400]
            if "just a moment" in body.lower() or "cf-ray" in r.headers:
                return "CHALLENGE", 403  # CF JS/Managed Challenge page
            return "CF_BLOCKED", 403

        if r.status_code in (407, 502, 503):
            return "DEAD", r.status_code

        return "CF_BLOCKED", r.status_code

    except requests.exceptions.ProxyError:
        return "DEAD", "PROXY_ERR"
    except requests.exceptions.ConnectTimeout:
        return "DEAD", "TIMEOUT"
    except Exception as e:
        return "DEAD", str(e)[:30]


def check(idx, proxy_str):
    t0 = time.time()
    verdict, code = classify(proxy_str)
    elapsed = time.time() - t0
    with lock:
        results[verdict].append(proxy_str)
    return idx, proxy_str, verdict, code, elapsed


# ── Main ──────────────────────────────────────────────────────────────────────
print(f"\n{B}{'─'*64}{W}")
print(f"  Cloudflare Bypass Filter  —  {len(PROXIES_RAW)} Webshare proxies")
print(f"  Target : {TARGET}")
print(f"  Workers: {WORKERS}  |  Timeout: {TIMEOUT}s")
print(f"{B}{'─'*64}{W}\n")
print(f"  {G}PASS{W}       = 200/401/404 — proxy bypassed Cloudflare")
print(f"  {Y}CHALLENGE{W}  = 403 HTML   — CF JS/Managed Challenge served")
print(f"  {Y}CF_BLOCKED{W} = 403        — CF blocked this proxy IP")
print(f"  {R}DEAD{W}       = timeout / proxy error\n")

t_start = time.time()

with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = [ex.submit(check, i+1, p) for i, p in enumerate(PROXIES_RAW)]
    done = 0
    for f in as_completed(futs):
        done += 1
        idx, proxy, verdict, code, elapsed = f.result()
        if verdict == "PASS":
            color = G
        elif verdict in ("CHALLENGE", "CF_BLOCKED"):
            color = Y
        else:
            color = R
        print(f"  [{done:>3}/{len(PROXIES_RAW)}] {color}{verdict:<12}{W}  "
              f"{proxy:<26}  HTTP={code}  {elapsed:.1f}s")

elapsed_total = time.time() - t_start

# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\n{B}{'─'*64}{W}")
print(f"  Results — {elapsed_total:.0f}s")
print(f"  {G}PASS      : {len(results['PASS'])}{W}")
print(f"  {Y}CHALLENGE : {len(results['CHALLENGE'])}{W}")
print(f"  {Y}CF_BLOCKED: {len(results['CF_BLOCKED'])}{W}")
print(f"  {R}DEAD      : {len(results['DEAD'])}{W}")
print(f"{B}{'─'*64}{W}\n")

if results["PASS"]:
    with open(OUTPUT_FILE, "w") as f:
        for p in results["PASS"]:
            f.write(p + "\n")
    print(f"  {G}Saved {len(results['PASS'])} CF-passing proxies → {OUTPUT_FILE}{W}")
    print(f"\n  Next: scp {OUTPUT_FILE} root@104.236.68.226:/root/proxies_clean.txt")
    print(f"        then run vps_burst.py on the VPS\n")
else:
    print(f"  {R}No proxies passed CF check. VPN off? Webshare IP whitelisted?{W}\n")

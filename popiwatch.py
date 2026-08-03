#!/usr/bin/env python3
"""
popiwatch.py — Origin-level attack fingerprinting and blocking.

Runs ON THE ORIGIN SERVER, independent of Cloudflare. Tails the web server's
access log in real time, fingerprints requests using signals available at the
origin (request rate, target path, User-Agent, source CIDR), and blocks
offending IPs directly via iptables — so protection holds even if Cloudflare
is bypassed, misconfigured, or turned off entirely.

IMPORTANT — read before running:
  The origin log's client IP field must be the REAL visitor IP, not
  Cloudflare's edge IP. If your site sits behind Cloudflare, your web server
  must be configured to log $http_cf_connecting_ip (nginx) or
  %{CF-Connecting-IP}i (Apache/LiteSpeed) instead of the raw connecting IP —
  otherwise every request looks like it comes from Cloudflare itself, and
  blocking would either do nothing or block Cloudflare's shared edge IPs
  outright. Verify this BEFORE enabling --live.

Usage:
  python3 popiwatch.py --log /var/log/litespeed/access.log            # dry-run, log only
  python3 popiwatch.py --log /var/log/litespeed/access.log --live     # actually block via iptables (needs root)
  python3 popiwatch.py --log /var/log/litespeed/access.log --live --block-minutes 120
  python3 popiwatch.py --replay /path/to/old.log                      # analyze a static log instead of tailing

Requires root for --live (iptables). Without --live, every action is logged
to alerts.log and printed, but nothing is actually blocked — safe to run
continuously to see what WOULD happen before trusting it to act.
"""

import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; W = "\033[0m";    B = "\033[1m"

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
STATE_FILE   = os.path.join(SCRIPT_DIR, "popiwatch_blocked.json")
ALERTS_FILE  = os.path.join(SCRIPT_DIR, "popiwatch_alerts.log")
ALLOWLIST    = os.path.join(SCRIPT_DIR, "popiwatch_allowlist.txt")
CIDR_BLOCKLIST = os.path.join(SCRIPT_DIR, "popiwatch_known_bad_cidrs.txt")

# ── sensitive paths worth extra scrutiny (worker-exhaustion / recon surface) ──
SENSITIVE_PATHS = [
    "/wp-json/contact-form-7/",
    "/wp-login.php",
    "/xmlrpc.php",
    "/wp-admin/admin-ajax.php",
    "/wp-json/wp/v2/",
]

# ── User-Agent substrings that flag naive/library traffic (sophisticated ────
# attackers spoof this, so absence of a hit here proves nothing — but a hit
# is a near-certain bot) ───────────────────────────────────────────────────
LIBRARY_UA_SIGNATURES = [
    "python-requests", "python-urllib", "curl/", "Go-http-client",
    "Wget/", "Scrapy", "libwww-perl", "Java/", "okhttp", "axios/",
    "node-fetch", "PostmanRuntime",
]

# Standard combined/extended log format:
#   IP - - [timestamp] "METHOD path HTTP/x" status size "referer" "user-agent"
LOG_LINE_RE = re.compile(
    r'(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<path>\S+) \S+" '
    r'(?P<status>\d+) (?P<size>\S+) '
    r'"(?P<referer>[^"]*)" "(?P<ua>[^"]*)"'
)


def load_lines(path):
    """List of CIDR/IP strings from a file, ignoring blanks and # comments."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                out.append(line)
    return out


def ip_in_any_cidr(ip_str, cidrs):
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for c in cidrs:
        try:
            if ip in ipaddress.ip_network(c, strict=False):
                return True
        except ValueError:
            continue
    return False


class BlockState:
    """Persists blocked IPs + expiry so restarts don't forget, and so blocks
    can be swept/expired instead of accumulating forever."""

    def __init__(self, path):
        self.path = path
        self.blocked = {}  # ip -> {"reason": str, "until": iso str}
        if os.path.exists(path):
            try:
                with open(path) as f:
                    self.blocked = json.load(f)
            except Exception:
                self.blocked = {}

    def save(self):
        with open(self.path, "w") as f:
            json.dump(self.blocked, f, indent=2)

    def is_blocked(self, ip):
        return ip in self.blocked

    def add(self, ip, reason, minutes):
        until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
        self.blocked[ip] = {"reason": reason, "until": until}
        self.save()

    def sweep_expired(self, live, verbose):
        now = datetime.now(timezone.utc)
        expired = [ip for ip, info in self.blocked.items()
                   if datetime.fromisoformat(info["until"]) < now]
        for ip in expired:
            if live:
                unblock_ip(ip)
            del self.blocked[ip]
            if verbose:
                print(f"  {Y}[EXPIRE]{W} {ip} unblocked (block window elapsed)")
        if expired:
            self.save()


def block_ip(ip):
    """Blocks at the OS firewall level — independent of Cloudflare entirely."""
    try:
        subprocess.run(
            ["iptables", "-C", "INPUT", "-s", ip, "-j", "DROP"],
            check=True, capture_output=True,
        )
        return True  # rule already exists
    except subprocess.CalledProcessError:
        pass
    try:
        subprocess.run(
            ["iptables", "-I", "INPUT", "-s", ip, "-j", "DROP"],
            check=True, capture_output=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"  {R}[ERROR]{W} iptables block failed for {ip}: {e.stderr.decode()[:100]}")
        return False


def unblock_ip(ip):
    try:
        subprocess.run(
            ["iptables", "-D", "INPUT", "-s", ip, "-j", "DROP"],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError:
        pass  # already gone


def log_alert(msg):
    with open(ALERTS_FILE, "a") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()}  {msg}\n")


class Fingerprinter:
    """Sliding-window per-IP request tracking + rule evaluation."""

    def __init__(self, window_seconds, rate_threshold, allowlist, bad_cidrs):
        self.window = window_seconds
        self.threshold = rate_threshold
        self.allowlist = allowlist
        self.bad_cidrs = bad_cidrs
        self.hits = defaultdict(deque)          # ip -> deque[timestamps] (sensitive paths)
        self.ua_by_ip_window = defaultdict(deque)  # ua -> deque[(ts, ip)] for shared-tool detection
        self.ua_ip_sets = defaultdict(set)

    def is_sensitive(self, path):
        return any(path.startswith(p) for p in SENSITIVE_PATHS)

    def evaluate(self, ip, ts, path, ua):
        """Returns (should_block: bool, reason: str|None)."""
        if ip_in_any_cidr(ip, self.allowlist):
            return False, None

        # Rule 1: known-bad CIDR (e.g. confirmed proxy-provider ranges)
        if ip_in_any_cidr(ip, self.bad_cidrs):
            return True, "known-bad CIDR match"

        # Rule 2: naive library UA hitting a sensitive path
        if self.is_sensitive(path) and any(sig in ua for sig in LIBRARY_UA_SIGNATURES):
            return True, f"library UA on sensitive path ({ua[:40]})"

        # Rule 3: empty/missing UA on a sensitive path — no real browser does this
        if self.is_sensitive(path) and not ua.strip():
            return True, "empty User-Agent on sensitive path"

        # Rule 4: per-IP burst rate on sensitive paths
        if self.is_sensitive(path):
            dq = self.hits[ip]
            dq.append(ts)
            while dq and (ts - dq[0]) > self.window:
                dq.popleft()
            if len(dq) >= self.threshold:
                return True, f"{len(dq)} requests to sensitive paths in {self.window}s"

        # Rule 5: same UA string hit by many distinct IPs in the window — the
        # signature of a shared tool (proxy pool) rotating IPs but reusing a
        # fixed UA, e.g. an unmodified stresser script or older popisiege runs.
        if self.is_sensitive(path) and ua.strip():
            dq = self.ua_by_ip_window[ua]
            dq.append((ts, ip))
            while dq and (ts - dq[0][0]) > self.window:
                _, old_ip = dq.popleft()
            distinct_ips = {i for _, i in dq}
            if len(distinct_ips) >= 8:
                return True, f"{len(distinct_ips)} distinct IPs shared UA in {self.window}s (rotating-proxy tool signature)"

        return False, None


def parse_line(line):
    m = LOG_LINE_RE.match(line)
    if not m:
        return None
    try:
        ts = time.mktime(time.strptime(m.group("ts").split()[0], "%d/%b/%Y:%H:%M:%S"))
    except ValueError:
        ts = time.time()
    return {
        "ip": m.group("ip"), "ts": ts, "method": m.group("method"),
        "path": m.group("path"), "status": m.group("status"),
        "referer": m.group("referer"), "ua": m.group("ua"),
    }


def follow(path):
    """Generator that yields new lines appended to a growing log file."""
    with open(path, "r") as f:
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            yield line


def run(args):
    allowlist = load_lines(ALLOWLIST)
    bad_cidrs = load_lines(CIDR_BLOCKLIST)
    state     = BlockState(STATE_FILE)
    fp        = Fingerprinter(args.window, args.threshold, allowlist, bad_cidrs)

    mode = f"{R}{B}LIVE — will block via iptables{W}" if args.live else f"{Y}DRY-RUN — logging only{W}"
    print(f"""
{B}{'='*66}{W}
  popiwatch — origin-level fingerprinting & blocking
{B}{'='*66}{W}
  Log source  : {args.log or args.replay}
  Mode        : {mode}
  Window      : {args.window}s
  Threshold   : {args.threshold} sensitive-path hits
  Allowlist   : {len(allowlist)} entries
  Known-bad   : {len(bad_cidrs)} CIDR entries
  Sensitive   : {', '.join(SENSITIVE_PATHS)}
{B}{'='*66}{W}
""")

    if args.live and os.geteuid() != 0:
        print(f"  {R}[ERROR]{W} --live requires root (iptables). Re-run with sudo.\n")
        sys.exit(1)

    source = follow(args.log) if args.log else open(args.replay)

    dry_run_flagged = set()  # mirrors state.blocked for --live so dry-run
                              # previews one alert per IP, not one per request
    last_sweep = time.time()
    try:
        for raw_line in source:
            rec = parse_line(raw_line)
            if rec is None:
                continue

            if time.time() - last_sweep > 30:
                state.sweep_expired(args.live, args.verbose)
                last_sweep = time.time()

            if state.is_blocked(rec["ip"]) or rec["ip"] in dry_run_flagged:
                continue  # already handled

            should_block, reason = fp.evaluate(rec["ip"], rec["ts"], rec["path"], rec["ua"])
            if should_block:
                msg = f"{rec['ip']} -> {reason} (path={rec['path']})"
                log_alert(msg)
                print(f"  {R}[FLAG]{W} {msg}")
                if args.live:
                    if block_ip(rec["ip"]):
                        state.add(rec["ip"], reason, args.block_minutes)
                        print(f"  {R}[BLOCKED]{W} {rec['ip']} for {args.block_minutes}min")
                else:
                    dry_run_flagged.add(rec["ip"])
                    print(f"  {Y}[DRY-RUN]{W} would block {rec['ip']} for {args.block_minutes}min")
            elif args.verbose:
                print(f"  . {rec['ip']} {rec['path']}")

    except KeyboardInterrupt:
        print(f"\n\n  {Y}[STOPPED]{W} Ctrl+C. Currently blocked: {len(state.blocked)}\n")
    finally:
        if hasattr(source, "close"):
            source.close()


def main():
    p = argparse.ArgumentParser(
        description="popiwatch — origin-level attack fingerprinting and blocking (Cloudflare-independent)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--log", help="Access log path to tail live (real-time monitoring)")
    src.add_argument("--replay", help="Static log file to analyze once instead of tailing")
    p.add_argument("--live", action="store_true",
                    help="Actually block via iptables (requires root). Default is dry-run/log-only.")
    p.add_argument("--window", type=int, default=10,
                    help="Sliding window in seconds for rate detection (default: 10)")
    p.add_argument("--threshold", type=int, default=15,
                    help="Sensitive-path hits within --window that trigger a block (default: 15)")
    p.add_argument("--block-minutes", type=int, default=60,
                    help="Minutes an IP stays blocked before auto-unblock (default: 60)")
    p.add_argument("--verbose", action="store_true", help="Print every parsed request, not just flags")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()

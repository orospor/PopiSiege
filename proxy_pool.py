#!/usr/bin/env python3
"""
Shared ProxyPool — used by popisiege.py and search_flood.py
  - 12 GitHub proxy sources (free)
  - Webshare backbone rotating proxy (if proxies_webshare_backbone_creds.txt present)
  - Auto-refresh in background when alive drops below 50% (free mode only)
  - Thread-safe next() / mark_dead()

Webshare creds file (gitignored): proxies_webshare_backbone_creds.txt
  Line 1: username  (e.g. nxatttwl)
  Line 2: password
"""

import requests, itertools, threading, concurrent.futures, time, os

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"; W = "\033[0m"

# ── 12 free proxy sources ──────────────────────────────────────────────────
SOURCES = [
    ("TheSpeedX HTTP",    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",         "http"),
    ("TheSpeedX SOCKS4",  "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",       "socks4"),
    ("TheSpeedX SOCKS5",  "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",       "socks5"),
    ("Proxifly All",      "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt", None),
    ("monosans HTTP",     "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",    "http"),
    ("monosans SOCKS5",   "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",  "socks5"),
    ("ShiftyTR HTTP",     "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",          "http"),
    ("ShiftyTR SOCKS5",   "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt",        "socks5"),
    ("clarketm HTTP",     "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt","http"),
    ("hookzof SOCKS5",    "https://raw.githubusercontent.com/hookzof/socks5_list/master/list.txt",          "socks5"),
    ("roosterkid HTTP",   "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",  "http"),
    ("roosterkid SOCKS5", "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt", "socks5"),
]

TEST_URL        = "https://httpbin.org/ip"
REFRESH_TIMEOUT = 6    # faster timeout during background refresh
REFRESH_WORKERS = 150


# ── fetch all sources ──────────────────────────────────────────────────────

def fetch_sources(verbose=False):
    proxies = set()
    for name, url, proto in SOURCES:
        try:
            r = requests.get(url, timeout=15)
            for line in r.text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith(("http", "socks")):
                    proxies.add(line)
                elif proto and "://" not in line:
                    proxies.add(f"{proto}://{line}")
            if verbose:
                print(f"  {G}[+]{W} {name:<22} fetched")
        except Exception as e:
            if verbose:
                print(f"  {R}[-]{W} {name:<22} failed: {e}")
    return list(proxies)


# ── test a single proxy ────────────────────────────────────────────────────

def _test_one(proxy):
    try:
        p = proxy if proxy.startswith(("http", "socks")) else f"http://{proxy}"
        s = requests.Session()
        s.proxies = {"http": p, "https": p}
        t0 = time.time()
        r  = s.get(TEST_URL, timeout=REFRESH_TIMEOUT)
        ms = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            return (proxy, ms)
    except:
        pass
    return None


# ── fetch + test all sources, return sorted list ───────────────────────────

def fetch_and_test(verbose=False):
    if verbose:
        print(f"  Fetching from {len(SOURCES)} sources...")
    raw = fetch_sources(verbose=verbose)
    if verbose:
        print(f"  Testing {len(raw)} unique proxies ({REFRESH_WORKERS} workers)...")

    working = []
    lock = threading.Lock()

    def _test(p):
        result = _test_one(p)
        if result:
            with lock:
                working.append(result)

    with concurrent.futures.ThreadPoolExecutor(max_workers=REFRESH_WORKERS) as ex:
        list(ex.map(_test, raw))

    working.sort(key=lambda x: x[1])
    return [p for p, ms in working]


# ── ProxyPool ──────────────────────────────────────────────────────────────

class ProxyPool:
    def __init__(self, path):
        self._path       = path
        self._lock       = threading.Lock()
        self._refreshing = False
        self._dead       = set()
        self.proxies     = self._load(path)
        self._cycle      = itertools.cycle(self.proxies)
        print(f"  {G}[PROXY]{W} {len(self.proxies)} proxies loaded from {path}")

    def _load(self, path):
        with open(path) as f:
            raw = [l.strip() for l in f if l.strip()]
        return [
            p if p.startswith(("http", "socks")) else f"http://{p}"
            for p in raw
        ]

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

    def pct_alive(self):
        if not self.proxies:
            return 0.0
        return self.alive() / len(self.proxies) * 100

    # ── auto-refresh ───────────────────────────────────────────────────────

    def maybe_refresh(self):
        """
        Called after every burst.
        If alive < 50% of total → spawn background thread to fetch + test
        fresh proxies from all 12 sources and merge into the pool.
        Does nothing if a refresh is already running.
        """
        with self._lock:
            if self._refreshing:
                return
            if self.alive() >= len(self.proxies) * 0.5:
                return
            self._refreshing = True

        print(f"\n  {Y}[PROXY]{W} Alive {self.alive()}/{len(self.proxies)} "
              f"({self.pct_alive():.0f}%) — below 50%. "
              f"Fetching fresh proxies in background...\n")
        t = threading.Thread(target=self._do_refresh, daemon=True)
        t.start()

    def _do_refresh(self):
        try:
            new_list = fetch_and_test()
            with self._lock:
                existing = set(self.proxies)
                added    = [p for p in new_list if p not in existing]
                self.proxies.extend(added)
                self._dead  = set()
                self._cycle = itertools.cycle(self.proxies)
            # persist updated list to disk
            with open(self._path, "w") as f:
                for p in self.proxies:
                    f.write(f"{p}\n")
            print(f"\n  {G}[PROXY]{W} Refresh complete — "
                  f"+{len(added)} new proxies added | "
                  f"{self.alive()} alive total\n")
        except Exception as e:
            print(f"\n  {R}[PROXY]{W} Background refresh failed: {e}\n")
        finally:
            with self._lock:
                self._refreshing = False


# ── Webshare backbone rotating pool ───────────────────────────────────────
# Single endpoint — Webshare rotates exit IP on every request automatically.
# No dead-tracking needed; backbone is always available.

class WebsharePool:
    """
    Drop-in replacement for ProxyPool using Webshare backbone.
    Reads creds from proxies_webshare_backbone_creds.txt (gitignored).
    """

    CREDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "proxies_webshare_backbone_creds.txt")
    HOST       = "p.webshare.io"
    PORT       = 80

    def __init__(self):
        user, pwd = self._load_creds()
        # -rotate suffix tells Webshare to pick a fresh exit IP each connection
        self._proxy = f"http://{user}-rotate:{pwd}@{self.HOST}:{self.PORT}"
        print(f"  {G}[PROXY]{W} Webshare backbone — rotating residential IPs via {self.HOST}:{self.PORT}")

    @classmethod
    def _load_creds(cls):
        with open(cls.CREDS_FILE) as f:
            lines = [l.strip() for l in f if l.strip()]
        if len(lines) < 2:
            raise ValueError(f"{cls.CREDS_FILE}: need username on line 1, password on line 2")
        return lines[0], lines[1]

    @classmethod
    def available(cls):
        return os.path.exists(cls.CREDS_FILE)

    def next(self, slot=None):
        return self._proxy

    def mark_dead(self, _proxy):
        pass  # backbone never dies — network errors are transient

    def alive(self):
        return 9999

    def pct_alive(self):
        return 100.0

    def maybe_refresh(self):
        pass  # nothing to refresh


# ── Tor rotating pool ─────────────────────────────────────────────────────
# Uses local Tor daemon (SOCKS5 on 127.0.0.1:9050).
# Sends NEWNYM signal via ControlPort 9051 after every request to get a
# fresh exit node — each request hits the target from a different IP.

class TorPool:
    SOCKS   = "socks5h://127.0.0.1:9050"   # h = DNS via Tor
    CTRL    = ("127.0.0.1", 9051)
    FLAG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "use_tor")

    def __init__(self):
        self._lock = threading.Lock()
        self._ctrl = None
        try:
            from stem.control import Controller
            self._ctrl = Controller.from_port(address=self.CTRL[0], port=self.CTRL[1])
            self._ctrl.authenticate()
            print(f"  {G}[PROXY]{W} Tor — NEWNYM every 10s (background), all workers share circuits")
            t = threading.Thread(target=self._rotation_loop, daemon=True)
            t.start()
        except Exception as e:
            print(f"  {Y}[PROXY]{W} Tor ControlPort unavailable ({e}) — circuit rotation disabled")

    @classmethod
    def available(cls):
        return os.path.exists(cls.FLAG_FILE)

    def next(self, slot=None):
        # High-concurrency mode: don't NEWNYM per request — that serializes all
        # workers behind one lock. Tor multiplexes streams across existing circuits
        # naturally; NEWNYM every 10s in background is enough for IP rotation.
        return self.SOCKS

    def _rotation_loop(self):
        """Background thread: sends NEWNYM every 10s to rotate exit nodes."""
        while True:
            time.sleep(10)
            if self._ctrl:
                try:
                    from stem import Signal
                    self._ctrl.signal(Signal.NEWNYM)
                except Exception:
                    pass

    def mark_dead(self, _proxy):
        pass

    def alive(self):
        return 9999

    def pct_alive(self):
        return 100.0

    def maybe_refresh(self):
        pass


# ── auto-detect: Tor > Webshare > file-based pool ─────────────────────────

def make_pool(proxy_file):
    if TorPool.available():
        return TorPool()
    if WebsharePool.available():
        return WebsharePool()
    return ProxyPool(proxy_file)

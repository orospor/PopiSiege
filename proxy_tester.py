#!/usr/bin/env python3
"""
PopiSiege — Proxy Tester
Fetches fresh proxy lists from public sources, tests all in parallel,
saves working ones to /tmp/working_proxies.txt

Usage:
  python3 proxy_tester.py
  python3 proxy_tester.py --output /tmp/working_proxies.txt
  python3 proxy_tester.py --workers 300
"""

import requests, concurrent.futures, time, threading, argparse, sys

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"; W = "\033[0m"; B = "\033[1m"

SOURCES = [
    ("TheSpeedX HTTP",   "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",   "http"),
    ("TheSpeedX SOCKS5", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt", "socks5"),
    ("Proxifly All",     "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt", None),
]

TEST_URL = "https://httpbin.org/ip"


def fetch_sources():
    proxies = set()
    for name, url, proto in SOURCES:
        try:
            r = requests.get(url, timeout=15)
            lines = [l.strip() for l in r.text.splitlines() if l.strip()]
            for line in lines:
                if line.startswith(("http","socks")):
                    proxies.add(line)
                elif proto and "://" not in line:
                    proxies.add(f"{proto}://{line}")
            print(f"  {G}[✓]{W} {name:<22} {len(lines):>5} proxies fetched")
        except Exception as e:
            print(f"  {R}[✗]{W} {name:<22} failed: {e}")
    return list(proxies)


def test_proxy(proxy, timeout, lock, working, tested, total):
    try:
        s = requests.Session()
        s.proxies = {"http": proxy, "https": proxy}
        t0 = time.time()
        r  = s.get(TEST_URL, timeout=timeout)
        ms = int((time.time()-t0)*1000)
        if r.status_code == 200:
            with lock:
                working.append((proxy, ms))
                tested[0] += 1
                print(f"  {G}[✓]{W} {proxy:<45} {ms:>5}ms")
            return
    except Exception:
        pass
    with lock:
        tested[0] += 1
        if tested[0] % 500 == 0:
            pct = tested[0]/total*100
            print(f"  {Y}...{W} {tested[0]}/{total} ({pct:.0f}%) | working: {len(working)}")


def main():
    p = argparse.ArgumentParser(description="PopiSiege Proxy Tester")
    p.add_argument("--output",  default="/tmp/working_proxies.txt")
    p.add_argument("--timeout", type=int, default=8)
    p.add_argument("--workers", type=int, default=200)
    args = p.parse_args()

    print(f"\n{B}{'='*55}{W}")
    print(f"  PopiSiege Proxy Tester")
    print(f"{B}{'='*55}{W}\n")

    print(f"  Fetching proxy lists...\n")
    proxies = fetch_sources()
    print(f"\n  {G}Total unique:{W} {len(proxies)} proxies\n")
    print(f"  Testing with {args.workers} parallel workers (timeout={args.timeout}s)...\n")

    working = []
    tested  = [0]
    lock    = threading.Lock()

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(test_proxy, p, args.timeout, lock, working, tested, len(proxies))
                for p in proxies]
        concurrent.futures.wait(futs)

    working.sort(key=lambda x: x[1])
    with open(args.output, "w") as f:
        for proxy, ms in working:
            f.write(f"{proxy}\n")

    elapsed = time.time()-t0
    print(f"\n{B}{'='*55}{W}")
    print(f"  Done in {elapsed:.0f}s")
    print(f"  Tested   : {len(proxies)}")
    print(f"  Working  : {G}{len(working)}{W}")
    print(f"  Dead     : {len(proxies)-len(working)}")
    print(f"  Saved to : {args.output}")
    if working:
        print(f"\n  Top 5 fastest:")
        for proxy, ms in working[:5]:
            print(f"    {proxy:<45} {ms}ms")
    print(f"{B}{'='*55}{W}\n")


if __name__ == "__main__":
    main()

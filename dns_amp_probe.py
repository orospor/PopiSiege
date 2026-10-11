#!/usr/bin/env python3
"""
DNS Amplification Probe — measures amplification ratio on an open recursive resolver.
Does NOT spoof source IPs. Sends real queries, measures response sizes.

Amplification factor = response_bytes / query_bytes
Anything > 1x is amplifiable. Common DNS records give 10x-100x+.
ANY queries on unpatched resolvers can give 50x-3000x.

Usage:
  python3 dns_amp_probe.py --resolver 104.236.68.226
  python3 dns_amp_probe.py --resolver 104.236.68.226 --domain metoo-shatkin.com
"""

import socket, struct, argparse, time, os, random
from datetime import datetime

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; B = "\033[1m"; W = "\033[0m"


def build_dns_query(domain, qtype=1, qclass=1):
    """Build a raw DNS query packet. Returns (packet_bytes, tx_id)."""
    tx_id = random.randint(0, 65535)
    flags = 0x0100          # standard query, recursion desired
    qdcount = 1
    header = struct.pack(">HHHHHH", tx_id, flags, qdcount, 0, 0, 0)

    labels = b""
    for part in domain.encode().split(b"."):
        labels += bytes([len(part)]) + part
    labels += b"\x00"

    question = labels + struct.pack(">HH", qtype, qclass)
    return header + question, tx_id


QTYPES = {
    "A":     1,
    "NS":    2,
    "MX":    15,
    "TXT":   16,
    "AAAA":  28,
    "ANY":   255,
    "DNSKEY":48,
    "RRSIG": 46,
}

TEST_DOMAINS = [
    "google.com",
    "cloudflare.com",
    "isc.org",
]


def probe_one(resolver, port, domain, qtype_name, qtype_num, timeout=5):
    """Send one DNS query, return (query_bytes, response_bytes, response_hex_preview)."""
    pkt, tx_id = build_dns_query(domain, qtype=qtype_num)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.sendto(pkt, (resolver, port))
        resp, _ = s.recvfrom(65535)
        s.close()
        return len(pkt), len(resp), resp[:20].hex()
    except socket.timeout:
        return len(pkt), 0, "TIMEOUT"
    except Exception as e:
        return len(pkt), 0, str(e)[:30]


def main():
    p = argparse.ArgumentParser(
        description="DNS Amplification Probe — measure amplification ratio"
    )
    p.add_argument("--resolver", default="104.236.68.226")
    p.add_argument("--port",     type=int, default=53)
    p.add_argument("--domain",   default=None,
                   help="Extra domain to test (default: uses built-in list)")
    p.add_argument("--timeout",  type=float, default=5.0)
    args = p.parse_args()

    domains = list(TEST_DOMAINS)
    if args.domain and args.domain not in domains:
        domains.insert(0, args.domain)

    print(f"""
{B}{'='*70}{W}
  DNS Amplification Probe
{B}{'='*70}{W}
  Resolver : {args.resolver}:{args.port}
  Domains  : {', '.join(domains)}
  Qt ypes  : {', '.join(QTYPES.keys())}
{B}{'='*70}{W}
  {"Domain":<25} {"QType":<8} {"Q bytes":>8} {"R bytes":>8} {"Amp":>8}  Status
{B}{'-'*70}{W}""")

    results = []

    for domain in domains:
        for qtype_name, qtype_num in QTYPES.items():
            q_bytes, r_bytes, preview = probe_one(
                args.resolver, args.port, domain, qtype_name, qtype_num, args.timeout
            )
            if r_bytes == 0:
                amp = 0.0
                status = f"{Y}TIMEOUT/ERR{W}"
            else:
                amp = r_bytes / q_bytes
                if amp >= 20:
                    status = f"{R}HIGH AMP ×{amp:.1f}{W}"
                elif amp >= 5:
                    status = f"{Y}MED AMP ×{amp:.1f}{W}"
                else:
                    status = f"{G}LOW ×{amp:.1f}{W}"

            results.append((domain, qtype_name, q_bytes, r_bytes, amp))
            print(f"  {domain:<25} {qtype_name:<8} {q_bytes:>8} {r_bytes:>8} {amp:>7.1f}x  {status}")
            time.sleep(0.1)

    print(f"\n{B}{'='*70}{W}")
    if results:
        best = max(results, key=lambda x: x[4])
        print(f"  {R}Max amplification:{W} {best[1]} query for {best[0]}")
        print(f"  Query: {best[2]} bytes → Response: {best[3]} bytes → {best[4]:.1f}x")
        print()
        reachable = [r for r in results if r[3] > 0]
        if reachable:
            avg_amp = sum(r[4] for r in reachable) / len(reachable)
            print(f"  Avg amplification (all responding): {avg_amp:.1f}x")
        print(f"\n  {Y}Attack potential:{W}")
        print(f"  1 Gbps spoofed UDP → ~{best[4]:.0f} Gbps at victim")
        print(f"  100k pps × {best[2]}B query → {100000 * best[3] / 1e6:.0f} MB/s response flood")
    print(f"{B}{'='*70}{W}\n")


if __name__ == "__main__":
    main()

# DNS Amplification via Open Recursive Resolver: Measurement, Analysis, and Mitigation

**Authors:** Gurcharanjit Singh (Guru), Orospor Consultants and Services LLP, Jalandhar, Punjab, India  
**Date:** November 10, 2026  
**Classification:** Authorized security research — own infrastructure  
**Tooling:** dns_amp_poc.py (open source, github.com/orospor/PopiSiege)

---

## Abstract

We document the discovery and measurement of a DNS amplification attack surface on a DigitalOcean VPS (104.236.68.226) running an open recursive DNS resolver publicly accessible on UDP/TCP port 53. Through systematic probing across 16 query type and domain combinations, we confirm the resolver amplifies DNSKEY record queries by up to **11.3×** with EDNS0 (realistic attack scenario) and **15.1×** without EDNS0. While RFC 8482 (ANY → minimal response) is correctly implemented and neutralizes the ANY vector, DNSKEY and NS query types remain fully unmitigated. We calculate real-world attack impact, provide CSV measurement data, and document the one-rule remediation.

---

## 1. Background

### 1.1 DNS Amplification Attacks

DNS amplification is a class of volumetric Distributed Denial-of-Service (DDoS) attack that exploits the asymmetry between small DNS query packets and large DNS response packets. An attacker:

1. Spoofs the victim's IP address as the UDP source address
2. Sends small query packets to an open recursive DNS resolver
3. The resolver, unaware of the spoofing, sends the large response to the victim

Since UDP does not require a handshake, there is no mechanism to validate the source IP before the response is sent.

**Amplification factor (AF):** `response_size / query_size`

An AF of 11.3× means an attacker with 1 Gbps of outbound capacity can deliver 11.3 Gbps of traffic to the victim — bypassing the attacker's bandwidth constraint entirely.

### 1.2 Why Open Recursive Resolvers Are Dangerous

An authoritative DNS server only answers queries about zones it owns. An open **recursive** resolver, by contrast, will resolve queries for any domain on behalf of any source IP. This is the property exploited in amplification attacks: the resolver fetches and returns large DNS record sets for domains it does not host.

The RA (Recursion Available) bit in the DNS response flags field confirms that a resolver accepts recursive queries. All 15 successful responses in our measurement had the RA bit set.

### 1.3 DNSSEC and Amplification

DNSSEC adds cryptographic signatures to DNS records. DNSKEY records (which carry public keys for DNSSEC validation) are substantially larger than A or CNAME records. This makes DNSSEC-enabled domains (such as verisign.com and paypal.com) the highest-amplification targets when queried via DNSKEY or RRSIG query types.

---

## 2. Target Configuration

| Property         | Value                              |
|------------------|------------------------------------|
| IP               | 104.236.68.226                     |
| Port             | UDP 53 (open), TCP 53 (open)       |
| Provider         | DigitalOcean                       |
| Resolver type    | Open recursive (RA bit confirmed)  |
| RFC 8482 (ANY)   | Implemented — ANY returns 0 answers |
| Cloudflare WAF   | Present on port 80/443 (web layer) |
| VPS purpose      | WordPress hosting (metoo-shatkin.com, metoo-buffalo.com) |

The resolver was not intentionally deployed as a public service. It was incidentally accessible because no network-level firewall rule blocked UDP/TCP 53 from external IPs.

---

## 3. Measurement Methodology

### 3.1 Tool

`dns_amp_poc.py` — a purpose-built Python 3 measurement tool that:

- Builds raw UDP DNS query packets using `struct` (no external DNS library dependency)
- Optionally includes EDNS0 OPT record (announces 4096-byte UDP buffer — matches real DNS client behavior)
- Sends queries to the target resolver from the researcher's own IP (no spoofing)
- Records exact query and response byte counts
- Calculates amplification factor, RCODE, answer count, and round-trip latency
- Outputs formatted table + CSV for paper/chart use

### 3.2 Two Measurement Passes

We ran the tool twice to isolate the effect of EDNS0:

| Pass | EDNS0 | Purpose |
|------|-------|---------|
| 1    | No    | Minimum viable query — smallest attacker packet |
| 2    | Yes   | Realistic attack query — matches what real DNS libraries send |

Pass 1 gives the theoretical maximum AF (smaller query denominator). Pass 2 gives the realistic attack scenario.

### 3.3 Test Matrix

16 (domain, query type) combinations across A, AAAA, MX, TXT, NS, DNSKEY, and ANY query types, targeting domains selected for maximum record count and DNSSEC deployment depth.

---

## 4. Results

### 4.1 Pass 1 — Without EDNS0

| Rank | QType  | Domain         | Query | Response | AF     |
|------|--------|----------------|-------|----------|--------|
| 1    | DNSKEY | verisign.com   | 30B   | 454B     | **15.1×** |
| 2    | DNSKEY | paypal.com     | 28B   | 348B     | 12.4×  |
| 3    | NS     | cloudflare.com | 32B   | 386B     | 12.1×  |
| 4    | NS     | yahoo.com      | 27B   | 309B     | 11.4×  |
| 5    | NS     | akamai.com     | 28B   | 302B     | 10.8×  |
| —    | ANY    | verisign.com   | 30B   | 30B      | 1.0×   |
| —    | ANY    | cloudflare.com | 32B   | 32B      | 1.0×   |

RFC 8482 observation: ANY queries return NOERROR with 0 answer records regardless of target domain. The resolver correctly neutralizes the ANY vector.

### 4.2 Pass 2 — With EDNS0 (Realistic Attack Scenario)

| Rank | QType  | Domain         | Query | Response | AF     |
|------|--------|----------------|-------|----------|--------|
| 1    | DNSKEY | verisign.com   | 41B   | 465B     | **11.3×** |
| 2    | NS     | cloudflare.com | 43B   | 397B     | 9.2×   |
| 3    | DNSKEY | paypal.com     | 39B   | 359B     | 9.2×   |
| 4    | NS     | yahoo.com      | 38B   | 320B     | 8.4×   |
| 5    | NS     | akamai.com     | 39B   | 313B     | 8.0×   |
| —    | ANY    | verisign.com   | 41B   | 41B      | 1.0×   |

RFC 8482 holds even with EDNS0 — ANY is neutralized in both passes. DNSKEY and NS remain fully open.

### 4.3 RFC 8482 Analysis

RFC 8482 ("Providing Minimal-Sized Responses to DNS Queries That Have QTYPE=ANY") instructs resolvers to return a minimal synthesized response (HINFO record or empty) for ANY queries rather than the full record set. This prevents the historically worst amplification vector (ANY against large zones with hundreds of records).

**What RFC 8482 fixes:** ANY queries. No impact on DNSKEY, NS, MX, or other typed queries.

**What RFC 8482 does NOT fix:** DNSKEY remains the primary amplification vector. A DNSSEC-signed zone (verisign.com) responds to DNSKEY with 2–4 large public key records + RRSIG signatures, totaling 350–465 bytes from a 28–41 byte query.

### 4.4 Attack Impact Projection

Using the peak measured vector (DNSKEY/verisign.com, AF = 11.3× with EDNS0):

| Attacker bandwidth | Victim receives  |
|--------------------|-----------------|
| 100 Mbps           | 1.1 Gbps        |
| 500 Mbps           | 5.7 Gbps        |
| **1 Gbps**         | **11.3 Gbps**   |
| 10 Gbps            | 113 Gbps        |

For context, 11.3 Gbps is sufficient to saturate most unprotected datacenter uplinks and exceeds DigitalOcean's default DDoS threshold for single-droplet protection.

---

## 5. Full Measurement Data (CSV)

```
domain,qtype,query_bytes,response_bytes,amplification,rcode,answer_count,latency_ms
verisign.com,ANY,41,41,1.00,NOERROR,0,294
cloudflare.com,ANY,43,43,1.00,NOERROR,0,376
google.com,ANY,39,39,1.00,NOERROR,0,282
verisign.com,DNSKEY,41,465,11.34,NOERROR,2,324
cloudflare.com,DNSKEY,43,0,0.00,TIMEOUT,0,5002
paypal.com,DNSKEY,39,359,9.21,NOERROR,4,269
isc.org,DNSKEY,36,196,5.44,NOERROR,2,269
cloudflare.com,NS,43,397,9.23,NOERROR,5,284
yahoo.com,NS,38,320,8.42,NOERROR,5,268
akamai.com,NS,39,313,8.03,NOERROR,13,310
google.com,MX,39,60,1.54,NOERROR,1,268
cloudflare.com,MX,43,172,4.00,NOERROR,4,310
google.com,TXT,39,39,1.00,NOERROR,0,268
cloudflare.com,TXT,43,43,1.00,NOERROR,0,331
google.com,A,39,55,1.41,NOERROR,1,268
cloudflare.com,AAAA,43,99,2.30,NOERROR,2,293
```

---

## 6. Root Cause

The VPS's `/etc/bind/named.conf` (or equivalent resolver daemon) had no `allow-recursion` or `allow-query` ACL restricting recursive queries to localhost. The DigitalOcean Firewall had no rule blocking UDP/TCP port 53 from `0.0.0.0/0`. The combined absence of both controls left the resolver fully accessible to any Internet host.

The resolver runs on the same VPS as the web application. This means:
1. Its outbound bandwidth is shared with the web server
2. High-volume abuse (attacker floods the resolver with spoofed queries) saturates the outbound pipe, taking down the web service
3. DigitalOcean's abuse detection may suspend the droplet if the resolver is observed participating in attacks against third parties

---

## 7. Remediation

### 7.1 DigitalOcean Cloud Firewall (Recommended)

Add an **inbound** rule to the droplet's firewall:

```
Protocol : UDP
Port     : 53
Sources  : 0.0.0.0/0, ::/0  → DENY

Protocol : TCP
Port     : 53
Sources  : 0.0.0.0/0, ::/0  → DENY
```

Cloud Firewall rules apply before traffic reaches the droplet — no kernel overhead, no FD consumption from connection attempts.

### 7.2 UFW (OS-level, Defense-in-Depth)

```bash
sudo ufw deny in on eth0 to any port 53 proto udp
sudo ufw deny in on eth0 to any port 53 proto tcp
sudo ufw allow in from 127.0.0.1 to any port 53
```

### 7.3 BIND named.conf ACL (Application-level)

```
options {
    allow-recursion { 127.0.0.1; ::1; };
    allow-query     { 127.0.0.1; ::1; };
};
```

**Recommended:** Apply all three layers (DO Firewall + UFW + named.conf ACL). Defense in depth ensures the resolver stays closed even if one layer is misconfigured.

---

## 8. Disclosure Timeline

| Date           | Event |
|----------------|-------|
| 2026-11-10     | Open resolver discovered during authorized VPS security audit |
| 2026-11-10     | Amplification factors measured using dns_amp_poc.py |
| 2026-11-10     | Paper drafted, remediation documented |
| TBD            | Firewall rule applied, resolver closed |
| TBD            | Post-fix verification run confirming 0/16 queries answered |

---

## 9. Conclusion

An open recursive DNS resolver running on a production VPS constitutes a significant dual-risk: it can be leveraged by external actors to amplify DDoS traffic against third-party victims, and it exposes the VPS operator to service disruption (bandwidth saturation) and potential account suspension. RFC 8482 is correctly implemented and neutralizes the historically most dangerous ANY vector, but DNSKEY and NS query types remain active amplifiers at 9–11× with standard EDNS0. A single DigitalOcean Firewall rule blocking port 53 from all external IPs fully closes this attack surface with no impact on the web application running on the same host.

---

## References

1. RFC 5358 — Preventing Use of Recursive Nameservers in Reflector Attacks (2008)
2. RFC 8482 — Providing Minimal-Sized Responses to DNS Queries That Have QTYPE=ANY (2019)
3. US-CERT Alert TA13-088A — DNS Amplification Attacks (2013)
4. Cloudflare — "Understanding and mitigating DNS amplification attacks"
5. dns_amp_poc.py — github.com/orospor/PopiSiege

---

*Authorized security research. All measurements performed against infrastructure owned and operated by the authors. No third-party systems were accessed or affected.*

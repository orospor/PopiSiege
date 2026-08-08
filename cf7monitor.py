#!/usr/bin/env python3
"""
cf7monitor.py — degradation monitor using the CF7 feedback POST endpoint.

Unlike GET /wp-json/ (which can be served from LiteSpeed cache without
touching PHP-FPM), the CF7 feedback endpoint never caches — every hit
consumes a real PHP-FPM worker + WordPress bootstrap. This is the actual
resource popisiege.py exhausts, so this is the correct probe for measuring
real degradation, not just connection-layer response.

Usage:
  python3 cf7monitor.py --target metoo-shatkin.com --rounds 20 --delay 2
"""

import argparse
import random
import time
from curl_cffi import requests as r
from curl_cffi import CurlMime

TARGETS = {
    "metoo-shatkin.com": {
        "url":      "https://metoo-shatkin.com/wp-json/contact-form-7/v1/contact-forms/50/feedback",
        "form_id":  "50",
        "unit_tag": "wpcf7-f50-p30-o1",
    },
    "metoo-buffalo.com": {
        "url":      "https://metoo-buffalo.com/wp-json/contact-form-7/v1/contact-forms/248/feedback",
        "form_id":  "248",
        "unit_tag": "wpcf7-f248-p850-o1",
    },
}

_FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda"]
_LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis"]
_SUBJECTS = ["Question", "Inquiry", "Feedback", "Support Request", "General"]
_MESSAGES = [
    "I would like more information please.",
    "Please contact me regarding this matter.",
    "Just checking in about my request.",
    "Following up on my previous message.",
]


def build_multipart(form_id, unit_tag):
    first = random.choice(_FIRST)
    last = random.choice(_LAST)
    mp = CurlMime()
    mp.addpart(name="_wpcf7", data=form_id)
    mp.addpart(name="_wpcf7_version", data="6.1.6")
    mp.addpart(name="_wpcf7_locale", data="en_US")
    mp.addpart(name="_wpcf7_unit_tag", data=unit_tag)
    mp.addpart(name="your-name", data=f"{first} {last}")
    mp.addpart(name="your-email", data=f"{first.lower()}.{last.lower()}{random.randint(1,9999)}@example.com")
    mp.addpart(name="your-subject", data=random.choice(_SUBJECTS))
    mp.addpart(name="your-message", data=random.choice(_MESSAGES))
    return mp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="metoo-shatkin.com", choices=TARGETS.keys())
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--impersonate", default="chrome")
    args = ap.parse_args()

    cfg = TARGETS[args.target]
    domain = args.target

    print(f"CF7 feedback monitor — {domain}")
    print(f"URL: {cfg['url']}")
    print(f"Rounds: {args.rounds}  Delay: {args.delay}s")
    print("=" * 60)

    for i in range(args.rounds):
        ts = time.strftime("%H:%M:%S")
        t0 = time.time()
        try:
            resp = r.post(
                cfg["url"],
                multipart=build_multipart(cfg["form_id"], cfg["unit_tag"]),
                headers={"Origin": f"https://{domain}", "Referer": f"https://{domain}/"},
                impersonate=args.impersonate,
                timeout=25,
            )
            lat = round((time.time() - t0) * 1000)
            cache = resp.headers.get("cf-cache-status", "n/a")
            mitigated = resp.headers.get("cf-mitigated", "")
            status_note = ""
            if resp.status_code == 200:
                try:
                    body_status = resp.json().get("status", "?")
                    status_note = f" body={body_status}"
                except Exception:
                    pass
            print(f"[{ts}] #{i+1:3d}  HTTP {resp.status_code}  {lat:5d}ms  "
                  f"cf-cache={cache}  mitigated={mitigated}{status_note}")
        except Exception as e:
            lat = round((time.time() - t0) * 1000)
            print(f"[{ts}] #{i+1:3d}  FAIL {type(e).__name__}  {lat:5d}ms")

        if i < args.rounds - 1:
            time.sleep(args.delay)

    print("=" * 60)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
popitest1.py — baseline control test: NO IP rotation, NO UA rotation.

Every request goes out from this machine's own IP (--no-proxy) using one
fixed browser profile (--fixed-profile chrome124) for the entire run. This
is the cleanest possible baseline to compare against rotating-IP and/or
rotating-UA runs — only one variable (time) changes between runs, so any
difference in block rate against a rotating run isolates what rotation
actually buys you.

Usage:
  python3 popitest1.py
  python3 popitest1.py --target metoo-buffalo.com
  python3 popitest1.py --concurrency 10 --verbose
  python3 popitest1.py --profile firefox147
"""

import sys
import argparse

# Reuse popisiege's own CLI — just force --no-proxy and a fixed profile on
# top of whatever the user passes, so this stays a thin wrapper instead of
# a second copy of the request logic that could drift out of sync.
pre = argparse.ArgumentParser(add_help=False)
pre.add_argument("--profile", default="chrome124",
                  help="Which single browser profile to hold fixed (default: chrome124)")
known, rest = pre.parse_known_args()

sys.argv = [sys.argv[0]] + rest + ["--no-proxy", "--fixed-profile", known.profile]

import popisiege
popisiege.main()

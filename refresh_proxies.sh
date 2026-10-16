#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$(realpath "$0" 2>/dev/null || echo "$0")")" && pwd)"
US_RAW="$SCRIPT_DIR/proxies_us_fresh.txt"
PASSING="$SCRIPT_DIR/proxies_cf_passing_new.txt"

echo "Fetching US proxies from proxyscrape CDN..."
curl -s "https://cdn.jsdelivr.net/gh/proxyscrape/free-proxy-list@main/proxies/countries/us/data.txt" > "$US_RAW"
COUNT=$(wc -l < "$US_RAW" | tr -d ' ')
echo "Got $COUNT US proxies"

echo "Testing against CF WAF..."
python3 "$SCRIPT_DIR/cf_proxy_test.py" "$US_RAW" --workers 100 --out "$PASSING" --append

echo "Deduplicating $PASSING..."
BEFORE=$(wc -l < "$PASSING" | tr -d ' ')
sort -u "$PASSING" -o "$PASSING"
AFTER=$(wc -l < "$PASSING" | tr -d ' ')
echo "Done: $BEFORE -> $AFTER unique passing proxies"

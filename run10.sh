#!/usr/bin/env bash
# run10.sh — run a given command N times (default 10), capture exit status + timing
#
# Usage:
#   ./run10.sh 'curl-cffi get https://metoo-shatkin.com/wp-json'
#   ./run10.sh -n 20 -d 3 'curl-cffi get https://metoo-shatkin.com/wp-json --headers'
#
#   -n <count>   number of runs (default 10)
#   -d <seconds> delay between runs (default 2)

N=10
DELAY=2

while getopts "n:d:" opt; do
  case $opt in
    n) N=$OPTARG ;;
    d) DELAY=$OPTARG ;;
  esac
done
shift $((OPTIND - 1))

CMD="$1"
if [ -z "$CMD" ]; then
  echo "Usage: $0 [-n count] [-d delay] 'command to run'"
  exit 1
fi

echo "Running: $CMD"
echo "Count: $N   Delay: ${DELAY}s"
echo "============================================"

ok=0
fail=0

now_ms() { python3 -c 'import time; print(int(time.time()*1000))'; }

for i in $(seq 1 "$N"); do
  ts=$(date +%H:%M:%S)
  t0=$(now_ms)
  out=$(eval "$CMD" 2>&1)
  code=$?
  t1=$(now_ms)
  lat=$((t1 - t0))

  # try to pull an HTTP status line out of the output, if present
  http_line=$(echo "$out" | grep -iE "^HTTP|HTTP/[0-9.]+ [0-9]{3}" | head -1)

  if [ $code -eq 0 ]; then
    ok=$((ok + 1))
    tag="OK"
  else
    fail=$((fail + 1))
    tag="FAIL(exit=$code)"
  fi

  echo "[$ts] #$i  $tag  ${lat}ms  ${http_line}"

  if [ "$i" -lt "$N" ]; then
    sleep "$DELAY"
  fi
done

echo "============================================"
echo "Done: $ok ok, $fail fail (of $N)"

#!/bin/bash
# Runs benchmark runs one at a time. Usage: BENCH_I_OWN_THESE_STORES=yes ./run_series.sh "C1:K:1 C1:S:1 ..."
# Needs BENCH_API_KEY (optionally BENCH_API_BASE, BENCH_MODEL, BENCH_TASKS).
# Run it only against stores you own or have the owner's permission to test.
B=$(cd "$(dirname "$0")" && pwd); cd "$B"; mkdir -p runs
if [ "${BENCH_I_OWN_THESE_STORES:-}" != "yes" ]; then
  echo "Refusing to run: set BENCH_I_OWN_THESE_STORES=yes to confirm you own these stores or have permission to test them."; exit 2
fi
GAP=${BENCH_GAP:-120}; [ "$GAP" -lt 60 ] && GAP=60          # at least one minute between runs
CAP=${BENCH_MAX_RUNS_PER_STORE_PER_DAY:-10}
for R in $1; do IFS=: read T A N <<< "$R"
  for S in $(python3 -c "import json,os;t=json.load(open(os.environ.get('BENCH_TASKS','tasks.json')))['$T'];print(' '.join(t.get('stores',[t['store']])))"); do
    DONE=$(grep -c "^$(date -u +%F).* start .* $S\$" runs/series.log 2>/dev/null)
    if [ "${DONE:-0}" -ge "$CAP" ]; then echo "Daily cap of $CAP runs reached for $S; stopping."; exit 3; fi
  done
  STORES=$(python3 -c "import json,os;t=json.load(open(os.environ.get('BENCH_TASKS','tasks.json')))['$T'];print(' '.join(t.get('stores',[t['store']])))")
  for S in $STORES; do echo "$(date -u +%FT%TZ) start $T-$A-$N $S" >> runs/series.log; done
  python3 agent.py "$T" "$A" "$N" >> runs/agent.log 2>&1
  echo "$(date -u +%FT%TZ) end $T-$A-$N" >> runs/series.log
  sleep "$GAP"
done

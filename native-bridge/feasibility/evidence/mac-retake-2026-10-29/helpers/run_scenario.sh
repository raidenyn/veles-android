#!/bin/bash
# OTP-23 Mac M-08 retake 2026-10-29: ONE fresh Chrome-owned scenario, unique attempt dir, fresh run id.
# Usage: run_scenario.sh <scenario> <attempt-n>
# Never overwrites (refuses existing local/remote attempt dirs); never retries.
set -u
SCEN="$1"; N="$2"
L=/tmp/opencode/otp23-mac-retake-20261029
KEY=/home/dev/.ssh/id_sirius_ed25519; MAC=raiden@192.168.11.116
SSH="ssh -o ConnectTimeout=10 -o BatchMode=yes -o ServerAliveInterval=10 -i $KEY $MAC"
W=/Users/raiden/homepc/work/otp23-mac-retake-20261029
REL=evidence/mac-retake-2026-10-29/$SCEN-$N
A=$L/attempts/$SCEN-$N
[ -e "$A" ] && { echo "local attempt dir $A exists; refusing"; exit 2; }
mkdir -p "$A/phones"
RUN_ID="mac-m08-$SCEN-$(date -u +%Y%m%dT%H%M%SZ)"
echo "$RUN_ID" > "$A/phones/run-id.txt"
macms() { timeout 30 $SSH 'python3 -c "import time; print(int(time.time()*1000))"' 2>/dev/null; }
log() { echo "[$(date -u +%FT%T.%3NZ)] $*" | tee -a "$A/operator-timeline.log"; }

log "scenario=$SCEN attempt=$N run_id=$RUN_ID"
timeout 20 $SSH "[ ! -e $W/$REL ] && mkdir -p $W/$REL && echo REMOTE-NEW" | tee -a "$A/operator-timeline.log" | grep -q REMOTE-NEW \
  || { log "remote attempt dir exists or ssh failed; refusing"; exit 2; }

# ---- 1. fixtures (fresh run id on BOTH phones) -------------------------------------------------
for pair in 6T:2be02e41 10Pro:864a3db0; do
  name=${pair%%:*}; s=${pair##*:}
  nohup adb -s $s shell am instrument -w -e class me.nagaev.veles.sharing.SharingFeasibilityTest \
    -e otp23 true -e otp23_transport_evidence true -e otp23_run_id "$RUN_ID" \
    me.nagaev.veles.otp23.test/androidx.test.runner.AndroidJUnitRunner > "$A/phones/fixture-$name.instrument.log" 2>&1 &
  echo $! > "$A/phones/fixture-$name.adb-client.pid"
  log "fixture $name ($s) started, adb client pid $!"
done
ready=0
for i in $(seq 1 60); do
  r1=$(adb -s 2be02e41 shell run-as me.nagaev.veles.otp23 ls files/otp23-ready 2>/dev/null | tr -d '\r')
  r2=$(adb -s 864a3db0 shell run-as me.nagaev.veles.otp23 ls files/otp23-ready 2>/dev/null | tr -d '\r')
  if [ "$r1" = "files/otp23-ready" ] && [ "$r2" = "files/otp23-ready" ]; then ready=1; break; fi
  sleep 1
done
log "fixtures ready=$ready after ${i}s"
[ $ready = 1 ] || { log "BLOCKER: fixtures not ready; stopping own fixtures, no driver run"; }

# ---- 2. driver (Chrome-owned scenario), argv sampler, host-gone check ------------------------------
if [ $ready = 1 ]; then
  log "driver start mac_ms=$(macms)"
  timeout 600 $SSH "
    cd $W
    ( while true; do d=\$(python3 -c 'import time; print(int(time.time()*1000))'); ps -axww -o pid=,ppid=,command= | grep 'veles-otp23-macos-profile-' | grep -v grep | grep 'Google Chrome ' | grep -v -- '--type=' | sed \"s#^#\$d #\"; sleep 0.5; done ) > $REL/chrome-argv-samples.txt 2>/dev/null &
    SAMPLER=\$!
    python3 - <<'EOF' > $REL/run-provenance.json
import json, os, sys, datetime
print(json.dumps({'what': 'M-08 retake scenario run provenance (Mac side, written before the driver starts)',
  'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds'),
  'cwd': os.getcwd(), 'python': sys.version.split()[0],
  'scenario': '$SCEN', 'attempt': '$N', 'phone_run_id': '$RUN_ID',
  'driver_argv': ['python3', 'scripts/scenario_matrix.py', 'lifecycle', '--scenario', '$SCEN', '--out-dir', '$REL'],
  'env_VELES_OTP23_LSOF_DIAG': '$REL/lsof-diagnostics.jsonl', 'env_VELES_OTP23_DETACHED_CLOSE_unset': True}, indent=2))
EOF
    env -u VELES_OTP23_DETACHED_CLOSE VELES_OTP23_LSOF_DIAG=$W/$REL/lsof-diagnostics.jsonl \
      python3 scripts/scenario_matrix.py lifecycle --scenario $SCEN --out-dir $REL > $REL/driver-stdout.txt 2> $REL/driver-stderr.txt
    echo \"DRIVER_RC=\$?\"
    kill \$SAMPLER 2>/dev/null
    echo \"DRIVER_RETURNED_MAC_MS=\$(python3 -c 'import time; print(int(time.time()*1000))')\"
    cat $REL/driver-stdout.txt
    echo HOSTS_AFTER:; ps -axo pid=,comm= | grep -i otp23-host | grep -v grep || echo none
    echo CHROME_OWNED_AFTER:; ps -axww -o pid=,command= | grep 'veles-otp23-macos-profile-' | grep -v grep || echo none
  " 2>&1 | tee "$A/driver-ssh.txt"
  log "driver returned"
  # >= 60 s AFTER the driver returned and the host was confirmed gone (above), before stopping fixtures
  sleep 65
  log "post-driver hold done; second host-gone check: $(timeout 20 $SSH 'ps -axo pid=,comm= | grep -i otp23-host | grep -v grep || echo none' | tr '\n' ' ')"
fi

# ---- 3. stop own fixtures with phone-side brackets + Mac ms readbacks --------------------------------
: > "$A/phones/stops.raw.txt"
for pair in 6T:2be02e41 10Pro:864a3db0; do
  name=${pair%%:*}; s=${pair##*:}
  m1=$(macms); pb=$(adb -s $s shell date +%s%3N | tr -d '\r')
  br=$(adb -s $s shell 'run-as me.nagaev.veles.otp23 sh -c "date +%s%3N; touch files/otp23-stop; date +%s%3N"' | tr -d '\r' | tr '\n' ' ')
  pa=$(adb -s $s shell date +%s%3N | tr -d '\r'); m2=$(macms)
  echo "$name $s mac_before_ms=$m1 phone_adb_before_ms=$pb in_shell_before_after_touch_ms=[$br] phone_adb_after_ms=$pa mac_after_ms=$m2" | tee -a "$A/phones/stops.raw.txt"
done
log "stop files created"
for i in $(seq 1 40); do
  grep -q "OK (1 test)\|FAILURES\|Error" "$A/phones/fixture-6T.instrument.log" && grep -q "OK (1 test)\|FAILURES\|Error" "$A/phones/fixture-10Pro.instrument.log" && break
  sleep 1
done
log "instrumentation ended (wait ${i}s)"
for pair in 6T:2be02e41 10Pro:864a3db0; do
  s=${pair##*:}
  adb -s $s shell run-as me.nagaev.veles.otp23 cat files/otp23-transport-evidence.json > "$A/phones/transport-$s.json"
done

# ---- 4. pull the Mac attempt dir (read-only copy) --------------------------------------------------
timeout 60 $SSH "cd $W/evidence/mac-retake-2026-10-29 && tar czf - $SCEN-$N" > "$A/mac-$SCEN-$N.tgz"
mkdir -p "$A/mac" && tar xzf "$A/mac-$SCEN-$N.tgz" -C "$A/mac"
log "pulled mac evidence: $(ls "$A/mac/$SCEN-$N" | tr '\n' ' ')"
echo "ATTEMPT_DIR=$A RUN_ID=$RUN_ID"

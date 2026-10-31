#!/bin/bash
# usage: itask.sh NAME 'C:\path\to\script.bat' DONEFILE [MAX_SECONDS]
# Runs a batch file in the logged-in console session via an interactive (/IT) scheduled task, waits for DONEFILE, deletes the task.
NAME=$1; BAT=$2; DONE=$3; MAX=${4:-300}
SSH="ssh -q -o BatchMode=yes -o ConnectTimeout=8 -i /home/dev/.ssh/id_homepc_ed25519 yuriy@192.168.11.100"
$SSH "schtasks /delete /tn $NAME /f" >/dev/null 2>&1
$SSH "schtasks /create /tn $NAME /tr \"cmd /c $BAT\" /sc once /st 00:00 /it /ru yuriy /rl limited /f" || exit 3
$SSH "schtasks /run /tn $NAME" || exit 4
start=$(date +%s)
while true; do
  if $SSH "cmd /c dir /b $DONE" >/dev/null 2>&1; then break; fi
  if [ $(( $(date +%s) - start )) -gt "$MAX" ]; then echo "TIMEOUT waiting for $DONE"; $SSH "schtasks /delete /tn $NAME /f" >/dev/null 2>&1; exit 5; fi
  sleep 5
done
$SSH "schtasks /delete /tn $NAME /f" >/dev/null 2>&1
echo "done after $(( $(date +%s) - start )) s"

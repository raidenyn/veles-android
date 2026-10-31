import json, os, shutil, subprocess
T = '/home/dev/.claude/jobs/52694ac4/tmp/'
E = '/home/dev/repos/veles-android/.claude/worktrees/otp23-investigations/native-bridge/feasibility/evidence/investigations-2026-10-27/'
for sub in ('macos/timer-probe', 'issues', 'signing'):
    os.makedirs(E + sub, exist_ok=True)
for d in ('ctl-discover-both', 'ctl-discover-6t-only', 'ctl-K1', 'ctl-K2', 'ctl-K3', 'ctl-K4', 'ctl-K5'):
    shutil.copytree(T + 'probe/' + d, E + 'windows-access-denied/' + d, dirs_exist_ok=True)
shutil.copy(T + 'mac-out/result-540.json', E + 'macos/timer-probe/result-540s.json')
shutil.copy(T + 'mac-out/host-send.log', E + 'macos/timer-probe/host-send.log')
shutil.copy(T + 'probe540.log', E + 'macos/timer-probe/driver-stdout.txt')
shutil.copy(T + 'dr.txt', E + 'signing/designated-requirement-readback.txt')
shutil.copy(T + 'dumpsys-6t-raw.txt', E + 'phones/phone-6t-dumpsys-bluetooth_manager-RAW-after-controls.txt')
shutil.copy(T + 'dumpsys-10pro-raw.txt', E + 'phones/phone-10pro-dumpsys-bluetooth_manager-RAW-after-controls.txt')
shutil.copy(T + 'fix2-6t.log', E + 'phones/fixture2-6t-instrument.log')
shutil.copy(T + 'fix2-10pro.log', E + 'phones/fixture2-10pro-instrument.log')
shutil.copy(T + 'prov-followup.json', E + 'provenance/followup-session-model-provenance.json')
for n in (86, 87, 88, 89, 90):
    shutil.copy(T + 'issues/issue-%d.json' % n, E + 'issues/issue-%d-full-2026-10-27.json' % n)
for name in ('ctl.bat', 'disc.bat', 'prov2.py', 'prov3.py', 'pin.py', 'pr.py', 'assemble2.py', 'showissue.py'):
    shutil.copy(T + name, E + 'scripts-used/' + name)
print('ok')

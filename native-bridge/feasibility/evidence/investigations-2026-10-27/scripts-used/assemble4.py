import os, shutil
T = '/home/dev/.claude/jobs/52694ac4/tmp/'
E = '/home/dev/repos/veles-android/.claude/worktrees/otp23-investigations/native-bridge/feasibility/evidence/investigations-2026-10-27/'
W = E + 'windows-chrome/'
for sub in ('timer-probe', 'lifecycle', 'interactive-session', 'retained-failed-attempts'):
    os.makedirs(W + sub, exist_ok=True)
# timer probe: valid hidden run + the invalid-condition run (tabs visible) + smoke
shutil.copy(T + 'win-out/result-540-hidden.json', W + 'timer-probe/result-540s-hidden-tabs.json')
shutil.copy(T + 'win-out/host-send-hidden.log', W + 'timer-probe/host-send-hidden-tabs.log')
shutil.copy(T + 'win-out/driver-stdout-hidden.txt', W + 'timer-probe/driver-stdout-hidden-tabs.txt')
shutil.copy(T + 'win-out/run1-tabs-visible-result.json', W + 'retained-failed-attempts/timer-probe-run1-tabs-were-visible-result.json')
shutil.copy(T + 'win-out/run1-host-send.log', W + 'retained-failed-attempts/timer-probe-run1-host-send.log')
shutil.copy(T + 'win-out/run1-driver-stdout.txt', W + 'retained-failed-attempts/timer-probe-run1-driver-stdout.txt')
shutil.copy(T + 'win-out/extras/tp-smoke-20s-result.json', W + 'timer-probe/smoke-20s-hidden-check-result.json')
shutil.copy(T + 'win-out/extras/tp-smoke.stdout.txt', W + 'timer-probe/smoke-20s-driver-stdout.txt')
# interactive session facts
for f in ('desk.txt', 'cr.stdout.txt', 'query-user-session.txt', 'chrome-install-info.txt'):
    shutil.copy(T + 'win-out/extras/' + f, W + 'interactive-session/' + f)
# chrome-lc (full pass runs) and attempt 1 (missing extension dir)
shutil.copytree(T + 'win-out/chrome-lc', W + 'lifecycle/chrome-lc-C1-C3', dirs_exist_ok=True)
shutil.copytree(T + 'win-out/chrome-lc-attempt1-missing-extension-dir', W + 'retained-failed-attempts/chrome-lc-attempt1-missing-extension-dir', dirs_exist_ok=True)
# stale-label attempts (nothing was connected) and the valid final controls
for d in ('chrome-R1', 'chrome-R2', 'ctl2-F1', 'ctl2-F2', 'ctl2-F3'):
    shutil.copytree(T + 'win-out/final/' + d, W + 'retained-failed-attempts/' + d + '-attempt1-stale-labels', dirs_exist_ok=True)
    shutil.copy(T + 'win-out/final/' + d + '.stdout.txt', W + 'retained-failed-attempts/' + d + '-attempt1-stale-labels.stdout.txt')
shutil.copy(T + 'win-out/final/final-ctl-order.txt', W + 'retained-failed-attempts/final-ctl-attempt1-order.txt')
for d in ('chrome-R1b', 'chrome-R2b', 'ctl2-F1b', 'ctl2-F2b'):
    shutil.copytree(T + 'win-out/final2/' + d, W + 'lifecycle/' + d, dirs_exist_ok=True)
    shutil.copy(T + 'win-out/final2/' + d + '.stdout.txt', W + 'lifecycle/' + d + '.stdout.txt')
shutil.copy(T + 'win-out/final2/final-ctl2-order.txt', W + 'lifecycle/final-ctl2-order.txt')
shutil.copy(T + 'fix3-6t.log', E + 'phones/fixture3-6t-adb-output-INCOMPLETE.log')
shutil.copy(T + 'fix3-10pro.log', E + 'phones/fixture3-10pro-adb-output-INCOMPLETE.log')
for name in ('tp-540.bat', 'tp-smoke.bat', 'itask.sh', 'chrome-lc.bat', 'final-ctl.bat', 'final-ctl2.bat', 'desk.ps1', 'desk.bat',
             'killmine.ps1', 'chromeinfo.ps1', 'cr.bat', 'assemble4.py'):
    shutil.copy(T + name, E + 'scripts-used/' + name)
print('ok')

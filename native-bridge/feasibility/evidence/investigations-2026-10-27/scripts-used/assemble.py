import os, shutil, subprocess, sys
T = '/home/dev/.claude/jobs/52694ac4/tmp/'
E = '/home/dev/repos/veles-android/.claude/worktrees/otp23-investigations/native-bridge/feasibility/evidence/investigations-2026-10-27/'
for sub in ('windows-access-denied', 'chromium-source', 'provenance', 'macos', 'scripts-used', 'phones'):
    os.makedirs(E + sub, exist_ok=True)
for d in ('ssh-A1', 'direct-A1', 'direct-A2', 'direct-C', 'direct-E', 'direct-F', 'direct-G', 'chrome-P1', 'sweep-1'):
    shutil.copytree(T + 'probe/' + d, E + 'windows-access-denied/' + d, dirs_exist_ok=True)
pairs = {
    'conn-2be02e41.txt': 'phones/phone-6t-bluetooth_manager-connection-history-filtered.txt',
    'conn-864a3db0.txt': 'phones/phone-10pro-bluetooth_manager-connection-history-filtered.txt',
    'fix-6t.log': 'phones/fixture-6t-instrument.log',
    'fix-10pro.log': 'phones/fixture-10pro-instrument.log',
    'src/tag-mojom.txt': 'chromium-source/155.0.8059.85-web_scheduler_tracked_feature.mojom',
    'src/tag-rtc.txt': 'chromium-source/155.0.8059.85-rtc_data_channel.cc',
    'src/tag-port.txt': 'chromium-source/155.0.8059.85-native_message_port.cc',
    'src/fs.txt': 'chromium-source/main-frame_scheduler_impl.cc',
    'src/sp2.txt': 'chromium-source/main-scheduling_policy.h',
    'src/third_party_blink_renderer_platform_scheduler_main_thread_page_scheduler_impl.cc.txt': 'chromium-source/main-page_scheduler_impl.cc',
}
for src, dst in pairs.items():
    shutil.copy(T + src, E + dst)
for name in ('runA.bat', 'runB.bat', 'sweep.bat', 'chrome1.bat', 'tl.py', 'dec.py', 'prov.py', 'assemble.py'):
    shutil.copy(T + name, E + 'scripts-used/' + name)
out = subprocess.run([sys.executable, '-I', T + 'prov.py'], capture_output=True, check=True).stdout
open(E + 'provenance/session-model-provenance.json', 'wb').write(out)
print('ok')

import json
r = json.load(open('/home/dev/.claude/jobs/52694ac4/tmp/mac-out/result-540.json'))
for k in ('chrome_version_from_cdp', 'user_agent', 'extension_id', 'hidden_s', 'browser_pid', 'setup_check', 'launch_command_flags'):
    print(k, '=', r.get(k))
for label in ('port', 'control'):
    log = r['raw_logs'][label]
    t = log['timers']
    print(label, 'visibility history:', log['visibility'], 'disconnects:', log['disconnects'])
    gaps = [round(b - a) for a, b in zip(t, t[1:])]
    print(label, 'timer count', len(t), 'first 8 gaps', gaps[:8], 'last 6 gaps', gaps[-6:])
    print(label, 'perf range s', round(t[0] / 1000), round(t[-1] / 1000))
print('hidden_start->read_back s', (r['read_back_epoch_ms'] - r['hidden_start_epoch_ms']) / 1000)

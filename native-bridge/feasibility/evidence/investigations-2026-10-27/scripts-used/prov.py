import json, hashlib, collections, sys
tp = '/home/dev/.claude/projects/-home-dev-repos-veles-android--claude-worktrees-otp23-investigations/52694ac4-1255-4eea-8273-bb7ff12298f0.jsonl'
raw = open(tp, 'rb').read()
models, versions, sessions, entrypoints, stop = (collections.Counter() for _ in range(5))
lines = raw.decode('utf-8').splitlines()
for line in lines:
    try:
        o = json.loads(line)
    except ValueError:
        continue
    if o.get('sessionId'):
        sessions[o['sessionId']] += 1
    if o.get('version'):
        versions[o['version']] += 1
    if o.get('entrypoint'):
        entrypoints[o['entrypoint']] += 1
    m = o.get('message') or {}
    if o.get('type') == 'assistant' and isinstance(m, dict) and m.get('model'):
        models[m['model']] += 1
st = json.load(open('/home/dev/.claude/jobs/52694ac4/state.json'))
out = {
    'source': 'Claude Code session transcript + background job state read from disk (not model self-report)',
    'transcript_snapshot_sha256': hashlib.sha256(raw).hexdigest(),
    'transcript_snapshot_bytes': len(raw),
    'transcript_snapshot_lines': len(lines),
    'assistant_message_model_counts': dict(models),
    'claude_code_versions_seen': dict(versions),
    'session_ids_seen': dict(sessions),
    'entrypoints_seen': dict(entrypoints),
    'job_state_respawnFlags': st.get('respawnFlags'),
    'job_template': st.get('template'),
    'note': 'The transcript keeps growing after this snapshot; counts and hash describe the bytes read at capture time.',
}
json.dump(out, sys.stdout, indent=2)
print()

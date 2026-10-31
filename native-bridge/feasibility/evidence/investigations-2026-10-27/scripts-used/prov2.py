import json, glob, hashlib, collections, os, sys, time
files = []
for p in glob.glob('/home/dev/.claude/projects/*veles-android*/*.jsonl'):
    if time.time() - os.path.getmtime(p) < 36 * 3600:
        files.append(p)
out = {'source': 'Claude Code session transcripts read from disk (assistant message.model as returned by the API); not self-report',
       'captured_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'sessions': []}
for p in sorted(files):
    raw = open(p, 'rb').read()
    models, versions, sids = collections.Counter(), collections.Counter(), collections.Counter()
    first_user = None
    for line in raw.decode('utf-8', 'replace').splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get('sessionId'):
            sids[o['sessionId']] += 1
        if o.get('version'):
            versions[o['version']] += 1
        m = o.get('message')
        if o.get('type') == 'assistant' and isinstance(m, dict) and m.get('model'):
            models[m['model']] += 1
    out['sessions'].append({'transcript': os.path.basename(p), 'project_dir': os.path.basename(os.path.dirname(p)),
                            'sha256_at_capture': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                            'assistant_message_model_counts': dict(models), 'claude_code_versions': dict(versions),
                            'session_ids': dict(sids)})
json.dump(out, sys.stdout, indent=2)
print()

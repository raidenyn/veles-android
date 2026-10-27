import json, subprocess, sys
full = json.loads(subprocess.run([sys.executable, '-I', '/home/dev/.claude/jobs/52694ac4/tmp/prov2.py'], capture_output=True, check=True).stdout)
keep = ('52694ac4', '0631948e', '2fe20385')
full['sessions'] = [s for s in full['sessions'] if s['transcript'][:8] in keep]
full['note'] = ('52694ac4 = first investigation session; 0631948e = its continuation/follow-up (Claude Code 2.1.310 then 2.1.311); '
                '2fe20385 = the independent reviewer, shown for contrast. "<synthetic>" entries are harness-generated, not model output. '
                'Transcripts keep growing; counts and hashes describe the bytes read at capture time. The earlier session-model-provenance.json '
                'counted 234 at its capture time; the review observed 241 later in the same transcript: counts differ only by capture time.')
json.dump(full, sys.stdout, indent=2)
print()

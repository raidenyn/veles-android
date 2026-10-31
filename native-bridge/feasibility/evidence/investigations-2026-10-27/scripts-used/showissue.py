import json, sys
for n in sys.argv[1:]:
    d = json.load(open('/home/dev/.claude/jobs/52694ac4/tmp/issues/issue-%s.json' % n))
    print('=' * 30, '#%s' % d['number'], d['state'], 'assignees=%s' % [a['login'] for a in d['assignees']], d['title'])
    print(d['body'])
    for c in d['comments']:
        print('--- comment by', c['author']['login'], c.get('createdAt'))
        print(c['body'])

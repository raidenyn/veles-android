import json, glob, os, sys
root = sys.argv[1]
rows = []
for d in glob.glob(root + '/*'):
    data = json.load(open(d + '/lifecycle.json'))
    m, r = data['meta'], data['results'][0]
    rows.append((m['started_utc'], os.path.basename(d), m['finished_utc'], r['status'], r['error_class']))
for s, n, f, st, ec in sorted(rows):
    print(n, s[11:19], f[11:19], st, ec)

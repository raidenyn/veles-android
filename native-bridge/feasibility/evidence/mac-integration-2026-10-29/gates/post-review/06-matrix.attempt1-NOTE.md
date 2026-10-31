`06-matrix.attempt1-mislabelled.log` is kept as written. Every command in it ran exactly as logged and
every exit code is real. The problem is one label: `doc_unchanged_vs_pre_write_exit=1` comes from
`git diff --quiet --exit-code -- ../../docs/otp-23-requirements-matrix.md`, which compares the doc
against the index/HEAD, not against its content before the write. The doc is intentionally modified
relative to HEAD, so exit 1 there says nothing about `render --write`.

`06-matrix.log` reruns the gate with a correct before/after sha256 comparison of the doc.

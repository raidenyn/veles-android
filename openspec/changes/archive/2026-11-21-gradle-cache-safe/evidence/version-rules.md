# Plugin 0.4.14 version rules (bytecode + harness runs)

Task 2, Step 1. Source of truth: `javap -p -c` on
`~/.gradle/caches/modules-2/files-2.1/com.gladed.androidgitversion/gradle-android-git-version/0.4.14/*/gradle-android-git-version-0.4.14.jar`
(`AndroidGitVersion`, `AndroidGitVersionExtension`, `AndroidGitVersionExtension$Results`,
`AndroidGitVersionExtension$CodePart` and the closures) and plugin-only runs of the task in a
throwaway repository (`/tmp/opencode/probe2.sh`, output reproduced below). Documentation was not used.

## Task and extension

`AndroidGitVersion.apply(project)` does exactly three things:

1. `project.extensions.create("androidGitVersion", AndroidGitVersionExtension, project)`;
2. `project.task("androidGitVersion") { group = "Android"; description = "Print version name and
   version code"; doLast { println "androidGitVersion.name\t${project.androidGitVersion.name()}";
   println "androidGitVersion.code\t${project.androidGitVersion.code()}" } }`;
3. registers the same two one-line tasks `androidGitVersionName` and `androidGitVersionCode`.

So the exact output under `-q` is two stdout lines, name first, tab-separated:

```text
androidGitVersion.name<TAB><name>
androidGitVersion.code<TAB><code>
```

**The Android plugin is not required.** Nothing in `apply`, the extension or the task looks at
`com.android.*`; the extension only needs the `Project` (for `projectDir` and the logger). The parity
harness therefore builds no AGP.

## Defaults (extension constructor)

| Setting | Default |
| --- | --- |
| `prefix` | `""` |
| `tagPattern` | `""` (means `^<prefix>[0-9]+.*`) |
| `onlyIn` | `""` (no path filter) |
| `multiplier` | `null` |
| `parts` | `null` |
| `baseCode` | `0` |
| `commitHashLength` | `7` (validated into `4..40`, message says `4..20`) |
| `hideBranches` | `["master", "release"]` |
| `untrackedIsDirty` | `false` |
| `format` | `"%tag%%-count%%-commit%%-branch%%-dirty%"` |
| `codeFormat` | `null`, which `readCodeFormat()` turns into `"MMMNNNPPP"` |

`readCodeFormat()` rejects `codeFormat` together with `parts`/`multiplier`, rejects a format longer
than 9 characters, and throws `Unrecognized char <c> in codeFormat` for a letter outside
`M N P B A X R`.

## Repository discovery

`scan()` runs `new FileRepositoryBuilder().findGitDir(project.projectDir).build()` — the project
directory **of the project the extension is applied to** (here always `android/app`), walking up to
the enclosing repository. Failures log `No git repository reachable from <projectDir>` and return the
default `Results`: `lastVersion = "unknown"`, `revCount = 0`, `dirty = false`. If `HEAD` does not
resolve to a commit the same default is returned. `name()` short-circuits on `"unknown"`.

`code()` still runs on those defaults: `versionParts("unknown")` splits on `[^0-9]+` into an empty
list, so every numeric part is absent and, in Groovy, `intUnbox(null)` yields `0` — every part
contributes zero, so the code is `0` (plus `baseCode`). Hence `unknown` / `0`.

## Tag selection

`getTagInfos()`:

- `tagPattern` empty (the default) means the search pattern is `^<prefix>[0-9]+.*`; Groovy `matches`
  is a whole-string match, so `android-v1.2.3`, `android-v1.2.3-rc1` and `android-v1.2` all match and
  `foo1.2.3` does not.
- Every tag ref is parsed with JGit (`git.tagList()`); a **lightweight** tag gives the commit id and
  the shortened ref name; an **annotated** tag gives the peeled commit id and the tag object's own
  `tag` header (the short name). A candidate must match the pattern **and** start with `prefix`.
- Because the name kept is the short name in both cases, annotated tags participate exactly like
  lightweight ones (confirmed by the `annotated-tag` probe below).

`scan()` then walks commits from `HEAD` (`RevWalk`, `markStart(parseCommit(head))`, in walk order):

- the first commit that carries at least one candidate tag ends the walk;
- `revCount` counts the commits walked **before** that commit, so a tag exactly on `HEAD` gives `0`;
- `lastVersion` is that commit's tag names (prefix stripped), sorted by `versionParts(strippedName)`
  compared element-wise over the shorter list and then `.last()`. On equal numeric parts the plugin's
  fallback compares `b.size() <=> a.size()`, so a **longer** name sorts first and `.last()` keeps the
  **shortest** of two numerically equal names (`1.2.3` beats `1.2.3-rc1`); otherwise the highest
  version on that commit wins;
- when no commit carries a candidate tag the walk still counts every reachable commit and then sets
  `lastVersion = "untagged"` with that total as `revCount`.

`onlyIn` (empty here) only gates whether a commit without a tag counts as a relevant change; with the
default every commit counts.

## Dirty rule

`dirty = git.status().call().hasUncommittedChanges() || (untrackedIsDirty && !status.untracked.isEmpty())`.
With the default `untrackedIsDirty = false`, only staged/unstaged changes, additions and deletions of
**tracked** paths make the tree dirty; untracked files and ignored files never do. JGit reads
`.git/info/exclude` like git does.

## Name composition (`name()`)

`format` defaults to `"%tag%%-count%%-commit%%-branch%%-dirty%"`. Each entry of the substitution map
is applied as `name.replaceAll("%([^%]*)<key>([^%]*)%") { group1 + value + group2 }`, which is why the
default template writes the separator *inside* the second and later placeholders (`%-count%`): the
hyphen is captured and re-emitted when the part is present, and a part that is not substituted leaves
its `%-part%` behind for the final `name.replaceAll("%[^%]+%", "")`. The map is filled in this order:

1. `tag` → `lastVersion` (already prefix-stripped);
2. `describe` → `git describe --tags` output (defaults to `"unknown"`; the default format has no
   `%describe%` and it never affects the result);
3. only when `revCount > 0`:
   - `count` → `revCount`;
   - `commit` → the **first `commitHashLength` (7) hex characters** of the `HEAD` commit id;
   - `branch` → the current branch, **omitted entirely** when the branch is in `hideBranches`
     (`master`, `release`), otherwise `branch.replaceAll("[^a-zA-Z0-9.-]", "_")`;
4. `dirty` → `dirty` when the tree is dirty;
5. every remaining `%<something>%` placeholder is deleted.

The net effect is a name whose parts are joined by `-`: `lastVersion`, then `revCount`, `<7 hex>` and
the sanitized branch when `revCount > 0`, then `dirty`.

Consequences, all observed: a tag on `HEAD` prints the bare version; commits after a tag append
`-<revCount>-<7-hex>`; a non-hidden branch appends `-<sanitized-branch>`; a dirty tree appends
`-dirty`; a **detached HEAD** makes JGit return the full 40-character commit id as the branch name, so
the name carries `-<40 hex>` (see the probe); `untagged` gets the same `-count-commit`/branch suffix
because the count is non-zero.

## Code arithmetic (`code()` with `MMNNPP`)

`readCodeFormat()` turns the format into runs of equal letters with widths:
`MMNNPP` → `[(MAJOR, 2), (MINOR, 2), (PATCH, 2)]`. `Results.addCodePart(acc, part, width)` maps a part
to `getVersionPart(i)` (the i-th numeric group of `lastVersion`, `0` when absent):

- `M` → part 0, `N` → part 1, `P` → part 2, `R` → `revCount`, `X` → `0`;
- a value that does not fit the width (`10^width <= value`) raises
  `Not enough room for <part> in <codeFormat> width=<width>`;
- the fold is `code = acc * 10^width + value`, starting from `baseCode` (`0`).

Therefore with `MMNNPP`, `version = M.N.P` gives `code = ((M*100 + N)*100 + P)`, e.g. `1.2.3` →
`10203`, `0.9.9` → `909`, `9.9.9` → `90909`, a bare `1.2` → `10200`, and any non-numeric
`lastVersion` (`untagged`, `unknown`) → `0`. `versionCode = maxOf(code, 1)` is the app's rule, not
the plugin's.

## Probe: plugin-only runs in a throwaway repository

State built with plain git (commit messages are synthetic, dates fixed); command
`<repo>/gradlew --project-dir <repo>/.gv-plugin -q androidGitVersion`:

```text
== on-tag            androidGitVersion.name  1.2.3                                            code 10203
== after-tag         androidGitVersion.name  1.2.3-3-7c4a7c3                                  code 10203
== dirty             androidGitVersion.name  1.2.3-3-7c4a7c3-dirty                            code 10203
== branch            androidGitVersion.name  1.2.3-3-7c4a7c3-feature_x                        code 10203
== back-on-master    androidGitVersion.name  1.2.3-3-7c4a7c3                                  code 10203
== detached          androidGitVersion.name  1.2.3-3-7c4a7c3-7c4a7c30ed079d498068f2e45da9cc4355f21db4  code 10203
== two-tags          androidGitVersion.name  1.2.4                                            code 10204
== prerelease        androidGitVersion.name  1.2.3-rc1                                        code 10203
== annotated-tag     androidGitVersion.name  1.2.5                                            code 10205
== no-tag            androidGitVersion.name  untagged-4-7c4a7c3                              code 0
```

(The raw probe output separates name and code on their own lines; it is collapsed here for width.)
`two-tags` had `android-v1.2.3` and `android-v1.2.4` on the same commit; `prerelease` replaced the
only tag with `android-v1.2.3-rc1` (the probe's `HEAD` was detached from the tagged commit by then, so
this row has one candidate only).

**Pre-release tags are accepted** (`^android-v[0-9]+.*`), so the harness keeps the `prerelease` state.
**Annotated tags are accepted** as well; the harness states use lightweight tags because the
plugin and `git for-each-ref` agree on both, and the rule above is recorded here.

## Repository states that this task must reproduce

| State | Name | Code |
| --- | --- | --- |
| `android-v1.2.3` on `HEAD` | `1.2.3` | `10203` |
| 3 commits after that tag | `1.2.3-3-<7 hex>` | `10203` |
| dirty tracked file | as above + `-dirty` | `10203` |
| on branch `feature/x` | `1.2.3-3-<7 hex>-feature_x` | `10203` |
| detached `HEAD` | `1.2.3-3-<7 hex>-<40 hex>` | `10203` |
| no `android-v*` tag | `untagged-<N>-<7 hex>` | `0` |
| two tags on one commit | the higher version | its code |
| pre-release tag on the tagged commit (3 commits back) | `1.2.3-rc1-3-<7 hex>` | `10203` |
| pre-release tag on `HEAD` | `1.2.3-rc1` | `10203` |

The linked-worktree limitation recorded in `task-1-report.md` (JGit cannot resolve `HEAD` through a
linked worktree's `commondir`, so plugin 0.4.14 prints `unknown` / `0` there) is an environment fact
of the plugin, not a rule; the parity harness runs in ordinary throwaway repositories.

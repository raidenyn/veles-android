# Review record: brainstorm — Opus

- Artifact: brainstorm (`brainstorm.md`)
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: none (brainstorm is the first planning file)

## Pass 1

### Round 1 — approve

Reviewed content: brainstorm sha256 a2f69e0155961eb9ceb4c9506dd9fc655d9fa93689824d6fb650a096154c6b2e (recomputed from the file by reversing the five fixes below; it was not hashed before the fixes)

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | medium | The trigger state is wider than "after an update" (reboot, force-stop, system stop, cold start, `onDestroy` reset); the copy and doc must not claim an update only, and the Task Manager Stop case must be accepted or not. | fixed: Key Decisions now lists the causes, requires copy and doc wording that does not claim an update only, and accepts the generic line for Task Manager Stop. |
| 2 | medium | The new text is not given and it is not said whether it is a new resource or an edit. | fixed: a new resource `sharing_stopped` with proposed text; `sharing_enabled_unavailable` stays for the other states. |
| 3 | low | The real-device check omits the service state the issue asks to read. | fixed: the check adds `dumpsys activity services` before and after Enable transport. |
| 4 | low | "Every combination" and the function's return type are unspecified. | fixed: 14 combinations (2 × 7) stated, with 13 unchanged; the function returns string resource ids. |
| 5 | low | The spec's Purpose does not cover the card; adding a card requirement leaves it stale. | deferred to the `specs` artifact; resolved there by a new capability `sharing-card-copy`, because a delta cannot widen a Purpose (see `design.md`, which supersedes the brainstorm's spec decision). |
| 6 | low | The follow-up change is archived; the brainstorm gives no path. | fixed: the archived path is named. |

Approved content: brainstorm sha256 a1cf3d73b6f5cfa15b1e49d067453ad7ec9795b9ea66654f529fd2054522f6d9 — re-pointed from a2f69e01… after 5 non-triggering minor fixes (findings 1, 2, 3, 4 and 6 above).

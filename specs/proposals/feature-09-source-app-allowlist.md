# Feature: Per-bank source app allowlist

**Type:** New feature (privacy/correctness)
**Priority:** Medium-high
**Effort:** Small-medium (~1–2 days)

## Motivation

The handler chain currently runs every bank's regexes against **every notification from every
app on the device**. Two problems:

1. **False positives.** `Message.source` (the posting package) is captured but never consulted —
   `RegexMessageHandler` only looks at `message.text`. A chat message quoting a bank SMS, or any
   app whose notification happens to satisfy all three regexes, gets intercepted, *cancelled*
   (the original notification is removed!), and re-posted as a Veles OTP. Cancelling someone's
   Signal message because it looked like a bank SMS is a serious misfire.
2. **Principle of least processing.** Privacy-conscious users reasonably want a guarantee that
   Veles only ever *evaluates* notifications from their SMS app / banking apps, even though the
   listener technically receives everything.

## Design

1. **Config.** Add `sourcePackages: String?` to `BankHandlerConfig` — comma-separated package
   list (null/empty = any source, preserving current behaviour for existing rows; migration is a
   no-op column add).
2. **Matching.** `RegexMessageHandler` gets the allowed set at construction; first check in
   `onMessageReceived`: `if (allowedSources.isNotEmpty() && message.source !in allowedSources)
   return FILTERED`. Cheap short-circuit before any regex work.
3. **UI.** In `BankConfigEditScreen`, an "Only from these apps" picker: query
   `PackageManager.getInstalledApplications`, show launcher apps with icons + a search box,
   multi-select chips. Store package names; render app labels. A "Common choices" shortcut
   pre-suggests the default SMS app (`Telephony.Sms.getDefaultSmsPackage`).
4. **Test Screen interplay.** Veles's own package must always be allowed implicitly (or the Test
   Screen's harness notifications would be filtered before reaching the chain) — handle in the
   short-circuit: `message.source == ownPackage` bypasses the source check.
5. **Global variant (phase 2).** An app-level allowlist ("Veles only processes: …") applied in
   `NotificationListener` before building `Message`, complementing per-bank lists. Also worth
   revisiting the manifest `default_filter_types` meta-data to narrow OS-level delivery.

## Testing

- Unit tests: wrong source → `FILTERED` without regex evaluation (verify via a regex that would
  match); empty allowlist → unchanged behaviour; own-package bypass.
- ViewModel/UI test for the picker state (selected packages round-trip to the config row).

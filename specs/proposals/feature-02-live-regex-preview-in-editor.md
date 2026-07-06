# Feature: Live regex match preview in the bank config editor

**Type:** New feature (UX)
**Priority:** High — directly de-risks the core workflow
**Effort:** Small-medium (~1–2 days)

## Motivation

Writing three coordinated regexes blind is the hardest thing Veles asks of a user. Today the loop
is: edit config → save → navigate to Test Screen → paste sample → send → check badge → navigate
back. The Test Screen also only reports a binary matched/not-matched for the *whole chain*, not
which regex failed or what was captured.

## Design

Extend `BankConfigEditScreen` with a **sample message** field and live extraction preview:

1. New state in `BankConfigEditState`: `sampleText: String`, plus derived
   `otpPreview / moneyPreview / merchantPreview: MatchPreview` where
   `MatchPreview = NoMatch | Invalid(reason) | Match(groups: List<String>)`.
2. On every keystroke (debounced ~150 ms in the ViewModel), compile each regex (`runCatching`)
   and run it against the sample. Under each regex field show:
   - ✗ red "invalid pattern: …" if compilation fails,
   - ○ neutral "no match" if it compiles but doesn't match,
   - ✓ green captured groups, labelled (`id`, `otp` / `currency`, `amount` / `merchant`).
3. A summary row mirrors `RegexMessageHandler`'s actual rule: "This message **would be accepted**"
   only when all three match — reusing the exact same extraction logic (pull the group-picking
   code out of `RegexMessageHandler` into a shared pure function so the preview can never drift
   from production behaviour).
4. Persist the last sample per config (nullable `sampleText` column on `BankHandlerConfig`) so
   returning to edit a bank keeps its reference SMS — this doubles as living documentation of
   what each bank's message looks like.

## Relationship to the Test Screen

The Test Screen stays as the end-to-end harness (real notification → listener → chain). This
feature covers the inner loop; consider linking "Test end-to-end" from the editor which pre-fills
the Test Screen with the sample.

## Testing

- Pure-function tests for the shared extractor (invalid regex, partial match, full match).
- ViewModel test: sample + regex changes produce the right `MatchPreview`s.
- Compose test: accepted-summary row appears only when all three previews are matches.

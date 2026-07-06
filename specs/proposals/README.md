# Proposals

Standalone improvement and feature proposals for Veles, from a codebase review on 2026-07-06.
Each file is self-contained: motivation, design sketch, and testing notes.

## Technical improvements

| # | Proposal | Priority | Notes |
|---|----------|----------|-------|
| [tech-01](tech-01-fix-pending-intent-collision.md) | Fix PendingIntent collision in Copy action | High | Real bug: concurrent notifications copy the wrong OTP |
| [tech-02](tech-02-stop-logging-sensitive-data.md) | Stop logging OTPs and notification content | High | Security/privacy |
| [tech-03](tech-03-reload-handlers-on-config-change.md) | Hot-reload handlers on config change | High | Config edits currently need a service restart |
| [tech-04](tech-04-regex-and-parse-error-hardening.md) | Harden against bad regexes / parse failures | High | User configs can crash the listener |
| [tech-05](tech-05-clipboard-hygiene.md) | Clipboard hygiene (sensitive flag, auto-clear) | Med-high | Prereq for feature-05 |
| [tech-06](tech-06-dependency-injection.md) | Introduce Hilt | Medium | Removes factory/singleton boilerplate |
| [tech-07](tech-07-release-build-hardening.md) | Release build hardening (R8, versioning, CI) | Medium | |

## New features

| # | Proposal | Priority | Depends on |
|---|----------|----------|------------|
| [feature-01](feature-01-otp-history.md) | OTP & transaction history | High | — |
| [feature-02](feature-02-live-regex-preview-in-editor.md) | Live regex preview in config editor | High | — |
| [feature-03](feature-03-config-import-export.md) | Config import/export (JSON) | Med-high | tech-04 |
| [feature-04](feature-04-bank-template-gallery.md) | Built-in bank template gallery | Medium | feature-03 format |
| [feature-05](feature-05-auto-copy-mode.md) | Auto-copy mode (zero-tap OTP) | Med-high | tech-05 |
| [feature-06](feature-06-spending-insights.md) | Spending insights | Medium | feature-01 |
| [feature-07](feature-07-glance-widget.md) | Home-screen OTP widget (Glance) | Low-med | — |
| [feature-08](feature-08-expiry-aware-notifications.md) | Expiry-aware notifications (countdown + auto-dismiss) | Med-high | — |
| [feature-09](feature-09-source-app-allowlist.md) | Per-bank source app allowlist | Med-high | — |
| [feature-10](feature-10-suspicious-transaction-guard.md) | Suspicious transaction guard | Medium | feature-01 |

## Suggested order

tech-01 → tech-02 → tech-04 → tech-03 (correctness/security first), then feature-02 + feature-01
(core UX), then the rest as desired.

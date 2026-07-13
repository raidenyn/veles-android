# Landing Page and In-App Links - Design

**Date:** 2026-07-13
**Issue:** [#51](https://github.com/raidenyn/veles-android/issues/51)

## Goal

Publish a trustworthy, mobile-first introduction to Veles at
`https://raidenyn.github.io/veles-android/` and link its pairing and adb guidance from the
sensitive-notifications card in the Android app.

The page positions Veles as the free, open-source, offline answer to a practical gap: banks
send OTPs in inconsistent formats that standard messaging apps do not always present in a
convenient, copy-ready form, while specialized alternatives may be paid, ad-supported, or
difficult to trust. Veles transforms locally configured bank-notification formats into clean
notifications containing the code, amount, merchant, and a one-tap Copy action.

## Decisions

- Use hand-written semantic HTML and CSS under `site/`; add no framework, Jekyll, JavaScript,
  analytics, remote fonts, or build tooling.
- Use an editorial, trust-focused visual direction with restrained product accents.
- Preserve the final copy from issue #51 except for the hero headline/subtitle and the
  `#problem` section, which will use the corrected positioning agreed during design.
- Use the headline **"Bank OTPs, made usable."**
- Describe configurable templates accurately rather than claiming Veles automatically
  understands every possible message format.
- Keep all public site URLs in one Android `VelesLinks` object.
- Deploy only `site/` with the official GitHub Pages Actions flow.

## Page Architecture

The static site has three web-facing assets:

- `site/index.html`: semantic document, verbatim issue copy except for the approved hero and
  problem revisions, and stable section anchors.
- `site/style.css`: responsive layout, brand palette, light/dark schemes, navigation, and
  accessible interaction states.
- A web-ready copy of the existing launcher artwork under `site/`, used as both the visible
  logo and favicon. The source launcher asset remains unchanged.

The document order is:

1. Sticky top navigation.
2. Hero with logo, headline, introduction, release/source actions, and project metadata.
3. `#problem`: incompatible and inconvenient bank OTP formats, the Veles transformation,
   the supplied before/after example, and locally configurable templates.
4. `#privacy`: no network permission, no notification storage, no accounts/tracking, and
   public reproducible source and release verification.
5. `#free`: no ads, payments, subscriptions, or premium tier.
6. `#pairing`: Android 15 redaction, why the companion-device profile is necessary, and how
   Veles verifies the grant.
7. `#adb`: manufacturer caveat, permission commands, verification, and when to repeat them.
8. Footer with license, source, issue tracker, and the origin of the Veles name.

The hero and problem-section revisions will make these points without changing the scope of
the app:

- Standard messaging apps do not consistently recognize or make all bank OTP formats easy
  to copy.
- Veles supports additional formats through user-managed local templates.
- Veles closes the gap left by alternatives that are paid, ad-supported, or not openly
  verifiable.
- Processing remains local, free, and open source.

## Layout and Visual Language

The desktop page uses a centered 760-840px reading column with selected examples and
callouts extending slightly beyond it. Mobile uses the full available width with compact
gutters. The sticky navigation becomes a horizontally scrollable single row on narrow
screens, avoiding a JavaScript menu.

The visual system follows the app palette:

- Light canvas: warm off-white `#FBFAF3`; primary emerald `#1E6B47`; dark text based on the
  app's on-background color; gold `#8A6D14` used sparingly.
- Dark canvas: `#12140F`; primary accent `#8FDBB0`; gold `#D8B84A`; text based on the app's
  dark on-background color.
- Headings use a strong system serif stack; body copy uses a system sans-serif stack. No
  font files are fetched.
- Most sections rely on spacing, rules, and typography rather than generic cards. The OTP
  transformation, privacy guarantees, and adb commands use subtle tinted callouts.

Every anchored section uses `scroll-margin-top` so direct cold loads of `#pairing` and
`#adb` land below the sticky header. Links and controls have visible keyboard-focus states,
adequate contrast, and comfortable touch targets. The dark variant follows
`prefers-color-scheme`; no manual theme toggle is added.

## Android Integration

Add `VelesLinks` under `me.nagaev.veles.common` as the single source of truth for:

```text
SITE    = https://raidenyn.github.io/veles-android/
PAIRING = https://raidenyn.github.io/veles-android/#pairing
ADB     = https://raidenyn.github.io/veles-android/#adb
```

`SensitiveNotificationsCard` obtains `LocalUriHandler.current` and delegates both external
links to the user's default browser. This remains a local Compose UI concern; no callback is
threaded through `PermissionsScreen`, `PermissionsActions`, or the view model.

- A **"Why is pairing needed?"** text link appears directly below the existing pairing
  explanation whenever that explanation is rendered (`NotGranted` or `Unknown` with CDM
  support).
- `FallbackSection` replaces the README wording with a **"Full guide"** link to `#adb`.
  It appears whenever the fallback content is visible.
- The copyable adb command and all grant, verification, and fallback behavior remain
  unchanged.
- `Verifying` and `ApplyingGrant` continue to suppress actions. `Granted` and
  `NotApplicable` continue to hide the entire card.

Opening a URI is delegated to Android. The app does not gain the `INTERNET` permission; the
browser, not Veles, performs network access.

## Stable Test Selectors

Add two constants to `TestTags`:

- `SENSITIVE_PAIRING_GUIDE`
- `SENSITIVE_ADB_GUIDE`

These identify the new pairing and adb links independently of visible copy.

## Deployment

Add `.github/workflows/pages.yml` with:

- Triggers: pushes to `master` limited to `site/**`, plus `workflow_dispatch`.
- Permissions: `contents: read`, `pages: write`, and `id-token: write`.
- Official sequence: checkout, `actions/configure-pages`,
  `actions/upload-pages-artifact` with `path: site`, then `actions/deploy-pages`.
- A Pages deployment environment exposing the deployment URL.
- Concurrency that serializes Pages deployments without cancelling one already running.

The maintainer must perform the one-time repository setting change: **Settings -> Pages ->
Source -> GitHub Actions**. The workflow cannot configure this setting.

## Failure Handling

- The static page has no runtime application dependency, client-side state, or JavaScript
  failure path. Content remains readable if decorative styling is unavailable.
- Missing network access is handled by the external browser; Veles does not attempt to test
  connectivity or show a duplicate error flow.
- Long navigation labels remain horizontally reachable on narrow screens.
- Direct anchor navigation accounts for the sticky header.
- A failed Pages deployment leaves the prior successful deployment intact through serialized,
  non-cancelling workflow runs.

## Testing and Validation

### Static site

- Confirm all required sections and IDs exist: `problem`, `privacy`, `free`, `pairing`, and
  `adb`, with the hero remaining unanchored as specified.
- Validate semantic HTML, local stylesheet/icon references, all external destinations, and
  the issue copy (allowing only the approved hero/problem revisions).
- Serve `site/` with a local static HTTP server and inspect phone and desktop widths in both
  light and dark schemes.
- Verify keyboard focus, contrast, horizontal mobile navigation, sticky-header offsets, and
  cold loads of `/#pairing` and `/#adb`.

### Android app

- Compose tests assert the pairing guide is present in card states that show the pairing
  explanation and absent when actions/card content are suppressed.
- Compose tests assert the adb guide is hidden with collapsed fallbacks and present when
  fallbacks are expanded or immediately required.
- Click tests use an injectable or test-provided URI handler to verify exact `PAIRING` and
  `ADB` destinations without launching a real browser.
- Existing unit and instrumented suites continue to pass.

## Out of Scope

- Rewriting the README sensitive-notifications section.
- A custom domain, custom 404 page, service worker, offline cache, analytics, JavaScript, or
  content-management/build system.
- Adding Android network permissions or an in-app browser.
- Changing sensitive-notification grant, verification, or adb-copy behavior.
- Claiming automatic support for formats that have not been configured by a built-in or
  user-created template.

## Acceptance Criteria

- A push to `master` touching `site/**` can deploy the page to the specified GitHub Pages
  URL after the one-time Pages source setting is enabled.
- The page contains the six requested content areas, approved revised positioning, stable
  anchors, brand-consistent light/dark styles, and a phone-first responsive layout.
- Cold external links to `/#pairing` and `/#adb` land at visible section headings.
- The sensitive-notifications card opens the pairing and adb guide URLs in the default
  browser from the expected states.
- Both links have stable test tags and Compose coverage.
- Veles still declares no internet permission, and existing tests continue to pass.

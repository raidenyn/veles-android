# Landing Page and In-App Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a mobile-first GitHub Pages introduction to Veles and link its pairing and adb sections from the Android sensitive-notifications card.

**Architecture:** A framework-free `site/` directory owns the public HTML, CSS, and copied launcher asset; a dedicated GitHub Actions workflow deploys that directory. The Android app centralizes the public URL contract in `VelesLinks` and opens anchor URLs through Compose's `LocalUriHandler`, without adding network permission or view-model state.

**Tech Stack:** Semantic HTML5, responsive CSS with `prefers-color-scheme`, GitHub Pages Actions, Kotlin, Jetpack Compose Material 3, Compose UI tests, Gradle.

## Global Constraints

- Work on `feature/issue-51-landing-page`; do not implement on `master`.
- Use hand-written HTML/CSS only: no framework, Jekyll, JavaScript, analytics, remote fonts, or web build tooling.
- Use `https://raidenyn.github.io/veles-android/` as the canonical site root.
- Preserve issue #51 copy verbatim except for the approved hero headline/subtitle and `#problem` positioning.
- Use the headline `Bank OTPs, made usable.` and never claim automatic support for an unconfigured format.
- Keep `#problem`, `#privacy`, `#free`, `#pairing`, and `#adb` stable.
- Preserve the app's no-network guarantee; do not add `android.permission.INTERNET`.
- Preserve all existing sensitive-notification grant, verification, fallback, and adb-copy behavior.
- Use the launcher icon and brand palette: `#1E6B47`, `#8FDBB0`, `#8A6D14`, `#D8B84A`, `#FBFAF3`, and `#12140F`.
- Deploy only `site/` via the official Pages Actions flow.

## File Structure

- Create `site/index.html`: semantic content, navigation, anchors, calls to action, and favicon metadata.
- Create `site/style.css`: editorial layout, responsive navigation, accessible interactions, and light/dark palettes.
- Create `site/veles-icon.webp`: copied web asset from `app/src/main/res/mipmap-xxxhdpi/ic_launcher.webp`.
- Create `.github/workflows/pages.yml`: GitHub Pages deployment for `site/`.
- Create `app/src/main/java/me/nagaev/veles/common/VelesLinks.kt`: canonical site and anchor URLs.
- Modify `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt`: stable selectors for both guide links.
- Modify `app/src/main/java/me/nagaev/veles/permissions/ui/components/SensitiveNotificationsCard.kt`: render and open both external links.
- Modify `app/src/androidTest/java/me/nagaev/veles/permissions/ui/SensitiveNotificationsCardComposeTest.kt`: exact click-destination and state tests.
- Modify `app/src/androidTest/java/me/nagaev/permissions/ui/VelesPermissionsAppTests.kt`: app-level link-presence coverage.

---

### Task 1: Static Editorial Landing Page

**Files:**
- Create: `site/index.html`
- Create: `site/style.css`
- Create: `site/veles-icon.webp`
- Source asset: `app/src/main/res/mipmap-xxxhdpi/ic_launcher.webp`

**Interfaces:**
- Consumes: Existing launcher artwork and the palette defined in `app/src/main/java/me/nagaev/veles/common/ui/theme/Colors.kt`.
- Produces: The canonical document at `/` and stable targets `/#problem`, `/#privacy`, `/#free`, `/#pairing`, and `/#adb` used by Task 2.

- [ ] **Step 1: Run the structural contract before creating the page**

Run:

```bash
python3 - <<'PY'
from pathlib import Path

html = Path("site/index.html").read_text()
for anchor in ("problem", "privacy", "free", "pairing", "adb"):
    assert f'id="{anchor}"' in html, anchor
assert "Bank OTPs, made usable." in html
assert "releases/latest" in html
assert "gh attestation verify veles-X.Y.Z.apk" in html
assert Path("site/style.css").is_file()
assert Path("site/veles-icon.webp").is_file()
PY
```

Expected: FAIL with `FileNotFoundError: site/index.html`.

- [ ] **Step 2: Create the semantic HTML document**

Create `site/index.html` with this complete structure and copy. The privacy, free, pairing,
adb, and footer copy is the final issue text; the hero and problem copy carry the approved
repositioning.

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Veles turns bank OTP notifications into clean, copy-ready codes. Free, open source, and entirely offline.">
  <meta name="theme-color" content="#FBFAF3" media="(prefers-color-scheme: light)">
  <meta name="theme-color" content="#12140F" media="(prefers-color-scheme: dark)">
  <title>Veles - Bank OTPs, made usable</title>
  <link rel="icon" href="veles-icon.webp" type="image/webp">
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="site-header">
    <nav class="site-nav" aria-label="Main navigation">
      <a class="brand" href="#top" aria-label="Veles home">
        <img src="veles-icon.webp" alt="" width="36" height="36">
        <span>Veles</span>
      </a>
      <div class="nav-links">
        <a href="#problem">Problem</a>
        <a href="#privacy">Privacy</a>
        <a href="#free">Free</a>
        <a href="#pairing">Pairing</a>
        <a href="#adb">adb guide</a>
      </div>
    </nav>
  </header>

  <main id="top">
    <section class="hero" aria-labelledby="hero-title">
      <img class="hero-logo" src="veles-icon.webp" alt="Veles gold bull icon" width="128" height="128">
      <p class="eyebrow">Open source · MIT · Android 13+</p>
      <h1 id="hero-title">Bank OTPs, made usable.</h1>
      <p class="lede">Veles turns bank OTP notifications that ordinary messaging apps cannot present conveniently into clean, copy-ready codes, with the amount and merchant alongside them.</p>
      <p>Bank messages come in many formats. Veles matches the templates stored on your phone, pulls out the one-time code, amount, and merchant, and re-posts them as one clean notification with a one-tap <strong>Copy</strong> button. Everything happens on your phone - nothing ever leaves it.</p>
      <div class="actions">
        <a class="button primary" href="https://github.com/raidenyn/veles-android/releases/latest">Download latest release</a>
        <a class="button secondary" href="https://github.com/raidenyn/veles-android">View source on GitHub</a>
      </div>
    </section>

    <section id="problem" aria-labelledby="problem-title">
      <p class="section-label">The problem</p>
      <h2 id="problem-title">Your bank sent the code. Your messaging app still makes you hunt for it.</h2>
      <p>Banks use inconsistent OTP formats, and standard messaging apps do not always recognize them or make the code easy to copy. You squint, memorize six digits, switch back to the app you were paying in, and hope you remembered them right. If the notification collapses, you dig through the shade to find it again.</p>
      <figure class="transformation">
        <div>
          <figcaption>Before</figcaption>
          <blockquote>“Your OTP is 674902 for payment of THB 1,250.00 to COFFEE HOUSE. Valid for 5 minutes. Do not share this code with anyone. Ref: 8842…”</blockquote>
        </div>
        <span class="arrow" aria-hidden="true">↓</span>
        <div class="clean-notification">
          <figcaption>After Veles</figcaption>
          <p><strong>674902</strong> · THB 1,250.00 → COFFEE HOUSE <span class="copy-chip">Copy</span></p>
        </div>
      </figure>
      <p>Veles extracts the three things that matter - the <strong>code</strong>, the <strong>amount</strong>, and <strong>who is charging you</strong> - cancels the noisy original, and posts a clean replacement. One tap copies the code to your clipboard. Paste, confirm, done.</p>
      <p>Specialized alternatives may charge money, show ads, or ask you to trust software you cannot inspect. Veles closes that gap: it is free, has no ads, works offline, and publishes all of its source.</p>
      <p>Banks are described by simple text-matching templates stored on your device. UOB Thailand works out of the box; you can add your own bank from the Templates screen and validate it instantly with the built-in Test screen - no coding, no waiting for a real bank SMS.</p>
    </section>

    <section id="privacy" aria-labelledby="privacy-title">
      <p class="section-label">Info safety</p>
      <h2 id="privacy-title">Your codes never leave your phone.</h2>
      <p>An app that reads banking notifications has to be held to a higher standard. Veles is built so you don't have to trust promises — you can check every claim:</p>
      <ul class="assurances">
        <li><strong>No internet — enforced by Android, not by promise.</strong> Veles does not declare the internet permission in its manifest, so the operating system itself will refuse to let it open a network connection. Your notifications physically cannot be sent anywhere.</li>
        <li><strong>Nothing is stored.</strong> Notification text is processed in memory and discarded. The only things saved on your device are your own bank templates, in a local database.</li>
        <li><strong>No accounts, no tracking.</strong> No sign-up, no telemetry, no analytics, no crash reporting, no third-party SDKs.</li>
        <li><strong>Open source and verifiable.</strong> The complete source code is public under the MIT license. Releases are built reproducibly — CI refuses to publish an APK it cannot rebuild bit-for-bit from the source you can read — and any release can be verified with one command:</li>
      </ul>
      <pre><code>gh attestation verify veles-X.Y.Z.apk --repo raidenyn/veles-android</code></pre>
    </section>

    <section id="free" class="statement" aria-labelledby="free-title">
      <p class="section-label">Free forever</p>
      <h2 id="free-title">No ads. No payments. Not now, not ever.</h2>
      <p>Veles is free software: no advertisements, no in-app purchases, no subscriptions, no "pro" tier — and no plans to ever add any. An app that reads your banking notifications must have zero monetization pressure, so this isn't a launch promo, it's a design constraint. The MIT license guarantees the code stays free.</p>
    </section>

    <section id="pairing" aria-labelledby="pairing-title">
      <p class="section-label">Android 15+</p>
      <h2 id="pairing-title">Why does Veles ask to pair with a Bluetooth device?</h2>
      <p>Starting with Android 15, the system redacts one-time codes out of notifications <em>before</em> any notification-listener app can read them. Instead of your bank's message, Veles receives only “Sensitive notification content hidden.”</p>
      <p>Android makes one exception: <strong>companion-device apps</strong> — the mechanism smartwatches use to mirror your notifications. It is the only door Android leaves open, so Veles knocks on it: to be registered as a companion app, it must pair with a nearby Bluetooth device.</p>
      <ul>
        <li><strong>Any Bluetooth device works</strong> — headphones, your car, a fitness band. The system dialog mentions a watch only because Veles has to request the "watch" companion profile; it's the only profile that carries the needed permission.</li>
        <li><strong>Veles never talks to the paired device.</strong> The pairing is a formality Android requires. No data is sent to it, read from it, or associated with it.</li>
        <li><strong>The grant is verified, not assumed.</strong> After pairing, Veles posts a hidden test notification with a random code through its own pipeline and checks whether it can read the text back. The setup card only disappears once access is <em>confirmed</em> to work.</li>
      </ul>
      <p>On Android 14 and below none of this applies — Veles never shows the pairing step there. If pairing doesn't stick on your device, see the <a href="#adb">adb fallback</a> below.</p>
    </section>

    <section id="adb" aria-labelledby="adb-title">
      <p class="section-label">Fallback guide</p>
      <h2 id="adb-title">Fallback: grant access with adb.</h2>
      <p>If companion pairing doesn't work on your device (some manufacturer builds keep redacting content even after the grant), you can give Veles the permission directly from a computer using adb. This targets <strong>only Veles</strong> — unlike disabling Enhanced notifications, it does not weaken protection for the rest of the device.</p>
      <p>You'll need a computer with <code>adb</code> and USB or wireless debugging enabled on the phone.</p>
      <ol class="steps">
        <li>
          <h3>Step 1 — OnePlus / OxygenOS only: disable system optimization.</h3>
          <p>OxygenOS blocks adb from changing per-app operations unless a developer override is on. In <em>Settings → Additional settings → Developer options</em>, find <strong>“Disable system optimization”</strong> (called “Disable permission monitoring” on older builds), turn it <strong>ON</strong>, and <strong>reboot</strong>. On other brands, skip this step.</p>
        </li>
        <li>
          <h3>Step 2 — grant the permission:</h3>
          <pre><code>adb shell appops set me.nagaev.veles RECEIVE_SENSITIVE_NOTIFICATIONS allow</code></pre>
          <p>If adb complains about user profiles:</p>
          <pre><code>adb shell cmd appops set --user 0 me.nagaev.veles RECEIVE_SENSITIVE_NOTIFICATIONS allow</code></pre>
          <p>Verify it saved:</p>
          <pre><code>adb shell appops get me.nagaev.veles RECEIVE_SENSITIVE_NOTIFICATIONS</code></pre>
          <p>Expected output: <code>RECEIVE_SENSITIVE_NOTIFICATIONS: allow</code>. Then tap <strong>Check now</strong> on the card in Veles.</p>
        </li>
        <li>
          <h3>Step 3 — optional:</h3>
          <p>Turn “Disable system optimization” back <strong>OFF</strong>; the granted permission persists.</p>
        </li>
      </ol>
      <p><strong>When you'd need to redo this:</strong> after a full uninstall + reinstall (updates in place are fine), and occasionally after major OS updates.</p>
    </section>
  </main>

  <footer>
    <p>Veles · <a href="https://github.com/raidenyn/veles-android/blob/master/LICENSE">MIT License</a> · <a href="https://github.com/raidenyn/veles-android">Source</a> · <a href="https://github.com/raidenyn/veles-android/issues">Report an issue</a></p>
    <p><em>Named after Veles, the Slavic god of cattle and wealth — the gold bull on the icon.</em></p>
  </footer>
</body>
</html>
```

- [ ] **Step 3: Add the responsive editorial stylesheet**

Create `site/style.css`:

```css
:root {
  color-scheme: light dark;
  --bg: #fbfaf3;
  --surface: #f4f3ea;
  --surface-strong: #eee7d4;
  --text: #1a1c16;
  --muted: #565d52;
  --primary: #1e6b47;
  --primary-text: #ffffff;
  --gold: #8a6d14;
  --border: #d9d8cc;
  --header-bg: rgba(251, 250, 243, 0.94);
  --shadow: 0 18px 50px rgba(31, 42, 34, 0.09);
  --content: 800px;
  --header-height: 68px;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 17px;
  line-height: 1.68;
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; background: var(--bg); color: var(--text); }
a { color: var(--primary); text-underline-offset: 0.18em; }
a:hover { text-decoration-thickness: 2px; }
a:focus-visible { outline: 3px solid var(--gold); outline-offset: 4px; border-radius: 3px; }

.site-header {
  position: sticky;
  top: 0;
  z-index: 10;
  border-bottom: 1px solid var(--border);
  background: var(--header-bg);
  backdrop-filter: blur(12px);
}

.site-nav {
  min-height: var(--header-height);
  max-width: 1120px;
  margin: 0 auto;
  padding: 0 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 28px;
}

.brand { display: inline-flex; align-items: center; gap: 10px; color: var(--text); font-weight: 800; text-decoration: none; }
.brand img { border-radius: 9px; }
.nav-links { display: flex; align-items: center; gap: 24px; white-space: nowrap; }
.nav-links a { color: var(--muted); font-size: 0.86rem; font-weight: 700; text-decoration: none; }
.nav-links a:hover { color: var(--primary); }

main, footer { width: min(calc(100% - 40px), var(--content)); margin: 0 auto; }
section { padding: 88px 0; scroll-margin-top: calc(var(--header-height) + 20px); border-bottom: 1px solid var(--border); }
.hero { min-height: calc(88vh - var(--header-height)); display: flex; flex-direction: column; justify-content: center; align-items: flex-start; border-bottom: 0; }
.hero-logo { border-radius: 28px; box-shadow: var(--shadow); margin-bottom: 28px; }
.eyebrow, .section-label { color: var(--gold); font-size: 0.76rem; font-weight: 800; letter-spacing: 0.12em; text-transform: uppercase; }
h1, h2, h3 { font-family: Iowan Old Style, Baskerville, "Times New Roman", serif; line-height: 1.08; text-wrap: balance; }
h1 { max-width: 760px; margin: 0 0 24px; font-size: clamp(3rem, 8vw, 6.3rem); letter-spacing: -0.055em; }
h2 { margin: 8px 0 26px; font-size: clamp(2.15rem, 5vw, 3.8rem); letter-spacing: -0.035em; }
h3 { margin: 0 0 8px; font-size: 1.35rem; }
p, li { max-width: 70ch; }
.lede { max-width: 62ch; color: var(--muted); font-size: clamp(1.2rem, 2.4vw, 1.5rem); line-height: 1.55; }
.actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 26px; }
.button { min-height: 48px; padding: 11px 18px; display: inline-flex; align-items: center; border: 2px solid var(--primary); border-radius: 8px; font-weight: 800; text-decoration: none; }
.button.primary { background: var(--primary); color: var(--primary-text); }
.button.secondary { color: var(--primary); }

.transformation { width: min(900px, calc(100vw - 32px)); margin: 38px 50% 38px 0; transform: translateX(calc((var(--content) - min(900px, calc(100vw - 32px))) / 2)); padding: 28px; border: 1px solid var(--border); border-radius: 14px; background: var(--surface); box-shadow: var(--shadow); }
.transformation figcaption { margin-bottom: 8px; color: var(--gold); font-size: 0.72rem; font-weight: 800; letter-spacing: 0.11em; text-transform: uppercase; }
blockquote { margin: 0; color: var(--muted); font-style: italic; }
.arrow { display: block; padding: 16px 0; color: var(--primary); font-size: 1.8rem; text-align: center; }
.clean-notification { padding: 18px; border-left: 4px solid var(--primary); border-radius: 7px; background: var(--bg); }
.clean-notification p { margin: 0; }
.copy-chip { float: right; padding: 2px 10px; border-radius: 999px; background: var(--primary); color: var(--primary-text); font-size: 0.78rem; font-weight: 800; }

.assurances { padding: 0; list-style: none; display: grid; gap: 12px; }
.assurances li { padding: 18px 20px; border-left: 3px solid var(--primary); background: var(--surface); }
.statement { margin-top: 32px; padding-right: clamp(20px, 7vw, 80px); padding-left: clamp(20px, 7vw, 80px); border: 1px solid var(--border); border-radius: 16px; background: var(--surface-strong); }
pre { max-width: 100%; overflow-x: auto; padding: 18px 20px; border: 1px solid var(--border); border-radius: 9px; background: #20251f; color: #eff6ed; font-size: 0.82rem; line-height: 1.5; }
code { font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; }
.steps { padding-left: 1.4rem; }
.steps > li { margin: 32px 0; padding-left: 10px; }
footer { padding: 48px 0 64px; color: var(--muted); font-size: 0.84rem; }
footer p { margin: 6px 0; }

@media (max-width: 700px) {
  :root { --header-height: 62px; font-size: 16px; }
  .site-nav { overflow-x: auto; justify-content: flex-start; padding: 0 16px; scrollbar-width: none; }
  .site-nav::-webkit-scrollbar { display: none; }
  .brand { position: sticky; left: 0; z-index: 1; padding-right: 14px; background: var(--header-bg); }
  .nav-links { gap: 18px; }
  main, footer { width: min(calc(100% - 32px), var(--content)); }
  section { padding: 64px 0; }
  .hero { min-height: auto; padding-top: 72px; }
  .hero-logo { width: 96px; height: 96px; border-radius: 22px; }
  h1 { font-size: clamp(3rem, 17vw, 4.6rem); }
  .transformation { transform: none; margin-right: 0; padding: 20px; }
  .copy-chip { float: none; display: inline-block; margin-left: 5px; }
  .statement { margin-right: -8px; margin-left: -8px; }
}

@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #12140f;
    --surface: #1e211a;
    --surface-strong: #29291c;
    --text: #e3e3d9;
    --muted: #c1c9be;
    --primary: #8fdbb0;
    --primary-text: #003920;
    --gold: #d8b84a;
    --border: #414941;
    --header-bg: rgba(18, 20, 15, 0.94);
    --shadow: 0 18px 50px rgba(0, 0, 0, 0.28);
  }
  pre { background: #090b08; color: #eff6ed; }
}
```

- [ ] **Step 4: Copy the existing launcher icon for web use**

Run:

```bash
cp app/src/main/res/mipmap-xxxhdpi/ic_launcher.webp site/veles-icon.webp
```

Expected: `site/veles-icon.webp` exists and `file site/veles-icon.webp` reports a WebP image.

- [ ] **Step 5: Run structural and content validation**

Run the Step 1 command again, then run:

```bash
python3 - <<'PY'
from html.parser import HTMLParser
from pathlib import Path

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.hrefs = []
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.add(values["id"])
        if tag == "a" and "href" in values:
            self.hrefs.append(values["href"])

parser = PageParser()
parser.feed(Path("site/index.html").read_text())
required = {"problem", "privacy", "free", "pairing", "adb"}
assert required <= parser.ids
assert {f"#{anchor}" for anchor in required} <= set(parser.hrefs)
assert not any(url.startswith(("http://", "https://")) for url in (
    "style.css", "veles-icon.webp"
))
print("site structure OK")
PY
```

Expected: `site structure OK`.

- [ ] **Step 6: Serve and inspect the page**

Run:

```bash
python3 -m http.server 8000 --directory site
```

Expected: `Serving HTTP on 0.0.0.0 port 8000`. From any machine that can reach the workspace,
open `http://<workspace-host>:8000/`, `/#pairing`, and `/#adb`. If no browser can reach the
workspace, use `curl -I http://127.0.0.1:8000/` to verify HTTP 200 and defer visual inspection
to the pull-request preview or downloaded artifact. Check phone/desktop widths, light/dark
schemes, keyboard focus, horizontal nav reachability, and anchor headings below the sticky
header.

- [ ] **Step 7: Commit the static site**

```bash
git add site/index.html site/style.css site/veles-icon.webp
git commit -m "feat: add Veles landing page"
```

---

### Task 2: Android Guide Links

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/common/VelesLinks.kt`
- Modify: `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt:14-23`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/components/SensitiveNotificationsCard.kt:23-40,112-150,187-250`
- Modify: `app/src/androidTest/java/me/nagaev/veles/permissions/ui/SensitiveNotificationsCardComposeTest.kt:1-117`
- Modify: `app/src/androidTest/java/me/nagaev/permissions/ui/VelesPermissionsAppTests.kt:203-217`

**Interfaces:**
- Consumes: Task 1 anchors `/#pairing` and `/#adb`; Compose `LocalUriHandler.current.openUri(String)`.
- Produces: `VelesLinks.SITE`, `VelesLinks.PAIRING`, `VelesLinks.ADB`, `TestTags.SENSITIVE_PAIRING_GUIDE`, and `TestTags.SENSITIVE_ADB_GUIDE`.

- [ ] **Step 1: Add failing card-level click and visibility tests**

In `SensitiveNotificationsCardComposeTest.kt`, add imports:

```kotlin
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import me.nagaev.veles.common.VelesLinks
import org.junit.Assert.assertEquals
```

Add a URI collector field and wrap the existing test content:

```kotlin
private val openedUris = mutableListOf<String>()
private val uriHandler = object : UriHandler {
    override fun openUri(uri: String) {
        openedUris += uri
    }
}

private fun setCard(
    state: SensitiveNotificationsUiState,
    cdmSupported: Boolean = true,
    showForceStopButton: Boolean = false,
    revealFallbacks: Boolean = false,
    onEnable: () -> Unit = {},
    onVerify: () -> Unit = {},
    onOpenAppInfo: () -> Unit = {},
) {
    openedUris.clear()
    composeTestRule.setContent {
        CompositionLocalProvider(LocalUriHandler provides uriHandler) {
            SensitiveNotificationsCard(
                state = state,
                cdmSupported = cdmSupported,
                settingsLocation = "Settings > Notifications",
                showOnePlusAdbPreStep = false,
                revealFallbacks = revealFallbacks,
                showForceStopButton = showForceStopButton,
                onEnableViaCompanion = onEnable,
                onOpenSettings = {},
                onOpenEnhancedSettings = {},
                onVerify = onVerify,
                onOpenAppInfo = onOpenAppInfo,
            )
        }
    }
}
```

Add these tests:

```kotlin
@Test
fun pairingGuideOpensPairingAnchor() {
    setCard(SensitiveNotificationsUiState.NotGranted)

    composeTestRule.onNodeWithTag(TestTags.SENSITIVE_PAIRING_GUIDE).performClick()

    assertEquals(listOf(VelesLinks.PAIRING), openedUris)
}

@Test
fun pairingGuideHiddenWithoutCompanionSupport() {
    setCard(SensitiveNotificationsUiState.NotGranted, cdmSupported = false)

    composeTestRule.onNodeWithTag(TestTags.SENSITIVE_PAIRING_GUIDE).assertDoesNotExist()
}

@Test
fun adbGuideAppearsWithFallbacksAndOpensAdbAnchor() {
    setCard(SensitiveNotificationsUiState.NotGranted, revealFallbacks = true)

    composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_GUIDE).performClick()

    assertEquals(listOf(VelesLinks.ADB), openedUris)
}

@Test
fun adbGuideHiddenWhileFallbacksCollapsed() {
    setCard(SensitiveNotificationsUiState.NotGranted)

    composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_GUIDE).assertDoesNotExist()
}
```

- [ ] **Step 2: Add failing app-level presence assertions**

In `VelesPermissionsAppTests.sensitiveCardVisibleWhenNotGranted`, add:

```kotlin
composeTestRule.onNodeWithTag(TestTags.SENSITIVE_PAIRING_GUIDE).assertExists()
composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_GUIDE).assertDoesNotExist()
composeTestRule.onNodeWithTag(TestTags.SENSITIVE_FALLBACKS_TOGGLE).performClick()
composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_GUIDE).assertExists()
```

This uses the default platform URI handler only for rendering; the test does not click either
external link.

- [ ] **Step 3: Run the tests to verify they fail**

Run with a connected emulator/device:

```bash
./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.permissions.ui.SensitiveNotificationsCardComposeTest,me.nagaev.permissions.ui.VelesPermissionsAppTests
```

Expected: compilation fails because `VelesLinks`, `SENSITIVE_PAIRING_GUIDE`, and
`SENSITIVE_ADB_GUIDE` do not exist.

- [ ] **Step 4: Add the canonical URL object**

Create `app/src/main/java/me/nagaev/veles/common/VelesLinks.kt`:

```kotlin
package me.nagaev.veles.common

object VelesLinks {
    const val SITE = "https://raidenyn.github.io/veles-android/"
    const val PAIRING = "${SITE}#pairing"
    const val ADB = "${SITE}#adb"
}
```

- [ ] **Step 5: Add stable test tags**

Add after `SENSITIVE_ENABLE_BUTTON` in `TestTags.kt`:

```kotlin
const val SENSITIVE_PAIRING_GUIDE = "sensitive_pairing_guide"
const val SENSITIVE_ADB_GUIDE = "sensitive_adb_guide"
```

- [ ] **Step 6: Render and open the pairing guide**

In `SensitiveNotificationsCard.kt`, import:

```kotlin
import androidx.compose.ui.platform.LocalUriHandler
import me.nagaev.veles.common.VelesLinks
```

Inside `SensitiveNotificationsCard`, immediately after the visibility guard and before
`rememberSaveable`, add:

```kotlin
val uriHandler = LocalUriHandler.current
```

Directly below the pairing explanation `Text` and before the existing 12dp spacer, add:

```kotlin
TextButton(
    onClick = { uriHandler.openUri(VelesLinks.PAIRING) },
    modifier = Modifier.testTag(TestTags.SENSITIVE_PAIRING_GUIDE),
) { Text("Why is pairing needed?") }
```

This places the link only inside the existing
`cdmSupported && (NotGranted || Unknown)` branch.

- [ ] **Step 7: Pass the URI action to the fallback section**

Extend the `FallbackSection` call:

```kotlin
FallbackSection(
    settingsLocation = settingsLocation,
    showOnePlusAdbPreStep = showOnePlusAdbPreStep,
    onOpenSettings = onOpenSettings,
    onOpenEnhancedSettings = onOpenEnhancedSettings,
    onOpenAdbGuide = { uriHandler.openUri(VelesLinks.ADB) },
)
```

Extend its signature:

```kotlin
private fun FallbackSection(
    settingsLocation: String,
    showOnePlusAdbPreStep: Boolean,
    onOpenSettings: () -> Unit,
    onOpenEnhancedSettings: () -> Unit,
    onOpenAdbGuide: () -> Unit,
)
```

Replace the README line with:

```kotlin
Text(
    text = "Last resort - grant via adb:",
    fontSize = 13.sp,
    color = MaterialTheme.colorScheme.onErrorContainer,
)
TextButton(
    onClick = onOpenAdbGuide,
    modifier = Modifier.testTag(TestTags.SENSITIVE_ADB_GUIDE),
) { Text("Full guide") }
```

Leave `ADB_COMMAND`, the monospace command text, and `Copy command` unchanged.

- [ ] **Step 8: Run focused tests and formatting checks**

Run:

```bash
./gradlew spotlessApply
./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.permissions.ui.SensitiveNotificationsCardComposeTest,me.nagaev.permissions.ui.VelesPermissionsAppTests
./gradlew testDebugUnitTest spotlessCheck detekt lintDebug
```

Expected: focused instrumented tests pass; unit tests, formatting, static analysis, and lint
all complete successfully.

- [ ] **Step 9: Verify the no-network invariant**

Run:

```bash
if grep -R 'android.permission.INTERNET' app/src/main; then exit 1; else echo "No INTERNET permission"; fi
```

Expected: `No INTERNET permission`.

- [ ] **Step 10: Commit the Android integration**

```bash
git add \
  app/src/main/java/me/nagaev/veles/common/VelesLinks.kt \
  app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt \
  app/src/main/java/me/nagaev/veles/permissions/ui/components/SensitiveNotificationsCard.kt \
  app/src/androidTest/java/me/nagaev/veles/permissions/ui/SensitiveNotificationsCardComposeTest.kt \
  app/src/androidTest/java/me/nagaev/permissions/ui/VelesPermissionsAppTests.kt
git commit -m "feat: link sensitive notification guides"
```

---

### Task 3: GitHub Pages Deployment

**Files:**
- Create: `.github/workflows/pages.yml`

**Interfaces:**
- Consumes: Task 1's complete `site/` directory.
- Produces: Automatic deployment to the repository's GitHub Pages environment after a
  `master` push touching `site/**`, and manual deployment through `workflow_dispatch`.

- [ ] **Step 1: Verify no Pages workflow exists**

Run:

```bash
test ! -e .github/workflows/pages.yml
```

Expected: exit status 0.

- [ ] **Step 2: Add the official Pages workflow**

Create `.github/workflows/pages.yml`:

```yaml
name: Deploy GitHub Pages

on:
  push:
    branches: [master]
    paths:
      - "site/**"
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v4
        with:
          path: site
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
```

- [ ] **Step 3: Validate the workflow contract**

Run:

```bash
python3 - <<'PY'
from pathlib import Path

workflow = Path(".github/workflows/pages.yml").read_text()
required = (
    "branches: [master]",
    '"site/**"',
    "workflow_dispatch:",
    "contents: read",
    "pages: write",
    "id-token: write",
    "actions/configure-pages@v5",
    "actions/upload-pages-artifact@v4",
    "path: site",
    "actions/deploy-pages@v4",
    "cancel-in-progress: false",
)
for value in required:
    assert value in workflow, value
print("Pages workflow contract OK")
PY
git diff --check
```

Expected: `Pages workflow contract OK`; `git diff --check` emits no errors.

- [ ] **Step 4: Commit the deployment workflow**

```bash
git add .github/workflows/pages.yml
git commit -m "ci: deploy landing page to GitHub Pages"
```

- [ ] **Step 5: Record the required post-merge repository setting**

Do not add code for this step. In the pull-request description, include:

```text
Post-merge maintainer step: open Settings -> Pages and select GitHub Actions as the source.
```

This is intentionally manual because the workflow cannot change the repository's Pages
source setting.

---

### Task 4: End-to-End Verification

**Files:**
- Verify only; modify files only to fix a discovered failure.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: Evidence that the page contract, Android behavior, quality gates, and build pass
  together before review.

- [ ] **Step 1: Re-run the static page contract**

Run:

```bash
python3 - <<'PY'
from html.parser import HTMLParser
from pathlib import Path

class Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = set()
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag == "a" and "href" in attrs:
            self.links.add(attrs["href"])

html = Path("site/index.html").read_text()
parser = Parser()
parser.feed(html)
anchors = {"problem", "privacy", "free", "pairing", "adb"}
assert anchors <= parser.ids
assert {f"#{anchor}" for anchor in anchors} <= parser.links
assert "Bank OTPs, made usable." in html
assert "ordinary messaging apps" in html
assert "paid" in html and "ads" in html and "open source" in html
assert Path("site/style.css").is_file()
assert Path("site/veles-icon.webp").is_file()
print("Landing page contract OK")
PY
```

Expected: `Landing page contract OK`.

- [ ] **Step 2: Run all repository quality gates available without a device**

Run:

```bash
./gradlew spotlessCheck detekt lintDebug testDebugUnitTest assembleDebug
```

Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 3: Run the complete instrumented test suite**

Run with a connected emulator/device:

```bash
./gradlew connectedDebugAndroidTest
```

Expected: `BUILD SUCCESSFUL` with all existing and new Compose tests passing. If no device is
available, explicitly report this command as unrun; do not claim instrumented coverage passed
based only on compilation.

- [ ] **Step 4: Inspect the final branch diff and invariants**

Run:

```bash
git status --short
git diff master...HEAD --stat
git diff master...HEAD -- app/src/main/AndroidManifest.xml
git log --oneline master..HEAD
```

Expected: no unintended working-tree changes; the manifest diff is empty; commits are limited
to the approved spec/plan, static page, Android guide links, and Pages deployment.

- [ ] **Step 5: Commit only if verification required a fix**

If any verification step required source changes, stage only those files and commit with a
message describing the specific fix. If no files changed, do not create an empty commit.

## Post-Merge Check

After the maintainer selects **Settings -> Pages -> Source -> GitHub Actions** and the first
eligible deployment completes:

```bash
curl --fail --location https://raidenyn.github.io/veles-android/ >/dev/null
curl --fail --location https://raidenyn.github.io/veles-android/#pairing >/dev/null
curl --fail --location https://raidenyn.github.io/veles-android/#adb >/dev/null
```

Expected: all commands exit 0. Then open the live anchor URLs on a phone to confirm the sticky
header does not obscure either heading and the system light/dark preference is respected.

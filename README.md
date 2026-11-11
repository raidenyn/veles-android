<p align="center">
  <a href="https://veles.nagaev.me/">
    <img src="site/veles-icon.png" alt="Veles Icon" width="112" height="112">
  </a>
</p>

<h1 align="center">Veles</h1>

<p align="center">
  <strong>Bank OTPs, made usable.</strong><br>
  <em>Clean, copy-ready one-time codes on Android — free, open source, and strictly offline.</em>
</p>

<p align="center">
  <a href="https://veles.nagaev.me/"><strong>Website</strong></a> ·
  <a href="https://github.com/raidenyn/veles-android/releases/latest"><strong>Download APK</strong></a> ·
  <a href="https://github.com/users/raidenyn/projects/3"><strong>Project Board (WIP)</strong></a> ·
  <a href="#quick-start"><strong>Quick Start</strong></a>
</p>

<p align="center">
  <a href="https://github.com/raidenyn/veles-android/actions/workflows/ci.yml"><img src="https://github.com/raidenyn/veles-android/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/raidenyn/veles-android/actions/workflows/publish-release.yml"><img src="https://github.com/raidenyn/veles-android/actions/workflows/publish-release.yml/badge.svg" alt="publish-release"></a>
  <a href="https://github.com/raidenyn/veles-android/releases/latest"><img src="https://img.shields.io/github/v/release/raidenyn/veles-android?label=release&color=blue" alt="Latest Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <a href="BUILD.md#prerequisites"><img src="https://img.shields.io/badge/Android-13%2B-3DDC84?logo=android&logoColor=white" alt="Android 13+"></a>
</p>

---

## About

Veles intercepts noisy bank OTP notifications, extracts the verification code, amount, and merchant name, and re-presents them as a clean, actionable notification with a one-tap **Copy** button. It runs strictly on-device with zero internet permissions, no telemetry, no tracking, and no monetization.

```
Incoming SMS: "Your OTP is 674902 for payment of THB 1,250.00 to COFFEE HOUSE. Ref: 8842..."
       ↓
Veles Notice: [ 674902 ] · THB 1,250.00 → COFFEE HOUSE   [ Copy ]
```

---

## 🚧 Active Work-in-Progress (WIP)

We are actively building cross-device OTP relay support to automatically send verification codes to desktop browsers and host systems (via a shared Rust core, WebExtension MV3, and a Tauri native messaging host).

👉 **Track progress and upcoming milestones on our [GitHub Project: Veles OTP External Devices](https://github.com/users/raidenyn/projects/3)**.

---

## Quick Start

1. **Download:** Get the latest release from the [Releases page](https://github.com/raidenyn/veles-android/releases/latest).
2. **Install:** Run `adb install -r <apk>` or open the APK directly on your Android device (Android 13+).
3. **Grant Access:** Follow the in-app prompts to enable **Notification access**.
   *(On Android 15+, companion pairing or our [Sensitive Notifications Guide](docs/sensitive-notifications.md) ensures OTPs are not hidden by the OS).*

---

## Project Links & Docs

- 🌐 **[Official Website](https://veles.nagaev.me/)** — Overview, visual demos, and FAQ.
- 📦 **[Latest Releases](https://github.com/raidenyn/veles-android/releases/latest)** — Download pre-built APKs and verification attestations.
- 🛠️ **[Build Guide (BUILD.md)](BUILD.md)** — Prerequisites, Gradle build commands, tests, and multi-component toolchains.
- 🤝 **[Contributing (CONTRIBUTING.md)](CONTRIBUTING.md)** — Architecture overview, adding bank regex templates, and commit conventions.
- 🔐 **[Sensitive Notifications](docs/sensitive-notifications.md)** — Android 15 companion pairing & ADB setup.
- 📜 **[License](LICENSE)** — Licensed under the [MIT License](LICENSE).

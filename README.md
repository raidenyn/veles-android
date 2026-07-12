# Veles

Veles is an Android app that intercepts bank OTP (one-time password) notifications, extracts the OTP code along with the transaction amount and merchant name, and re-presents the OTP as a clean, concise notification with a one-tap **Copy** action.

When a bank pushes a verbose SMS notification, Veles catches it, parses out the useful parts, cancels the original, and posts a simplified notification containing just what you need: the code, the amount, and who charged you. One tap copies the OTP to the clipboard so you can paste it straight into the banking app or website you're authenticating into.

## How it works

```
Incoming notification
  → NotificationListener (NotificationListenerService)
      → CompositeMessageHandler
          → RegexMessageHandler (one per bank, configs loaded from a Room DB)
              → UserNotifierOtpMessageHandler — posts a simplified notification
                  → CopyDataReceiver (BroadcastReceiver) — copies the OTP on tap
```

Each bank is described by a row in a local Room database (`bank_handler_configs`) holding three regular expressions:

- `otpRegex` — group 1 = OTP id/prefix, group 2 = OTP value
- `moneyRegex` — group 1 = currency code, group 2 = amount
- `merchantRegex` — group 1 = merchant name

All three must match for a notification to be accepted. When a handler returns `ACCEPTED`, the original notification is cancelled and the chain stops; otherwise the next handler is tried. UOB Thailand is seeded on first install; additional banks can be added by inserting new rows — no Kotlin code changes required.

Veles also ships with an in-app **Test Screen** that lets you type a notification body, post it as a test notification, and see whether the handler chain matches it — useful for validating regex configs without waiting for a real bank SMS.

## Privacy

Veles is a strictly on-device tool. There are **no backdoors, no advertisements, and no payments** (no in-app purchases, no paid tiers, no monetization of any kind).

- No network access. The app never connects to the internet and never sends your notifications anywhere.
- No telemetry, analytics, or crash reporting.
- No account, no sign-up, no cloud sync.
- Notification data is processed in memory and discarded; bank handler configs live in a local Room database on your device only.

Everything Veles does happens locally, inside the Android NotificationListenerService it is granted access to. You can audit the entire source code in this repository.

### Verify your download

Every release can be verified against its source — either in one command
(trusting GitHub):

```
gh attestation verify veles-X.Y.Z.apk --repo raidenyn/veles-android
```

or by rebuilding it bit-for-bit yourself with nothing but Docker. CI refuses to
publish a release whose APK it cannot independently reproduce. See
[docs/reproducible-builds.md](docs/reproducible-builds.md).

## Requirements

- Android device running **Android 13 (API 33)** or newer.
- Android Studio (or a standalone Android SDK + JDK 17) to build.
- A machine with `adb` available to install the APK and grant the sensitive-notification permission (see below).

## Build

From the repository root:

```bash
# Debug APK
./gradlew assembleDebug

# Run unit tests
./gradlew testDebugUnitTest
```

The debug APK is output to `app/build/outputs/apk/debug/app-debug.apk`.

For a release build:

```bash
./gradlew assembleRelease
```

## Install

Connect your device (USB or wireless debugging) and verify it's seen:

```bash
adb devices
```

Install the debug APK:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

After installing, open **Veles** from your launcher and follow the in-app permission flow to grant:

1. **Notification access** — Settings → Notifications → Notification access → Veles → allow.
2. **Send notifications** — the POST_NOTIFICATIONS runtime prompt.

---

## Granting access to sensitive notifications

On Android 15 and later (including OxygenOS 15/16 on OnePlus devices), the OS automatically redacts OTPs, 2FA tokens, and banking content inside notifications before handing them to any `NotificationListenerService`. Without an extra permission, Veles sees only `"Sensitive notification content hidden"` instead of the real text.

To let Veles read the actual notification bodies you must grant the hidden `RECEIVE_SENSITIVE_NOTIFICATIONS` AppOp via ADB. This targets **only** Veles — it does not disable the protection globally.

The steps below were validated on a **OnePlus 13 running OxygenOS 16.0.801** and should work on any Android 15+ device. On non-OnePlus devices you can skip Step 1.

### Step 1 — Disable system optimization (OnePlus / OxygenOS only)

OxygenOS blocks ADB from modifying granular app operations unless a developer override is enabled.

1. Open **Settings → Additional Settings → Developer Options**.
2. Use the search icon (or scroll to the very bottom) and find **"Disable system optimization"**. *(On older OxygenOS 16 builds this was named "Disable permission monitoring".)*
3. Turn the toggle **ON**.
4. **Reboot** the phone so the ADB daemon restarts with the override applied.

> This switch can be turned back OFF after Step 2 — the permission you grant via ADB is persisted in Android's AppOps database and is not erased by re-enabling system optimization.

### Step 2 — Grant the sensitive notification AppOp

With ADB connected (wireless debugging works fine), run:

```bash
adb shell appops set me.nagaev.veles RECEIVE_SENSITIVE_NOTIFICATIONS allow
```

If your terminal reports an error about user profiles, target the primary user explicitly:

```bash
adb shell cmd appops set --user 0 me.nagaev.veles RECEIVE_SENSITIVE_NOTIFICATIONS allow
```

Verify it was saved:

```bash
adb shell appops get me.nagaev.veles RECEIVE_SENSITIVE_NOTIFICATIONS
# Expected: RECEIVE_SENSITIVE_NOTIFICATIONS: allow
```

### Step 3 — (Optional) Re-enable system optimization

Once the AppOp is granted you can turn **"Disable system optimization"** back OFF to restore default device security. The granted permission persists.

### When you need to redo this

- **Full uninstall + reinstall.** A clean install wipes the app's AppOps container, so you'll need to toggle system optimization back ON, repeat Step 2, then turn it off again. Incremental installs (`adb install -r` / Android Studio Run) preserve the permission.
- **Major OxygenOS/OTA updates** can occasionally clear ADB-granted permissions. Keep the command handy.

## License

Veles is licensed under the [MIT License](LICENSE).
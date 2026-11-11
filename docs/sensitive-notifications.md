# Sensitive Notifications on Android 15+

On Android 15 and later (including OxygenOS 15/16 on OnePlus devices), the OS automatically redacts OTPs, 2FA tokens, and banking content inside notifications before handing them to any `NotificationListenerService`. Without an extra permission, Veles sees only `"Sensitive notification content hidden"` instead of the real text.

---

## In-app Setup (Recommended)

On Android 15+, the Home screen displays a sensitive-notifications status card when access is needed. Grant it from inside the app — no computer required:

1. Open **Veles**. If access is missing, a card showing *"Android hides OTP content from Veles"* appears at the top of the Home screen.
2. Tap **Enable (pair as companion)**. Android only shares sensitive notifications with companion-device apps, so Veles requests registration as one. The system dialog will ask you to pick a nearby Bluetooth device — **any** device works (headphones, car, watch, etc.). Turn Bluetooth on first.
3. After pairing, Veles posts a hidden verification probe through its test-notification pipeline and checks whether the real text comes back. The card updates automatically: it disappears when access is confirmed, or shows guidance if the device still redacts.
4. Veles waits while Android applies the permission, then reconnects its notification listener and verifies access automatically. If setup times out, the card opens App info so you can tap **Force stop** and reopen Veles.

If the card indicates access is granted but content is still redacted, try the Enhanced-notifications fallback below.

---

## Enhanced Notifications Fallback

Turning off **Enhanced notifications** stops Android from hiding sensitive content device-wide. Note that this also disables smart replies and notification actions for all apps, not just Veles. Use this only if the companion-pairing flow does not stick.

1. Open the card's **More options** → **Enhanced notifications settings**, or navigate to **Settings → Notifications → Enhanced notifications**.
2. Turn the toggle **OFF**.
3. Return to Veles and tap **Check now** on the card to verify.

---

## ADB Method (Manual Override)

If neither in-app method works on your build, grant the hidden `RECEIVE_SENSITIVE_NOTIFICATIONS` AppOp via ADB. This targets **only Veles** without disabling protection globally.

The steps below were validated on a **OnePlus 13 running OxygenOS 16.0.801** and work on any Android 15+ device (non-OnePlus devices can skip Step 1).

### Step 1 — Disable system optimization (OnePlus / OxygenOS only)

OxygenOS blocks ADB from modifying granular app operations unless a developer override is enabled:

1. Open **Settings → Additional Settings → Developer Options**.
2. Search or scroll to the bottom to find **"Disable system optimization"** *(on older OxygenOS 16 builds: "Disable permission monitoring")*.
3. Turn the toggle **ON**.
4. **Reboot** the phone so the ADB daemon restarts with the override applied.

> *Note:* This switch can be turned back OFF after Step 2. The permission granted via ADB is persisted in Android's AppOps database.

### Step 2 — Grant the sensitive notification AppOp

With ADB connected (via USB or wireless debugging):

```bash
adb shell appops set me.nagaev.veles RECEIVE_SENSITIVE_NOTIFICATIONS allow
```

If your terminal reports an error regarding user profiles, target the primary user explicitly:

```bash
adb shell cmd appops set --user 0 me.nagaev.veles RECEIVE_SENSITIVE_NOTIFICATIONS allow
```

Verify that it was saved:

```bash
adb shell appops get me.nagaev.veles RECEIVE_SENSITIVE_NOTIFICATIONS
# Expected: RECEIVE_SENSITIVE_NOTIFICATIONS: allow
```

### Step 3 — (Optional) Re-enable system optimization

Once the AppOp is granted, turn **"Disable system optimization"** back OFF to restore default device security. The granted permission persists.

---

## When You Need to Redo This

- **Full uninstall + reinstall:** A clean install wipes the app's AppOps container. Incremental updates (`adb install -r` or Android Studio run) preserve the permission.
- **Major OS updates:** System updates can occasionally clear ADB-granted permissions.

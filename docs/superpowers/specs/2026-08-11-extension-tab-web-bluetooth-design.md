# Extension-tab Web Bluetooth design

## Status

The action-popup replacement is validated. Background-tab liveness and
background automatic copy remain Issue #77 gates. This focused document records
the launch-context decision; the current product requirements live in
`2026-08-11-android-chrome-bluetooth-otp-sharing-design.md`.

## Problem

The MV3 action popup calls `navigator.bluetooth.requestDevice()` directly from
the Connect button, but stable desktop Chrome immediately rejects it with
`NotFoundError: User cancelled the requestDevice() chooser.` without displaying
the chooser.

Chromium's `ShowDeviceChooserDialogForExtension()` requires the requesting
extension `WebContents` to be the active contents of a browser tab. An action
popup is not a tab, so Chrome returns without constructing the chooser. The
chooser controller is then destroyed and reports cancellation. Web Bluetooth
does not require or support a `"bluetooth"` extension manifest permission.

## Architecture

The extension action opens `popup.html` in a normal extension tab instead
of declaring it as `action.default_popup`. A small MV3 service worker will own
tab launching:

- If no connector tab exists, create one.
- If a connector tab exists, focus its window and activate that tab.
- Do not move Bluetooth, GATT, protocol, OTP, or protected-session state into
  the service worker.

The extension tab remains the sole owner of every live Bluetooth object and
session. Closing the tab destroys all connection state. Reopening through the
action starts a fresh page and requires explicit device selection again.

After selection, the tab may be backgrounded while the user browses elsewhere.
Bluetooth ownership remains with that open tab. Production liveness must not
depend on short JavaScript timers that Chrome throttles in background tabs.

Background automatic copy uses a supported offscreen clipboard document
coordinated by the service worker. That document receives only the immediate
copy request and never owns Bluetooth, session keys, or OTP history.

## User interface

The current connector markup and controls remain unchanged. Its stylesheet
will replace the fixed popup body width with a bounded responsive width so the
same interface is usable as a browser tab.

## Error handling

Chooser, GATT, authentication, background liveness, and clipboard failures have
separate visible states. Launcher failures are reported through the service
worker console because no connector document exists when tab creation fails.

## Testing

Dependency-free Node tests will cover the launcher behavior:

- Create a connector tab when none exists.
- Activate and focus an existing connector tab instead of creating another.

Existing protocol tests and Android unit/build checks remain unchanged. The
tester guide and physical-validation report will explicitly test tab closure
and reopening instead of action-popup closure and reopening. Physical hardware
is still required to verify chooser, pairing, GATT, and clipboard behavior.

The follow-up physical matrix also keeps the tab backgrounded long enough to
trigger Chrome timer throttling, verifies that pushes continue, and verifies
automatic copy through the offscreen clipboard helper.

## Scope

This change records the action-popup result as unsupported by Chrome and
converts the feasibility spike to tab-owned Web Bluetooth. It does not claim
that popup-owned Web Bluetooth is viable, add persistent storage, or add
hidden Bluetooth ownership. Backgrounding the still-open connector tab is part
of stable product scope; moving Bluetooth into an offscreen document is not.

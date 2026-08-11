# Extension-tab Web Bluetooth design

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

The extension action will open `popup.html` in a normal extension tab instead
of declaring it as `action.default_popup`. A small MV3 service worker will own
only tab launching:

- If no connector tab exists, create one.
- If a connector tab exists, focus its window and activate that tab.
- Do not move Bluetooth, GATT, protocol, OTP, or clipboard state into the
  service worker.

The extension tab remains the sole owner of every live Bluetooth object and
session. Closing the tab destroys all connection state. Reopening through the
action starts a fresh page and requires explicit device selection again.

## User interface

The current connector markup and controls remain unchanged. Its stylesheet
will replace the fixed popup body width with a bounded responsive width so the
same interface is usable as a browser tab.

## Error handling

The existing chooser, GATT, authentication, and clipboard error reporting
remains in place. Launcher failures will be reported through the service
worker console because no connector document exists when tab creation fails.

## Testing

Dependency-free Node tests will cover the launcher behavior:

- Create a connector tab when none exists.
- Activate and focus an existing connector tab instead of creating another.

Existing protocol tests and Android unit/build checks remain unchanged. The
tester guide and physical-validation report will explicitly test tab closure
and reopening instead of action-popup closure and reopening. Physical hardware
is still required to verify chooser, pairing, GATT, and clipboard behavior.

## Scope

This change records the action-popup result as unsupported by Chrome and
converts the feasibility spike to tab-owned Web Bluetooth. It does not claim
that popup-owned Web Bluetooth is viable, add persistent storage, or add
background Bluetooth ownership.

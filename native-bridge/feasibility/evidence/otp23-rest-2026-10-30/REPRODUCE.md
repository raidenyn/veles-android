# Repeating the scoped physical checks

These are synthetic feasibility experiments. Use the existing approved app identities and signing
key; compare actual installed/new APK certificates before every `install -r`. The recorded signer
for these fixtures is `2081d17da5ec1a400e710720eba4a16939db774da54cdce936be30f4a848506e`.
The default production/debug manifest has not gained SCAN: the extra declaration is only an explicit
fixture-build input under `source/fixture-manifest.xml`.

## Android fixture build and roles

The actual controller staged `source/fixture.init.gradle` and `source/fixture-manifest.xml` under
`/tmp/opencode/otp23-rest-20261030/`, then ran from the repository root:

```sh
JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew \
  --init-script /tmp/opencode/otp23-rest-20261030/fixture.init.gradle \
  :app:assembleDebug :app:assembleDebugAndroidTest
```

That explicitly builds `me.nagaev.veles.otp23` / `.otp23.test` into the isolated output directory
defined by the init file. It does not replace the ordinary `.debug` application. Input paths are
recorded controller paths, not a portable build or reproducible-byte claim. Restore the original
fixture/test APKs with their existing signer after the experiment; the recorded restoration is in
`windows-first/capture/a01-restoration.json`.

The passing M4 run used these exact instrumentation selections (6T server, 10 Pro central):

```sh
adb -s 2be02e41 shell am instrument -w -r \
  -e otp23_a01 true -e otp23_run_id a01-m4-retake3-20261030 \
  -e class me.nagaev.veles.sharing.SharingInteropPhysicalTest#holdObservedPeripheral \
  me.nagaev.veles.otp23.test/androidx.test.runner.AndroidJUnitRunner

adb -s 864a3db0 shell am instrument -w -r \
  -e otp23_a01 true -e otp23_run_id a01-m4-retake3-20261030 \
  -e class me.nagaev.veles.sharing.SharingInteropPhysicalTest#cccdBeforeMtuIsRejectedOnRealRadio \
  me.nagaev.veles.otp23.test/androidx.test.runner.AndroidJUnitRunner
```

Run the server asynchronously before the central. It publishes `files/otp23-a01-ready`. Keep the
central phone awake/unlocked so its ordinary Nearby devices request can complete. Stop the server
by creating `files/otp23-a01-stop` via `run-as me.nagaev.veles.otp23`; wait for final `server_closed`
before collecting `otp23-a01-server.json`. Client writes `otp23-a01-client.json`.

For M3, the real Windows connector must prove the link and exchange 16+4096 bytes first. The
controller then atomically writes `otp23-a01-command.json` with matching `run_id`, fresh `nonce`,
and `cmd: inject_duplicate_connected`. Verify the nonce-bound phone ack before continuing the
Windows driver for its different post-injection 4096-byte exchange on the same handle. This is
injection into the actual callback on a real link, not a natural duplicate OS-event observation.

## Mac and Windows controls

`source/run_m04.py`, the recorded Mac source pins and `m04/state/host-trace.log` identify the exact
approved signed bundle and foreground controller. Start from granted baseline, bind each real
owner OFF/ON action to that phase's nonce, and retain one Chrome/profile/extension load/sentinel.
Read the rendered DOM guidance before closing the denied native port; do not reset TCC or reload
the extension during the run. The raw popup field remains unknown; M-03 is the separately recorded
same-identity prompt observation.

Windows operator sources are preserved under `windows-first/` and `windows-reboot/`. Adopt only
the post-reboot verified absent-adapter case and the final `w10-exclusive-direct.ps1` plus typed
`VelesGattVector.cs` control. Earlier constructors/invalid setup are diagnostic history. The final
exclusive holder must report Success/Exclusive/Success before the native denial probe runs, and
must exit before the recovery probe. It touches only the synthetic service, not privacy policy.
The exact native binary, Chrome versions, restore watchdog, owner consent and final cleanup
readbacks are retained alongside the captures.

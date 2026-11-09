//! F2: the compiled channel of the engine, and fixture isolation from production state.
//!
//! The Windows/macOS producers build ONE engine per channel. The channel is a compile-time fact of the
//! binary (`COMPILED_CHANNEL`), never a runtime argument, and a fixture-channel engine's fixed layout and
//! registration are disjoint from the production ones. Package-less commands (uninstall, rollback, inspect)
//! need no descriptor, so isolation cannot rest on the descriptor refusing a fixture package: it rests on
//! the layout the binary was compiled for.
//!
//! The first group of tests is configuration-sensitive: `scripts/test_build_contract.py` runs this file
//! once as built by default (production) and once with `--cfg veles_channel="fixture"`, the exact
//! RUSTFLAGS the fixture producer passes, and requires the matching assertion to be the one that ran.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use veles_bridge_installer::cli::{self, Context};
use veles_bridge_installer::{
    COMPILED_CHANNEL, Channel, FIXTURE_HOST_NAME, FakePlatform, Faults, HostState, InstallPaths,
    InstallReport, PRODUCTION_HOST_NAME, Target,
};

#[cfg(not(any(veles_channel = "fixture", veles_channel = "development")))]
#[test]
fn compiled_channel_is_production_by_default() {
    assert_eq!(COMPILED_CHANNEL, Channel::Production);
}

#[cfg(veles_channel = "fixture")]
#[test]
fn compiled_channel_is_fixture_when_built_for_it() {
    assert_eq!(COMPILED_CHANNEL, Channel::Fixture);
}

#[cfg(veles_channel = "development")]
#[test]
fn compiled_channel_is_development_when_built_for_it() {
    assert_eq!(COMPILED_CHANNEL, Channel::Development);
}

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct Temp(PathBuf);

impl Temp {
    fn new() -> Self {
        let unique = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "veles-installer-channel-{}-{unique}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).expect("temp dir");
        Self(path)
    }
}

impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn paths(temp: &Temp, target: Target, channel: Channel) -> InstallPaths {
    let home = temp.0.join("home");
    let local = temp.0.join("local");
    let local = (target == Target::WindowsX64).then_some(local.as_path());
    InstallPaths::for_user(target, channel, &home, local).expect("injected roots")
}

/// Every file and directory below `root`, with bytes.
fn snapshot(root: &Path) -> Vec<(String, Vec<u8>)> {
    fn walk(base: &Path, dir: &Path, out: &mut Vec<(String, Vec<u8>)>) {
        let Ok(entries) = fs::read_dir(dir) else {
            return;
        };
        for entry in entries {
            let path = entry.expect("entry").path();
            let relative = path
                .strip_prefix(base)
                .expect("below base")
                .to_string_lossy()
                .into_owned();
            if path.is_dir() {
                out.push((relative, Vec::new()));
                walk(base, &path, out);
            } else {
                out.push((relative, fs::read(&path).expect("bytes")));
            }
        }
    }
    let mut out = Vec::new();
    walk(root, root, &mut out);
    out.sort();
    out
}

fn run(platform: &FakePlatform, paths: &InstallPaths, operation: &str) -> (i32, InstallReport) {
    let context = Context {
        platform,
        paths,
        expected: None,
        quiescence: Some(Duration::from_millis(50)),
    };
    let arguments = vec![std::ffi::OsString::from(operation)];
    let (mut stdout, mut stderr) = (Vec::new(), Vec::new());
    let exit = cli::run(&arguments, &context, &mut stdout, &mut stderr);
    (
        exit,
        serde_json::from_slice(&stdout).expect("one strict report"),
    )
}

#[test]
fn channels_have_disjoint_roots_registrations_and_identities() {
    let temp = Temp::new();
    for target in [Target::WindowsX64, Target::MacosArm64] {
        let production = paths(&temp, target, Channel::Production);
        let fixture = paths(&temp, target, Channel::Fixture);
        assert_ne!(
            production.root(),
            fixture.root(),
            "{target:?}: distinct install roots"
        );
        assert!(
            !production.root().starts_with(fixture.root())
                && !fixture.root().starts_with(production.root())
        );
        assert_ne!(
            production.registration_path(),
            fixture.registration_path(),
            "{target:?}"
        );
        assert_eq!(production.registration_name(), PRODUCTION_HOST_NAME);
        assert_eq!(fixture.registration_name(), FIXTURE_HOST_NAME);
        assert_ne!(
            Channel::Production.identity().bundle_id,
            Channel::Fixture.identity().bundle_id
        );
    }
}

#[test]
fn a_fixture_channel_engine_cannot_address_a_production_installation() {
    // A production installation exists in the user's home: its root and its Native Messaging registration.
    for target in [Target::WindowsX64, Target::MacosArm64] {
        let temp = Temp::new();
        let production = paths(&temp, target, Channel::Production);
        fs::create_dir_all(production.root().join("versions/1.0.0-abc")).expect("production tree");
        fs::write(
            production
                .root()
                .join("versions/1.0.0-abc/veles-native-host"),
            b"production host",
        )
        .expect("host");
        fs::write(production.root().join("state-marker"), b"production state").expect("marker");
        fs::create_dir_all(
            production
                .registration_path()
                .parent()
                .expect("registration parent"),
        )
        .expect("registration dir");
        fs::write(
            production.registration_path(),
            b"{\"name\":\"production registration\"}\n",
        )
        .expect("registration");
        let before = snapshot(&temp.0);

        // The fixture-channel layout is what a fixture-compiled engine resolves for the same user.
        let fixture = paths(&temp, target, Channel::Fixture);
        let platform = FakePlatform::with_faults(Faults {
            host_state: HostState::Quiescent,
            ..Faults::default()
        });
        for operation in ["inspect", "uninstall", "rollback"] {
            let (_, report) = run(&platform, &fixture, operation);
            assert!(
                report.active_payload.is_none(),
                "{target:?} {operation}: the fixture channel reports no active production payload: {report:?}"
            );
            assert_eq!(
                snapshot(&temp.0)
                    .iter()
                    .filter(|(path, _)| path.contains("state-marker")
                        || path.contains("1.0.0-abc")
                        || path.ends_with(".json"))
                    .collect::<Vec<_>>(),
                before
                    .iter()
                    .filter(|(path, _)| path.contains("state-marker")
                        || path.contains("1.0.0-abc")
                        || path.ends_with(".json"))
                    .collect::<Vec<_>>(),
                "{target:?} {operation}: production bytes are untouched"
            );
        }
        let production_after: Vec<_> = snapshot(&temp.0)
            .into_iter()
            .filter(|(path, _)| before.iter().any(|(old, _)| old == path))
            .collect();
        assert_eq!(
            production_after, before,
            "{target:?}: nothing the production installation owns changed"
        );
    }
}

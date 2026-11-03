//! VERIFICATION ONLY. Bounded, seeded, dependency-free mutation driver over the fuzz targets.
//!
//! NOT libFuzzer and NOT coverage guided: it mutates the checked-in corpus with a fixed
//! SplitMix64 stream and keeps a mutant only when the target's coarse behavior signature is new.
//! It exists so the exact same checkers run on the pinned stable toolchain where no nightly /
//! cargo-fuzz is available. A clean run is bounded evidence, never proof of absence.
//!
//! ```text
//! smoke --target <strict_envelope|fragment_stream|core_sequence> --seed <u64>
//!       [--seconds N | --execs N] [--corpus DIR] [--crashers DIR] [--report FILE]
//! smoke --replay FILE --target T        # re-run one input (a saved crasher)
//! smoke --write-seeds DIR               # (re)generate the checked-in seed corpus
//! ```
//! Exit: 0 no finding, 1 at least one finding (crasher saved), 2 usage/environment error.

use std::collections::HashSet;
use std::fs;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::{Path, PathBuf};
use std::process::exit;
use std::time::{Duration, Instant};

use veles_fuzz::{Rng, encode_ops, max_len, run, seed_programs};

fn usage(msg: &str) -> ! {
    eprintln!("smoke: {msg}");
    exit(2)
}

fn arg(args: &[String], name: &str) -> Option<String> {
    args.iter()
        .position(|a| a == name)
        .and_then(|i| args.get(i + 1))
        .cloned()
}

fn fnv(data: &[u8]) -> u64 {
    data.iter().fold(0xcbf2_9ce4_8422_2325, |h, b| {
        (h ^ u64::from(*b)).wrapping_mul(0x0100_0000_01b3)
    })
}

fn mutate(rng: &mut Rng, base: &[u8], other: &[u8], max: usize) -> Vec<u8> {
    let mut out = base.to_vec();
    for _ in 0..1 + rng.below(4) {
        match rng.below(9) {
            0 if !out.is_empty() => {
                let i = rng.below(out.len());
                out[i] ^= 1 << rng.below(8);
            }
            1 if !out.is_empty() => {
                let i = rng.below(out.len());
                out[i] = rng.byte();
            }
            2 => {
                let at = rng.below(out.len() + 1);
                let n = 1 + rng.below(16);
                let bytes = rng.bytes(n);
                out.splice(at..at, bytes);
            }
            3 if !out.is_empty() => {
                let at = rng.below(out.len());
                let n = 1 + rng.below(out.len() - at);
                out.drain(at..at + n);
            }
            4 => {
                let at = rng.below(out.len() + 1);
                let from = rng.below(other.len() + 1);
                let n = rng.below((other.len() - from).min(64) + 1);
                out.splice(at..at, other[from..from + n].iter().copied());
            }
            5 if !out.is_empty() => out.truncate(rng.below(out.len())),
            6 if !out.is_empty() => {
                let at = rng.below(out.len());
                let n = 1 + rng.below((out.len() - at).min(32));
                let block = out[at..at + n].to_vec();
                out.splice(at..at, block);
            }
            7 if out.len() >= 2 => {
                let i = rng.below(out.len() - 1);
                let v = [
                    0u16, 1, 0x7f, 0x80, 0xff, 0x100, 0x1004, 0x2000, 0x3000, 0xfeff, 0xffff,
                ][rng.below(11)];
                out[i..i + 2].copy_from_slice(&v.to_le_bytes());
            }
            _ => {
                let n = rng.below(24);
                out.extend(rng.bytes(n));
            }
        }
    }
    out.truncate(max);
    out
}

fn read_corpus(dir: &Path, max: usize) -> Vec<Vec<u8>> {
    let mut names: Vec<PathBuf> = fs::read_dir(dir)
        .unwrap_or_else(|e| usage(&format!("corpus {}: {e}", dir.display())))
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.is_file())
        .collect();
    names.sort();
    names
        .iter()
        .map(|p| {
            let data = fs::read(p).unwrap_or_else(|e| usage(&format!("{}: {e}", p.display())));
            assert!(
                data.len() <= max,
                "corpus file {} is {} bytes, ceiling {max}",
                p.display(),
                data.len()
            );
            data
        })
        .collect()
}

fn write_seeds(dir: &Path) {
    let mut rng = Rng(0x5EED_0024);
    type Seeds = Vec<(String, Vec<u8>)>;
    let corpora: [(&str, Seeds); 3] = [
        ("strict_envelope", strict_seeds()),
        ("fragment_stream", fragment_seeds(&mut rng)),
        (
            "core_sequence",
            seed_programs()
                .into_iter()
                .map(|(n, ops)| (n.to_string(), encode_ops(&ops)))
                .collect(),
        ),
    ];
    for (target, seeds) in corpora {
        let sub = dir.join(target);
        fs::create_dir_all(&sub).unwrap();
        for (name, bytes) in seeds {
            assert!(
                bytes.len() <= max_len(target).unwrap(),
                "{target}/{name} over its ceiling"
            );
            fs::write(sub.join(format!("{name}.bin")), bytes).unwrap();
        }
    }
}

fn strict_seeds() -> Vec<(String, Vec<u8>)> {
    let h = "0123456789abcdef";
    let uuid = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51";
    let texts = [
        ("hello-versions", r#"{"envelope_version":1,"type":"hello","request_id":"h","api_versions":[1]}"#.to_string()),
        ("hello-range", r#"{"envelope_version":1,"type":"hello","request_id":"h","api_version_range":{"min":1,"max":4}}"#.to_string()),
        ("start-scan", format!(r#"{{"envelope_version":1,"api_version":1,"type":"start_scan","request_id":"a","service_uuid":"{uuid}"}}"#)),
        ("connect-hint", r#"{"envelope_version":1,"api_version":1,"type":"connect","request_id":"c","platform_hint":"phone-1"}"#.to_string()),
        ("disconnect", format!(r#"{{"envelope_version":1,"api_version":1,"type":"disconnect","request_id":"d","connection_handle":"{h}"}}"#)),
        ("send-small", format!(r#"{{"envelope_version":1,"api_version":1,"type":"send","request_id":"s","connection_handle":"{h}","record_base64":"AAECAw=="}}"#)),
        ("duplicate-key", r#"{"envelope_version":1,"envelope_version":1,"type":"shutdown","request_id":"z"}"#.to_string()),
        ("null-field", r#"{"envelope_version":1,"type":"shutdown","request_id":null}"#.to_string()),
        ("float", r#"{"envelope_version":1.0,"type":"shutdown","request_id":"z"}"#.to_string()),
        ("trailing", r#"{"envelope_version":1,"type":"shutdown","request_id":"z"} x"#.to_string()),
        ("deep-array", "[".repeat(2048)),
    ];
    let mut out: Vec<(String, Vec<u8>)> = texts
        .into_iter()
        .map(|(n, t)| (n.to_string(), t.into_bytes()))
        .collect();
    // Frame-shaped inputs (4-byte little-endian header first): zero, oversize, exact ceiling.
    out.push(("frame-zero".into(), vec![0, 0, 0, 0]));
    out.push(("frame-oversize".into(), 65_537u32.to_le_bytes().to_vec()));
    out.push(("frame-max-short".into(), 65_536u32.to_le_bytes().to_vec()));
    // Largest accepted record: 5120 decoded bytes, and one over the envelope bound (5121).
    for (name, n) in [
        ("send-5120", 5120usize),
        ("send-5121", 5121),
        ("send-4097", 4097),
    ] {
        let record: Vec<u8> = (0..n).map(|i| (i % 251) as u8).collect();
        let text = format!(
            r#"{{"envelope_version":1,"api_version":1,"type":"send","request_id":"s","connection_handle":"{h}","record_base64":"{}"}}"#,
            veles_ble_host::base64::encode(&record)
        );
        out.push((name.into(), text.into_bytes()));
    }
    out
}

fn fragment_seeds(rng: &mut Rng) -> Vec<(String, Vec<u8>)> {
    // The program layout is defined by `fragment_stream` (nine peers, pooled records, ops mod 20).
    // Seeds are therefore found, not hand-assembled: the first seeded random programs that reach
    // each branch the reference model must predict, plus degenerate fills. Deterministic: the
    // search order is the fixed SplitMix64 stream below.
    let mut out: Vec<(String, Vec<u8>)> = vec![
        ("all-zero-512".into(), vec![0; 512]),
        ("all-ff-512".into(), vec![0xFF; 512]),
    ];
    type Want = fn(&veles_fuzz::FragCensus) -> bool;
    let wants: [(&str, Want); 5] = [
        ("conflict-erase", |c| c.conflict_erased > 0),
        ("cap-refused", |c| c.cap_refused > 0),
        ("disconnect", |c| c.disconnect > 0),
        ("expired", |c| c.expired > 0),
        ("global-16", |c| c.peak_global_count == 16),
    ];
    for (name, want) in wants {
        for _ in 0..20_000 {
            let len = 64 + rng.below(3000);
            let bytes = rng.bytes(len);
            if want(&veles_fuzz::fragment_stream(&bytes)) {
                out.push((format!("found-{name}"), bytes));
                break;
            }
        }
    }
    out
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Some(dir) = arg(&args, "--write-seeds") {
        write_seeds(Path::new(&dir));
        return;
    }
    let target = arg(&args, "--target").unwrap_or_else(|| usage("--target required"));
    let max = max_len(&target).unwrap_or_else(|| usage("unknown target"));
    // Findings are panics; keep their default message off the progress stream's noise.
    std::panic::set_hook(Box::new(|info| eprintln!("FINDING-PANIC: {info}")));
    if let Some(file) = arg(&args, "--replay") {
        let data = fs::read(&file).unwrap_or_else(|e| usage(&format!("{file}: {e}")));
        let sig = run(&target, &data);
        println!("replay {file}: clean, signature {sig:#x}");
        return;
    }
    let seed: u64 = arg(&args, "--seed")
        .unwrap_or_else(|| usage("--seed required"))
        .parse()
        .unwrap_or_else(|_| usage("bad --seed"));
    let seconds: u64 =
        arg(&args, "--seconds").map_or(0, |s| s.parse().unwrap_or_else(|_| usage("bad --seconds")));
    let execs: u64 =
        arg(&args, "--execs").map_or(0, |s| s.parse().unwrap_or_else(|_| usage("bad --execs")));
    if seconds == 0 && execs == 0 {
        usage("--seconds or --execs required");
    }
    let corpus_dir = PathBuf::from(
        arg(&args, "--corpus")
            .unwrap_or_else(|| format!("{}/corpus/{target}", env!("CARGO_MANIFEST_DIR"))),
    );
    let crashers = PathBuf::from(arg(&args, "--crashers").unwrap_or_else(|| {
        format!(
            "{}/../../../build/native-bridge/host/verification/crashers/{target}",
            env!("CARGO_MANIFEST_DIR")
        )
    }));
    let mut corpus = read_corpus(&corpus_dir, max);
    if corpus.is_empty() {
        usage("empty corpus");
    }
    let seed_count = corpus.len();
    let mut rng = Rng(seed);
    let mut seen: HashSet<u64> = HashSet::new();
    let deadline = (seconds > 0).then(|| Instant::now() + Duration::from_secs(seconds));
    let started = Instant::now();
    let (mut n, mut findings) = (0u64, Vec::<String>::new());

    // Phase 1: every seed once, unmutated.
    let mut queue: Vec<Vec<u8>> = corpus.clone();
    loop {
        let input = if let Some(next) = queue.pop() {
            next
        } else {
            if deadline.is_some_and(|d| Instant::now() >= d) || (execs > 0 && n >= execs) {
                break;
            }
            let a = &corpus[rng.below(corpus.len())];
            let b = &corpus[rng.below(corpus.len())];
            if rng.below(16) == 0 {
                let len = rng.below(max.min(4096) + 1);
                rng.bytes(len)
            } else {
                mutate(&mut rng, a, b, max)
            }
        };
        n += 1;
        match catch_unwind(AssertUnwindSafe(|| run(&target, &input))) {
            Ok(sig) => {
                if seen.insert(sig) && corpus.len() < 4096 {
                    corpus.push(input);
                }
            }
            Err(_) => {
                fs::create_dir_all(&crashers).unwrap();
                let path = crashers.join(format!("crash-{:016x}.bin", fnv(&input)));
                fs::write(&path, &input).unwrap();
                findings.push(path.display().to_string());
                if findings.len() >= 10 {
                    break;
                }
            }
        }
    }
    let elapsed = started.elapsed();
    println!(
        "{{\"driver\":\"veles-fuzz-smoke\",\"kind\":\"stable-seeded-mutator-NOT-libfuzzer-NOT-coverage-guided\",\"target\":\"{target}\",\"seed\":{seed},\"seed_inputs\":{seed_count},\"max_len\":{max},\"execs\":{n},\"signatures\":{},\"corpus_final\":{},\"elapsed_ms\":{},\"findings\":{}, \"crashers\":{:?}}}",
        seen.len(),
        corpus.len(),
        elapsed.as_millis(),
        findings.len(),
        findings
    );
    exit(i32::from(!findings.is_empty()));
}

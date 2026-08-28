// OTP-01 sub-project 1c — Native Messaging host lifecycle test.
//
// The bridge is a headless Native Messaging host: it reads framed messages
// from stdin, processes them, and writes framed responses to stdout. When
// stdin closes (EOF), the host must exit cleanly. This test asserts the
// public `run` entry point returns `Ok(())` immediately when stdin is at EOF.

use std::io::Cursor;
use veles_native_bridge::run;

#[test]
fn run_returns_ok_on_immediate_eof() {
    let input = Cursor::new(Vec::<u8>::new());
    let mut output = Vec::new();
    let result = run(input, &mut output);
    assert!(
        result.is_ok(),
        "run should return Ok on stdin EOF: {:?}",
        result
    );
    assert!(output.is_empty(), "no output on immediate EOF");
}

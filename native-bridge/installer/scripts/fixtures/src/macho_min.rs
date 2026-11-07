// OTP-25 Task 9 correspondence fixture: smallest real arm64 Mach-O executable
// the pinned Rust 1.98.0 toolchain links for aarch64-apple-darwin with ld64.lld.
#![no_std]
#![no_main]

#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    loop {}
}

#[unsafe(no_mangle)]
pub extern "C" fn main() -> ! {
    loop {}
}

#[used]
#[unsafe(no_mangle)]
pub static VELES_FIXTURE_DATA: [u8; 64] = [7; 64];

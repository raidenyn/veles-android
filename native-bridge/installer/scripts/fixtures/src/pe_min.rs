// OTP-25 Task 9 correspondence fixture: smallest real PE32+ image the pinned
// Rust 1.98.0 toolchain links for x86_64-pc-windows-msvc with rust-lld.
#![no_std]
#![no_main]

#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    loop {}
}

#[unsafe(no_mangle)]
pub extern "C" fn mainCRTStartup() -> ! {
    loop {}
}

#[used]
#[unsafe(no_mangle)]
pub static VELES_FIXTURE_DATA: [u8; 64] = [7; 64];

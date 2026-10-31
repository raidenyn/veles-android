// Only the Tauri-runtime build needs the Tauri build script (context codegen, Windows resources).
fn main() {
    #[cfg(feature = "tauri-runtime")]
    tauri_build::build();
}

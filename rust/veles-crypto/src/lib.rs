#[cfg(target_arch = "wasm32")]
use wasm_bindgen::prelude::wasm_bindgen;

#[cfg_attr(target_arch = "wasm32", wasm_bindgen)]
pub fn reverse_bytes(input: &[u8]) -> Vec<u8> {
    input.iter().rev().copied().collect()
}

// jni 0.22 splits the legacy `JNIEnv` into an FFI-safe `EnvUnowned` (the type
// native methods must accept as their first argument) and `Env` (the full,
// non-FFI-safe API, obtained via `EnvUnowned::with_env`). `with_env` already
// wraps its closure in `catch_unwind`, so a panic surfaces as
// `EnvOutcome::Outcome::Panic` rather than unwinding into the JVM. We resolve
// that `EnvOutcome` with a custom `ErrorPolicy` that mirrors the previous
// behavior: preserve any exception already pending on entry, otherwise throw
// a generic `java.lang.RuntimeException`, and return null on failure. See the
// comment on `ThrowGenericRuntimeException` below for a nuance on what
// "already pending" does and doesn't cover.
#[cfg(target_os = "android")]
mod android {
    use std::any::Any;
    use std::ptr::null_mut;

    use jni::errors::{Error, ErrorPolicy, Result as JniResult};
    use jni::objects::{JByteArray, JClass};
    use jni::sys::jbyteArray;
    use jni::{Env, EnvUnowned, jni_str};

    use super::reverse_bytes;

    // `exception_check()` below only preserves an exception that was already
    // pending *before* this native method was entered (e.g. raised by JVM code
    // prior to calling `reverseBytes`). It does NOT catch a JVM exception
    // raised from inside `convert_byte_array` or `byte_array_from_slice`:
    // jni 0.22.4's own internal call macros always clear the pending JVM
    // exception before mapping it to a Rust `Error` (see the doc comment on
    // `jni_call_with_catch_and_null_check!` in jni-0.22.4/src/macros.rs). So
    // e.g. an `OutOfMemoryError` from `byte_array_from_slice` or an
    // `ArrayIndexOutOfBoundsException` from `convert_byte_array` is always
    // already cleared and reported to Java as our generic RuntimeException
    // below, by construction of the jni crate — not a gap in this policy.
    struct ThrowGenericRuntimeException;

    impl ErrorPolicy<jbyteArray, Error> for ThrowGenericRuntimeException {
        type Captures<'unowned_env_local: 'native_method, 'native_method> = ();

        fn on_error<'unowned_env_local: 'native_method, 'native_method>(
            env: &mut Env<'unowned_env_local>,
            _cap: &mut Self::Captures<'unowned_env_local, 'native_method>,
            _err: Error,
        ) -> JniResult<jbyteArray> {
            if !env.exception_check() {
                let _ = env.throw_new(
                    jni_str!("java/lang/RuntimeException"),
                    jni_str!("Veles crypto JNI conversion failed"),
                );
            }
            Ok(null_mut())
        }

        fn on_panic<'unowned_env_local: 'native_method, 'native_method>(
            env: &mut Env<'unowned_env_local>,
            _cap: &mut Self::Captures<'unowned_env_local, 'native_method>,
            _payload: Box<dyn Any + Send + 'static>,
        ) -> JniResult<jbyteArray> {
            if !env.exception_check() {
                let _ = env.throw_new(
                    jni_str!("java/lang/RuntimeException"),
                    jni_str!("Veles crypto JNI operation failed"),
                );
            }
            Ok(null_mut())
        }
    }

    #[unsafe(no_mangle)]
    pub extern "system" fn Java_me_nagaev_veles_crypto_VelesCrypto_reverseBytes<'local>(
        mut env: EnvUnowned<'local>,
        _class: JClass<'local>,
        input: JByteArray<'local>,
    ) -> jbyteArray {
        env.with_env(|env| -> JniResult<jbyteArray> {
            let input = env.convert_byte_array(&input)?;
            Ok(env
                .byte_array_from_slice(&reverse_bytes(&input))?
                .into_raw())
        })
        .resolve::<ThrowGenericRuntimeException>()
    }
}

#[cfg(test)]
mod tests {
    use super::reverse_bytes;

    #[test]
    fn reverses_known_bytes() {
        assert_eq!(
            reverse_bytes(&[0x00, 0x80, 0xff, 0x2a]),
            [0x2a, 0xff, 0x80, 0x00]
        );
    }

    #[test]
    fn reverses_empty_input() {
        assert!(reverse_bytes(&[]).is_empty());
    }

    #[test]
    fn reversing_twice_restores_arbitrary_binary_input() {
        let input = [0x00, 0x01, 0x7f, 0x80, 0xfe, 0xff];
        assert_eq!(reverse_bytes(&reverse_bytes(&input)), input);
    }
}

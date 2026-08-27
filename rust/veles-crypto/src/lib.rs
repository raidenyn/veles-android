#[cfg(target_arch = "wasm32")]
use wasm_bindgen::prelude::wasm_bindgen;

#[cfg_attr(target_arch = "wasm32", wasm_bindgen)]
pub fn reverse_bytes(input: &[u8]) -> Vec<u8> {
    input.iter().rev().copied().collect()
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

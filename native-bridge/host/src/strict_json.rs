//! Strict JSON intake for the control envelope (OTP2-BRD-002).

use std::fmt;

use serde::Deserialize;
use serde::de::{self, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value};

#[derive(Debug, PartialEq, Eq)]
pub enum JsonError {
    /// Not valid UTF-8 JSON.
    Syntax,
    DuplicateKey,
    /// Bytes (including whitespace) after the single JSON document.
    TrailingBytes,
    /// A number that is not an integer representable as i64/u64.
    NonInteger,
    Null,
}

const MARK_DUPLICATE: &str = "veles:duplicate-key";
const MARK_NON_INTEGER: &str = "veles:non-integer";
const MARK_NULL: &str = "veles:null";

/// The deepest container nesting `serde_json`'s default recursion limit accepts. Rejecting the
/// next level here, before entering the recursive deserializer, keeps hostile nesting off the
/// call stack: one recursive level costs over a kilobyte of stack in a debug build (more with
/// MSVC), so a small reader stack could otherwise overflow while merely rejecting the input.
const MAX_NESTING_DEPTH: usize = 127;

/// Scans the raw document for containers nested deeper than [`MAX_NESTING_DEPTH`]. Brackets and
/// braces inside strings (including escaped quotes) are not structural. This only decides the
/// same nesting boundary `serde_json` enforces; malformed input is left for the parser to
/// reject.
fn exceeds_nesting_limit(bytes: &[u8]) -> bool {
    let mut depth = 0usize;
    let mut in_string = false;
    let mut escaped = false;
    for &byte in bytes {
        if in_string {
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                in_string = false;
            }
        } else {
            match byte {
                b'"' => in_string = true,
                b'[' | b'{' => {
                    depth += 1;
                    if depth > MAX_NESTING_DEPTH {
                        return true;
                    }
                }
                b']' | b'}' => depth = depth.saturating_sub(1),
                _ => {}
            }
        }
    }
    false
}

struct Strict(Value);

struct StrictVisitor;

impl<'de> Visitor<'de> for StrictVisitor {
    type Value = Value;

    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("a JSON value without nulls, floats or duplicate keys")
    }

    fn visit_bool<E: de::Error>(self, v: bool) -> Result<Value, E> {
        Ok(Value::Bool(v))
    }

    fn visit_i64<E: de::Error>(self, v: i64) -> Result<Value, E> {
        Ok(Value::Number(Number::from(v)))
    }

    fn visit_u64<E: de::Error>(self, v: u64) -> Result<Value, E> {
        Ok(Value::Number(Number::from(v)))
    }

    fn visit_f64<E: de::Error>(self, _: f64) -> Result<Value, E> {
        Err(E::custom(MARK_NON_INTEGER))
    }

    fn visit_str<E: de::Error>(self, v: &str) -> Result<Value, E> {
        Ok(Value::String(v.to_owned()))
    }

    fn visit_string<E: de::Error>(self, v: String) -> Result<Value, E> {
        Ok(Value::String(v))
    }

    fn visit_unit<E: de::Error>(self) -> Result<Value, E> {
        Err(E::custom(MARK_NULL))
    }

    fn visit_none<E: de::Error>(self) -> Result<Value, E> {
        Err(E::custom(MARK_NULL))
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Value, A::Error> {
        let mut items = Vec::new();
        while let Some(Strict(item)) = seq.next_element()? {
            items.push(item);
        }
        Ok(Value::Array(items))
    }

    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Value, A::Error> {
        let mut object = Map::new();
        while let Some(key) = map.next_key::<String>()? {
            let Strict(value) = map.next_value()?;
            if object.insert(key, value).is_some() {
                return Err(de::Error::custom(MARK_DUPLICATE));
            }
        }
        Ok(Value::Object(object))
    }
}

impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        deserializer.deserialize_any(StrictVisitor).map(Strict)
    }
}

/// Parses exactly one JSON document, rejecting duplicate keys at any depth, nulls, non-integer
/// numbers and any trailing bytes.
pub fn parse_strict(bytes: &[u8]) -> Result<Value, JsonError> {
    if exceeds_nesting_limit(bytes) {
        return Err(JsonError::Syntax);
    }
    let mut stream = serde_json::Deserializer::from_slice(bytes).into_iter::<Strict>();
    let value = match stream.next() {
        Some(Ok(Strict(value))) => value,
        Some(Err(error)) => {
            let message = error.to_string();
            return Err(if message.starts_with(MARK_DUPLICATE) {
                JsonError::DuplicateKey
            } else if message.starts_with(MARK_NON_INTEGER) {
                JsonError::NonInteger
            } else if message.starts_with(MARK_NULL) {
                JsonError::Null
            } else {
                JsonError::Syntax
            });
        }
        None => return Err(JsonError::Syntax),
    };
    if stream.byte_offset() != bytes.len() {
        return Err(JsonError::TrailingBytes);
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn nested_arrays(depth: usize) -> Vec<u8> {
        let mut bytes = Vec::with_capacity(depth * 2);
        bytes.extend(std::iter::repeat_n(b'[', depth));
        bytes.extend(std::iter::repeat_n(b']', depth));
        bytes
    }

    #[test]
    fn nesting_boundary_matches_the_json_recursion_limit() {
        assert!(parse_strict(&nested_arrays(MAX_NESTING_DEPTH)).is_ok());
        assert_eq!(
            parse_strict(&nested_arrays(MAX_NESTING_DEPTH + 1)),
            Err(JsonError::Syntax)
        );
    }

    #[test]
    fn brackets_and_braces_inside_strings_do_not_count_as_nesting() {
        let text = format!("[[[[{{{{\\\"{}", "[]{}".repeat(200));
        let doc = serde_json::json!({ "text": text });
        let bytes = serde_json::to_vec(&doc).unwrap();
        assert_eq!(parse_strict(&bytes), Ok(doc));
    }
}

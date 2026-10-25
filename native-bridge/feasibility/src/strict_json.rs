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

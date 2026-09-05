// Minimal, dependency-free ZIP reader for extracting a single entry's bytes
// from a deterministic ZIP archive. Used by extract-view.mjs to obtain the
// Chrome native-messaging host manifest JSON the producer archives into the
// Windows package (the macOS package is a tar.gz, read via deterministic-tar).
//
// Only the subset of ZIP features the deterministic producer (yazl, with
// DEFLATE compression and no zip64) emits is supported:
//   - local file headers + central directory
//   - DEFLATE (method 8) and STORE (method 0) entries
//   - no data descriptors (yazl writes sizes in the local header)
//   - no zip64
//
// The implementation reads the End Of Central Directory record at the file
// tail, walks the central directory to locate the requested entry, then reads
// the entry's local file header to find the compressed payload offset and
// inflates it. Paths use forward slashes; backslashes are rejected.

import { inflateRawSync } from 'node:zlib';

const SIG_EOCD = 0x06054b50;
const SIG_CENTRAL = 0x02014b50;
const SIG_LOCAL = 0x04034b50;

function readU16(buffer, offset) {
  return buffer.readUInt16LE(offset);
}

function readU32(buffer, offset) {
  return buffer.readUInt32LE(offset);
}

function findEOCD(buffer) {
  // EOCD is at most 22 + 65535 bytes from the end; scan backwards for the
  // signature. The comment length field at EOCD+20 gives the exact offset,
  // but a backwards scan is robust to a trailing comment.
  const minEocd = 22;
  const maxBack = Math.min(buffer.length - minEocd, 65557);
  for (let back = 0; back <= maxBack; back += 1) {
    const off = buffer.length - minEocd - back;
    if (readU32(buffer, off) === SIG_EOCD) {
      // Validate the comment length matches the distance to EOF.
      const commentLength = readU16(buffer, off + 20);
      if (off + minEocd + commentLength === buffer.length) return off;
    }
  }
  throw new Error('zip: End Of Central Directory record not found');
}

function centralEntries(buffer) {
  const eocd = findEOCD(buffer);
  const entriesCount = readU16(buffer, eocd + 10);
  const cdOffset = readU32(buffer, eocd + 16);
  const entries = [];
  let off = cdOffset;
  for (let i = 0; i < entriesCount; i += 1) {
    if (readU32(buffer, off) !== SIG_CENTRAL) throw new Error(`zip: bad central directory signature at ${off}`);
    const method = readU16(buffer, off + 10);
    const compressedSize = readU32(buffer, off + 20);
    const uncompressedSize = readU32(buffer, off + 24);
    const nameLength = readU16(buffer, off + 28);
    const extraLength = readU16(buffer, off + 30);
    const commentLength = readU16(buffer, off + 32);
    const localHeaderOffset = readU32(buffer, off + 42);
    const name = buffer.subarray(off + 46, off + 46 + nameLength).toString('utf8');
    entries.push({ method, compressedSize, uncompressedSize, name, localHeaderOffset });
    off += 46 + nameLength + extraLength + commentLength;
  }
  return entries;
}

function readLocalEntry(buffer, entry) {
  const off = entry.localHeaderOffset;
  if (readU32(buffer, off) !== SIG_LOCAL) throw new Error(`zip: bad local file header signature at ${off}`);
  const method = readU16(buffer, off + 8);
  if (method !== entry.method) throw new Error(`zip: local/central method mismatch for ${entry.name}`);
  const nameLength = readU16(buffer, off + 26);
  const extraLength = readU16(buffer, off + 28);
  const dataStart = off + 30 + nameLength + extraLength;
  const dataEnd = dataStart + entry.compressedSize;
  if (dataEnd > buffer.length) throw new Error(`zip: entry data truncated: ${entry.name}`);
  const compressed = buffer.subarray(dataStart, dataEnd);
  if (method === 0) return compressed; // STORE
  if (method === 8) return inflateRawSync(compressed); // DEFLATE
  throw new Error(`zip: unsupported compression method ${method} for ${entry.name}`);
}

// List the entry names in a ZIP archive buffer.
export function listZip(buffer) {
  return centralEntries(buffer).map((entry) => entry.name);
}

// Extract a single entry's bytes from a ZIP archive buffer. Throws if the
// entry is absent. Returns a Buffer.
export function readZipEntry(buffer, name) {
  const entries = centralEntries(buffer);
  const entry = entries.find((e) => e.name === name);
  if (!entry) throw new Error(`zip: entry not found: ${name}`);
  const bytes = readLocalEntry(buffer, entry);
  if (bytes.length !== entry.uncompressedSize) {
    throw new Error(`zip: size mismatch for ${entry.name}: expected ${entry.uncompressedSize}, got ${bytes.length}`);
  }
  return bytes;
}
import { error, mismatch, validateRelativePath } from '../lib/checksum-manifest.mjs';
import { posix } from 'node:path';

const BLOCK = 512;

function tarPath(path) {
  const normalized = path.endsWith('/') ? path.slice(0, -1) : path;
  validateRelativePath(normalized, mismatch);
  if (Buffer.byteLength(path) > 100) throw mismatch(`tar path is too long: ${path}`);
  return path;
}

function writeString(buffer, offset, length, value) {
  const bytes = Buffer.from(value, 'utf8');
  if (bytes.length > length) throw error('tar header field is too long');
  bytes.copy(buffer, offset);
}

function writeOctal(buffer, offset, length, value) {
  const text = value.toString(8).padStart(length - 1, '0');
  writeString(buffer, offset, length - 1, text);
  buffer[offset + length - 1] = 0;
}

function padding(length) {
  return (BLOCK - (length % BLOCK)) % BLOCK;
}

function validateSymlinkTarget(path, target) {
  const normalized = typeof target === 'string' ? posix.normalize(posix.join(posix.dirname(path), target)) : '';
  if (typeof target !== 'string' || target === '' || target.startsWith('/') || target.includes('\\') || normalized === '..' || normalized.startsWith('../')) {
    throw mismatch(`unsafe tar symlink target: ${path}`);
  }
}

function requireZero(buffer, offset, length, field) {
  if (!buffer.subarray(offset, offset + length).every((byte) => byte === 0)) throw mismatch(`invalid fixed tar ${field}`);
}

export function createTar(entries) {
  if (!Array.isArray(entries)) throw error('tar entries must be an array');
  const seen = new Set();
  const chunks = [];
  for (const entry of entries) {
    if (!entry || typeof entry !== 'object' || !['file', 'directory', 'symlink'].includes(entry.type)) {
      throw error('invalid tar entry');
    }
    const path = tarPath(entry.path);
    if (seen.has(path)) throw error(`duplicate tar entry: ${path}`);
    seen.add(path);
    const data = entry.type === 'file' ? Buffer.from(entry.data ?? '') : Buffer.alloc(0);
    if (entry.type === 'symlink') validateSymlinkTarget(path, entry.target);
    const header = Buffer.alloc(BLOCK);
    writeString(header, 0, 100, path);
    writeOctal(header, 100, 8, entry.mode ?? (entry.type === 'directory' ? 0o755 : 0o644));
    writeOctal(header, 108, 8, 0);
    writeOctal(header, 116, 8, 0);
    writeOctal(header, 124, 12, data.length);
    writeOctal(header, 136, 12, 0);
    header.fill(0x20, 148, 156);
    header[156] = entry.type === 'file' ? 0x30 : entry.type === 'directory' ? 0x35 : 0x32;
    if (entry.type === 'symlink') writeString(header, 157, 100, entry.target ?? '');
    writeString(header, 257, 6, 'ustar\0');
    writeString(header, 263, 2, '00');
    writeOctal(header, 148, 8, header.reduce((sum, byte) => sum + byte, 0));
    chunks.push(header, data, Buffer.alloc(padding(data.length)));
  }
  chunks.push(Buffer.alloc(BLOCK * 2));
  return Buffer.concat(chunks);
}

function readString(buffer, offset, length) {
  const end = buffer.indexOf(0, offset);
  return buffer.subarray(offset, end === -1 || end > offset + length ? offset + length : end).toString('utf8');
}

function readOctal(buffer, offset, length) {
  const value = readString(buffer, offset, length).trim();
  if (!/^[0-7]+$/.test(value)) throw mismatch('invalid tar numeric field');
  return Number.parseInt(value, 8);
}

function isZeroBlock(buffer) {
  return buffer.every((byte) => byte === 0);
}

export function parseTar(bytes) {
  const buffer = Buffer.from(bytes);
  const entries = [];
  const seen = new Set();
  let offset = 0;
  while (offset + BLOCK <= buffer.length && !isZeroBlock(buffer.subarray(offset, offset + BLOCK))) {
    const header = buffer.subarray(offset, offset + BLOCK);
    const checksum = readOctal(header, 148, 8);
    const copied = Buffer.from(header);
    copied.fill(0x20, 148, 156);
    if (copied.reduce((sum, byte) => sum + byte, 0) !== checksum) throw mismatch('invalid tar checksum');
    if (readString(header, 257, 6) !== 'ustar') throw mismatch('tar must use USTAR headers');
    if (readString(header, 263, 2) !== '00') throw mismatch('tar must use USTAR version 00');
    if (readOctal(header, 108, 8) !== 0 || readOctal(header, 116, 8) !== 0 || readOctal(header, 136, 12) !== 0) {
      throw mismatch('invalid fixed tar ownership or timestamp');
    }
    requireZero(header, 265, 32, 'user name');
    requireZero(header, 297, 32, 'group name');
    requireZero(header, 329, 8, 'device major');
    requireZero(header, 337, 8, 'device minor');
    requireZero(header, 345, 155, 'prefix');
    const rawPath = readString(header, 0, 100);
    const typeFlag = String.fromCharCode(header[156]);
    const type = typeFlag === '0' || typeFlag === '\0' ? 'file' : typeFlag === '5' ? 'directory' : typeFlag === '2' ? 'symlink' : null;
    if (!type) throw mismatch(`unsupported tar entry type: ${typeFlag || 'NUL'}`);
    const path = type === 'directory' ? `${rawPath.replace(/\/$/, '')}/` : rawPath;
    tarPath(path);
    if (seen.has(path)) throw mismatch(`duplicate tar entry: ${path}`);
    seen.add(path);
    const size = readOctal(header, 124, 12);
    if (type !== 'file' && size !== 0) throw mismatch(`non-file tar entry has content: ${path}`);
    const dataStart = offset + BLOCK;
    const dataEnd = dataStart + size;
    if (dataEnd > buffer.length) throw mismatch(`truncated tar entry: ${path}`);
    const target = type === 'symlink' ? readString(header, 157, 100) : undefined;
    if (type === 'symlink') validateSymlinkTarget(path, target);
    entries.push({ path, type, mode: readOctal(header, 100, 8), data: buffer.subarray(dataStart, dataEnd), target });
    offset = dataEnd + padding(size);
  }
  if (offset + BLOCK * 2 !== buffer.length || !isZeroBlock(buffer.subarray(offset))) throw mismatch('invalid tar trailer');
  for (const entry of entries) {
    if (entry.type !== 'directory' && entries.some((other) => other.path.startsWith(`${entry.path}/`))) {
      throw mismatch(`tar entry has children: ${entry.path}`);
    }
  }
  return entries;
}

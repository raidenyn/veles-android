// Test-only pure-JS archive readers for extracting and inspecting the
// deterministic packages emitted by scripts/package.mjs. The host may not
// ship `unzip`, and the macOS packaging intentionally avoids GNU-tar flags,
// so these parse the bytes directly with Node's zlib and assert on real
// extracted artifacts rather than grepping source.
//
// NOT production code — kept under test/ and excluded from the shipped build.

import { gunzipSync, inflateRawSync } from 'node:zlib';

// --- USTAR tar reader -------------------------------------------------------

export interface TarEntry {
    name: string;
    typeflag: string; // '0' file, '5' dir, '2' symlink, 'L' GNU long-name, etc.
    mode: number;
    size: number;
    mtime: number;
    linkname: string;
    data: Buffer; // file contents (empty for dir/symlink)
    isSymlink: boolean;
    isDirectory: boolean;
}

// Read a (possibly gzipped) tar buffer into structured entries. Supports:
//   - regular files (typeflag '0' or '\0')
//   - directories (typeflag '5')
//   - symlinks (typeflag '2', target in linkname field)
//   - GNU long-name entries (typeflag 'L') — apply name to the next entry
//   - USTAR prefix field (name = prefix + '/' + name for long paths)
export function readTar(gzBuffer: Buffer): TarEntry[] {
    const tar = gzBuffer[0] === 0x1f && gzBuffer[1] === 0x8b ? gunzipSync(gzBuffer) : gzBuffer;
    const entries: TarEntry[] = [];
    let offset = 0;
    let pendingName: string | null = null;
    while (offset + 512 <= tar.length) {
        const header = tar.subarray(offset, offset + 512);
        // End-of-archive: two zero blocks. A single all-zero block stops parsing.
        if (header.every((b) => b === 0)) break;

        const name = readString(header, 0, 100);
        const mode = parseInt(readString(header, 100, 8), 8) || 0;
        const size = parseInt(readString(header, 124, 12), 8) || 0;
        const mtime = parseInt(readString(header, 136, 12), 8) || 0;
        const typeflag = String.fromCharCode(header[156]);
        const linkname = readString(header, 157, 100);
        const magic = readString(header, 257, 6);
        const prefix = readString(header, 345, 155);

        let entryName = name;
        // USTAR prefix: when the name doesn't fit in 100 bytes, the prefix field
        // holds the leading path components and the full name is prefix + '/' + name.
        if (magic.startsWith('ustar') && prefix.length > 0) {
            entryName = `${prefix}/${name}`;
        }

        const data = tar.subarray(offset + 512, offset + 512 + size);
        const blocks = Math.ceil(size / 512);
        offset += 512 + blocks * 512;

        if (typeflag === 'L') {
            // GNU long-name: the data is the full name for the *next* entry.
            pendingName = readString(data, 0, size).replace(/\0+$/, '');
            continue;
        }

        const resolvedName = pendingName ?? entryName;
        pendingName = null;

        entries.push({
            name: resolvedName,
            typeflag,
            mode,
            size,
            mtime,
            linkname,
            data: typeflag === '5' || typeflag === '2' ? Buffer.alloc(0) : Buffer.from(data),
            isSymlink: typeflag === '2',
            isDirectory: typeflag === '5',
        });
    }
    return entries;
}

function readString(buf: Buffer, start: number, len: number): string {
    const slice = buf.subarray(start, start + len);
    // tar fields are NUL-terminated; strip trailing NULs.
    const end = slice.indexOf(0);
    return (end === -1 ? slice : slice.subarray(0, end)).toString('utf8');
}

// --- ZIP (DEFLATE) reader ---------------------------------------------------

export interface ZipEntry {
    name: string;
    data: Buffer;
    crc32: number;
    mode: number; // external attributes low 16 bits (unix mode)
}

// Minimal ZIP central-directory reader supporting DEFLATE (method 8) and
// STORE (method 0) entries. Used to inspect the deterministic Windows zip.
export function readZip(buf: Buffer): ZipEntry[] {
    // Find the End Of Central Directory record (signature 0x06054b50).
    let eocd = -1;
    for (let i = buf.length - 22; i >= Math.max(0, buf.length - 65557); i--) {
        if (buf.readUInt32LE(i) === 0x06054b50) {
            eocd = i;
            break;
        }
    }
    if (eocd === -1) throw new Error('zip: EOCD record not found');

    const cdCount = buf.readUInt16LE(eocd + 10);
    const cdOffset = buf.readUInt32LE(eocd + 16);
    const entries: ZipEntry[] = [];
    let off = cdOffset;
    for (let i = 0; i < cdCount; i++) {
        if (buf.readUInt32LE(off) !== 0x02014b50)
            throw new Error('zip: bad central directory signature');
        const method = buf.readUInt16LE(off + 10);
        const crc = buf.readUInt32LE(off + 16);
        const compSize = buf.readUInt32LE(off + 20);
        const nameLen = buf.readUInt16LE(off + 28);
        const extraLen = buf.readUInt16LE(off + 30);
        const commentLen = buf.readUInt16LE(off + 32);
        const lho = buf.readUInt32LE(off + 42);
        const mode = buf.readUInt16LE(off + 38) >> 1; // external attr high 16 bits hold unix mode; shift
        const name = buf.subarray(off + 46, off + 46 + nameLen).toString('utf8');
        // Read the local header to find the data offset.
        const localNameLen = buf.readUInt16LE(lho + 26);
        const localExtraLen = buf.readUInt16LE(lho + 28);
        const dataOffset = lho + 30 + localNameLen + localExtraLen;
        const compData = buf.subarray(dataOffset, dataOffset + compSize);
        let data: Buffer;
        if (method === 0) {
            data = Buffer.from(compData);
        } else if (method === 8) {
            data = inflateRawSync(compData);
        } else {
            throw new Error(`zip: unsupported compression method ${method}`);
        }
        entries.push({ name, data, crc32: crc, mode });
        off += 46 + nameLen + extraLen + commentLen;
    }
    return entries;
}

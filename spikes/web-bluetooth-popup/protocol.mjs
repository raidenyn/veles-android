export const SERVICE_UUID = "7e570001-6f74-702d-7370-696b65000001";
export const COMMAND_UUID = "7e570002-6f74-702d-7370-696b65000001";
export const EVENT_UUID = "7e570003-6f74-702d-7370-696b65000001";
export const FRAME_VERSION = 1;
export const MAX_FRAME_BYTES = 20;
export const HEADER_BYTES = 6;
export const PAYLOAD_BYTES = 14;
export const MAX_CHUNKS = 32;
export const MAX_MESSAGE_BYTES = 448;
export const REASSEMBLY_TIMEOUT_MS = 5000;
const TEST_KEY = "VELES_WEB_BLUETOOTH_SPIKE_ONLY_2026";
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });

export function splitMessage(messageId, payload) {
  if (!Number.isInteger(messageId) || messageId < 0 || messageId > 0xffff) {
    throw new RangeError("Message ID must be an unsigned 16-bit integer");
  }
  if (!(payload instanceof Uint8Array) || payload.byteLength === 0 || payload.byteLength > MAX_MESSAGE_BYTES) {
    throw new RangeError("Payload must contain 1 to 448 bytes");
  }
  const chunkCount = Math.ceil(payload.byteLength / PAYLOAD_BYTES);
  return Array.from({ length: chunkCount }, (_, chunkIndex) => {
    const start = chunkIndex * PAYLOAD_BYTES;
    const chunk = payload.slice(start, start + PAYLOAD_BYTES);
    const frame = new Uint8Array(HEADER_BYTES + chunk.byteLength);
    const view = new DataView(frame.buffer);
    frame[0] = FRAME_VERSION;
    view.setUint16(1, messageId, false);
    frame[3] = chunkIndex;
    frame[4] = chunkCount;
    frame[5] = chunk.byteLength;
    frame.set(chunk, HEADER_BYTES);
    return frame;
  });
}

function decodeFrame(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength < HEADER_BYTES || bytes.byteLength > MAX_FRAME_BYTES) {
    throw new Error("Frame length is outside 6 to 20 bytes");
  }
  if (bytes[0] !== FRAME_VERSION) throw new Error("Unsupported frame version");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const messageId = view.getUint16(1, false);
  const chunkIndex = bytes[3];
  const chunkCount = bytes[4];
  const payloadLength = bytes[5];
  if (chunkCount < 1 || chunkCount > MAX_CHUNKS || chunkIndex >= chunkCount) {
    throw new Error("Invalid chunk index or count");
  }
  if (payloadLength < 1 || payloadLength > PAYLOAD_BYTES || bytes.byteLength !== HEADER_BYTES + payloadLength) {
    throw new Error("Invalid frame payload length");
  }
  return { messageId, chunkIndex, chunkCount, payload: bytes.slice(HEADER_BYTES) };
}

export class FrameReassembler {
  constructor(clock = () => performance.now()) {
    this.clock = clock;
    this.messages = new Map();
  }

  accept(clientId, frameBytes) {
    const frame = decodeFrame(frameBytes);
    const key = `${clientId}\u0000${frame.messageId}`;
    let pending = this.messages.get(key);
    if (!pending) {
      if (frame.chunkIndex !== 0) return null;
      pending = {
        clientId,
        createdAt: this.clock(),
        chunkCount: frame.chunkCount,
        chunks: Array.from({ length: frame.chunkCount }),
      };
      this.messages.set(key, pending);
    }
    if (pending.chunkCount !== frame.chunkCount) throw new Error("Chunk count changed");
    if (pending.chunks[frame.chunkIndex] !== undefined) throw new Error("Duplicate chunk");
    pending.chunks[frame.chunkIndex] = frame.payload;
    if (pending.chunks.some((chunk) => chunk === undefined)) return null;
    this.messages.delete(key);
    const size = pending.chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
    const result = new Uint8Array(size);
    let offset = 0;
    for (const chunk of pending.chunks) {
      result.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return result;
  }

  expire() {
    const now = this.clock();
    let removed = 0;
    for (const [key, pending] of this.messages) {
      if (now - pending.createdAt > REASSEMBLY_TIMEOUT_MS) {
        this.messages.delete(key);
        removed += 1;
      }
    }
    return removed;
  }

  clearClient(clientId) {
    for (const [key, pending] of this.messages) {
      if (pending.clientId === clientId) this.messages.delete(key);
    }
  }
}

export function encodeMessage(message) {
  if (!message || Array.isArray(message) || typeof message.type !== "string") {
    throw new TypeError("Message must be an object with a string type");
  }
  return encoder.encode(JSON.stringify(message));
}

export function decodeMessage(bytes) {
  const message = JSON.parse(decoder.decode(bytes));
  if (!message || Array.isArray(message) || typeof message !== "object" || typeof message.type !== "string") {
    throw new TypeError("Message must be an object with a string type");
  }
  return message;
}

function bytesToBase64Url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

export function randomBase64Url(byteLength = 16) {
  return bytesToBase64Url(crypto.getRandomValues(new Uint8Array(byteLength)));
}

export async function hmacProof(role, clientNonce, serverNonce, sessionId) {
  if (role !== "server" && role !== "client") throw new TypeError("Unknown proof role");
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(TEST_KEY),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const transcript = `veles-spike-v1|${role}|${clientNonce}|${serverNonce}|${sessionId}`;
  return bytesToBase64Url(new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(transcript))));
}

export function constantTimeEqual(left, right) {
  if (typeof left !== "string" || typeof right !== "string" || left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

export async function runProtocolSelfCheck() {
  const clientNonce = "AAECAwQFBgcICQoLDA0ODw";
  const serverNonce = "EBESExQVFhcYGRobHB0eHw";
  const sessionId = "ICEiIyQlJicoKSorLC0uLw";
  const serverProof = await hmacProof("server", clientNonce, serverNonce, sessionId);
  const clientProof = await hmacProof("client", clientNonce, serverNonce, sessionId);
  if (serverProof !== "x8jA8SVauz7qJV7-jmtss7zTKkJz0lrMN3kRCVl3pdw") throw new Error("Server proof self-check failed");
  if (clientProof !== "8yMAdRW6GfNygOivmLy1c498vXbXzbN9oMuEfwi0FMI") throw new Error("Client proof self-check failed");

  const payload = Uint8Array.from({ length: 29 }, (_, index) => index);
  const reassembler = new FrameReassembler(() => 1000);
  let result = null;
  for (const frame of splitMessage(7, payload)) result = reassembler.accept("self-check", frame);
  if (!result || !result.every((byte, index) => byte === payload[index])) {
    throw new Error("Framing self-check failed");
  }
}
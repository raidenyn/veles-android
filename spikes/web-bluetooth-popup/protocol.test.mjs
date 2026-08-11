import test from "node:test";
import assert from "node:assert/strict";
import {
  FrameReassembler,
  hmacProof,
  splitMessage,
} from "./protocol.mjs";

test("framing round trips across indexed 20-byte chunks", () => {
  const payload = new TextEncoder().encode("authenticated payload");
  const frames = splitMessage(0x1234, payload);
  const reassembler = new FrameReassembler(() => 1000);
  let result = null;
  for (const frame of frames) result = reassembler.accept("phone-a", frame);
  assert.deepEqual(result, payload);
  assert.ok(frames.every((frame) => frame.byteLength <= 20));
});

test("HMAC proofs match Android vectors", async () => {
  const clientNonce = "AAECAwQFBgcICQoLDA0ODw";
  const serverNonce = "EBESExQVFhcYGRobHB0eHw";
  const sessionId = "ICEiIyQlJicoKSorLC0uLw";
  assert.equal(
    await hmacProof("server", clientNonce, serverNonce, sessionId),
    "x8jA8SVauz7qJV7-jmtss7zTKkJz0lrMN3kRCVl3pdw",
  );
  assert.equal(
    await hmacProof("client", clientNonce, serverNonce, sessionId),
    "8yMAdRW6GfNygOivmLy1c498vXbXzbN9oMuEfwi0FMI",
  );
});

test("duplicate and malformed frames are rejected", () => {
  const frames = splitMessage(7, new Uint8Array(20));
  const reassembler = new FrameReassembler(() => 1000);
  reassembler.accept("phone", frames[0]);
  assert.throws(() => reassembler.accept("phone", frames[0]), /Duplicate chunk/);

  const wrongVersion = frames[0].slice();
  wrongVersion[0] = 2;
  assert.throws(() => new FrameReassembler(() => 1000).accept("phone", wrongVersion), /version/);

  const wrongLength = frames[0].slice();
  wrongLength[5] -= 1;
  assert.throws(() => new FrameReassembler(() => 1000).accept("phone", wrongLength), /payload length/);
});

test("incomplete messages expire and nonzero chunks cannot restart them", () => {
  let now = 1000;
  const frames = splitMessage(9, new Uint8Array(20));
  const reassembler = new FrameReassembler(() => now);
  assert.equal(reassembler.accept("phone", frames[0]), null);
  now += 5001;
  assert.equal(reassembler.expire(), 1);
  assert.equal(reassembler.accept("phone", frames[1]), null);
});

test("message size and per-phone state are bounded independently", () => {
  assert.throws(() => splitMessage(10, new Uint8Array(449)), /1 to 448/);
  const payload = Uint8Array.from({ length: 20 }, (_, index) => index);
  const frames = splitMessage(10, payload);
  const reassembler = new FrameReassembler(() => 1000);
  assert.equal(reassembler.accept("phone-a", frames[0]), null);
  assert.equal(reassembler.accept("phone-b", frames[0]), null);
  assert.deepEqual(reassembler.accept("phone-a", frames[1]), payload);
  assert.deepEqual(reassembler.accept("phone-b", frames[1]), payload);
});
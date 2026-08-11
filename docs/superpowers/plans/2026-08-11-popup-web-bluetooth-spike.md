# Popup-Owned Web Bluetooth Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a debug-only Android BLE peripheral and an unpacked Manifest V3 popup that can be handed to separate Windows, macOS, and Android test machines for the physical go/no-go validation in issue #77.

**Architecture:** Pure Kotlin protocol, authentication, and client-state units sit below a debug-only Android GATT foreground service and Compose harness activity. A dependency-free JavaScript protocol module mirrors the framing and HMAC transcript, while the popup owns every Web Bluetooth connection and all ephemeral UI state. The implementation environment creates a draft PR with every hardware result marked `Not run`; the user tests that exact PR commit on physical machines and returns observations for the same PR.

**Tech Stack:** Kotlin 2.1.10, Android SDK 35/minSdk 33, coroutines, kotlinx.serialization, Jetpack Compose Material 3, Android BLE GATT/advertising, Manifest V3, browser Web Bluetooth, Web Crypto, Async Clipboard, and Node's built-in test runner without npm dependencies.

## Global Constraints

- Keep every Android harness component, permission, resource, and test-facing implementation under the debug source set; release behavior must remain unchanged.
- Do not connect the harness to `NotificationListener`, `OtpMessage`, bank handlers, real notifications, or production clipboard data.
- Use only synthetic OTP values and a public key named `VELES_WEB_BLUETOOTH_SPIKE_ONLY_2026`.
- Do not make a production PAKE, trust, persistence, encryption-above-GATT, framing, or compatibility decision.
- Keep the extension under `spikes/web-bluetooth-popup/` with no npm project, TypeScript, bundler, Gradle task, service worker, offscreen document, side panel, or tab.
- The popup document must own all `BluetoothDevice`, GATT, heartbeat, pull, push, and clipboard state; reopening starts empty and calls `requestDevice()` again.
- Support one Android phone serving Windows and macOS concurrently and one popup holding two phone connections independently.
- Do not claim real Bluetooth success from implementation-environment builds or automated tests. Every physical result begins as `Not run`.
- Create a draft PR after implementation verification. Keep it open while the user tests the exact commit on separate machines.
- Use branch `feat/77-popup-web-bluetooth-spike`. If PR #76 has merged, target `master`; otherwise target `docs/chrome-bluetooth-otp-sharing-design` and state the dependency in the PR body.

## File Map

### Android protocol and tests

- Create `app/src/debug/java/me/nagaev/veles/bluetoothspike/SpikeProtocol.kt`: UUIDs, wire envelope, JSON encoding, limits, and synthetic data constants.
- Create `app/src/debug/java/me/nagaev/veles/bluetoothspike/SpikeFrameCodec.kt`: 20-byte chunk encoding and bounded per-client reassembly.
- Create `app/src/debug/java/me/nagaev/veles/bluetoothspike/SpikeAuthenticator.kt`: role-separated HMAC proof generation and verification.
- Create `app/src/debug/java/me/nagaev/veles/bluetoothspike/SpikeSessionRegistry.kt`: per-client subscription, challenge, authentication, heartbeat, and expiry state.
- Create `app/src/debug/java/me/nagaev/veles/bluetoothspike/SpikeDeliveryQueues.kt`: independent serialized notification queues.
- Create `app/src/testDebug/java/me/nagaev/veles/bluetoothspike/SpikeFrameCodecTest.kt`: frame and reassembly tests.
- Create `app/src/testDebug/java/me/nagaev/veles/bluetoothspike/SpikeAuthenticatorTest.kt`: shared proof-vector and rejection tests.
- Create `app/src/testDebug/java/me/nagaev/veles/bluetoothspike/SpikeSessionRegistryTest.kt`: authentication gating, replay, expiry, and fan-out tests.
- Create `app/src/testDebug/java/me/nagaev/veles/bluetoothspike/SpikeDeliveryQueuesTest.kt`: per-client queue isolation tests.

### Android framework harness

- Create `app/src/debug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeState.kt`: immutable UI state and bounded process-local state store.
- Create `app/src/debug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeService.kt`: foreground service, advertiser, GATT server, session dispatch, pushes, and cleanup.
- Create `app/src/debug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeActivity.kt`: permission flow and service actions.
- Create `app/src/debug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeScreen.kt`: utilitarian Compose harness UI.
- Create `app/src/debug/AndroidManifest.xml`: debug-only Bluetooth permissions, launcher activity, and connected-device service.
- Modify `app/src/debug/res/values/strings.xml`: debug harness labels, status, controls, and notification strings.
- Create `app/src/testDebug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeStateTest.kt`: bounded state-store tests.
- Create `app/src/testDebug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeManifestTest.kt`: merged debug manifest assertions.

### Chrome harness

- Create `spikes/web-bluetooth-popup/protocol.mjs`: JavaScript UUIDs, framing, reassembly, Base64URL, HMAC, JSON, and self-check.
- Create `spikes/web-bluetooth-popup/protocol.test.mjs`: Node tests sharing the Kotlin HMAC vectors.
- Create `spikes/web-bluetooth-popup/manifest.json`: MV3 action and `clipboardWrite` permission.
- Create `spikes/web-bluetooth-popup/popup.html`: connection controls, phone list, event list, and log containers.
- Create `spikes/web-bluetooth-popup/popup.css`: compact diagnostic popup layout.
- Create `spikes/web-bluetooth-popup/popup.mjs`: independent phone connections, handshake, pull, push, heartbeat, copy, and errors.
- Create `spikes/web-bluetooth-popup/README.md`: cross-machine checkout, Android setup, Chrome installation/reset, and validation procedure.

### Evidence

- Create `docs/spikes/2026-08-11-popup-web-bluetooth-validation.md`: environment record, eight `Not run` cases, observations, limitations, and decision gate.

---

### Task 1: Define The Shared Android Wire Format And Framing

**Files:**
- Create: `app/src/debug/java/me/nagaev/veles/bluetoothspike/SpikeProtocol.kt`
- Create: `app/src/debug/java/me/nagaev/veles/bluetoothspike/SpikeFrameCodec.kt`
- Test: `app/src/testDebug/java/me/nagaev/veles/bluetoothspike/SpikeFrameCodecTest.kt`

**Interfaces:**
- Consumes: Existing kotlinx.serialization plugin and JSON dependency from `app/build.gradle.kts`.
- Produces: `SpikeProtocol`, `SpikeWireMessage`, `SpikeFrameCodec.split(messageId, payload)`, and `SpikeFrameReassembler.accept(clientId, frameBytes)` for the Android service and JavaScript mirror.

- [ ] **Step 1: Write the frame and wire-envelope tests**

Create tests that lock the exact header, bounds, JSON round trip, duplicate rejection, client isolation, and expiry behavior:

```kotlin
package me.nagaev.veles.bluetoothspike

import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull

class SpikeFrameCodecTest {
    @Test
    fun `split uses six byte header and payloads no larger than fourteen bytes`() {
        val payload = ByteArray(29) { it.toByte() }
        val frames = SpikeFrameCodec.split(messageId = 0x1234, payload = payload)

        assertEquals(3, frames.size)
        assertContentEquals(byteArrayOf(1, 0x12, 0x34, 0, 3, 14), frames[0].copyOfRange(0, 6))
        assertContentEquals(byteArrayOf(1, 0x12, 0x34, 2, 3, 1), frames[2].copyOfRange(0, 6))
        frames.forEach { frame -> require(frame.size <= SpikeProtocol.MAX_FRAME_BYTES) }
    }

    @Test
    fun `reassembler isolates clients and returns complete payload only once`() {
        val now = longArrayOf(1_000L)
        val reassembler = SpikeFrameReassembler(clockMillis = { now[0] })
        val frames = SpikeFrameCodec.split(7, "authenticated payload".encodeToByteArray())

        assertNull(reassembler.accept("client-a", frames[0]))
        assertNull(reassembler.accept("client-b", frames[0]))
        val resultA = frames.drop(1).fold<ByteArray, ByteArray?>(null) { _, frame ->
            reassembler.accept("client-a", frame)
        }
        val resultB = frames.drop(1).fold<ByteArray, ByteArray?>(null) { _, frame ->
            reassembler.accept("client-b", frame)
        }

        assertContentEquals("authenticated payload".encodeToByteArray(), resultA)
        assertContentEquals("authenticated payload".encodeToByteArray(), resultB)
    }

    @Test
    fun `duplicate frame is rejected`() {
        val reassembler = SpikeFrameReassembler(clockMillis = { 1_000L })
        val frame = SpikeFrameCodec.split(9, ByteArray(20))[0]
        reassembler.accept("client", frame)

        assertFailsWith<SpikeFrameException> { reassembler.accept("client", frame) }
    }

    @Test
    fun `incomplete message expires after five seconds`() {
        var now = 1_000L
        val reassembler = SpikeFrameReassembler(clockMillis = { now })
        val frames = SpikeFrameCodec.split(11, ByteArray(20))
        reassembler.accept("client", frames[0])
        now += SpikeProtocol.REASSEMBLY_TIMEOUT_MILLIS + 1

        assertEquals(1, reassembler.expire())
        assertNull(reassembler.accept("client", frames[1]))
    }

    @Test
    fun `oversized payload and malformed header are rejected`() {
        assertFailsWith<SpikeFrameException> { SpikeFrameCodec.split(1, ByteArray(449)) }
        val wrongVersion = SpikeFrameCodec.split(1, byteArrayOf(7)).single().also { it[0] = 2 }
        assertFailsWith<SpikeFrameException> { SpikeFrameCodec.decode(wrongVersion) }
        val wrongLength = SpikeFrameCodec.split(1, byteArrayOf(7)).single().also { it[5] = 2 }
        assertFailsWith<SpikeFrameException> { SpikeFrameCodec.decode(wrongLength) }
    }

    @Test
    fun `wire message survives strict JSON round trip`() {
        val message = SpikeWireMessage(
            type = SpikeProtocol.TYPE_OTP,
            delivery = SpikeProtocol.DELIVERY_PUSH,
            eventId = 42,
            code = "654321",
            merchant = "Synthetic Shop",
            amount = "10.00",
            currency = "USD",
            phoneLabel = "Pixel-1234",
        )

        assertEquals(message, SpikeProtocol.decode(SpikeProtocol.encode(message)))
    }
}
```

- [ ] **Step 2: Run the focused test and confirm the red state**

Run:

```bash
./gradlew testDebugUnitTest --tests "me.nagaev.veles.bluetoothspike.SpikeFrameCodecTest"
```

Expected: compilation fails because `SpikeProtocol`, `SpikeWireMessage`, `SpikeFrameCodec`, and `SpikeFrameReassembler` do not exist.

- [ ] **Step 3: Implement the strict wire envelope and constants**

Create `SpikeProtocol.kt` with these exact protocol values and fields:

```kotlin
package me.nagaev.veles.bluetoothspike

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.util.UUID

@Serializable
internal data class SpikeWireMessage(
    val type: String,
    val clientNonce: String? = null,
    val serverNonce: String? = null,
    val sessionId: String? = null,
    val proof: String? = null,
    val delivery: String? = null,
    val eventId: Int? = null,
    val code: String? = null,
    val merchant: String? = null,
    val amount: String? = null,
    val currency: String? = null,
    val phoneLabel: String? = null,
    val errorCode: String? = null,
)

internal object SpikeProtocol {
    val SERVICE_UUID: UUID = UUID.fromString("7e570001-6f74-702d-7370-696b65000001")
    val COMMAND_UUID: UUID = UUID.fromString("7e570002-6f74-702d-7370-696b65000001")
    val EVENT_UUID: UUID = UUID.fromString("7e570003-6f74-702d-7370-696b65000001")
    val CCC_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

    const val FRAME_VERSION = 1
    const val MAX_FRAME_BYTES = 20
    const val FRAME_HEADER_BYTES = 6
    const val FRAME_PAYLOAD_BYTES = MAX_FRAME_BYTES - FRAME_HEADER_BYTES
    const val MAX_CHUNKS = 32
    const val MAX_MESSAGE_BYTES = FRAME_PAYLOAD_BYTES * MAX_CHUNKS
    const val REASSEMBLY_TIMEOUT_MILLIS = 5_000L

    const val TYPE_HELLO = "hello"
    const val TYPE_CHALLENGE = "challenge"
    const val TYPE_AUTHENTICATE = "authenticate"
    const val TYPE_AUTHENTICATED = "authenticated"
    const val TYPE_PULL = "pull"
    const val TYPE_OTP = "otp"
    const val TYPE_HEARTBEAT = "heartbeat"
    const val TYPE_ERROR = "error"
    const val DELIVERY_CURRENT = "current"
    const val DELIVERY_PUSH = "push"

    private val json = Json {
        encodeDefaults = false
        explicitNulls = false
        ignoreUnknownKeys = false
    }

    fun encode(message: SpikeWireMessage): ByteArray =
        json.encodeToString(SpikeWireMessage.serializer(), message).encodeToByteArray()

    fun decode(bytes: ByteArray): SpikeWireMessage =
        json.decodeFromString(SpikeWireMessage.serializer(), bytes.decodeToString())
}
```

- [ ] **Step 4: Implement bounded chunking and reassembly**

Create `SpikeFrameCodec.kt` with a six-byte header: version, two-byte big-endian message ID, zero-based chunk index, chunk count, and payload length. `split` must reject IDs outside `0..65535`, empty payloads, payloads over 448 bytes, and more than 32 chunks. `decode` must reject a wrong version, frame lengths outside `6..20`, invalid count/index, a mismatched payload length, and trailing bytes.

Use this public shape so later tasks can consume it without Android framework types:

```kotlin
package me.nagaev.veles.bluetoothspike

internal class SpikeFrameException(message: String) : IllegalArgumentException(message)

internal data class SpikeFrame(
    val messageId: Int,
    val chunkIndex: Int,
    val chunkCount: Int,
    val payload: ByteArray,
)

internal object SpikeFrameCodec {
    fun split(messageId: Int, payload: ByteArray): List<ByteArray>
    fun decode(bytes: ByteArray): SpikeFrame
}

internal class SpikeFrameReassembler(
    private val clockMillis: () -> Long,
) {
    @Synchronized fun accept(clientId: String, frameBytes: ByteArray): ByteArray?
    @Synchronized fun expire(): Int
    @Synchronized fun clearClient(clientId: String)
    @Synchronized fun clear()
}
```

Key reassembly rules:

```kotlin
val key = clientId to frame.messageId
val pending = messages.getOrPut(key) {
    PendingMessage(
        chunkCount = frame.chunkCount,
        createdAtMillis = clockMillis(),
        chunks = arrayOfNulls(frame.chunkCount),
    )
}
if (pending.chunkCount != frame.chunkCount) throw SpikeFrameException("Chunk count changed")
if (pending.chunks[frame.chunkIndex] != null) throw SpikeFrameException("Duplicate chunk")
pending.chunks[frame.chunkIndex] = frame.payload
if (pending.chunks.any { it == null }) return null
messages.remove(key)
return pending.chunks.filterNotNull().fold(ByteArray(0)) { result, chunk -> result + chunk }
```

If an expired message receives a nonzero chunk after `expire()`, start no new message and return `null`; only chunk zero may create a pending message.

- [ ] **Step 5: Run formatting and the focused tests**

Run:

```bash
./gradlew spotlessApply testDebugUnitTest --tests "me.nagaev.veles.bluetoothspike.SpikeFrameCodecTest"
```

Expected: all `SpikeFrameCodecTest` tests pass.

- [ ] **Step 6: Commit the protocol foundation**

```bash
git add app/src/debug/java/me/nagaev/veles/bluetoothspike/SpikeProtocol.kt app/src/debug/java/me/nagaev/veles/bluetoothspike/SpikeFrameCodec.kt app/src/testDebug/java/me/nagaev/veles/bluetoothspike/SpikeFrameCodecTest.kt
git commit -m "test: define Bluetooth spike framing"
```

### Task 2: Add Challenge Authentication And Independent Client State

**Files:**
- Create: `app/src/debug/java/me/nagaev/veles/bluetoothspike/SpikeAuthenticator.kt`
- Create: `app/src/debug/java/me/nagaev/veles/bluetoothspike/SpikeSessionRegistry.kt`
- Create: `app/src/debug/java/me/nagaev/veles/bluetoothspike/SpikeDeliveryQueues.kt`
- Test: `app/src/testDebug/java/me/nagaev/veles/bluetoothspike/SpikeAuthenticatorTest.kt`
- Test: `app/src/testDebug/java/me/nagaev/veles/bluetoothspike/SpikeSessionRegistryTest.kt`
- Test: `app/src/testDebug/java/me/nagaev/veles/bluetoothspike/SpikeDeliveryQueuesTest.kt`

**Interfaces:**
- Consumes: `SpikeWireMessage`, `SpikeProtocol.TYPE_CHALLENGE`, and frame lists from Task 1.
- Produces: `SpikeAuthenticator`, `SpikeSessionRegistry`, and `SpikeDeliveryQueues` used by `BluetoothSpikeService`.

- [ ] **Step 1: Write the fixed HMAC vector tests**

Use values that the JavaScript test will repeat byte-for-byte:

```kotlin
package me.nagaev.veles.bluetoothspike

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class SpikeAuthenticatorTest {
    private val clientNonce = "AAECAwQFBgcICQoLDA0ODw"
    private val serverNonce = "EBESExQVFhcYGRobHB0eHw"
    private val sessionId = "ICEiIyQlJicoKSorLC0uLw"

    @Test
    fun `server proof matches shared cross-language vector`() {
        assertEquals(
            "x8jA8SVauz7qJV7-jmtss7zTKkJz0lrMN3kRCVl3pdw",
            SpikeAuthenticator.proof(SpikeProofRole.SERVER, clientNonce, serverNonce, sessionId),
        )
    }

    @Test
    fun `client proof matches shared cross-language vector`() {
        assertEquals(
            "8yMAdRW6GfNygOivmLy1c498vXbXzbN9oMuEfwi0FMI",
            SpikeAuthenticator.proof(SpikeProofRole.CLIENT, clientNonce, serverNonce, sessionId),
        )
    }

    @Test
    fun `verify rejects changed proof`() {
        assertTrue(SpikeAuthenticator.verify(SpikeProofRole.CLIENT, clientNonce, serverNonce, sessionId, "8yMAdRW6GfNygOivmLy1c498vXbXzbN9oMuEfwi0FMI"))
        assertFalse(SpikeAuthenticator.verify(SpikeProofRole.CLIENT, clientNonce, serverNonce, sessionId, "8yMAdRW6GfNygOivmLy1c498vXbXzbN9oMuEfwi0FMJ"))
    }
}
```

- [ ] **Step 2: Write session and queue tests**

Cover these exact behaviors in `SpikeSessionRegistryTest` and `SpikeDeliveryQueuesTest`:

```kotlin
@Test
fun `OTP delivery is gated until subscription and client proof succeed`() {
    var now = 1_000L
    val randomValues = ArrayDeque(listOf(ByteArray(16) { 0x10 }, ByteArray(16) { 0x20 }))
    val sessions = SpikeSessionRegistry(
        clockMillis = { now },
        randomBytes = { randomValues.removeFirst() },
    )
    sessions.onConnected("desktop-a", "Windows")
    sessions.onSubscribed("desktop-a")

    val challenge = sessions.beginAuthentication("desktop-a", "AAECAwQFBgcICQoLDA0ODw")
    val proof = SpikeAuthenticator.proof(
        SpikeProofRole.CLIENT,
        challenge.clientNonce!!,
        challenge.serverNonce!!,
        challenge.sessionId!!,
    )

    assertTrue(sessions.authenticate("desktop-a", proof))
    assertEquals(listOf("desktop-a"), sessions.authenticatedTargets())
    assertFalse(sessions.authenticate("desktop-a", proof), "A consumed challenge must not replay")
}

@Test
fun `heartbeat expiry removes only the stale authenticated client`() {
    var now = 1_000L
    val randomValues = ArrayDeque(
        listOf(
            ByteArray(16) { 0x10 },
            ByteArray(16) { 0x20 },
            ByteArray(16) { 0x30 },
            ByteArray(16) { 0x40 },
        ),
    )
    val sessions = SpikeSessionRegistry(
        clockMillis = { now },
        randomBytes = { randomValues.removeFirst() },
    )
    listOf("desktop-a", "desktop-b").forEach { clientId ->
        sessions.onConnected(clientId, clientId)
        sessions.onSubscribed(clientId)
        val challenge = sessions.beginAuthentication(clientId, "AAECAwQFBgcICQoLDA0ODw")
        val proof = SpikeAuthenticator.proof(
            SpikeProofRole.CLIENT,
            challenge.clientNonce!!,
            challenge.serverNonce!!,
            challenge.sessionId!!,
        )
        assertTrue(sessions.authenticate(clientId, proof))
    }

    now += SpikeSessionRegistry.HEARTBEAT_TIMEOUT_MILLIS - 1
    sessions.heartbeat("desktop-b")
    now += 2

    assertEquals(listOf("desktop-a"), sessions.expiredAuthenticatedClients())
    assertEquals(listOf("desktop-b"), sessions.authenticatedTargets())
}

@Test
fun `delivery completion advances only the matching client queue`() {
    val queues = SpikeDeliveryQueues()
    queues.enqueue("desktop-a", listOf(byteArrayOf(1), byteArrayOf(2)))
    queues.enqueue("desktop-b", listOf(byteArrayOf(9)))

    assertContentEquals(byteArrayOf(1), queues.peek("desktop-a"))
    assertNull(queues.complete("desktop-b"))
    assertContentEquals(byteArrayOf(2), queues.complete("desktop-a"))
}
```

Add imports for `assertContentEquals` and `assertNull`; keep all setup in the test files and do not bypass public registry methods.

- [ ] **Step 3: Run tests and confirm missing-type failures**

```bash
./gradlew testDebugUnitTest --tests "me.nagaev.veles.bluetoothspike.SpikeAuthenticatorTest" --tests "me.nagaev.veles.bluetoothspike.SpikeSessionRegistryTest" --tests "me.nagaev.veles.bluetoothspike.SpikeDeliveryQueuesTest"
```

Expected: compilation fails because the authentication, registry, and queue types do not exist.

- [ ] **Step 4: Implement role-separated HMAC proofs**

Create `SpikeAuthenticator.kt` with these exact transcript and key rules:

```kotlin
package me.nagaev.veles.bluetoothspike

import java.security.MessageDigest
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

internal enum class SpikeProofRole(val wireName: String) {
    SERVER("server"),
    CLIENT("client"),
}

internal object SpikeAuthenticator {
    private const val TEST_KEY = "VELES_WEB_BLUETOOTH_SPIKE_ONLY_2026"
    private val encoder = Base64.getUrlEncoder().withoutPadding()

    fun proof(
        role: SpikeProofRole,
        clientNonce: String,
        serverNonce: String,
        sessionId: String,
    ): String {
        val transcript = "veles-spike-v1|${role.wireName}|$clientNonce|$serverNonce|$sessionId"
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(TEST_KEY.encodeToByteArray(), "HmacSHA256"))
        return encoder.encodeToString(mac.doFinal(transcript.encodeToByteArray()))
    }

    fun verify(
        role: SpikeProofRole,
        clientNonce: String,
        serverNonce: String,
        sessionId: String,
        candidate: String,
    ): Boolean = MessageDigest.isEqual(
        proof(role, clientNonce, serverNonce, sessionId).encodeToByteArray(),
        candidate.encodeToByteArray(),
    )
}
```

- [ ] **Step 5: Implement session transitions and expiry**

Create `SpikeSessionRegistry.kt` with this API and constants:

```kotlin
internal class SpikeSessionRegistry(
    private val clockMillis: () -> Long,
    private val randomBytes: (Int) -> ByteArray,
) {
    companion object {
        const val AUTHENTICATION_TIMEOUT_MILLIS = 10_000L
        const val HEARTBEAT_TIMEOUT_MILLIS = 15_000L
    }

    @Synchronized fun onConnected(clientId: String, label: String)
    @Synchronized fun onSubscribed(clientId: String)
    @Synchronized fun beginAuthentication(clientId: String, clientNonce: String): SpikeWireMessage
    @Synchronized fun authenticate(clientId: String, proof: String): Boolean
    @Synchronized fun heartbeat(clientId: String): Boolean
    @Synchronized fun isAuthenticated(clientId: String): Boolean
    @Synchronized fun authenticatedTargets(): List<String>
    @Synchronized fun expiredAuthenticatedClients(): List<String>
    @Synchronized fun remove(clientId: String)
    @Synchronized fun clear()
}
```

Store a private mutable `Session` per client with label, subscribed flag, pending challenge,
authenticated flag, and last heartbeat. Validate that the Base64URL client nonce decodes to 16
bytes. Generate 16-byte server nonce and session ID values. Return a challenge whose
`clientNonce`, `serverNonce`, `sessionId`, and server `proof` are all populated. Reject proofs
after ten seconds, clear every pending challenge after one authentication attempt, and update
the heartbeat timestamp only for authenticated sessions. Sort returned client IDs for stable
tests and UI output. `expiredAuthenticatedClients()` removes stale sessions before returning
their sorted IDs, which makes `authenticatedTargets()` immediately exclude them. Require the
client to be connected and subscribed before creating a challenge. Synchronize every public
method because GATT callbacks and the maintenance timer run on different threads.

- [ ] **Step 6: Implement independent delivery queues**

Create `SpikeDeliveryQueues.kt` as a synchronized map from client ID to `ArrayDeque<ByteArray>`:

```kotlin
internal class SpikeDeliveryQueues {
    @Synchronized fun enqueue(clientId: String, frames: List<ByteArray>): Boolean
    @Synchronized fun peek(clientId: String): ByteArray?
    @Synchronized fun complete(clientId: String): ByteArray?
    @Synchronized fun remove(clientId: String)
    @Synchronized fun clear()
}
```

`enqueue` returns `true` only when the client queue was empty before insertion, signaling the
service to start sending. `complete` removes the current frame and returns the next frame, or
`null` when that client queue is empty. Copy arrays at the boundary so callers cannot mutate
queued frames.

- [ ] **Step 7: Run focused and full debug unit tests**

```bash
./gradlew spotlessApply testDebugUnitTest --tests "me.nagaev.veles.bluetoothspike.*"
./gradlew testDebugUnitTest
```

Expected: all new tests and the existing debug unit suite pass.

- [ ] **Step 8: Commit authenticated session state**

```bash
git add app/src/debug/java/me/nagaev/veles/bluetoothspike/SpikeAuthenticator.kt app/src/debug/java/me/nagaev/veles/bluetoothspike/SpikeSessionRegistry.kt app/src/debug/java/me/nagaev/veles/bluetoothspike/SpikeDeliveryQueues.kt app/src/testDebug/java/me/nagaev/veles/bluetoothspike/SpikeAuthenticatorTest.kt app/src/testDebug/java/me/nagaev/veles/bluetoothspike/SpikeSessionRegistryTest.kt app/src/testDebug/java/me/nagaev/veles/bluetoothspike/SpikeDeliveryQueuesTest.kt
git commit -m "test: gate Bluetooth spike sessions"
```

### Task 3: Implement The Android BLE Foreground Service

**Files:**
- Create: `app/src/debug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeState.kt`
- Create: `app/src/debug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeService.kt`
- Modify: `app/src/debug/res/values/strings.xml`
- Test: `app/src/testDebug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeStateTest.kt`

**Interfaces:**
- Consumes: All protocol, session, frame, and delivery types from Tasks 1 and 2.
- Produces: `BluetoothSpikeStateStore.state`, service actions, BLE advertising, GATT callbacks, authenticated pull/push, heartbeat cleanup, and scheduled synthetic pushes for the activity in Task 4.

- [ ] **Step 1: Write the bounded state-store test**

```kotlin
package me.nagaev.veles.bluetoothspike

import kotlin.test.Test
import kotlin.test.assertEquals

class BluetoothSpikeStateTest {
    @Test
    fun `event log retains newest one hundred entries`() {
        BluetoothSpikeStateStore.reset()
        repeat(105) { index -> BluetoothSpikeStateStore.event("event-$index") }

        val events = BluetoothSpikeStateStore.state.value.events
        assertEquals(100, events.size)
        assertEquals("event-5", events.first().message)
        assertEquals("event-104", events.last().message)
    }
}
```

- [ ] **Step 2: Run the state test and confirm the missing-type failure**

```bash
./gradlew testDebugUnitTest --tests "me.nagaev.veles.bluetoothspike.BluetoothSpikeStateTest"
```

Expected: compilation fails because `BluetoothSpikeStateStore` does not exist.

- [ ] **Step 3: Implement observable debug state**

Create immutable `BluetoothSpikeUiState`, `BluetoothSpikeClientState`, and `BluetoothSpikeEvent`
data classes. Implement a process-local singleton store with `MutableStateFlow.update` and these
methods:

```kotlin
internal data class BluetoothSpikeUiState(
    val supported: Boolean? = null,
    val availabilityDetail: String = "Not checked",
    val serviceRunning: Boolean = false,
    val advertising: Boolean = false,
    val clients: List<BluetoothSpikeClientState> = emptyList(),
    val events: List<BluetoothSpikeEvent> = emptyList(),
    val lastError: String? = null,
)

internal data class BluetoothSpikeClientState(
    val id: String,
    val label: String,
    val subscribed: Boolean,
    val authenticated: Boolean,
)

internal data class BluetoothSpikeEvent(
    val timestamp: String,
    val message: String,
    val error: Boolean,
)

internal object BluetoothSpikeStateStore {
    val state: StateFlow<BluetoothSpikeUiState>
    fun reset()
    fun serviceRunning(running: Boolean)
    fun availability(supported: Boolean, detail: String)
    fun advertising(advertising: Boolean)
    fun client(clientId: String, label: String, subscribed: Boolean, authenticated: Boolean)
    fun removeClient(clientId: String)
    fun clearClients()
    fun event(message: String)
    fun error(message: String)
}
```

Use `DateTimeFormatter.ISO_LOCAL_TIME` with the system clock for visible timestamps, cap the log
at 100 entries, and never put the shared HMAC key or proof values in state.

- [ ] **Step 4: Implement the foreground-service shell and notification**

Create `BluetoothSpikeService : Service` with these action constants and helpers:

```kotlin
companion object {
    const val ACTION_START = "me.nagaev.veles.bluetoothspike.START"
    const val ACTION_PUSH_NOW = "me.nagaev.veles.bluetoothspike.PUSH_NOW"
    const val ACTION_SCHEDULE_PUSH = "me.nagaev.veles.bluetoothspike.SCHEDULE_PUSH"
    const val ACTION_OPEN = "me.nagaev.veles.bluetoothspike.OPEN"
    const val EXTRA_DELAY_MILLIS = "delay_millis"
    const val SMOKE_PUSH_DELAY_MILLIS = 10_000L
    const val LIFECYCLE_PUSH_DELAY_MILLIS = 20 * 60 * 1_000L
    private const val CHANNEL_ID = "VelesBluetoothSpike"
    private const val NOTIFICATION_ID = 7701

    fun intent(context: Context, action: String): Intent =
        Intent(context, BluetoothSpikeService::class.java).setAction(action)
}
```

Call `startForeground` immediately for `ACTION_START`, create a low-importance debug channel,
and use an `ACTION_OPEN` package-scoped pending intent so Task 4 can attach the spike activity.
Return `START_NOT_STICKY`; process restoration is outside scope. Ignore and stop a service created
with Push or Schedule before a successful Start action. On destroy, cancel all delayed pushes,
advertising, GATT sessions, the coroutine scope, queues, and reassembly state. Set running and
advertising false and clear client rows, but retain the bounded event log so the tester can read
the shutdown sequence.
Add debug resources `bluetooth_spike_notification_channel`,
`bluetooth_spike_notification_title`, and `bluetooth_spike_notification_text` in this step so
the service never hard-codes visible notification copy.

```xml
<string name="bluetooth_spike_notification_channel">BLE spike service</string>
<string name="bluetooth_spike_notification_title">Veles BLE spike active</string>
<string name="bluetooth_spike_notification_text">Synthetic-only Bluetooth testing is running</string>
```

- [ ] **Step 5: Add BLE capability checks, GATT service, and advertising**

Require `BLUETOOTH_ADVERTISE` and `BLUETOOTH_CONNECT` before touching the adapter. Distinguish a
missing adapter, disabled Bluetooth, unsupported multiple advertising, missing permissions,
failure to open a GATT server, GATT service-add failure, and advertiser error codes in state.

Build the service exactly as follows:

```kotlin
val command = BluetoothGattCharacteristic(
    SpikeProtocol.COMMAND_UUID,
    BluetoothGattCharacteristic.PROPERTY_WRITE,
    BluetoothGattCharacteristic.PERMISSION_WRITE_ENCRYPTED_MITM,
)
val events = BluetoothGattCharacteristic(
    SpikeProtocol.EVENT_UUID,
    BluetoothGattCharacteristic.PROPERTY_NOTIFY,
    BluetoothGattCharacteristic.PERMISSION_READ_ENCRYPTED_MITM,
)
events.addDescriptor(
    BluetoothGattDescriptor(
        SpikeProtocol.CCC_UUID,
        BluetoothGattDescriptor.PERMISSION_READ_ENCRYPTED_MITM or
            BluetoothGattDescriptor.PERMISSION_WRITE_ENCRYPTED_MITM,
    ),
)
val service = BluetoothGattService(
    SpikeProtocol.SERVICE_UUID,
    BluetoothGattService.SERVICE_TYPE_PRIMARY,
).apply {
    addCharacteristic(command)
    addCharacteristic(events)
}
```

After `onServiceAdded` succeeds, advertise connectably at low latency. Put the 128-bit service
UUID in primary advertisement data and the adapter device name in scan-response data so the
31-byte primary packet cannot overflow. If the advertiser reports
`ADVERTISE_FAILED_DATA_TOO_LARGE`, retry once without the device-name scan response and record
`Advertising without device name` as a visible limitation rather than failing the harness.

- [ ] **Step 6: Implement callback dispatch and authenticated messages**

Handle only non-prepared, offset-zero writes to the command characteristic. Send the Android
write response, feed valid frames into the per-client reassembler, strictly decode a complete
`SpikeWireMessage`, and dispatch:

```kotlin
when (message.type) {
    SpikeProtocol.TYPE_HELLO -> sendMessage(clientId, sessions.beginAuthentication(clientId, requireNotNull(message.clientNonce)))
    SpikeProtocol.TYPE_AUTHENTICATE -> {
        if (sessions.authenticate(clientId, requireNotNull(message.proof))) {
            updateClientState(clientId)
            sendMessage(
                clientId,
                SpikeWireMessage(
                    type = SpikeProtocol.TYPE_AUTHENTICATED,
                    phoneLabel = phoneLabel,
                ),
            )
        } else {
            sendError(clientId, "authentication_rejected")
        }
    }
    SpikeProtocol.TYPE_PULL -> {
        if (sessions.isAuthenticated(clientId)) sendSyntheticOtp(clientId, SpikeProtocol.DELIVERY_CURRENT)
        else sendError(clientId, "not_authenticated")
    }
    SpikeProtocol.TYPE_HEARTBEAT -> {
        if (!sessions.heartbeat(clientId)) sendError(clientId, "not_authenticated")
    }
    else -> sendError(clientId, "unsupported_message")
}
```

Use `BluetoothDevice.address` as the debug-only connection key and `BluetoothDevice.name` only
as a visible client label after permission checks. Generate `phoneLabel` once per service run as
`Build.MODEL` plus a four-digit random suffix.

Respond to a valid CCC enable write by marking only that client subscribed. On disconnect,
clear that client's session, reassembly, queue, device map, and UI row. On notification
completion, advance only that client's `SpikeDeliveryQueues` entry. Use the API 33
`notifyCharacteristicChanged(device, characteristic, false, frame)` overload and treat a
non-`BluetoothStatusCodes.SUCCESS` return as a per-client delivery failure.

Use a `ConcurrentHashMap<String, BluetoothDevice>` for connected devices and synchronized core
objects for all session, reassembly, and delivery transitions. Maintain a separate unsigned
16-bit outbound message counter per client, wrap `65535` to `1`, and call
`SpikeDeliveryQueues.enqueue`; only an enqueue that returns `true` starts the first notification.

- [ ] **Step 7: Implement synthetic pushes and heartbeat cleanup**

Use an `AtomicInteger` event counter. Event `n` has code `(100000 + n % 900000).toString()`,
merchant `Synthetic Shop`, amount `10.00`, currency `USD`, and the service-run phone label.
Current pulls use event ID zero and code `123456`. Pushes increment the counter and enqueue a
separate frame list for every current `authenticatedTargets()` client.

Run a five-second maintenance callback that expires incomplete frames and calls
`BluetoothGattServer.cancelConnection(device)` for every client returned by
`expiredAuthenticatedClients()`. Implement scheduled pushes with `Handler.postDelayed`; keep
the runnable references so stopping the service cancels them.

- [ ] **Step 8: Run tests and compile the debug service**

```bash
./gradlew spotlessApply testDebugUnitTest --tests "me.nagaev.veles.bluetoothspike.*"
./gradlew compileDebugKotlin
```

Expected: all core tests pass and the framework service compiles without a manifest entry yet.

- [ ] **Step 9: Commit the Android BLE service**

```bash
git add app/src/debug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeState.kt app/src/debug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeService.kt app/src/debug/res/values/strings.xml app/src/testDebug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeStateTest.kt
git commit -m "feat: add debug Bluetooth spike service"
```

### Task 4: Add The Debug Manifest And Android Harness UI

**Files:**
- Create: `app/src/debug/AndroidManifest.xml`
- Create: `app/src/debug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeActivity.kt`
- Create: `app/src/debug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeScreen.kt`
- Modify: `app/src/debug/res/values/strings.xml`
- Test: `app/src/testDebug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeManifestTest.kt`

**Interfaces:**
- Consumes: `BluetoothSpikeStateStore`, `BluetoothSpikeService.intent`, `SMOKE_PUSH_DELAY_MILLIS`, and `LIFECYCLE_PUSH_DELAY_MILLIS` from Task 3.
- Produces: A second debug launcher named Veles BLE Spike, permission flow, visible service controls, client status, scheduled pushes, and event diagnostics.

- [ ] **Step 1: Write merged-manifest assertions**

```kotlin
package me.nagaev.veles.bluetoothspike

import android.Manifest
import android.app.ServiceInfo
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.test.core.app.ApplicationProvider
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class BluetoothSpikeManifestTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()

    @Test
    fun `debug manifest declares connected device foreground service`() {
        val info = context.packageManager.getServiceInfo(
            ComponentName(context, BluetoothSpikeService::class.java),
            PackageManager.ComponentInfoFlags.of(0),
        )
        assertEquals(ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE, info.foregroundServiceType)
        assertTrue(!info.exported)
    }

    @Test
    fun `debug manifest exposes spike launcher and Bluetooth permissions`() {
        val launchers = context.packageManager.queryIntentActivities(
            Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER).setPackage(context.packageName),
            PackageManager.ResolveInfoFlags.of(0),
        )
        val permissions = context.packageManager.getPackageInfo(
            context.packageName,
            PackageManager.PackageInfoFlags.of(PackageManager.GET_PERMISSIONS.toLong()),
        ).requestedPermissions.toSet()

        assertTrue(launchers.any { it.activityInfo.name == BluetoothSpikeActivity::class.java.name })
        assertTrue(Manifest.permission.BLUETOOTH_ADVERTISE in permissions)
        assertTrue(Manifest.permission.BLUETOOTH_CONNECT in permissions)
        assertTrue(Manifest.permission.POST_NOTIFICATIONS in permissions)
        assertTrue(Manifest.permission.FOREGROUND_SERVICE_CONNECTED_DEVICE in permissions)
    }
}
```

- [ ] **Step 2: Run the manifest test and confirm failure**

```bash
./gradlew testDebugUnitTest --tests "me.nagaev.veles.bluetoothspike.BluetoothSpikeManifestTest"
```

Expected: compilation fails because `BluetoothSpikeActivity` does not exist.

- [ ] **Step 3: Add only debug permissions and components**

Create `app/src/debug/AndroidManifest.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-permission android:name="android.permission.BLUETOOTH_ADVERTISE" />
    <uses-permission android:name="android.permission.BLUETOOTH_CONNECT" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_CONNECTED_DEVICE" />

    <uses-feature
        android:name="android.hardware.bluetooth_le"
        android:required="false" />

    <application>
        <activity
            android:name=".bluetoothspike.BluetoothSpikeActivity"
            android:exported="true"
            android:label="@string/bluetooth_spike_title">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
            <intent-filter>
                <action android:name="me.nagaev.veles.bluetoothspike.OPEN" />
                <category android:name="android.intent.category.DEFAULT" />
            </intent-filter>
        </activity>

        <service
            android:name=".bluetoothspike.BluetoothSpikeService"
            android:exported="false"
            android:foregroundServiceType="connectedDevice" />
    </application>
</manifest>
```

- [ ] **Step 4: Implement permission and service actions in the activity**

Create a plain `ComponentActivity`, without Hilt, that registers
`ActivityResultContracts.RequestMultiplePermissions` for `BLUETOOTH_ADVERTISE` and
`BLUETOOTH_CONNECT`, plus the already-declared `POST_NOTIFICATIONS` permission so the foreground
indication is visible during the lifecycle test. A pending Start action resumes only if all
three grants are true. Use
`ContextCompat.startForegroundService` for Start and normal `startService` for push actions.
Use `stopService` for Stop.

```kotlin
private fun sendServiceAction(action: String, delayMillis: Long? = null) {
    val intent = BluetoothSpikeService.intent(this, action).apply {
        delayMillis?.let { putExtra(BluetoothSpikeService.EXTRA_DELAY_MILLIS, it) }
    }
    if (action == BluetoothSpikeService.ACTION_START) {
        ContextCompat.startForegroundService(this, intent)
    } else {
        startService(intent)
    }
}
```

In `setContent`, collect `BluetoothSpikeStateStore.state` with
`collectAsStateWithLifecycle()`, wrap the screen in `VelesTheme`, and pass callbacks for Start,
Stop, Push now, Schedule 10 seconds, Schedule 20 minutes, and opening
`Settings.ACTION_BLUETOOTH_SETTINGS`.

- [ ] **Step 5: Implement the diagnostic Compose screen**

Use a `LazyColumn` with Material 3 cards and no production navigation changes. Show:

- A title and explicit `Synthetic data only` warning.
- Capability detail, service running, advertising, and last error.
- Start, Stop, Bluetooth settings, Push now, Schedule 10s, and Schedule 20m buttons.
- Connected client cards with label, address, subscribed state, and authenticated state.
- The newest event at the bottom of a bounded timestamped event list.

Disable Start while running, disable Stop/push/schedule while stopped, and use a confirmation
dialog before scheduling the 20-minute lifecycle push. Put every displayed string in
`app/src/debug/res/values/strings.xml`; retain the existing debug `app_name` override.

Use this screen interface so the activity contains Android actions while the composable only
renders state:

```kotlin
@Composable
internal fun BluetoothSpikeScreen(
    state: BluetoothSpikeUiState,
    onStart: () -> Unit,
    onStop: () -> Unit,
    onOpenBluetoothSettings: () -> Unit,
    onPushNow: () -> Unit,
    onScheduleSmokePush: () -> Unit,
    onScheduleLifecyclePush: () -> Unit,
)
```

Append these exact resources:

```xml
<string name="bluetooth_spike_title">Veles BLE Spike</string>
<string name="bluetooth_spike_synthetic_warning">Synthetic data only. Never use real OTPs.</string>
<string name="bluetooth_spike_start">Start service</string>
<string name="bluetooth_spike_stop">Stop service</string>
<string name="bluetooth_spike_open_settings">Bluetooth settings</string>
<string name="bluetooth_spike_push_now">Push now</string>
<string name="bluetooth_spike_schedule_smoke">Schedule in 10 seconds</string>
<string name="bluetooth_spike_schedule_lifecycle">Schedule in 20 minutes</string>
<string name="bluetooth_spike_schedule_confirm_title">Schedule lifecycle push?</string>
<string name="bluetooth_spike_schedule_confirm_body">The service will push synthetic data in 20 minutes.</string>
<string name="bluetooth_spike_confirm">Schedule</string>
<string name="bluetooth_spike_cancel">Cancel</string>
<string name="bluetooth_spike_service_status">Service running: %1$b</string>
<string name="bluetooth_spike_advertising_status">Advertising: %1$b</string>
<string name="bluetooth_spike_clients">Connected clients</string>
<string name="bluetooth_spike_no_clients">No clients connected</string>
<string name="bluetooth_spike_client_address">Address: %1$s</string>
<string name="bluetooth_spike_client_subscription">Subscribed: %1$b</string>
<string name="bluetooth_spike_client_authentication">Authenticated: %1$b</string>
<string name="bluetooth_spike_events">Event log</string>
<string name="bluetooth_spike_no_events">No events recorded</string>
<string name="bluetooth_spike_event_row">%1$s  %2$s</string>
```

- [ ] **Step 6: Run manifest tests and build the debug APK**

```bash
./gradlew spotlessApply testDebugUnitTest --tests "me.nagaev.veles.bluetoothspike.*"
./gradlew assembleDebug
```

Expected: tests pass and `app/build/outputs/apk/debug/app-debug.apk` exists.

- [ ] **Step 7: Commit the Android harness UI**

```bash
git add app/src/debug/AndroidManifest.xml app/src/debug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeActivity.kt app/src/debug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeScreen.kt app/src/debug/res/values/strings.xml app/src/testDebug/java/me/nagaev/veles/bluetoothspike/BluetoothSpikeManifestTest.kt
git commit -m "feat: add Bluetooth spike debug harness"
```

### Task 5: Mirror Framing And Authentication In JavaScript

**Files:**
- Create: `spikes/web-bluetooth-popup/protocol.mjs`
- Create: `spikes/web-bluetooth-popup/protocol.test.mjs`

**Interfaces:**
- Consumes: Exact UUID, frame header, limits, JSON field names, key, transcript, and proof vectors from Tasks 1 and 2.
- Produces: `splitMessage`, `FrameReassembler`, `encodeMessage`, `decodeMessage`, `randomBase64Url`, `hmacProof`, `constantTimeEqual`, and `runProtocolSelfCheck` for the popup.

- [ ] **Step 1: Write dependency-free Node protocol tests**

Create `protocol.test.mjs` using `node:test` and `node:assert/strict`:

```javascript
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
```

- [ ] **Step 2: Run the Node test and confirm module-not-found failure**

```bash
node --test spikes/web-bluetooth-popup/protocol.test.mjs
```

Expected: failure because `protocol.mjs` does not exist.

- [ ] **Step 3: Implement the exact browser protocol mirror**

Implement these constants and functions in `protocol.mjs`:

```javascript
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
        chunks: Array(frame.chunkCount),
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
```

This uses the same six-byte big-endian header and strict bounds as Kotlin. HMAC signs this exact
transcript:

```javascript
`veles-spike-v1|${role}|${clientNonce}|${serverNonce}|${sessionId}`
```

Base64URL has no padding. `runProtocolSelfCheck` verifies both fixed proofs and a 29-byte frame
round trip and throws an `Error` on mismatch.

- [ ] **Step 4: Run JavaScript protocol tests and syntax checks**

```bash
node --test spikes/web-bluetooth-popup/protocol.test.mjs
node --check spikes/web-bluetooth-popup/protocol.mjs
```

Expected: all Node tests pass and syntax checking exits zero.

- [ ] **Step 5: Commit the cross-language protocol mirror**

```bash
git add spikes/web-bluetooth-popup/protocol.mjs spikes/web-bluetooth-popup/protocol.test.mjs
git commit -m "test: mirror Bluetooth spike protocol in JavaScript"
```

### Task 6: Build The Popup-Owned Multi-Phone Web Bluetooth Harness

**Files:**
- Create: `spikes/web-bluetooth-popup/manifest.json`
- Create: `spikes/web-bluetooth-popup/popup.html`
- Create: `spikes/web-bluetooth-popup/popup.css`
- Create: `spikes/web-bluetooth-popup/popup.mjs`

**Interfaces:**
- Consumes: All exports from `protocol.mjs` and Android wire types from Tasks 1 through 3.
- Produces: A directly loadable MV3 action popup with explicit device selection, per-phone GATT sessions, current pull, push, one-shot asynchronous copy, manual copy, heartbeat, and visible errors.

- [ ] **Step 1: Create and validate the minimal MV3 manifest**

Create `manifest.json`:

```json
{
  "manifest_version": 3,
  "name": "Veles Web Bluetooth Spike",
  "version": "0.1.0",
  "description": "Synthetic-only popup Web Bluetooth feasibility harness.",
  "permissions": ["clipboardWrite"],
  "action": {
    "default_title": "Veles BLE Spike",
    "default_popup": "popup.html"
  }
}
```

Validate it without adding a package file:

```bash
node -e 'JSON.parse(require("node:fs").readFileSync("spikes/web-bluetooth-popup/manifest.json", "utf8"))'
```

Expected: exit zero.

- [ ] **Step 2: Build the static popup structure and diagnostic styling**

Create a 420-pixel-wide popup with these stable element IDs:

```html
<button id="connect-phone" type="button">Connect phone</button>
<label><input id="copy-next-push" type="checkbox"> Copy next push</label>
<p id="self-check" role="status">Checking protocol...</p>
<section id="phones" aria-label="Connected phones"></section>
<section id="otp-events" aria-label="Synthetic OTP events"></section>
<ol id="event-log" aria-label="Event log"></ol>
<template id="phone-template">
  <article class="phone-card">
    <h2 data-field="phone-label">Selected phone</h2>
    <p data-field="phone-status" role="status">Connecting...</p>
    <div class="actions">
      <button data-action="pull" type="button" disabled>Pull current</button>
      <button data-action="disconnect" type="button">Disconnect</button>
    </div>
  </article>
</template>
<template id="otp-template">
  <article class="otp-card">
    <h2><span data-field="delivery"></span> from <span data-field="source"></span></h2>
    <p class="otp-code" data-field="code"></p>
    <p><span data-field="merchant"></span>, <span data-field="amount"></span> <span data-field="currency"></span></p>
    <p>Event <span data-field="event-id"></span></p>
    <button data-action="copy" type="button">Copy code</button>
  </article>
</template>
<script type="module" src="popup.mjs"></script>
```

The phone template contains source label, status, Pull current, and Disconnect controls. The
OTP template contains source, delivery type, event ID, code, merchant, amount/currency, and a
manual Copy button. CSS must keep controls keyboard accessible, show error/success states
without color alone, cap the event and log regions, and avoid visual polish that could obscure
diagnostics.

Use this compact baseline in `popup.css`, adding only matching status classes used by
`popup.mjs`:

```css
:root {
  color-scheme: light dark;
  font-family: system-ui, sans-serif;
}
body {
  box-sizing: border-box;
  margin: 0;
  padding: 12px;
  width: 420px;
}
button, input { font: inherit; }
button { min-height: 36px; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; }
.phone-card, .otp-card {
  border: 1px solid currentColor;
  border-radius: 8px;
  margin-block: 10px;
  padding: 10px;
}
.otp-code {
  font-family: ui-monospace, monospace;
  font-size: 1.5rem;
  font-weight: 700;
  letter-spacing: 0.12em;
}
#phones, #otp-events, #event-log {
  max-height: 240px;
  overflow-y: auto;
}
.status-error::before { content: "Error: "; font-weight: 700; }
.status-success::before { content: "Success: "; font-weight: 700; }
```

- [ ] **Step 3: Implement one isolated connection object per selected phone**

Create `class PhoneConnection` in `popup.mjs` with this concrete state and connection/write
implementation:

```javascript
import {
  COMMAND_UUID,
  EVENT_UUID,
  FrameReassembler,
  SERVICE_UUID,
  constantTimeEqual,
  decodeMessage,
  encodeMessage,
  hmacProof,
  randomBase64Url,
  runProtocolSelfCheck,
  splitMessage,
} from "./protocol.mjs";

class PhoneConnection {
  constructor(device, ui, log, onClosed) {
    this.device = device;
    this.ui = ui;
    this.log = log;
    this.onClosed = onClosed;
    this.server = null;
    this.command = null;
    this.events = null;
    this.authenticated = false;
    this.phoneLabel = device.name || "Unnamed phone";
    this.messageId = 1;
    this.writeChain = Promise.resolve();
    this.reassembler = new FrameReassembler();
    this.authentication = null;
    this.clientNonce = null;
    this.heartbeatTimer = null;
    this.closed = false;
    this.onNotification = this.handleNotification.bind(this);
    this.onDisconnected = () => this.disconnect("GATT disconnected");
  }

  async connect() {
    this.device.addEventListener("gattserverdisconnected", this.onDisconnected);
    this.server = await this.device.gatt.connect();
    const service = await this.server.getPrimaryService(SERVICE_UUID);
    this.command = await service.getCharacteristic(COMMAND_UUID);
    this.events = await service.getCharacteristic(EVENT_UUID);
    this.events.addEventListener("characteristicvaluechanged", this.onNotification);
    await this.events.startNotifications();

    this.clientNonce = randomBase64Url(16);
    this.authentication = Promise.withResolvers();
    const timeout = setTimeout(
      () => this.authentication.reject(new Error("Authentication timed out")),
      10000,
    );
    await this.send({ type: "hello", clientNonce: this.clientNonce });
    try {
      await this.authentication.promise;
    } finally {
      clearTimeout(timeout);
    }
  }

  async send(message) {
    const messageId = this.messageId;
    this.messageId = this.messageId === 0xffff ? 1 : this.messageId + 1;
    const frames = splitMessage(messageId, encodeMessage(message));
    this.writeChain = this.writeChain.then(async () => {
      for (const frame of frames) await this.command.writeValueWithResponse(frame);
    });
    return this.writeChain;
  }

  async pull() {
    if (!this.authenticated) throw new Error("Phone is not authenticated");
    await this.send({ type: "pull" });
  }

  disconnect(reason) {
    if (this.closed) return;
    this.closed = true;
    clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
    this.authenticated = false;
    this.events?.removeEventListener("characteristicvaluechanged", this.onNotification);
    this.device.removeEventListener("gattserverdisconnected", this.onDisconnected);
    if (this.device.gatt.connected) this.device.gatt.disconnect();
    this.ui.setStatus(reason);
    this.onClosed(this.device.id);
  }
}
```

`connect()` must call `device.gatt.connect()`, obtain the fixed service and characteristics,
attach `gattserverdisconnected`, attach the notification listener, call `startNotifications()`,
create a fresh 16-byte client nonce, send `hello`, and await authentication with a ten-second
timeout. Do not use `navigator.bluetooth.getDevices()` or retain a device outside the popup.

Wrap `connect()` and every UI-triggered `send()` in a per-phone `try/catch`; a write failure
changes only that phone to an error state and calls `disconnect(error.name + ": " + error.message)`.
Implement the small `ui` adapter in the same file with `setStatus(text)`, `setLabel(text)`,
`addOtp(message)`, and `remove()` methods that write through `textContent` and the phone card's
buttons. Enable Pull only after `setStatus("Authenticated")`. Pass an `onClosed` callback that
deletes the device ID from the connection `Map` and removes its card, allowing a later explicit
chooser selection of the same phone.

- [ ] **Step 4: Implement challenge handling, OTP rendering, and heartbeat**

On `challenge`, require all nonce/session/proof fields, verify the server proof with
`constantTimeEqual`, send the client proof, and keep the session unauthenticated. On
`authenticated`, require `phoneLabel`, resolve the pending authentication, start a five-second
heartbeat interval, and immediately issue a pull. On `otp`, require every synthetic data field
and render it with `textContent`, never `innerHTML`. On `error`, show the Android `errorCode` on
that phone card. Reject unknown message types in the visible log.

Implement the message transitions with this structure:

```javascript
async handleMessage(message) {
  if (message.type === "challenge") {
    for (const field of ["clientNonce", "serverNonce", "sessionId", "proof"]) {
      if (typeof message[field] !== "string") throw new Error(`Challenge missing ${field}`);
    }
    if (message.clientNonce !== this.clientNonce) throw new Error("Challenge client nonce changed");
    const expected = await hmacProof(
      "server",
      message.clientNonce,
      message.serverNonce,
      message.sessionId,
    );
    if (!constantTimeEqual(expected, message.proof)) throw new Error("Server proof rejected");
    const proof = await hmacProof(
      "client",
      message.clientNonce,
      message.serverNonce,
      message.sessionId,
    );
    await this.send({ type: "authenticate", proof });
    return;
  }

  if (message.type === "authenticated") {
    if (typeof message.phoneLabel !== "string") throw new Error("Authenticated message missing phone label");
    this.authenticated = true;
    this.phoneLabel = message.phoneLabel;
    this.ui.setLabel(message.phoneLabel);
    this.ui.setStatus("Authenticated");
    this.authentication.resolve();
    this.heartbeatTimer = setInterval(
      () => {
        this.reassembler.expire();
        this.send({ type: "heartbeat" }).catch((error) => this.disconnect(error.message));
      },
      5000,
    );
    await this.pull();
    return;
  }

  if (message.type === "otp") {
    for (const field of ["delivery", "eventId", "code", "merchant", "amount", "currency", "phoneLabel"]) {
      if (message[field] === null || message[field] === undefined) throw new Error(`OTP missing ${field}`);
    }
    await this.ui.addOtp(message);
    return;
  }

  if (message.type === "error") {
    throw new Error(`Android error: ${message.errorCode || "unknown"}`);
  }
  throw new Error(`Unsupported message type: ${message.type}`);
}
```

The notification callback must copy the `DataView` bytes immediately before awaiting anything:

```javascript
const bytes = new Uint8Array(
  event.target.value.buffer,
  event.target.value.byteOffset,
  event.target.value.byteLength,
).slice();
const complete = this.reassembler.accept(this.device.id, bytes);
if (complete) await this.handleMessage(decodeMessage(complete));
```

Place that body inside `handleNotification(event)` and catch errors at its event-listener
boundary. Reject the pending authentication promise, record the exact exception, and disconnect
only that phone.

- [ ] **Step 5: Implement explicit multi-phone selection and copy behavior**

The Connect button directly calls:

```javascript
const device = await navigator.bluetooth.requestDevice({
  filters: [{ services: [SERVICE_UUID] }],
});
```

Keep a `Map` keyed by `device.id`. If the selected device is already connected, report that and
do not replace it. A second Connect click may add another `PhoneConnection`; never disconnect
the first as part of selection.

Manual Copy calls `navigator.clipboard.writeText(code)` from its button. For a pushed OTP, if
`copy-next-push` is checked, clear the checkbox before awaiting
`navigator.clipboard.writeText(code)` so only the first concurrently received push consumes the
one-shot request. Record exact success or exception name in the log. Do not auto-copy current
pull responses.

On popup `pagehide`, clear every heartbeat and call `device.gatt.disconnect()` when connected.
Android heartbeat expiry remains the fallback when `pagehide` does not run.

- [ ] **Step 6: Gate connection on startup self-check and platform support**

At startup, run `runProtocolSelfCheck()`. Disable Connect and display the exception if it fails.
Also disable Connect with a clear message when `navigator.bluetooth` or
`navigator.clipboard.writeText` is unavailable. Bound visible OTPs to the newest 20 and log
entries to the newest 100 for the popup lifetime.

- [ ] **Step 7: Run static extension verification**

```bash
node --test spikes/web-bluetooth-popup/protocol.test.mjs
node --check spikes/web-bluetooth-popup/protocol.mjs
node --check spikes/web-bluetooth-popup/popup.mjs
node -e 'const manifest = JSON.parse(require("node:fs").readFileSync("spikes/web-bluetooth-popup/manifest.json", "utf8")); if (manifest.manifest_version !== 3 || manifest.action.default_popup !== "popup.html" || !manifest.permissions.includes("clipboardWrite")) process.exit(1)'
```

Expected: all commands exit zero. This does not count as physical Bluetooth validation.

- [ ] **Step 8: Commit the popup harness**

```bash
git add spikes/web-bluetooth-popup/manifest.json spikes/web-bluetooth-popup/popup.html spikes/web-bluetooth-popup/popup.css spikes/web-bluetooth-popup/popup.mjs
git commit -m "feat: add popup Web Bluetooth spike"
```

### Task 7: Write Cross-Machine Instructions And The Empty Validation Report

**Files:**
- Create: `spikes/web-bluetooth-popup/README.md`
- Create: `docs/spikes/2026-08-11-popup-web-bluetooth-validation.md`
- Include: `docs/superpowers/specs/2026-08-11-popup-web-bluetooth-spike-design.md`
- Include: `docs/superpowers/plans/2026-08-11-popup-web-bluetooth-spike.md`

**Interfaces:**
- Consumes: Branch name, APK path, Android controls, extension directory, and eight validation cases from all previous tasks.
- Produces: A complete handoff that the user can follow on other machines without implementation-environment context, plus a report that makes no unperformed claims.

- [ ] **Step 1: Write exact PR-branch checkout and update instructions**

The README starts with a synthetic-only warning and gives both checkout methods.

GitHub CLI:

```bash
git switch master
git pull --ff-only
gh pr checkout "$(gh pr list --repo raidenyn/veles-android --head feat/77-popup-web-bluetooth-spike --json number --jq '.[0].number')"
git rev-parse HEAD
```

Normal Git:

```bash
git fetch origin feat/77-popup-web-bluetooth-spike
git switch --create feat/77-popup-web-bluetooth-spike --track origin/feat/77-popup-web-bluetooth-spike
git rev-parse HEAD
```

For later PR updates:

```bash
git switch feat/77-popup-web-bluetooth-spike
git pull --ff-only
git rev-parse HEAD
```

Tell the tester to copy the SHA into the report before every run and never reuse results after a
harness change unless the PR explicitly says the case is unaffected.

- [ ] **Step 2: Document Android build, install, launch, and reset**

Document JDK 17 and Android SDK/platform-tools prerequisites, then use:

```bash
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Explain that the debug APK exposes a second launcher named **Veles BLE Spike**. Give the exact
flow: grant Nearby Devices, enable Bluetooth, Start service, verify the foreground indication
and Advertising state, use Push now or the schedule buttons, and Stop before resetting.

For a clean app state:

```bash
adb shell am force-stop me.nagaev.veles.debug
adb shell pm clear me.nagaev.veles.debug
```

State that force-stop is a reset operation and is not part of the foreground lifecycle pass.
Explain how to remove each desktop from Android Bluetooth settings before fresh-pairing cases.

- [ ] **Step 3: Document unpacked Chrome installation and clean-state handling**

Give these exact stable Chrome instructions for Windows and macOS:

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Select **Load unpacked**.
4. Select the checked-out `spikes/web-bluetooth-popup/` directory.
5. Pin **Veles Web Bluetooth Spike** from the extensions toolbar menu.
6. Open the popup and verify `Protocol self-check passed` before selecting **Connect phone**.

Explain that after pulling a new commit the tester opens `chrome://extensions` and selects the
extension's Reload button. For fresh extension state, remove it, remove the OS Bluetooth pairing,
reload the same unpacked directory, and record the resulting prompts. Explain how to open the
popup inspector only for troubleshooting.

Add this warning prominently: popup DevTools can keep a popup document alive and invalidate
lifecycle observations. Every chooser-survival, popup-closure, multi-device, and clipboard
pass/fail run must be repeated with popup DevTools closed.

- [ ] **Step 4: Write the eight-case physical procedure**

The README repeats exact setup, steps, expected observations, and reset boundaries for:

1. Windows fresh chooser/pair/auth/pull/push/asynchronous copy.
2. macOS fresh chooser/pair/auth/pull/push/asynchronous copy.
3. Windows popup close, Android disconnect/15-second expiry, reopen, explicit reselection, pull.
4. macOS popup close, Android disconnect/15-second expiry, reopen, explicit reselection, pull.
5. One phone concurrently serving Windows and macOS, with a push reaching both.
6. One Windows popup concurrently connected to two phones, with source-specific pull and push.
7. One macOS popup concurrently connected to two phones, with source-specific pull and push.
8. Android schedule-20m lifecycle: remove task and lock screen, reconnect after 15 minutes from
   an already paired desktop, pull, then remain connected until the scheduled push arrives.

Fresh pairing runs once per OS. Closure and both multi-device cases run twice. Include exact
clipboard verification: place known text on the clipboard, select Copy next push, trigger a
push, paste into a local text editor, and confirm the synthetic six-digit code replaced it.

- [ ] **Step 5: Create a report containing only explicit `Not run` results**

Create `docs/spikes/2026-08-11-popup-web-bluetooth-validation.md` with:

- Status `Awaiting physical validation`.
- PR, commit, phone, Windows, macOS, Bluetooth adapter, and Chrome-version fields initialized to
  `Not run` rather than blank values.
- A table with the eight named cases, expected result, actual result `Not run`, outcome
  `Not run`, timing `Not run`, and limitations `Not run`.
- A repetitions table for closure and multi-device reruns.
- Separate observed limitations and environment failures sections initialized to `None recorded`.
- Decision `Inconclusive: physical validation has not run`.
- Exact go/no-go rules copied from the approved spec.
- A statement that implementation-environment tests validate construction only.

- [ ] **Step 6: Check documentation for stale paths and unperformed claims**

```bash
test -f app/build/outputs/apk/debug/app-debug.apk
test -f spikes/web-bluetooth-popup/manifest.json
rg -n "TBD|TODO|PLACEHOLDER" spikes/web-bluetooth-popup/README.md docs/spikes/2026-08-11-popup-web-bluetooth-validation.md
rg -n "Not run|Inconclusive: physical validation has not run" docs/spikes/2026-08-11-popup-web-bluetooth-validation.md
```

Expected: both file checks succeed, the placeholder search has no matches, and the report-state
search shows every unexecuted result and the inconclusive decision.

- [ ] **Step 7: Commit the tester handoff**

```bash
git add spikes/web-bluetooth-popup/README.md docs/spikes/2026-08-11-popup-web-bluetooth-validation.md docs/superpowers/specs/2026-08-11-popup-web-bluetooth-spike-design.md docs/superpowers/plans/2026-08-11-popup-web-bluetooth-spike.md
git commit -m "docs: add Web Bluetooth spike test guide"
```

### Task 8: Verify Release Isolation And Open The Draft Pull Request

**Files:**
- Verify: all files from Tasks 1 through 7
- Include in PR: `docs/superpowers/specs/2026-08-11-popup-web-bluetooth-spike-design.md`
- Include in PR: `docs/superpowers/plans/2026-08-11-popup-web-bluetooth-spike.md`

**Interfaces:**
- Consumes: Complete implementation, tests, spec, plan, and testing handoff.
- Produces: A reviewed draft PR on branch `feat/77-popup-web-bluetooth-spike`, ready for the user's separate physical test environments.

- [ ] **Step 1: Run all repository and extension verification from a clean status**

```bash
./gradlew spotlessApply
./gradlew testDebugUnitTest
./gradlew assembleDebug
./gradlew assembleRelease
node --test spikes/web-bluetooth-popup/protocol.test.mjs
node --check spikes/web-bluetooth-popup/protocol.mjs
node --check spikes/web-bluetooth-popup/popup.mjs
git diff --check
```

Expected: every command exits zero. If `spotlessApply` changes Kotlin, inspect and commit only
those formatting changes before continuing.

- [ ] **Step 2: Inspect the release APK manifest for debug leakage**

Use the `aapt2` executable from Android build tools. When no release keystore environment is
configured, run:

```bash
aapt2 dump permissions app/build/outputs/apk/release/app-release-unsigned.apk
aapt2 dump xmltree --file AndroidManifest.xml app/build/outputs/apk/release/app-release-unsigned.apk
```

When release signing is configured:

```bash
aapt2 dump permissions app/build/outputs/apk/release/app-release.apk
aapt2 dump xmltree --file AndroidManifest.xml app/build/outputs/apk/release/app-release.apk
```

Expected output contains none of:

```text
me.nagaev.veles.bluetoothspike
android.permission.BLUETOOTH_ADVERTISE
android.permission.BLUETOOTH_CONNECT
android.permission.FOREGROUND_SERVICE
android.permission.FOREGROUND_SERVICE_CONNECTED_DEVICE
```

Also inspect the debug APK and confirm those entries are present:

```bash
aapt2 dump permissions app/build/outputs/apk/debug/app-debug.apk
aapt2 dump xmltree --file AndroidManifest.xml app/build/outputs/apk/debug/app-debug.apk
```

- [ ] **Step 3: Review the implementation against the approved spec**

Invoke the `requesting-code-review` skill. The review must check BLE callback correctness,
Android permission/foreground-service rules, per-client isolation, frame bounds, proof transcript
agreement, asynchronous clipboard behavior, popup-only ownership, release isolation, absence of
real OTP data, and whether the README can be followed on a separate machine. Fix findings in new
commits; do not amend existing commits.

- [ ] **Step 4: Confirm branch contents and working-tree cleanliness**

```bash
git status --short --branch
git log --oneline --decorate -15
```

Expected: no uncommitted files, the spec and plan are included, implementation commits are
focused, and no unrelated workspace changes appear in the PR range. If PR #76 is merged, inspect
`git diff --stat origin/master...HEAD`. If it remains open, inspect
`git diff --stat origin/docs/chrome-bluetooth-otp-sharing-design...HEAD`.

- [ ] **Step 5: Push the feature branch**

```bash
git push -u origin feat/77-popup-web-bluetooth-spike
```

Expected: the branch is published without force-push.

- [ ] **Step 6: Determine the correct PR base from issue #76**

```bash
gh pr view 76 --repo raidenyn/veles-android --json state,headRefName,baseRefName,url
```

If state is `MERGED`, use base `master`. If state is `OPEN`, use base
`docs/chrome-bluetooth-otp-sharing-design` and include `Depends on #76` at the top of the body.
Do not create a PR against a closed-unmerged design branch; stop and ask the user for the desired
base in that case.

- [ ] **Step 7: Create the draft PR with explicit external-test status**

Write `/tmp/opencode/issue-77-pr.md` with this body, omitting `Depends on #76` only when #76 is
already merged:

```markdown
Depends on #76

## Summary
- add a debug-only Android BLE peripheral and connected-device foreground-service harness
- add a dependency-free MV3 popup for authenticated synthetic pull, push, and asynchronous copy
- add cross-machine installation instructions and the issue #77 physical validation matrix

## Automated verification
- `./gradlew testDebugUnitTest`
- `./gradlew assembleDebug`
- `./gradlew assembleRelease`
- `node --test spikes/web-bluetooth-popup/protocol.test.mjs`
- JavaScript syntax and MV3 manifest checks
- release APK manifest contains no spike components or Bluetooth spike permissions

## Physical validation
Not run in the implementation environment. This PR remains draft while the exact commit is
tested on physical Windows, macOS, and Android devices. See
`spikes/web-bluetooth-popup/README.md` and
`docs/spikes/2026-08-11-popup-web-bluetooth-validation.md`.

Closes #77 only after the physical report contains a supported go/no-go decision.
```

Create the PR with the base selected in Step 6:

```bash
gh pr create --draft --repo raidenyn/veles-android --base docs/chrome-bluetooth-otp-sharing-design --head feat/77-popup-web-bluetooth-spike --title "spike: validate popup-owned Web Bluetooth" --body-file /tmp/opencode/issue-77-pr.md
```

When #76 is merged, replace the base argument with `master` and remove `Depends on #76` from the
body. Return the resulting PR URL to the user together with the exact commit SHA they should pull.

- [ ] **Step 8: Stop at the external physical-test handoff**

Do not mark the PR ready, merge it, close issue #77, or change the report from `Not run`. Wait for
the user's Windows, macOS, and Android observations. When results arrive, update the same PR,
rerun affected cases after any harness fix, commit the final decision, and only then mark the PR
ready for review.

### Task 9: Incorporate External Results And Finalize The Decision

**Files:**
- Modify: `docs/spikes/2026-08-11-popup-web-bluetooth-validation.md`
- Modify if a harness defect is found: the affected files from Tasks 1 through 7
- Modify if instructions were unclear: `spikes/web-bluetooth-popup/README.md`

**Interfaces:**
- Consumes: Written observations from the user's physical Windows, macOS, and Android test
  environments, each tied to an exact PR commit.
- Produces: A supported go/no-go report, updated draft PR, and issue #77 comment. Inconclusive
  evidence leaves the PR draft and identifies the exact rerun needed.

- [ ] **Step 1: Validate the returned evidence before editing the report**

Confirm that every result identifies the PR commit SHA, phone model/Android build, desktop
model/OS build, stable Chrome version, actual steps, outcome, timing, and limitation. Reject a
result captured with popup DevTools open unless it was repeated with DevTools closed. Reject a
result from an older harness commit when a later change affected that case.

- [ ] **Step 2: Separate harness defects from platform failures**

If an observation exposes a harness exception, malformed frame, incorrect permission, or unclear
instruction, invoke the `systematic-debugging` skill, reproduce it where possible, add a failing
automated regression test, fix it in a new commit, push the same branch, and ask the user to pull
the new SHA and rerun affected cases. Do not record a known harness defect as a Chrome or Android
platform failure.

- [ ] **Step 3: Replace every tested `Not run` cell with written evidence**

For each completed case, record the environment, actual chooser/pairing sequence, authentication,
pull, push, clipboard, disconnect or heartbeat timing, repetitions, and limitations. Keep
untested cases as `Not run`; do not infer results from another platform or topology.

Set exactly one decision:

- `Go` only if all eight core cases and required repetitions pass.
- `No-go` if a core assumption fails reproducibly after harness defects are excluded; include the
  recommendation to return the roadmap to a persistent side-panel or tab design.
- `Inconclusive` if required evidence is missing or an environment problem remains unidentified;
  list the exact missing rerun and keep the PR draft.

- [ ] **Step 4: Re-run implementation verification after any report or harness update**

```bash
./gradlew spotlessApply
./gradlew testDebugUnitTest
./gradlew assembleDebug
./gradlew assembleRelease
node --test spikes/web-bluetooth-popup/protocol.test.mjs
node --check spikes/web-bluetooth-popup/protocol.mjs
node --check spikes/web-bluetooth-popup/popup.mjs
git diff --check
```

Expected: every command exits zero. Automated success remains separate from the recorded physical
result.

- [ ] **Step 5: Commit and push the written physical result**

```bash
git add docs/spikes/2026-08-11-popup-web-bluetooth-validation.md spikes/web-bluetooth-popup/README.md
git commit -m "docs: record Web Bluetooth spike results"
git push
```

If no README change was required, omit it from `git add`. Keep harness fixes in earlier focused
commits rather than folding them into the report commit.

- [ ] **Step 6: Update PR and issue state from the decision**

For `Go` or `No-go`, update the PR's Physical validation section with the tested commit, decision,
and report link, then mark it ready:

```bash
gh pr ready "$(gh pr view --json number --jq .number)"
```

Write `/tmp/opencode/issue-77-result.md` with the device/OS/Chrome matrix, decision, observed
limitations, and a repository link to
`docs/spikes/2026-08-11-popup-web-bluetooth-validation.md`, then post it:

```bash
gh issue comment 77 --repo raidenyn/veles-android --body-file /tmp/opencode/issue-77-result.md
```

For `Inconclusive`, post the current report and exact blocker to issue #77 but leave the PR draft.
Do not merge the PR or close the issue on the user's behalf.

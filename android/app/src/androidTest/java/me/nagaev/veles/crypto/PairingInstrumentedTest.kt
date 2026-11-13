package me.nagaev.veles.crypto

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeNotNull
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Loaded only by the dedicated fixed-scalar conformance run, never by product code. */
internal object VelesCryptoConformance {
    @JvmStatic external fun pairingCommand(input: ByteArray): ByteArray
}

@RunWith(AndroidJUnit4::class)
class PairingInstrumentedTest {
    private val fixtures: List<JSONObject> get() {
        val text = InstrumentationRegistry.getInstrumentation().context.assets
            .open("fixtures/crypto.json").bufferedReader().use { it.readText() }
        val array = JSONObject(text).getJSONArray("fixtures")
        return (0 until array.length()).map { array.getJSONObject(it) }
    }
    private fun hex(s: String) = s.removePrefix("0x").chunked(2).map { it.toInt(16).toByte() }.toByteArray()
    private fun command(op: Int, handle: ByteArray, body: ByteArray = byteArrayOf()) = byteArrayOf(1, op.toByte()) + handle + body
    private fun start(role: Int, code: String, ctx: ByteArray) = VelesCrypto.pairingCommand(
        byteArrayOf(1, 1, role.toByte()) + code.toByteArray(Charsets.US_ASCII) + ctx,
    )

    private fun exchange(a: ByteArray, b: ByteArray, call: (ByteArray) -> ByteArray, expected: JSONObject? = null) {
        assertEquals(74, a.size)
        assertEquals(74, b.size)
        assertEquals(0, a[0].toInt())
        assertEquals(0, b[0].toInt())
        val ah = a.copyOfRange(1, 9)
        val bh = b.copyOfRange(1, 9)
        if (expected != null) {
            assertArrayEquals(hex(expected.getString("p_a")), a.copyOfRange(9, 74))
            assertArrayEquals(hex(expected.getString("p_b")), b.copyOfRange(9, 74))
        }
        val ca = call(command(2, ah, b.copyOfRange(9, 74)))
        assertEquals(33, ca.size)
        assertEquals(0, ca[0].toInt())
        assertArrayEquals(byteArrayOf(0), call(command(2, bh, a.copyOfRange(9, 74))))
        val cb = call(command(3, bh, ca.copyOfRange(1, 33)))
        assertEquals(33, cb.size)
        assertEquals(0, cb[0].toInt())
        assertArrayEquals(byteArrayOf(0), call(command(3, ah, cb.copyOfRange(1, 33))))
        val ka = call(command(4, ah))
        val kb = call(command(4, bh))
        assertEquals(17, ka.size)
        assertEquals(0, ka[0].toInt())
        assertArrayEquals(ka, kb)
        if (expected != null) {
            assertArrayEquals(hex(expected.getString("c_a")), ca.copyOfRange(1, 33))
            assertArrayEquals(hex(expected.getString("c_b")), cb.copyOfRange(1, 33))
            assertArrayEquals(hex(expected.getString("ke")), ka.copyOfRange(1, 17))
        }
        assertArrayEquals(byteArrayOf(1), call(command(4, ah)))
    }

    @Test fun productionJniRoundTripRejectsWrongCodeAndReplay() {
        val base = fixtures.first { it.getString("id") == "veles-pairing-code-000000" }
        val ctx = hex(base.getJSONObject("expected").getJSONObject("outputs").getString("ctx"))
        exchange(start(1, "000000", ctx), start(2, "000000", ctx), VelesCrypto::pairingCommand)
        val a = start(1, "000000", ctx)
        val b = start(2, "123456", ctx)
        val ah = a.copyOfRange(1, 9)
        val bh = b.copyOfRange(1, 9)
        val ca = VelesCrypto.pairingCommand(command(2, ah, b.copyOfRange(9, 74)))
        VelesCrypto.pairingCommand(command(2, bh, a.copyOfRange(9, 74)))
        assertArrayEquals(byteArrayOf(1), VelesCrypto.pairingCommand(command(3, bh, ca.copyOfRange(1, 33))))
        assertArrayEquals(byteArrayOf(1), VelesCrypto.pairingCommand(command(4, bh)))
        VelesCrypto.pairingCommand(command(5, ah))
        for (n in listOf(0, 1, 4106)) assertArrayEquals(byteArrayOf(1), VelesCrypto.pairingCommand(ByteArray(n)))
        val testStart = byteArrayOf(1, 127, 1) + ByteArray(32) + ByteArray(31) + byteArrayOf(1, 0, 0)
        assertArrayEquals(byteArrayOf(1), VelesCrypto.pairingCommand(testStart))
    }

    @Test fun separateConformanceLibraryMatchesEveryFrozenPairingVector() {
        val path = InstrumentationRegistry.getArguments().getString("velesConformanceLibrary")
        assumeNotNull(path)
        System.load(path!!)
        var count = 0
        for (f in fixtures) {
            val i = f.getJSONObject("input")
            val e = f.getJSONObject("expected")
            if (i.getString("kind") !in listOf("rfc9382_anchor", "spake2_pairing") || !e.has("outputs")) continue
            val o = e.getJSONObject("outputs")
            val rfc = i.getString("kind") == "rfc9382_anchor"
            val idA = if (rfc) i.getString("id_a") else "Veles Android SPAKE2 party A v1"
            val idB = if (rfc) i.getString("id_b") else "Veles client SPAKE2 party B v1"
            val ctx = if (rfc) byteArrayOf() else hex(o.getString("ctx"))
            fun make(role: Int, scalar: String) = VelesCryptoConformance.pairingCommand(
                if (rfc) {
                    byteArrayOf(1, 127, role.toByte()) + hex(i.getString("w")) +
                        hex(scalar) + byteArrayOf(idA.length.toByte(), idB.length.toByte()) +
                        idA.toByteArray(Charsets.US_ASCII) + idB.toByteArray(Charsets.US_ASCII) + ctx
                } else {
                    byteArrayOf(1, 128.toByte(), role.toByte()) + hex(scalar) + i.getString("code").toByteArray(Charsets.US_ASCII) + ctx
                },
            )
            exchange(make(1, i.getString("x")), make(2, i.getString("y")), VelesCryptoConformance::pairingCommand, o)
            count++
        }
        assertEquals(4, count)
    }

    @Test fun frozenInvalidPointsFailClosedThroughProductionJni() {
        val base = fixtures.first { it.getString("id") == "veles-pairing-code-000000" }
        val ctx = hex(base.getJSONObject("expected").getJSONObject("outputs").getString("ctx"))
        var count = 0
        for (f in fixtures) {
            val e = f.getJSONObject("expected")
            if (e.optString("stage") !in listOf("point", "point_format", "shared_secret")) continue
            val d = f.getJSONObject("input").getJSONObject("deviation")
            val a = start(1, "000000", ctx)
            val ah = a.copyOfRange(1, 9)
            assertArrayEquals(f.getString("id"), byteArrayOf(1), VelesCrypto.pairingCommand(command(2, ah, hex(d.getString("value")))))
            assertArrayEquals(byteArrayOf(1), VelesCrypto.pairingCommand(command(4, ah)))
            count++
        }
        assertEquals(5, count)
    }

    @Test fun productionJniStateExpiresOnNativeMonotonicClock() {
        assumeTrue(InstrumentationRegistry.getArguments().getString("velesRunExpiry") == "true")
        val base = fixtures.first { it.getString("id") == "veles-pairing-code-000000" }
        val ctx = hex(base.getJSONObject("expected").getJSONObject("outputs").getString("ctx"))
        val a = start(1, "000000", ctx)
        val b = start(2, "000000", ctx)
        assertEquals(74, a.size)
        assertEquals(74, b.size)
        android.os.SystemClock.sleep(300_001)
        assertArrayEquals(byteArrayOf(1), VelesCrypto.pairingCommand(command(2, a.copyOfRange(1, 9), b.copyOfRange(9, 74))))
        assertArrayEquals(byteArrayOf(1), VelesCrypto.pairingCommand(command(5, b.copyOfRange(1, 9))))
    }
}

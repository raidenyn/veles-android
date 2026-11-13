package me.nagaev.veles.sharing

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SyntheticTransportFixtureTest {
    @Test fun fixedFixtureRemainsCompatible() {
        assertArrayEquals(byteArrayOf(0x56, 0x45, 0x4c, 2), SyntheticTransportFixture.reply(byteArrayOf(0x56, 0x45, 0x4c, 1)))
    }

    @Test fun boundaryFixturesReturnOnlySyntheticDataAndDoNotMutateInput() {
        for (size in listOf(4, 178, 179, 180, 4095, 4096)) {
            for (seed in listOf(0, 1, 127, 255)) {
                val input = ByteArray(size) { ((it + seed) and 0xff).toByte() }
                byteArrayOf(0x56, 0x45, 0x4c, 1).copyInto(input)
                val original = input.copyOf()
                val expected = input.copyOf().apply { this[3] = 2 }
                assertArrayEquals(expected, SyntheticTransportFixture.reply(input))
                assertArrayEquals(original, input)
            }
        }
    }

    @Test fun nonFixtureAndOutOfBoundsRecordsAreIgnored() {
        for (size in listOf(0, 1, 3, 4097)) assertNull(SyntheticTransportFixture.reply(ByteArray(size)))
        assertNull(SyntheticTransportFixture.reply(byteArrayOf(0x56, 0x45, 0x4c, 2)))
        assertNull(SyntheticTransportFixture.reply("OTP 123456".toByteArray()))
        assertNull(SyntheticTransportFixture.reply(byteArrayOf(0x56, 0x45, 0x4c, 1, 4, 9)))
    }

    @Test fun unsolicitedRecordIsDeterministicTypeThreeAndNeverAnEchoRequest() {
        val record = SyntheticTransportFixture.unsolicited()
        assertEquals(4096, record.size)
        assertArrayEquals(byteArrayOf(0x56, 0x45, 0x4c, 3), record.copyOfRange(0, 4))
        for (i in 4 until record.size) assertEquals(((i + 61) and 0xff).toByte(), record[i])
        assertArrayEquals(record, SyntheticTransportFixture.unsolicited())
        // a type-3 record is not a type-1 request, so a central echoing it back is ignored
        assertNull(SyntheticTransportFixture.reply(record))
        assertEquals(508, SyntheticTransportFixture.unsolicited(508).size)
    }

    @Test fun unsolicitedPushIsOffByDefault() {
        assertNull(SyntheticTransportFixture.unsolicitedAfterMillis)
    }
}

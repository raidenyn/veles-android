package me.nagaev.veles.settings

import androidx.datastore.core.CorruptionException
import androidx.datastore.core.Serializer
import com.google.protobuf.InvalidProtocolBufferException
import me.nagaev.veles.settings.proto.VelesSettings
import java.io.InputStream
import java.io.OutputStream

object SettingsSerializer : Serializer<VelesSettings> {
    override val defaultValue: VelesSettings = VelesSettings.getDefaultInstance()

    override suspend fun readFrom(input: InputStream): VelesSettings = try {
        VelesSettings.parseFrom(input)
    } catch (exception: InvalidProtocolBufferException) {
        throw CorruptionException("Cannot read Veles settings.", exception)
    }

    override suspend fun writeTo(t: VelesSettings, output: OutputStream) = t.writeTo(output)
}

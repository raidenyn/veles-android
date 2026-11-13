package me.nagaev.veles.otp.config

import android.content.Context
import android.net.Uri
import java.io.File

object TestFileUris {
    fun writeTempFile(context: Context, content: String): Uri {
        val file = File.createTempFile("import-test", ".json", context.cacheDir)
        file.writeText(content)
        return Uri.fromFile(file)
    }
}

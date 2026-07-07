package me.nagaev.veles.common

import android.content.Context

interface LogConfig {
    val rawContentEnabled: Boolean
}

class SharedPreferencesLogConfig(
    context: Context,
) : LogConfig {
    private val prefs = context.getSharedPreferences("veles_log_config", Context.MODE_PRIVATE)

    override val rawContentEnabled: Boolean
        get() = prefs.getBoolean(KEY_RAW_CONTENT, false)

    fun saveRawContentEnabled(value: Boolean) {
        prefs.edit().putBoolean(KEY_RAW_CONTENT, value).apply()
    }

    companion object {
        private const val KEY_RAW_CONTENT = "raw_content_enabled"
    }
}

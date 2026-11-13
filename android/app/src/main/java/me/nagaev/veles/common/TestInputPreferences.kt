package me.nagaev.veles.common

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import me.nagaev.veles.R
import javax.inject.Inject

@Suppress("MaxLineLength")
class TestInputPreferences @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private val prefs by lazy {
        context.getSharedPreferences("test_input_preferences", Context.MODE_PRIVATE)
    }

    fun save(text: String) {
        prefs.edit().putString("test_input_text", text).apply()
    }

    fun load(): String = prefs.getString(
        "test_input_text",
        context.getString(R.string.test_default_notification),
    ) ?: ""
}

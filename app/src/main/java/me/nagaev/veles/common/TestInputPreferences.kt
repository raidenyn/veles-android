package me.nagaev.veles.common

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
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
        "For purchase THB600.00 (OTP=511066) at WWWSFCINEMACITYCOMCORP: Ref-VjKp. Never share OTP with anyone. If you didn't make it, call 02-285-1573.",
    ) ?: ""
}

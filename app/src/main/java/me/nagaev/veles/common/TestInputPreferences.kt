package me.nagaev.veles.common

import android.content.Context

class TestInputPreferences(private val context: Context) {
    private val prefs by lazy {
        context.getSharedPreferences("test_input_preferences", Context.MODE_PRIVATE)
    }

    fun save(text: String) {
        prefs.edit().putString("test_input_text", text).apply()
    }

    fun load(): String = prefs.getString("test_input_text", "") ?: ""
}

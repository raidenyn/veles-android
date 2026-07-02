package me.nagaev.veles.testing.viewmodel

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import me.nagaev.veles.common.TestInputPreferences
import me.nagaev.veles.testing.TestNotificationSender

class TestViewModelFactory(
    private val context: Context,
) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        require(modelClass.isAssignableFrom(TestViewModel::class.java)) { "ViewModel Not Found" }
        return TestViewModel(
            preferences = TestInputPreferences(context),
            sender = TestNotificationSender(context),
        ) as T
    }
}

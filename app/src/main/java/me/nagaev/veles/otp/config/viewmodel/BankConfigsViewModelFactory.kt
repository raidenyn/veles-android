package me.nagaev.veles.otp.config.viewmodel

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import me.nagaev.veles.otp.config.BankHandlerRepository

class BankConfigsViewModelFactory(context: Context) : ViewModelProvider.Factory {
    private val repository = BankHandlerRepository(context)

    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T =
        BankConfigsViewModel(repository) as T
}

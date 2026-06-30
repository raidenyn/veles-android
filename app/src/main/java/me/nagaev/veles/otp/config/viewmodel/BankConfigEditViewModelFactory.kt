package me.nagaev.veles.otp.config.viewmodel

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import me.nagaev.veles.otp.config.BankHandlerRepository

class BankConfigEditViewModelFactory(
    context: Context,
    private val configId: Long?
) : ViewModelProvider.Factory {
    private val repository = BankHandlerRepository(context)

    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T =
        BankConfigEditViewModel(repository, configId) as T
}

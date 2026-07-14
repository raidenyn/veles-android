package me.nagaev.veles.common

import androidx.annotation.PluralsRes
import androidx.annotation.StringRes
import androidx.compose.runtime.Composable
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource

sealed interface UiText {
    data class Res(
        @StringRes val id: Int,
        val args: List<Any> = emptyList(),
    ) : UiText

    data class Plural(
        @PluralsRes val id: Int,
        val quantity: Int,
        val args: List<Any> = emptyList(),
    ) : UiText
}

@Suppress("SpreadOperator")
@Composable
fun UiText.asString(): String = when (this) {
    is UiText.Res -> if (args.isEmpty()) {
        stringResource(id)
    } else {
        stringResource(id, *args.toTypedArray())
    }
    is UiText.Plural -> if (args.isEmpty()) {
        pluralStringResource(id, quantity)
    } else {
        pluralStringResource(id, quantity, *args.toTypedArray())
    }
}

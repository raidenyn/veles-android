package me.nagaev.veles.common

import androidx.annotation.PluralsRes
import androidx.annotation.StringRes
import androidx.compose.runtime.Composable
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource

sealed interface UiText {
    @ConsistentCopyVisibility
    data class Res private constructor(
        @StringRes val id: Int,
        private val immutableArgs: List<Any>,
    ) : UiText {
        val args: List<Any>
            get() = immutableArgs

        companion object {
            operator fun invoke(
                @StringRes id: Int,
                args: List<Any> = emptyList(),
            ): Res = Res(id, args.toList())
        }
    }

    @ConsistentCopyVisibility
    data class Plural private constructor(
        @PluralsRes val id: Int,
        val quantity: Int,
        private val immutableArgs: List<Any>,
    ) : UiText {
        val args: List<Any>
            get() = immutableArgs

        companion object {
            operator fun invoke(
                @PluralsRes id: Int,
                quantity: Int,
                args: List<Any> = emptyList(),
            ): Plural = Plural(id, quantity, args.toList())
        }
    }
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

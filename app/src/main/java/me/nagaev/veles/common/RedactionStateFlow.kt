package me.nagaev.veles.common

import kotlinx.coroutines.flow.MutableStateFlow

enum class RedactionState { Unknown, Readable, Hidden }

object RedactionStateFlow {
    val current: MutableStateFlow<RedactionState> = MutableStateFlow(RedactionState.Unknown)
}
package me.nagaev.veles.common

import kotlinx.coroutines.flow.MutableStateFlow
import javax.inject.Inject
import javax.inject.Singleton

enum class RedactionState { Unknown, Readable, Hidden }

@Singleton
class RedactionStateFlow @Inject constructor() {
    val current: MutableStateFlow<RedactionState> = MutableStateFlow(RedactionState.Unknown)
}

package me.nagaev.veles.viewmodel

data class UiState(
    val permissions: PermissionsState
) {
    companion object {
        val Init = UiState(
            permissions = PermissionsState.Init
        )
        val Mocked = UiState(
            permissions = PermissionsState.Mocked
        )
    }
}
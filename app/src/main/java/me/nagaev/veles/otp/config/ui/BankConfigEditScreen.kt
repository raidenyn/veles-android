package me.nagaev.veles.otp.config.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import me.nagaev.veles.otp.config.viewmodel.BankConfigEditState

@Suppress("LongParameterList")
@Composable
fun BankConfigEditScreen(
    state: BankConfigEditState,
    isNew: Boolean,
    onNameChanged: (String) -> Unit,
    onOtpRegexChanged: (String) -> Unit,
    onMoneyRegexChanged: (String) -> Unit,
    onMerchantRegexChanged: (String) -> Unit,
    onSave: () -> Unit,
    onNavigateBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    LaunchedEffect(state.savedSuccessfully) {
        if (state.savedSuccessfully) onNavigateBack()
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp)
            .statusBarsPadding()
            .verticalScroll(rememberScrollState()),
    ) {
        Text(
            text = if (isNew) "New Bank Config" else "Edit Bank Config",
            style = MaterialTheme.typography.titleLarge,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.padding(vertical = 10.dp),
        )
        OutlinedTextField(
            value = state.name,
            onValueChange = onNameChanged,
            label = { Text("Name") },
            isError = state.nameError != null,
            supportingText = state.nameError?.let { error -> { Text(error) } },
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = state.otpRegex,
            onValueChange = onOtpRegexChanged,
            label = { Text("OTP Regex") },
            isError = state.otpRegexError != null,
            supportingText = state.otpRegexError?.let { error -> { Text(error) } },
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = state.moneyRegex,
            onValueChange = onMoneyRegexChanged,
            label = { Text("Money Regex") },
            isError = state.moneyRegexError != null,
            supportingText = state.moneyRegexError?.let { error -> { Text(error) } },
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = state.merchantRegex,
            onValueChange = onMerchantRegexChanged,
            label = { Text("Merchant Regex") },
            isError = state.merchantRegexError != null,
            supportingText = state.merchantRegexError?.let { error -> { Text(error) } },
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = onSave,
            enabled = !state.isSaving,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Save")
        }
    }
}

@Preview(showBackground = true)
@Composable
fun BankConfigEditScreenPreview() {
    BankConfigEditScreen(
        state = BankConfigEditState(
            name = "UOB Thailand",
            otpRegex = """ (\w{4})-(\d{6}) """,
            moneyRegex = """of ([A-Z]{3})(\d+)""",
            merchantRegex = """at (.+) expiring""",
        ),
        isNew = false,
        onNameChanged = {},
        onOtpRegexChanged = {},
        onMoneyRegexChanged = {},
        onMerchantRegexChanged = {},
        onSave = {},
        onNavigateBack = {},
    )
}

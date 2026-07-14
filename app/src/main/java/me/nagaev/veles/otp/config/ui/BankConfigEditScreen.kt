package me.nagaev.veles.otp.config.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.annotation.StringRes
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.nagaev.veles.common.UiText
import me.nagaev.veles.common.asString
import me.nagaev.veles.R
import me.nagaev.veles.otp.config.viewmodel.BankConfigEditState

@Suppress("LongParameterList", "LongMethod")
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
            .navigationBarsPadding()
            .imePadding()
            .verticalScroll(rememberScrollState()),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(vertical = 8.dp),
        ) {
            IconButton(
                onClick = onNavigateBack,
                modifier = Modifier.size(36.dp),
            ) {
                Icon(
                    imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                    contentDescription = stringResource(R.string.bank_config_edit_back),
                )
            }
            Spacer(Modifier.width(8.dp))
            Text(
                text = stringResource(
                    if (isNew) R.string.bank_config_edit_new_title else R.string.bank_config_edit_title,
                ),
                fontSize = 18.sp,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface,
            )
        }
        OutlinedTextField(
            value = state.name,
            onValueChange = onNameChanged,
            label = { Text(stringResource(R.string.bank_config_edit_name)) },
            isError = state.nameError != null,
            supportingText = state.nameError?.let { error -> { Text(error.asString()) } },
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        RegexField(
            caption = R.string.bank_config_edit_otp_caption,
            value = state.otpRegex,
            onValueChange = onOtpRegexChanged,
            label = R.string.bank_config_edit_otp_label,
            error = state.otpRegexError,
        )
        Spacer(Modifier.height(8.dp))
        RegexField(
            caption = R.string.bank_config_edit_money_caption,
            value = state.moneyRegex,
            onValueChange = onMoneyRegexChanged,
            label = R.string.bank_config_edit_money_label,
            error = state.moneyRegexError,
        )
        Spacer(Modifier.height(8.dp))
        RegexField(
            caption = R.string.bank_config_edit_merchant_caption,
            value = state.merchantRegex,
            onValueChange = onMerchantRegexChanged,
            label = R.string.bank_config_edit_merchant_label,
            error = state.merchantRegexError,
        )
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = onSave,
            enabled = !state.isSaving,
            shape = MaterialTheme.shapes.small,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(stringResource(R.string.bank_config_edit_save))
        }
        Spacer(Modifier.height(16.dp))
    }
}

@Composable
private fun RegexField(
    @StringRes caption: Int,
    value: String,
    onValueChange: (String) -> Unit,
    @StringRes label: Int,
    error: UiText?,
) {
    Column {
        Text(
            text = stringResource(caption),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 4.dp),
        )
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            label = { Text(stringResource(label)) },
            textStyle = LocalTextStyle.current.copy(
                fontFamily = FontFamily.Monospace,
                fontSize = 13.sp,
            ),
            isError = error != null,
            supportingText = error?.let { message -> { Text(message.asString()) } },
            modifier = Modifier.fillMaxWidth(),
        )
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

package me.nagaev.veles.settings

import androidx.datastore.core.DataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import me.nagaev.veles.settings.proto.VelesSettings
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class SettingsRepository @Inject constructor(
    private val dataStore: DataStore<VelesSettings>,
) {
    val autoCopyEnabled: Flow<Boolean> = dataStore.data
        .catch { exception ->
            if (exception is IOException) {
                emit(VelesSettings.getDefaultInstance())
            } else {
                throw exception
            }
        }
        .map { it.autoCopyOtp }

    suspend fun isAutoCopyEnabled(): Boolean = autoCopyEnabled.first()

    suspend fun setAutoCopyEnabled(enabled: Boolean) {
        dataStore.updateData { settings ->
            settings.toBuilder().setAutoCopyOtp(enabled).build()
        }
    }
}

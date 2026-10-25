package me.nagaev.veles.trust

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.withTransaction
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File

@Entity(tableName = "trust_snapshot")
data class TrustRow(@PrimaryKey val id: Int = 1, val payload: String)

@Dao
interface TrustDao {
    @Query("SELECT * FROM trust_snapshot")
    suspend fun rows(): List<TrustRow>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun put(row: TrustRow)
}

@Database(entities = [TrustRow::class], version = 1, exportSchema = false)
abstract class TrustDatabase : RoomDatabase() {
    abstract fun trustDao(): TrustDao

    companion object {
        const val FILE_NAME = "installation-trust.db"
        fun open(context: Context): TrustDatabase = Room.databaseBuilder(
            context.applicationContext, TrustDatabase::class.java,
            File(context.noBackupFilesDir, FILE_NAME).absolutePath,
        ).build()
    }
}

/** SQLite transactions serialize readers and writers, including across repository instances.
 * No destructive migration or corruption recovery: unreadable trust must remain unusable.
 */
class RoomTrustStorage(private val database: TrustDatabase) : TrustStorage {
    private val json = Json { ignoreUnknownKeys = false; encodeDefaults = true }

    override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T {
        try {
            return database.withTransaction {
                val rows = database.trustDao().rows()
                checkTrust(rows.size <= 1 && rows.all { it.id == 1 })
                val previous = rows.singleOrNull()?.let { json.decodeFromString<TrustSnapshot>(it.payload) }
                val (next, result) = block(previous)
                database.trustDao().put(TrustRow(payload = json.encodeToString(next)))
                result
            }
        } catch (cancelled: kotlinx.coroutines.CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            // Do not propagate database payloads, parser messages, peer identifiers or key details.
            throw TrustException()
        }
    }
}

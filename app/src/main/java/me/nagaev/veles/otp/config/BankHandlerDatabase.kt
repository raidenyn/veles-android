package me.nagaev.veles.otp.config

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Suppress("MaxLineLength")
@Database(entities = [BankHandlerConfig::class], version = 2)
abstract class BankHandlerDatabase : RoomDatabase() {
    abstract fun bankHandlerConfigDao(): BankHandlerConfigDao

    companion object {
        @Volatile
        private var INSTANCE: BankHandlerDatabase? = null

        private val MIGRATION_1_2 =
            object : Migration(1, 2) {
                override fun migrate(db: SupportSQLiteDatabase) {
                    val now = System.currentTimeMillis()
                    db.execSQL(
                        "INSERT INTO bank_handler_configs (name, otpRegex, moneyRegex, merchantRegex, createdAt, updatedAt) VALUES (?, ?, ?, ?, ?, ?)",
                        arrayOf<Any>(
                            "UOB Thai",
                            """\((OTP=)(\d{6})\)""",
                            """purchase ([A-Z]{3})(\d{1,15}\.\d{1,4})""",
                            """ at (.+?):""",
                            now,
                            now,
                        ),
                    )
                }
            }

        fun getInstance(context: Context): BankHandlerDatabase = INSTANCE ?: synchronized(this) {
            Room
                .databaseBuilder(
                    context.applicationContext,
                    BankHandlerDatabase::class.java,
                    "bank_handler_configs.db",
                ).allowMainThreadQueries()
                .addMigrations(MIGRATION_1_2)
                .addCallback(SeedCallback())
                .build()
                .also { INSTANCE = it }
        }
    }

    private class SeedCallback : Callback() {
        override fun onCreate(db: SupportSQLiteDatabase) {
            super.onCreate(db)
            val now = System.currentTimeMillis()
            db.execSQL(
                "INSERT INTO bank_handler_configs (name, otpRegex, moneyRegex, merchantRegex, createdAt, updatedAt) VALUES (?, ?, ?, ?, ?, ?)",
                arrayOf<Any>(
                    "UOB Thailand",
                    """ (\w{4})-(\d{6}) """,
                    """of ([A-Z]{3})(\d{1,15}\.\d{1,4}) at""",
                    """at (.{1,64}) expiring""",
                    now,
                    now,
                ),
            )
            db.execSQL(
                "INSERT INTO bank_handler_configs (name, otpRegex, moneyRegex, merchantRegex, createdAt, updatedAt) VALUES (?, ?, ?, ?, ?, ?)",
                arrayOf<Any>(
                    "UOB Thai",
                    """\((OTP=)(\d{6})\)""",
                    """purchase ([A-Z]{3})(\d{1,15}\.\d{1,4})""",
                    """ at (.+?):""",
                    now,
                    now,
                ),
            )
        }
    }
}

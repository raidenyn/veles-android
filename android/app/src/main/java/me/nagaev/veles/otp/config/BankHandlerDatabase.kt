package me.nagaev.veles.otp.config

import androidx.room.Database
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Suppress("MaxLineLength")
@Database(entities = [BankHandlerConfig::class], version = 2)
abstract class BankHandlerDatabase : RoomDatabase() {
    abstract fun bankHandlerConfigDao(): BankHandlerConfigDao

    companion object {
        internal val MIGRATION_1_2 =
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
    }

    internal class SeedCallback : Callback() {
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

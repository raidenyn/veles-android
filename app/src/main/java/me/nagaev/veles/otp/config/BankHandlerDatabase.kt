package me.nagaev.veles.otp.config

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.sqlite.db.SupportSQLiteDatabase

@Database(entities = [BankHandlerConfig::class], version = 1)
abstract class BankHandlerDatabase : RoomDatabase() {
    abstract fun bankHandlerConfigDao(): BankHandlerConfigDao

    companion object {
        @Volatile
        private var INSTANCE: BankHandlerDatabase? = null

        fun getInstance(context: Context): BankHandlerDatabase =
            INSTANCE ?: synchronized(this) {
                Room.databaseBuilder(
                    context.applicationContext,
                    BankHandlerDatabase::class.java,
                    "bank_handler_configs.db"
                )
                    .allowMainThreadQueries()
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
                    now
                )
            )
        }
    }
}

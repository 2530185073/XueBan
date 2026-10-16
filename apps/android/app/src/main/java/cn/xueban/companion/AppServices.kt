package cn.xueban.companion

import android.content.Context
import android.os.storage.StorageManager
import cn.xueban.companion.data.AccountAuth
import cn.xueban.companion.data.BitmapPageRenderer
import cn.xueban.companion.data.LocalCaptureRepository
import cn.xueban.companion.data.SessionVault
import java.io.File

/** One repository/vault per process, shared by the UI and WorkManager so journal writes stay serialised. */
class AppServices private constructor(context: Context) {
    val auth = AccountAuth(SessionVault(context), BuildConfig.DEBUG)
    val repository: LocalCaptureRepository

    init {
        val root = File(context.noBackupFilesDir, "correction-v2")
        val storage = context.getSystemService(StorageManager::class.java)
        // A failed measurement must not block capture; real write failures are still reported.
        repository = LocalCaptureRepository(root, BitmapPageRenderer()) {
            runCatching { storage.getAllocatableBytes(storage.getUuidForPath(root)) }.getOrDefault(Long.MAX_VALUE)
        }
    }

    companion object {
        @Volatile private var instance: AppServices? = null
        fun get(context: Context): AppServices = instance ?: synchronized(this) {
            instance ?: AppServices(context.applicationContext).also { instance = it }
        }
    }
}

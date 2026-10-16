package cn.xueban.companion.data

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import cn.xueban.companion.AppServices
import cn.xueban.companion.BuildConfig
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.withContext

class CorrectionSyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val services = AppServices.get(applicationContext)
        when (BackgroundSync(services.auth, services.repository, BuildConfig.DEBUG).run(RUN_BUDGET_MILLIS)) {
            SyncOutcome.Done -> Result.success()
            SyncOutcome.Retry -> Result.retry()
            is SyncOutcome.Stopped -> Result.failure()
        }
    }

    private companion object { const val RUN_BUDGET_MILLIS = 8 * 60_000L }
}

object SyncScheduler {
    private const val NAME = "correction-sync"

    /** [now] replaces a waiting/backed-off run so a student's explicit action starts immediately. */
    fun enqueue(context: Context, now: Boolean) {
        val request = OneTimeWorkRequestBuilder<CorrectionSyncWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(NAME, if (now) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.KEEP, request)
    }

    fun cancel(context: Context) { WorkManager.getInstance(context).cancelUniqueWork(NAME) }

    fun state(context: Context): Flow<WorkInfo.State?> =
        WorkManager.getInstance(context).getWorkInfosForUniqueWorkFlow(NAME).map { infos -> infos.firstOrNull { !it.state.isFinished }?.state ?: infos.firstOrNull()?.state }
}

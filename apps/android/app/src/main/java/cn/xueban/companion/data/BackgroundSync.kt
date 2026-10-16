package cn.xueban.companion.data

import java.io.IOException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

sealed interface SyncOutcome {
    data object Done : SyncOutcome
    data object Retry : SyncOutcome
    data class Stopped(val kind: FailureKind) : SyncOutcome
}

/**
 * Drives submitted records from the durable ledger. Drafts are never uploaded, the same saved
 * idempotency keys are reused, and a record only advances on server-confirmed responses.
 */
class BackgroundSync(
    private val auth: AccountAuth,
    private val repository: LocalCaptureRepository,
    private val debug: Boolean,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    suspend fun run(budgetMillis: Long): SyncOutcome = lock.withLock {
        val session = auth.activeSession() ?: return SyncOutcome.Stopped(FailureKind.SessionExpired)
        val api = BackendApi(session.server, session.token, debug)
        val deadline = clock() + budgetMillis
        var retry = false
        val pending = repository.listCaptures(session.server, session.actorId, session.studentId)
            .filter { !it.isDraft && (canResumeAutomatically(it.stage) || it.state.optBoolean("sync_requested")) }
            .sortedBy { it.capturedAt }
        for (record in pending) {
            if (clock() > deadline) { retry = true; break }
            try {
                CorrectionSync(repository, api, deadline).run(record.id)
                val after = repository.update(record.id) { it.remove("sync_requested") }
                if (canResumeAutomatically(after.stage)) retry = true
            } catch (cancel: CancellationException) { throw cancel }
            catch (error: Exception) {
                when (val kind = classifyFailure(error)) {
                    FailureKind.SessionExpired, FailureKind.AccessDenied -> { auth.expire(); return SyncOutcome.Stopped(kind) }
                    FailureKind.PasswordChangeRequired -> { auth.markPasswordChangeRequired(); return SyncOutcome.Stopped(kind) }
                    FailureKind.Other -> if (recordFailure(record.id, error)) retry = true
                }
            }
        }
        if (retry) SyncOutcome.Retry else SyncOutcome.Done
    }

    /** Returns whether the record should be retried later with the same saved operations. */
    private fun recordFailure(id: String, error: Exception): Boolean {
        val unknown = error is IOException && (error !is ApiFailure || processFailureRecovery("", error.status).keepActiveRecord)
        repository.update(id) { state ->
            state.put("error", syncFailureMessage(error))
            if (!unknown) state.put("stage", "failed").remove("sync_requested")
        }
        return unknown
    }

    private companion object { val lock = Mutex() }
}

fun syncFailureMessage(error: Exception): String = when (error) {
    is ApiFailure -> if (error.status == 0 && error.code == "still_running") error.message ?: "检查还在进行" else error.message ?: "服务暂时不可用"
    is IOException -> "网络不稳定，原图和进度已保留，联网后会自动继续"
    is IllegalArgumentException, is IllegalStateException -> error.message ?: "暂时无法继续，请联系老师"
    else -> "暂时无法读取记录，请重试或联系老师"
}

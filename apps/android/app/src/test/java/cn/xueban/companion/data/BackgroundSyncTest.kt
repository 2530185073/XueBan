package cn.xueban.companion.data

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class BackgroundSyncTest {
    private val backend = FakeBackend()
    private lateinit var server: MockWebServer
    private val root = tempDir()
    private val repository = LocalCaptureRepository(root, CopyRenderer()) { Long.MAX_VALUE }
    private lateinit var store: MemorySessionStore
    private val auth by lazy { AccountAuth(store, true) }
    private val sync by lazy { BackgroundSync(auth, repository, true) }

    @Before fun start() { server = backend.start(); store = signedInStore(backend.base) }
    @After fun stop() { server.shutdown(); root.deleteRecursively() }

    @Test fun submittedWorkCompletesWhileDraftsStayLocal() = runBlocking {
        val submitted = submittedRecord(repository, backend.base, listOf("page"))
        val draft = repository.createDraft(backend.base, "actor_a", "student_a", null)
        repository.beginPage(draft, "camera").second.writeText("draft page")
        repository.recoverInterruptedCaptures(backend.base, "actor_a", "student_a")

        assertEquals(SyncOutcome.Done, sync.run(60_000))
        assertEquals("complete", repository.capture(submitted).stage)
        assertEquals("local", repository.capture(draft).stage)
        assertEquals(1, backend.posts("/v1/homeworks/homework_a/uploads").size)
    }

    @Test fun expiredSessionStopsWithoutLosingUnsentWorkAndResumesWithTheSameKeys() = runBlocking {
        val id = submittedRecord(repository, backend.base, listOf("page"))
        backend.failNext("/v1/students/student_a/homeworks", 401, "authentication_required")

        assertEquals(SyncOutcome.Stopped(FailureKind.SessionExpired), sync.run(60_000))
        val saved = requireNotNull(store.value)
        assertFalse(saved.has("access_token"))
        assertEquals("student.one", saved.getString("username"))
        val paused = repository.capture(id)
        assertTrue(canResumeAutomatically(paused.stage))
        assertTrue(recordStageLabel(paused.state, emptyList()).startsWith("未上传"))
        val key = paused.state.getJSONObject("ops").getJSONObject("homework").getString("key")

        assertEquals(SyncOutcome.Stopped(FailureKind.SessionExpired), sync.run(60_000))
        store = signedInStore(backend.base)
        val resumed = BackgroundSync(AccountAuth(store, true), repository, true)
        assertEquals(SyncOutcome.Done, resumed.run(60_000))
        assertEquals(listOf(key, key), backend.posts("/v1/students/student_a/homeworks").map { it.key })
        assertEquals("complete", repository.capture(id).stage)
    }

    @Test fun passwordChangeRequirementPausesBackgroundWork() = runBlocking {
        val id = submittedRecord(repository, backend.base, listOf("page"))
        backend.mustChange = true
        assertEquals(SyncOutcome.Stopped(FailureKind.PasswordChangeRequired), sync.run(60_000))
        assertTrue(requireNotNull(store.value).getBoolean("must_change_password"))
        assertNull(auth.activeSession())
        assertTrue(canResumeAutomatically(repository.capture(id).stage))
    }

    @Test fun unknownServerOutcomeIsRetriedLaterAndNeverMarkedDone() = runBlocking {
        val id = submittedRecord(repository, backend.base, listOf("page"))
        backend.failNext("/v1/students/student_a/homeworks", 503, "service_unavailable")
        assertEquals(SyncOutcome.Retry, sync.run(60_000))
        val waiting = repository.capture(id)
        assertEquals("creating", waiting.stage)
        assertNotNull(waiting.error)
        assertTrue(waiting.state.getJSONObject("ops").has("homework"))
        backend.dropResponseOnce("/v1/homeworks/homework_a/uploads")
        assertEquals(SyncOutcome.Retry, sync.run(60_000))
        assertFalse(repository.capture(id).pages.single().confirmed)
        assertEquals(SyncOutcome.Done, sync.run(60_000))
        assertEquals(1, backend.assets.size)
    }

    @Test fun definiteRejectionMarksTheRecordFailedForAnExplicitRetry() = runBlocking {
        val id = submittedRecord(repository, backend.base, listOf("page"))
        backend.failNext("/v1/students/student_a/homeworks", 422, "invalid_request")
        assertEquals(SyncOutcome.Done, sync.run(60_000))
        val failed = repository.capture(id)
        assertEquals("failed", failed.stage)
        assertTrue(recordStageLabel(failed.state, emptyList()).startsWith("未上传完成"))
        assertFalse(failed.state.getJSONObject("ops").has("homework"))

        repository.requestSync(id, retryFailed = true)
        assertEquals("queued", repository.capture(id).stage)
        assertEquals(SyncOutcome.Done, sync.run(60_000))
        assertEquals("complete", repository.capture(id).stage)
    }

    @Test fun exhaustedBudgetAsksWorkManagerToRetry() = runBlocking {
        submittedRecord(repository, backend.base, listOf("page"))
        var now = 0L
        val slow = BackgroundSync(auth, repository, true) { now.also { now += 1_000 } }
        assertEquals(SyncOutcome.Retry, slow.run(budgetMillis = 0))
        assertTrue(backend.calls.isEmpty())
    }

    @Test fun signedOutDeviceDoesNothing() = runBlocking {
        submittedRecord(repository, backend.base, listOf("page"))
        auth.expire()
        assertEquals(SyncOutcome.Stopped(FailureKind.SessionExpired), sync.run(60_000))
        assertTrue(backend.calls.isEmpty())
    }
}

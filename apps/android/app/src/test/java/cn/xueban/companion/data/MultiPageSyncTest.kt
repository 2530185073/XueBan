package cn.xueban.companion.data

import java.io.File
import java.io.IOException
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class MultiPageSyncTest {
    private val backend = FakeBackend()
    private lateinit var server: MockWebServer
    private val root = tempDir()
    private val repository = LocalCaptureRepository(root, CopyRenderer()) { Long.MAX_VALUE }
    private val api by lazy { BackendApi(backend.base, "token-a", true) }

    @Before fun start() { server = backend.start() }
    @After fun stop() { server.shutdown(); root.deleteRecursively() }

    @Test fun pagesUploadInConfirmedOrderAndRecognitionUsesEveryAsset() = runBlocking {
        val id = repository.createDraft(backend.base, "actor_a", "student_a", null)
        val pageIds = listOf("page one", "page two", "page three").map { content ->
            val (pageId, original) = repository.beginPage(id, "camera")
            original.writeText(content); repository.completePage(id, pageId); pageId
        }
        repository.movePage(id, pageIds[2], -2)
        repository.submit(id, 3)
        val ordered = repository.capture(id).pages

        CorrectionSync(repository, api).run(id)

        val uploads = backend.posts("/v1/homeworks/homework_a/uploads").map { it.json }
        assertEquals(listOf(0, 1, 2), uploads.map { it.getInt("page_index") })
        assertEquals(ordered.map { it.state.getString("sha256") }, uploads.map { it.getString("sha256") })
        assertEquals(listOf(1, 3, 5), uploads.map { it.getInt("expected_revision") })
        val puts = backend.calls.filter { it.method == "PUT" }.map { it.path }
        assertEquals(listOf("/v1/assets/asset_1/content", "/v1/assets/asset_2/content", "/v1/assets/asset_3/content"), puts)
        ordered.forEachIndexed { index, page -> assertArrayEquals(page.upload.readBytes(), backend.stored.getValue("asset_${index + 1}")) }
        assertTrue(backend.calls.filter { it.method == "PUT" }.all { it.auth == null })
        assertEquals(listOf("asset_1", "asset_2", "asset_3"), backend.recognitionAssets)
        val done = repository.capture(id)
        assertEquals("complete", done.stage)
        assertTrue(done.pages.all { it.confirmed })
        assertEquals(listOf("asset_1", "asset_2", "asset_3"), done.pages.map { it.assetId })
        assertTrue(ordered.zip(done.pages).all { (before, after) -> before.original!!.readText() == after.original!!.readText() })
    }

    @Test fun lostUploadResponseReplaysTheSamePageWriteInsteadOfCreatingAnotherAsset() = runBlocking {
        val id = submittedRecord(repository, backend.base, listOf("first", "second"))
        backend.dropResponseOnce("/v1/homeworks/homework_a/uploads")
        assertThrows(IOException::class.java) { runBlocking { CorrectionSync(repository, api).run(id) } }
        val interrupted = repository.capture(id)
        assertEquals("uploading", interrupted.stage)
        assertNull(interrupted.pages[0].assetId)
        assertFalse(interrupted.pages[0].confirmed)

        CorrectionSync(repository, api).run(id)
        val uploads = backend.posts("/v1/homeworks/homework_a/uploads")
        assertEquals(uploads[0].key, uploads[1].key)
        assertEquals(uploads[0].body, uploads[1].body)
        assertEquals(listOf("asset_1", "asset_2"), backend.assets.keys.toList())
        assertEquals(listOf("asset_1", "asset_2"), backend.recognitionAssets)
        assertEquals(1, backend.posts("/v1/students/student_a/homeworks").size)
    }

    @Test fun ledgerFrom020ResumesItsPendingUploadWithTheOriginalKeyAfterAppUpdate() = runBlocking {
        val bytes = "photo saved by 0.2.0".toByteArray()
        val directory = File(root, "legacy-record").apply { mkdirs() }
        File(directory, "original.jpg").writeBytes(bytes)
        File(directory, "upload.jpg").writeBytes(bytes)
        val legacyUpload = JSONObject().put("path", "/v1/homeworks/homework_a/uploads").put("key", "legacy-upload-key")
            .put("body", JSONObject().put("expected_revision", 1).put("page_index", 0).put("sha256", sha256(bytes)).put("byte_size", bytes.size.toLong())
                .put("mime_type", "image/jpeg").put("width", 1200).put("height", 1600))
        val legacy = JSONObject().put("id", "legacy-record").put("server", backend.base).put("actor_id", "actor_a").put("student_id", "student_a")
            .put("captured_at", 1_700_000_000_000L).put("stage", "uploading").put("generation", 0).put("title", "学校作业")
            .put("homework_id", "homework_a").put("width", 1200).put("height", 1600).put("byte_size", bytes.size.toLong()).put("sha256", sha256(bytes))
            .put("ops", JSONObject().put("upload", legacyUpload))
        File(directory, "record.json").writeText(legacy.toString())

        val listed = repository.listCaptures(backend.base, "actor_a", "student_a").single()
        assertEquals(LEDGER_VERSION, JSONObject(File(directory, "record.json").readText()).getInt("schema_version"))
        assertEquals(File(directory, "upload.jpg"), listed.pages.single().upload)
        assertTrue(canResumeAutomatically(listed.stage))

        CorrectionSync(repository, api).run("legacy-record")
        val upload = backend.posts("/v1/homeworks/homework_a/uploads").single()
        assertEquals("legacy-upload-key", upload.key)
        assertEquals(legacyUpload.getJSONObject("body").toString(), upload.json.toString())
        assertEquals(listOf("asset_1"), backend.recognitionAssets)
        assertEquals("complete", repository.capture("legacy-record").stage)
    }

    @Test fun draftsAreEditableOnlyUntilTheStudentConfirmsThePageCount() {
        val renderer = CopyRenderer()
        val local = LocalCaptureRepository(root, renderer) { Long.MAX_VALUE }
        val id = local.createDraft("https://homework.example.com", "actor_a", "student_a", null)
        assertTrue(local.listCaptures("https://homework.example.com", "actor_a", "student_a").isEmpty())
        val (first, original) = local.beginPage(id, "camera")
        original.writeText("original bytes")
        local.completePage(id, first)
        val (second, pending) = local.beginPage(id, "picker")
        assertEquals("还有照片没有处理完，请删除出错的页后再提交", assertThrows(IllegalArgumentException::class.java) { local.submit(id, 2) }.message)
        pending.writeText("second page")
        local.completePage(id, second)

        local.editPage(id, first, PageEdit(90, NormRect(0.1f, 0.1f, 0.9f, 0.8f)))
        val edited = local.capture(id).pages.first()
        assertEquals("original bytes", edited.original!!.readText())
        assertEquals(PageEdit(90, NormRect(0.1f, 0.1f, 0.9f, 0.8f)), edited.edit)
        assertEquals(renderer.edits.last(), edited.edit)
        assertEquals(sha256(edited.upload.readBytes()), edited.state.getString("sha256"))

        local.movePage(id, second, -1)
        assertEquals(listOf(second, first), local.capture(id).pages.map { it.id })
        assertThrows(IllegalArgumentException::class.java) { local.submit(id, 3) }
        local.removePage(id, second)
        assertFalse(File(root, "$id/$second-original.jpg").exists())
        assertEquals(listOf(first), local.capture(id).pages.map { it.id })

        local.submit(id, 1)
        assertEquals("queued", local.capture(id).stage)
        assertThrows(IllegalArgumentException::class.java) { local.beginPage(id, "camera") }
        assertThrows(IllegalArgumentException::class.java) { local.editPage(id, first, PageEdit()) }
        assertThrows(IllegalArgumentException::class.java) { local.discardDraft(id) }
        assertTrue(recordStageLabel(local.capture(id).state, emptyList()).startsWith("未上传"))
    }

    @Test fun interruptedCaptureIsRecoveredAndEmptyDraftsCanBeDiscarded() {
        val id = repository.createDraft(backend.base, "actor_a", "student_a", null)
        val (kept, original) = repository.beginPage(id, "camera")
        original.writeText("bytes reached disk before the process died")
        val (lost, _) = repository.beginPage(id, "camera")
        repository.recoverInterruptedCaptures(backend.base, "actor_a", "student_a")
        val pages = repository.capture(id).pages
        assertEquals(listOf(kept), pages.map { it.id })
        assertTrue(pages.single().ready)
        assertFalse(pages.any { it.id == lost })

        val empty = repository.createDraft(backend.base, "actor_a", "student_a", null)
        repository.discardDraft(empty)
        assertFalse(File(root, empty).exists())
    }

    @Test fun lowStorageIsReportedBeforeAnythingIsWritten() {
        val full = LocalCaptureRepository(root, CopyRenderer()) { 10L * 1_048_576 }
        val error = assertThrows(IllegalStateException::class.java) { full.createDraft(backend.base, "actor_a", "student_a", null) }
        assertTrue(error.message.orEmpty().contains("存储空间不足"))
        assertTrue(root.listFiles().orEmpty().isEmpty())
    }

    @Test fun recordsStayPartitionedByServerActorAndStudent() {
        submittedRecord(repository, backend.base, listOf("mine"))
        assertEquals(1, repository.listCaptures(backend.base, "actor_a", "student_a").size)
        assertTrue(repository.listCaptures(backend.base, "actor_b", "student_a").isEmpty())
        assertTrue(repository.listCaptures(backend.base, "actor_a", "student_b").isEmpty())
        assertTrue(repository.listCaptures("https://other.example.com", "actor_a", "student_a").isEmpty())
    }
}

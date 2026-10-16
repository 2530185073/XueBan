package cn.xueban.companion.data

import java.io.File
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class LedgerMigrationTest {
    private fun op(key: String, path: String, response: JSONObject? = null) = JSONObject().put("key", key).put("path", path)
        .put("body", JSONObject().put("expected_revision", 3).put("page_index", 0)).also { if (response != null) it.put("response", response) }

    private fun legacy(stage: String) = JSONObject().put("id", "record-1").put("server", "https://homework.example.com").put("actor_id", "actor_a")
        .put("student_id", "student_a").put("captured_at", 1234L).put("stage", stage).put("generation", 1).put("title", "学校作业")
        .put("homework_id", "homework_a").put("width", 1800).put("height", 2400).put("byte_size", 4321).put("sha256", "a".repeat(64))
        .put("asset_id", "asset_a").put("ticket_generation", 1)
        .put("upload_ticket", JSONObject().put("put_url", "https://homework.example.com/v1/assets/asset_a/content"))
        .put("ops", JSONObject()
            .put("homework", op("key-homework", "/v1/students/student_a/homeworks", JSONObject().put("id", "homework_a")))
            .put("upload", op("key-upload", "/v1/homeworks/homework_a/uploads", JSONObject().put("asset", JSONObject().put("id", "asset_a"))))
            .put("renew-1", op("key-renew", "/v1/assets/asset_a/upload-ticket"))
            .put("complete", op("key-complete", "/v1/assets/asset_a/complete"))
            .put("recognize-1", op("key-recognize", "/v1/homeworks/homework_a/recognitions")))

    @Test fun singlePhotoLedgerBecomesFirstPageWithIdenticalOperations() {
        val old = legacy("uploading")
        val migrated = migrateLedger(old)
        assertEquals(LEDGER_VERSION, migrated.getInt("schema_version"))
        listOf("width", "height", "byte_size", "sha256", "asset_id", "upload_ticket", "ticket_generation").forEach { assertFalse(it, migrated.has(it)) }
        val page = migrated.pageObjects().single()
        assertEquals(MIGRATED_PAGE_ID, page.getString("id"))
        assertEquals("original.jpg", page.getString("original_file"))
        assertEquals("upload.jpg", page.getString("upload_file"))
        assertEquals("asset_a", page.getString("asset_id"))
        assertEquals("a".repeat(64), page.getString("sha256"))
        assertEquals(1, page.getInt("ticket_generation"))
        assertEquals("ready", page.getString("status"))
        val ops = migrated.getJSONObject("ops")
        assertEquals(setOf("homework", "upload-page_0", "renew-page_0-1", "complete-page_0", "recognize-1"), ops.keys().asSequence().toSet())
        mapOf("upload" to "upload-page_0", "renew-1" to "renew-page_0-1", "complete" to "complete-page_0", "homework" to "homework", "recognize-1" to "recognize-1").forEach { (before, after) ->
            assertEquals(canonical(old.getJSONObject("ops").getJSONObject(before)), canonical(ops.getJSONObject(after)))
        }
        assertEquals("uploading", migrated.getString("stage"))
        assertEquals(1, migrated.getInt("generation"))
        assertEquals("asset_a", legacy("uploading").getString("asset_id"))
    }

    @Test fun migrationIsIdempotentAndRefusesNewerFormats() {
        val once = migrateLedger(legacy("checking"))
        assertSame(once, migrateLedger(once))
        assertEquals(canonical(once), canonical(migrateLedger(JSONObject(once.toString()))))
        assertThrows(IllegalArgumentException::class.java) { migrateLedger(JSONObject().put("schema_version", LEDGER_VERSION + 1)) }
    }

    @Test fun interruptedCapturesBecomeRecoverableDraftPages() {
        val capturing = migrateLedger(JSONObject().put("id", "record-2").put("stage", "capturing").put("ops", JSONObject()))
        assertEquals("local", capturing.getString("stage"))
        assertEquals("capturing", capturing.pageObjects().single().getString("status"))
        val failed = migrateLedger(JSONObject().put("id", "record-3").put("stage", "capture_failed").put("error", "上次拍照中断").put("ops", JSONObject()))
        assertEquals("local", failed.getString("stage"))
        assertEquals("failed", failed.pageObjects().single().getString("status"))
        assertEquals("上次拍照中断", failed.pageObjects().single().getString("error"))
    }

    @Test fun serverHistoryCacheKeepsItsDownloadedPage() {
        val remote = migrateLedger(JSONObject().put("id", "remote_abc").put("stage", "remote").put("asset_id", "asset_r").put("width", 10).put("height", 20).put("ops", JSONObject()))
        val page = remote.pageObjects().single()
        assertFalse(page.has("original_file"))
        assertEquals("asset_r", page.getString("asset_id"))
        val uncached = migrateLedger(JSONObject().put("id", "remote_def").put("stage", "remote").put("ops", JSONObject()))
        assertTrue(uncached.pageObjects().isEmpty())
    }

    @Test fun repositoryPersistsTheMigrationOnFirstRead() {
        val root = tempDir()
        try {
            val directory = File(root, "record-1").apply { mkdirs() }
            File(directory, "record.json").writeText(legacy("complete").toString())
            val repository = LocalCaptureRepository(root, CopyRenderer()) { Long.MAX_VALUE }
            val capture = repository.capture("record-1")
            assertEquals(File(directory, "upload.jpg"), capture.pages.single().upload)
            assertEquals(File(directory, "original.jpg"), capture.pages.single().original)
            assertEquals(LEDGER_VERSION, JSONObject(File(directory, "record.json").readText()).getInt("schema_version"))
        } finally { root.deleteRecursively() }
    }
}

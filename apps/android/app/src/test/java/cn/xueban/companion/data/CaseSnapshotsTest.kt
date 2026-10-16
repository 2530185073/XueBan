package cn.xueban.companion.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class CaseSnapshotsTest {
    private fun scope() = JSONObject().put("tenant_id", "tenant_test").put("campus_id", "campus_test").put("student_id", "student_test")
    private fun row(revision: Int, correct: Boolean, caseId: String = "case_test") = JSONObject()
        .put("case", JSONObject().put("id", caseId).put("revision", revision).put("scope", scope()).put("homework_id", "homework_test")
            .put("current_attempt_id", if (correct) "attempt_new" else "attempt_old")
            .put("current_decision", JSONObject().put("verdict", if (correct) "correct" else "incorrect")))
        .put("question", JSONObject().put("id", "question_test"))
        .put("attempt", JSONObject().put("id", if (correct) "attempt_new" else "attempt_old"))
    private fun record(id: String, rows: List<JSONObject>) = JSONObject().put("id", id).put("server", "https://example.test")
        .put("actor_id", "actor_test").put("student_id", "student_test").put("homework_id", "homework_test")
        .put("homework", JSONObject().put("scope", scope())).put("asset_id", "asset_$id").put("captured_at", 1234L)
        .put("ops", JSONObject().put("upload", JSONObject().put("key", "original-upload-key"))).put("cases", JSONArray(rows))

    @Test fun correctedCaseUpdatesOriginalHistoryButPreservesOriginalPhotoAndJournal() {
        val before = record("original", listOf(row(2, false), row(2, false, "case_other")))
        val snapshot = before.toString()
        val source = record("correction", listOf(row(8, true)))
        val merged = requireNotNull(mergeCaseSnapshot(before, source, row(8, true)))
        val current = merged.getJSONArray("cases").getJSONObject(0)
        assertEquals("attempt_new", current.getJSONObject("attempt").getString("id"))
        assertEquals("correct", current.getJSONObject("case").getJSONObject("current_decision").getString("verdict"))
        assertEquals("asset_original", merged.getString("asset_id"))
        assertEquals(1234L, merged.getLong("captured_at"))
        assertEquals("original-upload-key", merged.getJSONObject("ops").getJSONObject("upload").getString("key"))
        assertEquals("case_other", merged.getJSONArray("cases").getJSONObject(1).getJSONObject("case").getString("id"))
        assertEquals(snapshot, before.toString())
    }

    @Test fun synchronizationNeverCrossesIdentityHomeworkOrScopeAndDoesNotInsertUnrelatedCases() {
        val target = record("original", listOf(row(2, false)))
        val source = record("correction", listOf(row(8, true)))
        listOf("server", "actor_id", "student_id", "homework_id").forEach { field ->
            assertNull(mergeCaseSnapshot(target.copyJson().put(field, "other"), source, row(8, true)))
        }
        listOf("tenant_id", "campus_id", "student_id").forEach { field ->
            val otherScope = row(8, true)
            otherScope.getJSONObject("case").getJSONObject("scope").put(field, "other")
            assertNull(mergeCaseSnapshot(target, source, otherScope))
        }
        assertNull(mergeCaseSnapshot(target, source, row(8, true, "case_unrelated")))
    }

    @Test fun delayedReadsCannotRegressCaseAndSourceCanAddItsFirstSnapshot() {
        val source = record("correction", emptyList())
        val inserted = requireNotNull(mergeCaseSnapshot(source, source, row(8, true), insertForSource = true))
        assertEquals(1, inserted.getJSONArray("cases").length())
        assertNull(mergeCaseSnapshot(inserted, source, row(2, false)))
        assertNull(mergeCaseSnapshot(inserted, source, row(8, true)))
        assertNull(mergeCaseSnapshot(record("original", emptyList()), source, row(8, true), insertForSource = true))
    }
}

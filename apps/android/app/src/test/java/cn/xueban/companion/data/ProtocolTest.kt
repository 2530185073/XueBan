package cn.xueban.companion.data

import java.io.File
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ProtocolTest {
    @Test fun refreshedOldRecordSelectsCurrentAnswerPhotoInsteadOfOriginal() {
        val localAssets = listOf("asset_original", "asset_new_answer")
        assertEquals(1, currentAttemptPhotoIndex(listOf("asset_new_answer"), localAssets))
        assertEquals(0, currentAttemptPhotoIndex(listOf("asset_original"), localAssets))
        assertNull(currentAttemptPhotoIndex(listOf("asset_new_answer"), listOf("asset_original", null)))
        assertNull(currentAttemptPhotoIndex(emptyList(), localAssets))
    }
    @Test fun rejectedDraftDoesNotResumeWhenReturningFromPhotoPicker() {
        listOf(400, 401, 403, 404, 409, 413, 415, 422).forEach { status ->
            val recovery = processFailureRecovery("recognizing", status)
            assertEquals("failed", recovery.stage)
            assertFalse(recovery.keepActiveRecord)
            assertFalse(canResumeInForeground(recovery.stage))
        }
        listOf(null, 0, 408, 425, 429, 500, 503).forEach { status ->
            val recovery = processFailureRecovery("recognizing", status)
            assertEquals("recognizing", recovery.stage)
            assertTrue(recovery.keepActiveRecord)
            assertTrue(canResumeInForeground(recovery.stage))
        }
    }
    @Test fun correctionHelpMatchesActualRequestContractIncludingOldHintDrafts() {
        val contract = generateSequence(File(requireNotNull(System.getProperty("user.dir")))) { it.parentFile }
            .map { File(it, "contracts/phase1/requests.schema.json") }.first { it.isFile }
        val allowed = JSONObject(contract.readText()).getJSONObject("\$defs").getJSONObject("CreateCorrection")
            .getJSONObject("properties").getJSONObject("reported_help").strings("enum").toSet()
        assertEquals(setOf("unknown", "none_reported", "staff_help", "answer_shown"), allowed)
        allowed.forEach { assertEquals(it, correctionReportedHelp(it)) }
        listOf(null, "hint_used", "", "unrecognized_value").forEach {
            assertEquals("unknown", correctionReportedHelp(it))
            assertTrue(correctionReportedHelp(it) in allowed)
        }
        val oldDraft = JSONObject("{\"reported_help\":\"hint_used\"}")
        assertEquals("unknown", correctionReportedHelp(oldDraft.text("reported_help")))
        assertNotEquals("none_reported", correctionReportedHelp(oldDraft.text("reported_help")))
        assertEquals("hint_used", oldDraft.getString("reported_help")) // Preserve local evidence.
    }
    @Test fun unreadableOrUncheckedQuestionsNeverAppearAsCompletedChecks() {
        val oldCorrect = JSONObject().put("validity", "candidate").put("origin", "model").put("verdict", "correct")
        val row = JSONObject().put("case", JSONObject().put("current_decision", oldCorrect))
            .put("attempt", JSONObject().put("recognition_status", "needs_revision"))
        assertEquals("识别待核实，请重拍或请老师看", caseResultLabel(row))
        assertEquals("有题目识别待核实 · 请重拍或请老师看", completedPageLabel(listOf(row)))
        assertEquals("未识别出可检查的题目 · 请重拍或请老师看", completedPageLabel(emptyList()))
        row.getJSONObject("attempt").put("recognition_status", "ready")
        row.getJSONObject("case").put("current_decision", JSONObject.NULL)
        assertEquals("部分题目检查尚未完成", completedPageLabel(listOf(row)))
        row.getJSONObject("attempt").put("recognition_status", "failed")
        assertEquals("识别未完成，请重拍或请老师看", caseResultLabel(row))
    }
    @Test fun oldCheckCannotBeReusedForAnotherAnswerOrRecognitionRevision() {
        val case = JSONObject().put("question_id", "question_a").put("current_attempt_id", "attempt_new").put("current_question_revision", 2)
        val attempt = JSONObject().put("recognition_revision", 3)
        val versions = JSONObject().put("question_id", "question_a").put("attempt_id", "attempt_new").put("question_revision", 2).put("attempt_recognition_revision", 3)
        assertTrue(matchesCurrentInput(versions, case, attempt))
        assertFalse(matchesCurrentInput(null, case, attempt))
        listOf("question_id" to "question_old", "attempt_id" to "attempt_old", "question_revision" to 1, "attempt_recognition_revision" to 2).forEach { (key, value) ->
            assertFalse(matchesCurrentInput(versions.copyJson().put(key, value), case, attempt))
        }
        assertEquals("服务暂不可用，正在重试", jobState(JSONObject().put("status", "retryable_failed")))
    }
    @Test fun releaseRejectsAllCleartextAndUnsafeServerComponents() {
        listOf("http://10.0.2.2:8000", "https://user:pass@example.com", "https://example.com?key=x", "https://example.com#x", "https://example.com/v1", "https://example.com?").forEach {
            assertThrows(IllegalArgumentException::class.java) { UrlPolicy.server(it, false) }
        }
        assertEquals("https://example.com", UrlPolicy.server("https://example.com/", false))
    }
    @Test fun debugAllowsOnlyLiteralPrivateAddressesForHttp() {
        listOf("10.0.2.2", "127.0.0.1", "192.168.1.10", "172.16.0.2", "172.31.255.2").forEach { assertEquals("http://$it:8000", UrlPolicy.server("http://$it:8000", true)) }
        listOf("example.com", "localhost", "8.8.8.8", "172.32.0.1", "192.169.0.1", "010.0.0.1").forEach {
            assertThrows(IllegalArgumentException::class.java) { UrlPolicy.server("http://$it", true) }
        }
    }
    @Test fun signedImagesMustStayAtConfiguredOrigin() {
        val base = "https://homework.example.com"
        assertEquals("$base/v1/blob?ticket=synthetic", UrlPolicy.storage("$base/v1/blob?ticket=synthetic", base, false))
        listOf("https://other.example.com/blob?ticket=x", "$base:444/blob", "http://homework.example.com/blob", "https://user@homework.example.com/blob", "$base/blob#fragment").forEach {
            assertThrows(IllegalArgumentException::class.java) { UrlPolicy.storage(it, base, true) }
        }
    }
    @Test fun unknownMutationPreservesOriginalKeyAndPayloadAfterRestart() {
        val first = prepareOperation(null, "/v1/cases/case_a/checks", JSONObject().put("expected_revision", 1), "key-original")
        val fromDisk = JSONObject(first.toString())
        val retry = prepareOperation(fromDisk, "/v1/cases/case_a/checks", JSONObject().put("expected_revision", 2), "key-new-must-not-be-used")
        assertEquals("key-original", retry.getString("key"))
        assertEquals(1, retry.getJSONObject("body").getInt("expected_revision"))
        assertThrows(IllegalArgumentException::class.java) { prepareOperation(fromDisk, "/v1/cases/case_b/checks", JSONObject(), "key-new") }
    }
    @Test fun jobSuccessAndAiVerdictNeverMeanStaffConfirmation() {
        assertFalse(jobState(JSONObject().put("status", "succeeded")).contains("正确"))
        val candidate = JSONObject().put("validity", "candidate").put("origin", "model").put("verdict", "correct")
        assertEquals("AI 初判 · 正确", decisionLabel(candidate))
        candidate.put("validity", "confirmed")
        assertEquals("AI 初判 · 正确", decisionLabel(candidate))
        candidate.put("origin", "staff")
        assertEquals("老师确认 · 正确", decisionLabel(candidate))
        candidate.put("validity", "stale")
        assertEquals("结果已更新，等待重新检查", decisionLabel(candidate))
        candidate.put("validity", "needs_review").put("verdict", "unable_to_judge")
        assertEquals("暂时看不准 · 请老师一起看", decisionLabel(candidate))
    }
}

package cn.xueban.companion.data

import java.time.Instant
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import org.json.JSONArray
import org.json.JSONObject

/** Foreground resumable coordinator. Every mutation is journaled before sending. */
class CorrectionSync(private val repository: LocalCaptureRepository, private val api: BackendApi, private val changed: suspend () -> Unit) {
    private val pendingStates = listOf("queued", "running", "retryable_failed")
    private suspend fun update(id: String, action: (JSONObject) -> Unit) { repository.update(id, action); changed() }
    private suspend fun stage(id: String, stage: String) = update(id) { it.put("stage", stage).remove("error") }
    private fun post(id: String, op: String, path: String, body: JSONObject) = repository.postOnce(id, op, api, path, body)
    private fun get(path: String) = api.get(path)
    private fun state(id: String) = repository.read(id)

    suspend fun run(id: String) {
        currentCoroutineContext().ensureActive()
        var local = state(id)
        val generation = local.optInt("generation")
        var homeworkId = local.text("homework_id")
        if (homeworkId == null) {
            stage(id, "creating")
            val body = JSONObject().put("curriculum", JSONObject().put("stage", "junior_middle").put("publisher_code", "PEP").put("grade", JSONObject.NULL).put("subject", JSONObject.NULL).put("volume", JSONObject.NULL).put("edition", JSONObject.NULL))
                .put("title", "学校作业").put("occurred_at", Instant.ofEpochMilli(local.getLong("captured_at")).toString()).put("collection_scope", "partial")
            val homework = post(id, "homework", "/v1/students/${idPath(local.getString("student_id"))}/homeworks", body)
            homeworkId = homework.getString("id")
            update(id) { it.put("homework_id", homeworkId).put("homework", homework) }
        }
        val hwPath = "/v1/homeworks/${idPath(requireNotNull(homeworkId))}"
        var homework = get(hwPath)
        local = state(id)
        val isRemote = local.getString("id").startsWith("remote_")
        if (!isRemote) {
            stage(id, "uploading")
            var assetId = local.text("asset_id")
            var ticket = local.optJSONObject("upload_ticket")
            if (assetId == null) {
                val body = JSONObject().put("expected_revision", homework.getInt("revision")).put("page_index", homework.strings("asset_ids").size)
                    .put("sha256", local.getString("sha256")).put("byte_size", local.getLong("byte_size")).put("mime_type", "image/jpeg").put("width", local.getInt("width")).put("height", local.getInt("height"))
                ticket = post(id, "upload", "$hwPath/uploads", body)
                assetId = ticket.getJSONObject("asset").getString("id")
                update(id) { it.put("asset_id", assetId).put("upload_ticket", ticket) }
            }
            val assetPath = "/v1/assets/${idPath(requireNotNull(assetId))}"
            var asset = get(assetPath)
            if (asset.getString("status") != "uploaded") {
                require(asset.getString("status") == "awaiting_upload") { "这张照片未通过核验，请重新拍照" }
                if (ticket == null || Instant.parse(ticket.getString("expires_at")).isBefore(Instant.now().plusSeconds(15))) {
                    val saved = state(id)
                    val previous = saved.optInt("ticket_generation")
                    val pendingRenewal = saved.getJSONObject("ops").optJSONObject("renew-$previous")
                    val renew = if (pendingRenewal != null && !pendingRenewal.has("response")) previous else previous + 1
                    update(id) { it.put("ticket_generation", renew) }
                    ticket = post(id, "renew-$renew", "$assetPath/upload-ticket", JSONObject().put("expected_revision", asset.getInt("revision")))
                    update(id) { it.put("upload_ticket", ticket) }
                }
                currentCoroutineContext().ensureActive()
                val file = repository.write(id, state(id)).file
                require(file.isFile) { "本机照片不存在，请重新拍照" }
                val activeTicket = requireNotNull(ticket)
                api.upload(activeTicket.getString("put_url"), activeTicket.getJSONObject("required_headers"), file)
                asset = get(assetPath)
                if (asset.getString("status") != "uploaded") {
                    asset = post(id, "complete", "$assetPath/complete", JSONObject().put("expected_revision", asset.getInt("revision")).put("sha256", local.getString("sha256")).put("byte_size", local.getLong("byte_size")))
                }
                require(asset.getString("status") == "uploaded") { "服务端尚未确认图片上传" }
            }
            update(id) { it.put("asset", asset).remove("upload_ticket") }
        } else {
            cacheRemoteImage(id, homework)
        }
        local = state(id)
        homework = get(hwPath)
        val correction = local.optJSONObject("correction")
        var recognitionJob = local.text("recognition_job_id")
        if (recognitionJob == null && (correction != null || homework.strings("case_ids").isEmpty())) {
            // Recover server jobs on another installation before starting new work.
            if (isRemote && generation == 0) {
                recognitionJob = homework.strings("job_ids").asReversed().firstOrNull { get("/v1/jobs/${idPath(it)}").getString("kind") == "recognize_homework" }
                recognitionJob?.let { job -> update(id) { it.put("recognition_job_id", job) } }
            }
            if (recognitionJob == null) {
                stage(id, "recognizing")
                val accepted = if (correction == null) {
                    post(id, "recognize-$generation", "$hwPath/recognitions", JSONObject().put("expected_revision", homework.getInt("revision")).put("asset_ids", JSONArray().put(local.getString("asset_id"))))
                } else {
                    val caseId = correction.getString("case_id")
                    val case = get("/v1/cases/${idPath(caseId)}")
                    val recovering = state(id).getJSONObject("ops").has("correction-$generation")
                    require(recovering || case.getString("current_attempt_id") == correction.getString("parent_attempt_id")) { "这道题已有更新的订正，请先打开最新记录" }
                    post(id, "correction-$generation", "/v1/cases/${idPath(caseId)}/attempts", JSONObject().put("expected_revision", case.getInt("revision")).put("parent_attempt_id", correction.getString("parent_attempt_id"))
                        .put("question_revision", case.getInt("current_question_revision")).put("asset_ids", JSONArray().put(local.getString("asset_id"))).put("occurred_at", Instant.ofEpochMilli(local.getLong("captured_at")).toString()).put("reported_help", correctionReportedHelp(correction.text("reported_help"))))
                }
                recognitionJob = accepted.getJSONObject("job").getString("id")
                update(id) { it.put("recognition_job_id", recognitionJob) }
            }
        }
        if (recognitionJob != null) {
            stage(id, "recognizing")
            val job = awaitJob(id, requireNotNull(recognitionJob))
            if (job.getString("status") != "succeeded") { failed(id, job, null); return }
        }
        homework = get(hwPath)
        update(id) { it.put("homework", homework) }
        val caseIds = correction?.let { listOf(it.getString("case_id")) } ?: homework.strings("case_ids")
        if (caseIds.isEmpty()) {
            update(id) { it.put("stage", "complete").put("cases", JSONArray()).put("notice", "这张照片暂未识别出可检查的题目，请拍清楚后再试，或请老师一起看。") }
            return
        }
        stage(id, "checking")
        val jobs = state(id).optJSONObject("check_jobs") ?: JSONObject()
        for (caseId in caseIds) {
            currentCoroutineContext().ensureActive()
            val row = readCase(caseId)
            saveRow(id, row)
            val case = row.getJSONObject("case")
            val decision = case.optJSONObject("current_decision")
            val attempt = row.getJSONObject("attempt")
            val currentDecision = decision != null && decision.optString("validity") !in listOf("stale", "retracted") && matchesCurrentInput(decision.optJSONObject("input_versions"), case, attempt)
            if (currentDecision || attempt.getString("recognition_status") != "ready") { jobs.remove(caseId); continue }
            val currentChecks = case.strings("check_ids").asReversed().map { get("/v1/checks/${idPath(it)}") }
                .filter { matchesCurrentInput(it.optJSONObject("input_versions"), case, attempt) }
            // A previous attempt's job must never stand in for a new answer's check.
            if (jobs.has(caseId) && currentChecks.none { it.getString("job_id") == jobs.getString(caseId) }) jobs.remove(caseId)
            if (!jobs.has(caseId)) {
                val active = currentChecks.firstOrNull { get("/v1/jobs/${idPath(it.getString("job_id"))}").getString("status") in pendingStates }
                active?.let { jobs.put(caseId, it.getString("job_id")) }
            }
            if (!jobs.has(caseId)) {
                val checkPath = "/v1/cases/${idPath(caseId)}/checks"
                val inputKey = "${case.getString("current_attempt_id")}-${case.getInt("current_question_revision")}-${attempt.getInt("recognition_revision")}"
                val accepted = post(id, "check-$caseId-$inputKey-$generation", checkPath, JSONObject().put("expected_revision", case.getInt("revision")).put("attempt_id", case.getString("current_attempt_id"))
                    .put("question_revision", case.getInt("current_question_revision")).put("attempt_recognition_revision", attempt.getInt("recognition_revision")))
                jobs.put(caseId, accepted.getJSONObject("job").getString("id"))
                update(id) { it.put("check_jobs", jobs.copyJson()) }
            }
        }
        update(id) { it.put("check_jobs", jobs.copyJson()) }
        val pending = jobs.keys().asSequence().toMutableList()
        val failures = JSONArray()
        var polls = 0
        while (pending.isNotEmpty()) {
            currentCoroutineContext().ensureActive()
            var waitSeconds = 2L
            for (caseId in pending.toList()) {
                val job = get("/v1/jobs/${idPath(jobs.getString(caseId))}")
                update(id) { it.put("last_job", job) }
                if (job.getString("status") !in pendingStates) {
                    pending.remove(caseId)
                    if (job.getString("status") != "succeeded") failures.put(JSONObject().put("case_id", caseId).put("job", job))
                    saveRow(id, readCase(caseId))
                } else waitSeconds = maxOf(waitSeconds, job.optLong("retry_after_seconds", 2).coerceIn(2, 15))
            }
            if (pending.isNotEmpty()) {
                if (++polls >= 300) { update(id) { it.put("notice", "检查仍在进行，可以稍后从历史继续查看。") }; return }
                delay(waitSeconds * 1000)
            }
        }
        update(id) { it.put("stage", if (failures.length() == 0) "complete" else "failed").put("failed_checks", failures)
            .put("notice", if (failures.length() == 0) "AI 初判供订正参考，拿不准时请老师一起看。" else "部分题目这次没有完成，已完成的结果仍可查看。") }
    }

    private suspend fun awaitJob(id: String, jobId: String): JSONObject {
        repeat(300) {
            currentCoroutineContext().ensureActive()
            val job = get("/v1/jobs/${idPath(jobId)}")
            update(id) { it.put("last_job", job) }
            if (job.getString("status") !in pendingStates) return job
            delay(job.optLong("retry_after_seconds", 2).coerceIn(2, 15) * 1000)
        }
        throw ApiFailure(0, "still_running", "检查还在进行，稍后可从历史继续查看")
    }

    private suspend fun failed(id: String, job: JSONObject, caseId: String?) = update(id) {
        it.put("stage", "failed").put("failed_job", job).put("error", job.optJSONObject("failure")?.optString("message") ?: "任务未完成，请刷新记录或重试")
        caseId?.let { c -> it.put("failed_case_id", c) }
    }

    private fun readCase(caseId: String): JSONObject {
        val case = get("/v1/cases/${idPath(caseId)}")
        val question = get("/v1/questions/${idPath(case.getString("question_id"))}")
        val attempt = get("/v1/attempts/${idPath(case.getString("current_attempt_id"))}")
        require(question.getBoolean("currently_effective") && attempt.getBoolean("currently_effective")) { "题目已更新，请刷新后继续" }
        return JSONObject().put("case", case).put("question", question.getJSONObject("record")).put("attempt", attempt.getJSONObject("record"))
    }

    private suspend fun saveRow(id: String, row: JSONObject) {
        repository.saveCaseSnapshot(id, row)
        changed()
    }

    private fun cacheRemoteImage(id: String, homework: JSONObject) {
        val record = repository.write(id, state(id))
        if (record.file.isFile) return
        val assetId = homework.strings("asset_ids").firstOrNull() ?: return
        val asset = get("/v1/assets/${idPath(assetId)}")
        val access = get("/v1/assets/${idPath(assetId)}/access")
        api.download(access.getString("read_url"), record.file)
        repository.update(id) { it.put("asset_id", assetId).put("width", asset.getInt("width")).put("height", asset.getInt("height")) }
    }

    suspend fun requestHint(id: String, caseId: String, level: String) {
        val row = readCase(caseId); val case = row.getJSONObject("case")
        val decision = case.optJSONObject("current_decision") ?: error("请先完成检查")
        require(decision.getString("validity") !in listOf("stale", "retracted")) { "检查结果已更新，请刷新后再看提示" }
        val baseOperation = "hint-$caseId-${decision.getString("id")}-${decision.getInt("revision")}-$level"
        val saved = state(id)
        var retry = saved.optJSONObject("hint_retries")?.optInt(baseOperation) ?: 0
        val previousJob = saved.getJSONObject("ops").optJSONObject("$baseOperation-$retry")?.optJSONObject("response")?.optJSONObject("job")?.text("id")
        if (previousJob != null && get("/v1/jobs/${idPath(previousJob)}").getString("status") in listOf("failed", "cancelled", "superseded")) {
            retry++
            update(id) { val counters = it.optJSONObject("hint_retries") ?: JSONObject(); counters.put(baseOperation, retry); it.put("hint_retries", counters) }
        }
        val accepted = post(id, "$baseOperation-$retry", "/v1/cases/${idPath(caseId)}/hints", JSONObject().put("expected_revision", case.getInt("revision"))
            .put("attempt_id", case.getString("current_attempt_id")).put("source_decision_id", decision.getString("id")).put("source_decision_revision", decision.getInt("revision")).put("level", level))
        val job = awaitJob(id, accepted.getJSONObject("job").getString("id"))
        require(job.getString("status") == "succeeded") { "提示暂时没有生成，请老师一起看" }
        val hintId = job.getJSONObject("result").getString("resource_id")
        val hint = get("/v1/hints/${idPath(hintId)}")
        require(hint.getString("validity") == "active") { "提示已过期，请重新检查" }
        update(id) {
            val hints = it.optJSONObject("hints") ?: JSONObject()
            hints.put(caseId, hint); it.put("hints", hints)
        }
        saveRow(id, readCase(caseId))
    }

    suspend fun requestHelp(id: String, caseId: String, kind: String) {
        val case = get("/v1/cases/${idPath(caseId)}")
        post(id, "help-$caseId-${case.getString("current_attempt_id")}", "/v1/cases/${idPath(caseId)}/help-requests", JSONObject().put("expected_revision", case.getInt("revision"))
            .put("attempt_id", case.getString("current_attempt_id")).put("kind", kind).put("note", if (kind == "recognition") "照片中的识别内容需要老师核对。" else "我需要老师帮助核对这道题。"))
        saveRow(id, readCase(caseId))
    }
}

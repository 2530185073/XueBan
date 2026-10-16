package cn.xueban.companion.data

import java.net.URI
import org.json.JSONArray
import org.json.JSONObject

object UrlPolicy {
    fun server(value: String, debug: Boolean): String {
        val raw = value.trim().trimEnd('/')
        val uri = URI(raw)
        require(uri.userInfo == null && uri.host != null && uri.query == null && uri.fragment == null && !raw.contains('?') && !raw.contains('#')) { "请输入不含账号或参数的服务器地址" }
        require(uri.path.orEmpty().isEmpty() && uri.port in -1..65535) { "服务器地址只需填写域名或 IP 和端口" }
        require(uri.scheme == "https" || (debug && uri.scheme == "http" && privateHost(uri.host))) { "请使用 HTTPS；联调版仅允许内网 IP 的 HTTP" }
        return raw
    }

    fun storage(value: String, server: String, debug: Boolean): String {
        val uri = URI(value)
        val origin = URI(server)
        require(uri.host != null && uri.userInfo == null && uri.fragment == null && uri.port in -1..65535) { "图片访问地址无效" }
        fun port(u: URI) = if (u.port != -1) u.port else if (u.scheme == "https") 443 else 80
        require(uri.scheme == origin.scheme && uri.host == origin.host && port(uri) == port(origin)) { "图片地址与配对服务器不一致" }
        require(uri.scheme == "https" || (debug && uri.scheme == "http" && privateHost(uri.host) && uri.host == origin.host && uri.port == origin.port)) { "图片传输地址不安全" }
        return value
    }

    private fun privateHost(host: String): Boolean {
        if (host == "[::1]" || host == "::1") return true
        val parts = host.split('.')
        if (parts.size != 4 || parts.any { it.isEmpty() || !it.all(Char::isDigit) || (it.length > 1 && it.startsWith('0')) }) return false
        val n = parts.map { it.toIntOrNull() ?: return false }
        if (n.any { it !in 0..255 }) return false
        return n[0] == 10 || n[0] == 127 || (n[0] == 192 && n[1] == 168) || (n[0] == 172 && n[1] in 16..31)
    }
}

fun JSONObject.text(key: String): String? = if (isNull(key)) null else optString(key).takeIf { it.isNotEmpty() }
fun JSONObject.strings(key: String): List<String> = optJSONArray(key)?.let { a -> (0 until a.length()).map { a.getString(it) } }.orEmpty()
fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }
fun JSONObject.copyJson(): JSONObject = JSONObject(toString())
data class FailureRecovery(val stage: String, val keepActiveRecord: Boolean)
fun processFailureRecovery(previousStage: String, httpStatus: Int?): FailureRecovery {
    // A definite client/auth/revision rejection needs an explicit user action.
    // Timeouts, rate limits and transport/5xx uncertainty retain the exact operation.
    val rejected = httpStatus != null && httpStatus in 400..499 && httpStatus !in listOf(408, 425, 429)
    return if (rejected) FailureRecovery("failed", false) else FailureRecovery(previousStage, true)
}
fun canResumeInForeground(stage: String): Boolean = stage in listOf("creating", "uploading", "recognizing", "checking")
fun currentAttemptPhotoIndex(attemptAssets: List<String>, localAssets: List<String?>): Int? =
    localAssets.indexOfFirst { it != null && it in attemptAssets }.takeIf { it >= 0 }
/** Local hint_used is evidence, not a valid CreateCorrection.reported_help enum.
 * The backend inherits confirmed hint views from the parent attempt. Unknown
 * remains conservative if the view event has not reached the backend yet.
 * Normalize on submission so pre-existing private drafts are also compatible.
 */
fun correctionReportedHelp(local: String?): String = when (local) {
    "unknown", "none_reported", "staff_help", "answer_shown" -> local
    else -> "unknown"
}
fun matchesCurrentInput(versions: JSONObject?, case: JSONObject, attempt: JSONObject): Boolean = versions != null &&
    versions.optString("question_id") == case.getString("question_id") &&
    versions.optString("attempt_id") == case.getString("current_attempt_id") &&
    versions.optInt("question_revision") == case.getInt("current_question_revision") &&
    versions.optInt("attempt_recognition_revision") == attempt.getInt("recognition_revision")
fun prepareOperation(existing: JSONObject?, path: String, proposed: JSONObject, newKey: String): JSONObject {
    if (existing != null) {
        require(existing.getString("path") == path) { "待恢复操作与当前记录不一致，请联系老师" }
        return existing.copyJson()
    }
    return JSONObject().put("path", path).put("body", proposed.copyJson()).put("key", newKey)
}
fun idPath(value: String): String {
    require(Regex("[a-z][a-z0-9_-]{2,63}").matches(value)) { "服务器返回了无效的记录编号" }
    return value
}

/** No task success/AI candidate is presented as staff confirmation. */
fun caseResultLabel(row: JSONObject): String = when (row.getJSONObject("attempt").optString("recognition_status")) {
    "needs_revision" -> "识别待核实，请重拍或请老师看"
    "failed" -> "识别未完成，请重拍或请老师看"
    "pending" -> "正在识别题目与作答"
    "ready" -> decisionLabel(row.getJSONObject("case").optJSONObject("current_decision"))
    else -> "识别状态待核实，请老师看"
}

fun completedPageLabel(rows: List<JSONObject>): String = when {
    rows.isEmpty() -> "未识别出可检查的题目 · 请重拍或请老师看"
    rows.any { it.getJSONObject("attempt").optString("recognition_status") in listOf("needs_revision", "failed") } -> "有题目识别待核实 · 请重拍或请老师看"
    rows.any { it.getJSONObject("attempt").optString("recognition_status") != "ready" } -> "部分题目识别尚未完成"
    rows.any { it.getJSONObject("case").optJSONObject("current_decision") == null } -> "部分题目检查尚未完成"
    else -> "结果已返回 · 可查看逐题初判"
}

fun decisionLabel(decision: JSONObject?): String {
    if (decision == null) return "等待检查"
    val validity = decision.optString("validity")
    if (validity in listOf("stale", "retracted")) return "结果已更新，等待重新检查"
    if (decision.optString("verdict") == "unable_to_judge") return "暂时看不准 · 请老师一起看"
    val verdict = when (decision.optString("verdict")) {
        "correct" -> "正确"
        "incorrect" -> "需要订正"
        "partially_correct" -> "部分需要订正"
        "unanswered" -> "尚未作答"
        else -> return "暂时无法判断"
    }
    return if (validity == "confirmed" && decision.optString("origin") == "staff") "老师确认 · $verdict" else "AI 初判 · $verdict"
}

fun jobState(job: JSONObject): String = when (job.getString("status")) {
    "queued" -> "排队中"
    "running" -> "正在检查，请稍等"
    "succeeded" -> "处理完成，正在读取结果"
    "retryable_failed" -> "服务暂不可用，正在重试"
    "failed" -> "这次未能完成，可以稍后重试"
    "superseded", "cancelled" -> "这次任务已结束，请刷新记录"
    else -> "任务状态无法识别"
}

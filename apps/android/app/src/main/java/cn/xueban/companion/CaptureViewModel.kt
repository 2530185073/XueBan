package cn.xueban.companion

import android.app.Application
import android.net.Uri
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import cn.xueban.companion.data.*
import java.io.File
import java.net.URLEncoder
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

class CaptureViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = LocalCaptureRepository(application)
    private val vault = SessionVault(application)
    private var session: JSONObject? = null
    private var work: Job? = null
    private var foreground = true
    private var activeRecordId: String? = null
    private var correctionTarget: JSONObject? = null
    var captures by mutableStateOf<List<LocalCapture>>(emptyList()); private set
    var loading by mutableStateOf(true); private set
    var busy by mutableStateOf(false); private set
    var message by mutableStateOf<String?>(null); private set
    var backendAddress by mutableStateOf(""); private set
    var identity by mutableStateOf<JSONObject?>(null); private set
    var selectedStudent by mutableStateOf<String?>(null); private set
    var pairingPending by mutableStateOf(false); private set
    var historyCursor by mutableStateOf<String?>(null); private set
    val students: List<JSONObject> get() = identity?.optJSONArray("authorized_students")?.objects().orEmpty()
    val studentName: String get() = students.firstOrNull { it.getString("student_id") == selectedStudent }?.getString("display_name").orEmpty()
    val canCapture: Boolean get() = identity != null && selectedStudent != null && !busy
    val canReconnect: Boolean get() = identity == null && session?.text("access_token") != null
    val isCorrectionCapture: Boolean get() = correctionTarget != null

    init { restore() }
    fun reconnect() { if (!busy && !loading) restore() }

    private fun restore() = viewModelScope.launch {
        loading = true
        try {
            session = withContext(Dispatchers.IO) { vault.load() }
            backendAddress = session?.text("server").orEmpty()
            pairingPending = session?.text("pairing_key") != null
            if (session?.text("access_token") != null) {
                ensureSession()
                val me = withContext(Dispatchers.IO) { api().get("/v1/me") }
                acceptIdentity(me)
                loadLocal()
            }
        } catch (error: Exception) { report(error) }
        loading = false
        if (identity != null) setForeground(foreground)
    }

    fun pair(address: String, code: String) = viewModelScope.launch {
        if (busy) return@launch
        busy = true; message = null
        try {
            val base = UrlPolicy.server(address, BuildConfig.DEBUG)
            var pending = session?.takeIf { it.text("pairing_key") != null }
            if (pending != null) {
                require(pending.getString("server") == base && (code.isBlank() || code.trim() == pending.getString("pairing_code"))) { "上次配对结果待确认，请先继续上次配对；需要更换请退出配对后重新开始" }
            } else {
                require(code.trim().length in 6..200 && code.trim().none(Char::isWhitespace)) { "请输入老师提供的配对码" }
                pending = JSONObject().put("server", base).put("pairing_code", code.trim()).put("pairing_key", UUID.randomUUID().toString())
                withContext(Dispatchers.IO) { vault.save(requireNotNull(pending)) }
                session = pending; pairingPending = true
            }
            backendAddress = base
            val saved = requireNotNull(pending)
            if (saved.text("access_token") == null) {
                val result = withContext(Dispatchers.IO) { BackendApi(base, null, BuildConfig.DEBUG).post("/v1/device-sessions", JSONObject().put("pairing_code", saved.getString("pairing_code")), saved.getString("pairing_key")) }
                require(result.getString("access_token").isNotBlank() && Instant.parse(result.getString("expires_at")).isAfter(Instant.now())) { "配对已过期，请重新获取配对码" }
                saved.put("access_token", result.getString("access_token")).put("expires_at", result.getString("expires_at"))
                withContext(Dispatchers.IO) { vault.save(saved) }
                session = saved
            }
            ensureSession()
            val me = withContext(Dispatchers.IO) { api().get("/v1/me") }
            acceptIdentity(me)
            loadLocal()
            message = "配对成功，请确认当前孩子"
        } catch (error: Exception) { report(error) }
        busy = false
    }

    private suspend fun acceptIdentity(me: JSONObject) {
        val saved = requireNotNull(session)
        val allowed = me.getJSONArray("authorized_students").objects()
        require(allowed.isNotEmpty()) { "该账号尚未授权学生，请联系老师" }
        val selected = saved.text("student_id")?.takeIf { id -> allowed.any { it.getString("student_id") == id } } ?: allowed.first().getString("student_id")
        saved.put("identity", me.copyJson()).put("student_id", selected).remove("pairing_code")
        saved.remove("pairing_key")
        withContext(Dispatchers.IO) { vault.save(saved) }
        identity = me; selectedStudent = selected; pairingPending = false
        withContext(Dispatchers.IO) { repository.recoverInterruptedCaptures(backendAddress, me.getString("actor_id"), selected) }
    }

    private fun ensureSession() {
        val saved = session ?: throw ApiFailure(401, "session_missing", "请先让老师配对")
        val expiry = saved.text("expires_at") ?: throw ApiFailure(401, "session_missing", "请先让老师配对")
        if (!Instant.parse(expiry).isAfter(Instant.now())) throw ApiFailure(401, "expired", "配对已到期，请让老师重新配对")
    }
    private fun api(): BackendApi { ensureSession(); return BackendApi(backendAddress, requireNotNull(session).getString("access_token"), BuildConfig.DEBUG) }

    fun selectStudent(id: String) = viewModelScope.launch {
        if (busy || students.none { it.getString("student_id") == id }) return@launch
        selectedStudent = id; captures = emptyList(); activeRecordId = null; historyCursor = null
        session?.put("student_id", id)?.let { withContext(Dispatchers.IO) { vault.save(it) } }
        loadLocal()
    }

    fun unpair() = viewModelScope.launch {
        identity = null; selectedStudent = null; captures = emptyList(); activeRecordId = null
        work?.cancel(); work?.join()
        withContext(Dispatchers.IO) { vault.clear() }
        session = null; identity = null; selectedStudent = null; captures = emptyList(); pairingPending = false; activeRecordId = null
        message = "已退出本机配对，作业记录仍保留，重新授权后可查看"
    }

    private suspend fun loadLocal() {
        val actor = identity?.text("actor_id") ?: return
        val student = selectedStudent ?: return
        captures = withContext(Dispatchers.IO) { repository.listCaptures(backendAddress, actor, student) }
    }
    fun refresh() = viewModelScope.launch { try { loadLocal() } catch (error: Exception) { report(error) } }

    fun refreshHistory(more: Boolean = false) = viewModelScope.launch {
        if (busy) return@launch
        busy = true
        try {
            val actor = identity?.getString("actor_id") ?: error("请先配对")
            val student = selectedStudent ?: error("请先选择孩子")
            val suffix = if (more && historyCursor != null) "?cursor=" + URLEncoder.encode(historyCursor, "UTF-8") else ""
            val page = withContext(Dispatchers.IO) { api().get("/v1/students/${idPath(student)}/homeworks$suffix") }
            withContext(Dispatchers.IO) { page.getJSONArray("items").objects().forEach { repository.importHomework(backendAddress, actor, student, it) } }
            historyCursor = page.text("next_cursor"); loadLocal()
        } catch (error: Exception) { report(error) }
        busy = false
    }

    fun prepareCamera(recordId: String? = null, caseId: String? = null) {
        correctionTarget = if (recordId == null || caseId == null) null else {
            val record = captures.first { it.id == recordId }
            val row = record.cases.first { it.getJSONObject("case").getString("id") == caseId }
            val case = row.getJSONObject("case")
            val saved = repository.read(recordId)
            val shownHint = saved.optJSONObject("hints")?.optJSONObject(caseId)?.takeIf { it.optString("attempt_id") == case.getString("current_attempt_id") && it.optString("validity") == "active" }
            val help = saved.optJSONObject("shown_help")?.text(case.getString("current_attempt_id"))
                ?: if (shownHint != null) { if (shownHint.optBoolean("exposes_answer")) "answer_shown" else "hint_used" } else "unknown"
            JSONObject().put("case_id", caseId).put("homework_id", case.getString("homework_id")).put("parent_attempt_id", case.getString("current_attempt_id")).put("reported_help", help)
        }
    }
    fun beginCapture(): File {
        ensureSession(); check(canCapture) { "请先完成配对并确认当前孩子" }
        return repository.beginCapture(backendAddress, requireNotNull(identity).getString("actor_id"), requireNotNull(selectedStudent), correctionTarget)
    }
    fun finishCapture(file: File, onComplete: (LocalCapture?) -> Unit) = viewModelScope.launch {
        try { val result = withContext(Dispatchers.IO) { repository.completeCapture(file) }; loadLocal(); onComplete(result) }
        catch (error: Exception) { report(error); onComplete(null) }
    }
    fun abandonCapture(file: File) = viewModelScope.launch(Dispatchers.IO) { repository.abandonCapture(file) }

    fun importPhoto(uri: Uri, onComplete: (LocalCapture?) -> Unit) = viewModelScope.launch {
        var pending: File? = null
        try {
            val resolver = getApplication<Application>().contentResolver
            require(resolver.getType(uri) in listOf("image/jpeg", "image/png")) { "请选择 JPEG 或 PNG 照片" }
            val target = beginCapture(); pending = target
            val result = withContext(Dispatchers.IO) {
                resolver.openInputStream(uri)?.use { input -> target.outputStream().use { output ->
                    val buffer = ByteArray(16384); var total = 0L
                    while (true) { val count = input.read(buffer); if (count < 0) break; total += count; require(total <= 20_000_000) { "照片大于 20 MB，请选择较小的照片" }; output.write(buffer, 0, count) }
                } } ?: error("无法读取所选照片")
                repository.completeCapture(target)
            }
            loadLocal(); onComplete(result)
        } catch (error: Exception) {
            pending?.let { withContext(Dispatchers.IO) { repository.abandonCapture(it) } }
            report(error); onComplete(null)
        }
    }

    fun process(id: String, retryFailed: Boolean = false) {
        if (busy || !foreground) return
        activeRecordId = id
        work = viewModelScope.launch {
            busy = true
            try {
                val actor = identity?.getString("actor_id") ?: error("请先配对")
                val student = selectedStudent ?: error("请先选择孩子")
                val client = api()
                withContext(Dispatchers.IO) {
                    val record = repository.read(id)
                    require(record.getString("server") == backendAddress && record.getString("actor_id") == actor && record.getString("student_id") == student) { "这份作业不属于当前配对身份" }
                    if (retryFailed && record.getString("stage") == "failed") {
                        repository.update(id) {
                            if (it.optJSONObject("failed_job") != null) it.remove("recognition_job_id")
                            val jobs = it.optJSONObject("check_jobs")
                            it.optJSONArray("failed_checks")?.objects()?.forEach { failed -> jobs?.remove(failed.getString("case_id")) }
                            it.remove("failed_job"); it.remove("failed_checks"); it.put("generation", it.optInt("generation") + 1)
                        }
                    }
                    CorrectionSync(repository, client) { withContext(Dispatchers.Main) { loadLocal() } }.run(id)
                }
                activeRecordId = null; loadLocal()
            } catch (cancel: CancellationException) { throw cancel }
            catch (error: Exception) {
                val recovery = withContext(Dispatchers.IO) {
                    val recovery = processFailureRecovery(repository.read(id).getString("stage"), (error as? ApiFailure)?.status)
                    repository.update(id) { it.put("error", safeMessage(error)).put("stage", recovery.stage) }
                    recovery
                }
                if (!recovery.keepActiveRecord) activeRecordId = null
                report(error); loadLocal()
            } finally {
                busy = false
                // A foreground return may arrive while the previous blocking request is finishing.
                if (foreground && work?.isCancelled == true && activeRecordId != null && identity != null) {
                    viewModelScope.launch { activeRecordId?.let { process(it) } }
                }
            }
        }
    }

    fun setForeground(value: Boolean) {
        foreground = value
        if (!value) work?.cancel()
        else if (identity != null && !busy) {
            val pending = activeRecordId ?: captures.firstOrNull { canResumeInForeground(it.stage) }?.id
            pending?.let { process(it) }
        }
    }

    fun hint(id: String, caseId: String, level: String) = action(id) { it.requestHint(id, caseId, level) }
    fun help(id: String, caseId: String, recognition: Boolean) = action(id) { it.requestHelp(id, caseId, if (recognition) "recognition" else "question") }
    private fun action(id: String, run: suspend (CorrectionSync) -> Unit) {
        if (busy) return
        work = viewModelScope.launch {
            busy = true
            try { val client = api(); withContext(Dispatchers.IO) { run(CorrectionSync(repository, client) { withContext(Dispatchers.Main) { loadLocal() } }) }; loadLocal() }
            catch (cancel: CancellationException) { throw cancel }
            catch (error: Exception) { report(error) }
            finally { busy = false }
        }
    }

    fun markHintShown(recordId: String, hint: JSONObject) = viewModelScope.launch {
        try {
            val client = api()
            withContext(Dispatchers.IO) {
                repository.update(recordId) {
                    val shown = it.optJSONObject("shown_help") ?: JSONObject()
                    val attemptId = hint.getString("attempt_id")
                    val help = if (hint.getBoolean("exposes_answer") || shown.text(attemptId) == "answer_shown") "answer_shown" else "hint_used"
                    shown.put(attemptId, help); it.put("shown_help", shown)
                }
                repository.postOnce(recordId, "hint-view-${hint.getString("id")}", client, "/v1/hints/${idPath(hint.getString("id"))}/views", JSONObject().put("expected_revision", hint.getInt("revision")).put("shown_at", Instant.now().toString()))
            }
            loadLocal()
        } catch (cancel: CancellationException) { throw cancel }
        catch (error: Exception) { report(error) }
    }

    private fun safeMessage(error: Exception): String = when (error) {
        is ApiFailure -> error.message ?: "服务暂时不可用"
        is java.io.IOException -> "连接中断，原图和进度已保留，请检查网络后继续"
        is IllegalArgumentException, is IllegalStateException -> error.message ?: "暂时无法继续，请联系老师"
        else -> "暂时无法读取记录，请重试或联系老师"
    }
    private suspend fun report(error: Exception) {
        if (error is CancellationException) throw error
        if (error is ApiFailure && error.status == 401 && error.code != "upload_failed" && error.code != "image_unavailable") {
            withContext(Dispatchers.IO) { vault.clear() }
            session = null; identity = null; selectedStudent = null; captures = emptyList(); pairingPending = false; activeRecordId = null
        }
        if (error is ApiFailure && error.status == 403 && error.code != "upload_failed") { identity = null; selectedStudent = null; captures = emptyList() }
        message = safeMessage(error)
    }
    fun clearMessage() { message = null }
}

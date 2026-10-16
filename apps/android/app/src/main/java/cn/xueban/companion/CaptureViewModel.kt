package cn.xueban.companion

import android.app.Application
import android.net.Uri
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.work.WorkInfo
import cn.xueban.companion.data.*
import java.io.File
import java.io.IOException
import java.net.URLEncoder
import java.time.Instant
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

enum class Screen { Loading, Login, ChangePassword, Home }
data class PendingPage(val draftId: String, val pageId: String, val file: File)

class CaptureViewModel(application: Application) : AndroidViewModel(application) {
    private val services = AppServices.get(application)
    private val auth = services.auth
    private val repository = services.repository
    private var correctionTarget: JSONObject? = null
    var screen by mutableStateOf(Screen.Loading); private set
    var account by mutableStateOf<Account?>(null); private set
    var offline by mutableStateOf(false); private set
    var savedServer by mutableStateOf(""); private set
    var savedUsername by mutableStateOf(""); private set
    var captures by mutableStateOf<List<LocalCapture>>(emptyList()); private set
    var draft by mutableStateOf<LocalCapture?>(null); private set
    var busy by mutableStateOf(false); private set
    var syncState by mutableStateOf<WorkInfo.State?>(null); private set
    var message by mutableStateOf<String?>(null); private set
    var historyCursor by mutableStateOf<String?>(null); private set
    val canCapture: Boolean get() = account != null && !busy
    val isCorrectionCapture: Boolean get() = correctionTarget != null
    val syncRunning: Boolean get() = syncState == WorkInfo.State.RUNNING
    val syncWaiting: Boolean get() = syncState == WorkInfo.State.ENQUEUED || syncState == WorkInfo.State.BLOCKED
    val unsentCount: Int get() = captures.count { it.isDraft || canResumeAutomatically(it.stage) }

    init {
        restore()
        viewModelScope.launch { repository.changes.drop(1).collectLatest { delay(200); loadLocal() } }
        viewModelScope.launch {
            SyncScheduler.state(application).collect { state ->
                syncState = state
                if (state == null || state.isFinished) reconcileSession()
            }
        }
    }

    private suspend fun <T> io(block: () -> T): T = withContext(Dispatchers.IO) { block() }

    fun reconnect() { if (!busy) restore() }

    private fun restore() = viewModelScope.launch {
        screen = Screen.Loading
        try {
            val saved = io { auth.saved() }
            savedServer = saved?.text("server").orEmpty(); savedUsername = saved?.text("username").orEmpty()
            if (!io { auth.hasToken() }) { screen = Screen.Login; return@launch }
            accept(io { auth.verify() })
        } catch (cancel: CancellationException) { throw cancel }
        catch (error: Exception) {
            val cached = io { auth.cachedAccount() }
            if (cached != null && isUnknownOutcome(error)) {
                account = cached; offline = true; screen = Screen.Home; loadLocal()
                SyncScheduler.enqueue(getApplication(), now = false)
                message = "暂时连不上服务器：可以继续拍照保存，联网后会自动上传；检查结果需要联网后查看。"
            } else {
                report(error)
                if (screen == Screen.Loading) screen = Screen.Login
            }
        }
    }

    private suspend fun accept(result: AuthResult) {
        when (result) {
            is AuthResult.Ready -> {
                val next = result.account
                if (account?.let { it.server != next.server || it.actorId != next.actorId || it.studentId != next.studentId } == true) { captures = emptyList(); draft = null; historyCursor = null }
                account = next; offline = false; savedServer = next.server; savedUsername = next.username; screen = Screen.Home
                io { repository.recoverInterruptedCaptures(next.server, next.actorId, next.studentId) }
                loadLocal()
                SyncScheduler.enqueue(getApplication(), now = false)
            }
            is AuthResult.PasswordChangeRequired -> { account = null; savedUsername = result.username; screen = Screen.ChangePassword }
        }
    }

    fun login(address: String, username: String, password: String) = viewModelScope.launch {
        if (busy) return@launch
        busy = true; message = null
        try { accept(io { auth.login(address, username, password) }) }
        catch (cancel: CancellationException) { throw cancel }
        catch (error: Exception) {
            savedServer = address.trim(); savedUsername = username.trim()
            message = if (isUnknownOutcome(error)) "网络中断，登录结果未确认。请保持账号和密码不变，再点一次登录。" else syncFailureMessage(error)
        }
        busy = false
    }

    fun changePassword(current: String, new: String) = viewModelScope.launch {
        if (busy) return@launch
        busy = true; message = null
        try { accept(io { auth.changePassword(current, new) }); message = "新密码已生效，请记住它。" }
        catch (cancel: CancellationException) { throw cancel }
        catch (error: Exception) {
            if (classifyFailure(error) == FailureKind.Other) message = if (isUnknownOutcome(error)) "网络中断，改密结果未确认。请输入相同的内容再保存一次。" else syncFailureMessage(error)
            else report(error)
        }
        busy = false
    }

    fun logout() = viewModelScope.launch {
        if (busy) return@launch
        busy = true
        SyncScheduler.cancel(getApplication())
        val confirmed = io { auth.logout() }
        clearAccountView(); screen = Screen.Login; busy = false
        message = if (confirmed) "已退出登录。本机照片仍按账号分开保存，重新登录同一账号后可见并继续上传。"
        else "本机已退出，但服务器尚未确认会话撤销；联网前原会话仍可能有效直到到期。"
    }

    private fun clearAccountView() {
        account = null; captures = emptyList(); draft = null; correctionTarget = null; historyCursor = null; offline = false
    }

    /** Picks up a session ended by the background worker (expired login or forced password change). */
    private suspend fun reconcileSession() {
        if (screen != Screen.Home || offline) return
        val saved = io { auth.saved() }
        when {
            saved?.optBoolean("must_change_password") == true -> { clearAccountView(); screen = Screen.ChangePassword }
            !io { auth.hasToken() } -> { clearAccountView(); screen = Screen.Login; message = SESSION_EXPIRED }
        }
    }

    fun onForeground() = viewModelScope.launch {
        if (screen != Screen.Home) return@launch
        reconcileSession()
        loadLocal()
        if (captures.any { !it.isDraft && canResumeAutomatically(it.stage) }) SyncScheduler.enqueue(getApplication(), now = false)
    }

    private suspend fun loadLocal() {
        val current = account ?: return
        captures = io { repository.listCaptures(current.server, current.actorId, current.studentId) }
        draft = draft?.let { open -> io { runCatching { repository.capture(open.id) }.getOrNull() } }?.takeIf { it.isDraft }
    }

    fun refresh() = viewModelScope.launch { try { loadLocal() } catch (error: Exception) { report(error) } }

    fun refreshHistory(more: Boolean = false) = viewModelScope.launch {
        if (busy) return@launch
        busy = true
        try {
            val current = account ?: error("请先登录")
            val suffix = if (more && historyCursor != null) "?cursor=" + URLEncoder.encode(historyCursor, "UTF-8") else ""
            val page = io { api().get("/v1/students/${idPath(current.studentId)}/homeworks$suffix") }
            io { page.getJSONArray("items").objects().forEach { repository.importHomework(current.server, current.actorId, current.studentId, it) } }
            historyCursor = page.text("next_cursor"); offline = false; loadLocal()
        } catch (cancel: CancellationException) { throw cancel }
        catch (error: Exception) { report(error) }
        busy = false
    }

    fun prepareCapture(recordId: String? = null, caseId: String? = null) {
        draft = null
        correctionTarget = if (recordId == null || caseId == null) null else {
            val record = captures.first { it.id == recordId }
            val row = record.cases.first { it.getJSONObject("case").getString("id") == caseId }
            val case = row.getJSONObject("case")
            val saved = record.state
            val shownHint = saved.optJSONObject("hints")?.optJSONObject(caseId)?.takeIf { it.optString("attempt_id") == case.getString("current_attempt_id") && it.optString("validity") == "active" }
            val help = saved.optJSONObject("shown_help")?.text(case.getString("current_attempt_id"))
                ?: if (shownHint != null) { if (shownHint.optBoolean("exposes_answer")) "answer_shown" else "hint_used" } else "unknown"
            JSONObject().put("case_id", caseId).put("homework_id", case.getString("homework_id")).put("parent_attempt_id", case.getString("current_attempt_id")).put("reported_help", help)
        }
    }

    fun openDraft(id: String) {
        draft = captures.firstOrNull { it.id == id && it.isDraft }
        correctionTarget = draft?.state?.optJSONObject("correction")
    }

    private suspend fun ensureDraft(): String {
        draft?.let { return it.id }
        val current = account ?: throw ApiFailure(401, "session_missing", "请先登录")
        val id = io { repository.createDraft(current.server, current.actorId, current.studentId, correctionTarget) }
        draft = io { repository.capture(id) }
        return id
    }

    fun beginPage(onReady: (PendingPage?) -> Unit) = viewModelScope.launch {
        try {
            check(canCapture) { "请先登录" }
            val id = ensureDraft()
            val (pageId, file) = io { repository.beginPage(id, "camera") }
            onReady(PendingPage(id, pageId, file))
        } catch (cancel: CancellationException) { throw cancel }
        catch (error: Exception) { report(error); onReady(null) }
    }

    fun finishPage(pending: PendingPage, onComplete: () -> Unit) = viewModelScope.launch {
        try { io { repository.completePage(pending.draftId, pending.pageId) } }
        catch (cancel: CancellationException) { throw cancel }
        catch (error: Exception) {
            io { runCatching { repository.removePage(pending.draftId, pending.pageId) } }
            report(error)
        }
        loadLocal(); onComplete()
    }

    fun abandonPage(pending: PendingPage) = viewModelScope.launch { io { runCatching { repository.abandonPage(pending.draftId, pending.pageId) } }; loadLocal() }

    fun importPages(uris: List<Uri>) = viewModelScope.launch {
        if (busy || uris.isEmpty()) return@launch
        busy = true
        val problems = mutableListOf<String>()
        try {
            val id = ensureDraft()
            val resolver = getApplication<Application>().contentResolver
            for (uri in uris) {
                if (resolver.getType(uri) !in listOf("image/jpeg", "image/png", "image/webp")) { problems += "有照片不是 JPEG/PNG/WebP 格式，已跳过"; continue }
                val (pageId, target) = io { repository.beginPage(id, "picker") }
                try {
                    io {
                        resolver.openInputStream(uri)?.use { input -> target.outputStream().use { output ->
                            val buffer = ByteArray(16384); var total = 0L
                            while (true) {
                                val count = input.read(buffer); if (count < 0) break
                                total += count
                                require(total <= ImageLimits.SOURCE_MAX_BYTES) { "有照片大于 20 MB，已跳过；请换一张较小的照片或直接用相机拍" }
                                output.write(buffer, 0, count)
                            }
                        } } ?: error("有照片无法读取，已跳过")
                        repository.completePage(id, pageId)
                    }
                } catch (cancel: CancellationException) { throw cancel }
                catch (error: Exception) {
                    io { runCatching { repository.removePage(id, pageId) } }
                    problems += syncFailureMessage(error)
                }
            }
        } catch (cancel: CancellationException) { throw cancel }
        catch (error: Exception) { report(error) }
        loadLocal()
        if (problems.isNotEmpty()) message = problems.distinct().joinToString("\n")
        busy = false
    }

    private fun draftAction(block: suspend (String) -> Unit) = viewModelScope.launch {
        val id = draft?.id ?: return@launch
        if (busy) return@launch
        busy = true
        try { block(id) }
        catch (cancel: CancellationException) { throw cancel }
        catch (error: Exception) { report(error) }
        loadLocal(); busy = false
    }

    fun removePage(pageId: String) = draftAction { io { repository.removePage(it, pageId) } }
    fun movePage(pageId: String, delta: Int) = draftAction { io { repository.movePage(it, pageId, delta) } }
    fun editPage(pageId: String, edit: PageEdit, onSaved: () -> Unit) = draftAction { io { repository.editPage(it, pageId, edit) }; onSaved() }
    fun discardDraft(onDone: () -> Unit) = draftAction { io { repository.discardDraft(it) }; draft = null; onDone() }

    fun submitDraft(confirmedPages: Int, onSubmitted: (String) -> Unit) = draftAction { id ->
        io { repository.submit(id, confirmedPages) }
        draft = null; correctionTarget = null
        SyncScheduler.enqueue(getApplication(), now = true)
        onSubmitted(id)
    }

    /** Explicit continue/retry: the worker resumes from the ledger with the same saved keys. */
    fun process(id: String, retryFailed: Boolean = false) = viewModelScope.launch {
        try {
            io { repository.requestSync(id, retryFailed) }
            SyncScheduler.enqueue(getApplication(), now = true)
            loadLocal()
        } catch (cancel: CancellationException) { throw cancel }
        catch (error: Exception) { report(error) }
    }

    private fun api(): BackendApi {
        val session = auth.activeSession() ?: throw ApiFailure(401, "session_missing", "请先登录")
        return BackendApi(session.server, session.token, BuildConfig.DEBUG)
    }

    fun hint(id: String, caseId: String, level: String) = action { it.requestHint(id, caseId, level) }
    fun help(id: String, caseId: String, recognition: Boolean) = action { it.requestHelp(id, caseId, if (recognition) "recognition" else "question") }
    private fun action(run: suspend (CorrectionSync) -> Unit) = viewModelScope.launch {
        if (busy) return@launch
        busy = true
        try { withContext(Dispatchers.IO) { run(CorrectionSync(repository, api())) }; offline = false; loadLocal() }
        catch (cancel: CancellationException) { throw cancel }
        catch (error: Exception) { report(error) }
        finally { busy = false }
    }

    fun markHintShown(recordId: String, hint: JSONObject) = viewModelScope.launch {
        try {
            withContext(Dispatchers.IO) {
                val client = api()
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

    private fun isUnknownOutcome(error: Exception): Boolean =
        error is IOException && (error !is ApiFailure || processFailureRecovery("", error.status).keepActiveRecord)

    private suspend fun report(error: Exception) {
        if (error is CancellationException) throw error
        when (classifyFailure(error)) {
            FailureKind.SessionExpired -> endSession(SESSION_EXPIRED)
            FailureKind.AccessDenied -> endSession(error.message ?: "当前账号无权使用学伴平板，请联系老师")
            FailureKind.PasswordChangeRequired -> { io { auth.markPasswordChangeRequired() }; clearAccountView(); screen = Screen.ChangePassword; message = error.message }
            FailureKind.Other -> message = syncFailureMessage(error)
        }
    }

    private suspend fun endSession(text: String) {
        io { auth.expire() }
        SyncScheduler.cancel(getApplication())
        clearAccountView(); screen = Screen.Login; message = text
    }

    fun clearMessage() { message = null }

    private companion object {
        const val SESSION_EXPIRED = "登录已过期，请重新登录。本机未上传的作业已保留，登录同一账号后会继续。"
    }
}

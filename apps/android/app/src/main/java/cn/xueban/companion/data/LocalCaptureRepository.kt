package cn.xueban.companion.data

import androidx.core.util.AtomicFile
import java.io.File
import java.util.UUID
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import org.json.JSONArray
import org.json.JSONObject

data class LocalPage(val id: String, val original: File?, val upload: File, val state: JSONObject) {
    val status: String get() = state.optString("status", "ready")
    val ready: Boolean get() = status == "ready"
    val edit: PageEdit get() = PageEdit(state.optInt("rotation"), NormRect.fromJson(state.optJSONObject("crop")))
    val assetId: String? get() = state.text("asset_id")
    val quality: QualityReport? get() = QualityReport.fromJson(state.optJSONObject("quality"))
    val error: String? get() = state.text("error")
    val confirmed: Boolean get() = isServerConfirmed(state)
}

data class LocalCapture(val id: String, val directory: File, val capturedAt: Long, val state: JSONObject) {
    val title: String get() = state.optString("title", "我的作业")
    val stage: String get() = state.optString("stage", "local")
    val error: String? get() = state.text("error")
    val cases: List<JSONObject> get() = state.optJSONArray("cases")?.objects().orEmpty()
    val isDraft: Boolean get() = stage == "local"
    val pages: List<LocalPage> get() = state.pageObjects().map { page ->
        LocalPage(page.getString("id"), page.text("original_file")?.let { File(directory, it) }, File(directory, page.optString("upload_file", "upload.jpg")), page)
    }
}

data class RenderedPage(val width: Int, val height: Int, val byteSize: Long, val sha256: String, val quality: QualityReport?)

/** Derives the upload image from an untouched original; the original file is never modified. */
fun interface PageRenderer {
    fun render(original: File, target: File, edit: PageEdit): RenderedPage
}

/** Versioned atomic journal + private originals, partitioned by server/actor/student. */
class LocalCaptureRepository(private val root: File, private val renderer: PageRenderer, private val usableSpace: () -> Long) {
    private val revision = MutableStateFlow(0L)
    val changes: StateFlow<Long> = revision.asStateFlow()
    private val validId = Regex("[a-zA-Z0-9_-]{1,100}")
    private val validFile = Regex("[a-zA-Z0-9_-]{1,100}\\.jpg")

    init { root.mkdirs() }

    private fun directory(id: String): File { require(validId.matches(id)); return File(root, id) }
    private fun journal(id: String) = AtomicFile(File(directory(id), "record.json"))
    fun file(id: String, name: String): File { require(validFile.matches(name)) { "本机文件名无效" }; return File(directory(id), name) }

    @Synchronized fun read(id: String): JSONObject {
        val raw = JSONObject(String(journal(id).readFully(), Charsets.UTF_8))
        val migrated = migrateLedger(raw)
        if (migrated !== raw) store(id, migrated)
        return migrated
    }

    @Synchronized fun write(id: String, value: JSONObject): LocalCapture {
        store(id, value)
        revision.update { it + 1 }
        return capture(id, value)
    }

    private fun store(id: String, value: JSONObject) {
        check(directory(id).isDirectory || directory(id).mkdirs()) { "本机空间不足，无法保存记录" }
        val file = journal(id); val stream = file.startWrite()
        try { stream.write(value.toString().toByteArray(Charsets.UTF_8)); file.finishWrite(stream) }
        catch (error: Exception) { file.failWrite(stream); throw error }
    }

    @Synchronized fun update(id: String, action: (JSONObject) -> Unit): LocalCapture = read(id).let { action(it); write(id, it) }
    @Synchronized fun capture(id: String): LocalCapture = capture(id, read(id))
    private fun capture(id: String, value: JSONObject) = LocalCapture(id, directory(id), value.optLong("captured_at"), value)

    @Synchronized fun listCaptures(server: String, actor: String, student: String): List<LocalCapture> {
        val records = root.listFiles().orEmpty()
            .filter { it.isDirectory && validId.matches(it.name) && (File(it, "record.json").exists() || File(it, "record.json.bak").exists()) }
            .mapNotNull { runCatching { capture(it.name, read(it.name)) }.getOrNull() }
            .filter { it.state.optString("server") == server && it.state.optString("actor_id") == actor && it.state.optString("student_id") == student }
            .filter { it.stage != "capturing" && !(it.isDraft && it.pages.isEmpty()) }
            .sortedByDescending { it.capturedAt }.toMutableList()
        // Reconcile old-version journals from locally known revisions, with no HTTP/model calls.
        val snapshots = records.flatMap { record -> record.cases.map { record.state to it } }
            .sortedByDescending { it.second.getJSONObject("case").getLong("revision") }
        snapshots.forEach { (source, row) -> records.indices.forEach { index ->
            mergeCaseSnapshot(records[index].state, source, row)?.let { records[index] = write(records[index].id, it) }
        } }
        return records
    }

    @Synchronized fun saveCaseSnapshot(sourceId: String, row: JSONObject) {
        val source = read(sourceId)
        listCaptures(source.getString("server"), source.getString("actor_id"), source.getString("student_id")).forEach { record ->
            mergeCaseSnapshot(record.state, source, row, insertForSource = true)?.let { write(record.id, it) }
        }
    }

    @Synchronized fun createDraft(server: String, actor: String, student: String, correction: JSONObject?): String {
        storageProblem(usableSpace())?.let { throw IllegalStateException(it) }
        val id = UUID.randomUUID().toString()
        val state = JSONObject().put("schema_version", LEDGER_VERSION).put("id", id).put("server", server).put("actor_id", actor).put("student_id", student)
            .put("captured_at", System.currentTimeMillis()).put("stage", "local").put("ops", JSONObject()).put("generation", 0).put("pages", JSONArray())
            .put("title", if (correction == null) "学校作业" else "新的订正")
        correction?.let { state.put("correction", it.copyJson()).put("homework_id", it.getString("homework_id")) }
        write(id, state)
        return id
    }

    /** Reserves a page entry before any bytes are written, so an interrupted capture is recoverable. */
    @Synchronized fun beginPage(id: String, source: String): Pair<String, File> {
        storageProblem(usableSpace())?.let { throw IllegalStateException(it) }
        val pageId = "page_" + UUID.randomUUID().toString().replace("-", "").take(12)
        update(id) { state ->
            requireDraft(state)
            state.getJSONArray("pages").put(JSONObject().put("id", pageId).put("status", "capturing").put("source", source)
                .put("original_file", "$pageId-original.jpg").put("upload_file", "$pageId-upload.jpg").put("rotation", 0).put("crop", JSONObject.NULL))
        }
        return pageId to file(id, "$pageId-original.jpg")
    }

    fun completePage(id: String, pageId: String): LocalCapture = render(id, pageId, null)
    fun editPage(id: String, pageId: String, edit: PageEdit): LocalCapture = render(id, pageId, edit)

    private fun render(id: String, pageId: String, edit: PageEdit?): LocalCapture {
        val page = synchronized(this) { read(id).also(::requireDraft).let { page(it, pageId).copyJson() } }
        val nextEdit = edit ?: PageEdit(page.optInt("rotation"), NormRect.fromJson(page.optJSONObject("crop")))
        val original = file(id, page.getString("original_file"))
        val rendered = renderer.render(original, file(id, page.getString("upload_file")), nextEdit)
        return update(id) { state ->
            page(state, pageId).put("status", "ready").put("rotation", nextEdit.rotation).put("crop", nextEdit.crop?.takeUnless { it.isFull }?.toJson() ?: JSONObject.NULL)
                .put("width", rendered.width).put("height", rendered.height).put("byte_size", rendered.byteSize).put("sha256", rendered.sha256)
                .put("quality", rendered.quality?.toJson() ?: JSONObject.NULL).remove("error")
        }
    }

    @Synchronized fun abandonPage(id: String, pageId: String) {
        val state = read(id)
        if (page(state, pageId).optString("status") == "capturing") removePage(id, pageId)
    }

    @Synchronized fun removePage(id: String, pageId: String): LocalCapture {
        val state = read(id); requireDraft(state)
        val page = page(state, pageId)
        listOfNotNull(page.text("original_file"), page.text("upload_file")).forEach { file(id, it).delete() }
        val pages = state.pageObjects().filter { it.getString("id") != pageId }
        return write(id, state.put("pages", JSONArray(pages)))
    }

    @Synchronized fun movePage(id: String, pageId: String, delta: Int): LocalCapture = update(id) { state ->
        requireDraft(state)
        val pages = state.pageObjects().toMutableList()
        val from = pages.indexOfFirst { it.getString("id") == pageId }
        require(from >= 0) { "这一页已不存在" }
        val to = (from + delta).coerceIn(0, pages.lastIndex)
        pages.add(to, pages.removeAt(from))
        state.put("pages", JSONArray(pages))
    }

    @Synchronized fun discardDraft(id: String) {
        val state = read(id); requireDraft(state)
        check(state.getJSONObject("ops").length() == 0) { "这份作业已有提交记录，不能直接删除" }
        directory(id).deleteRecursively()
        revision.update { it + 1 }
    }

    /** The student confirms the page count; only then may background work upload anything. */
    @Synchronized fun submit(id: String, confirmedPages: Int): LocalCapture = update(id) { state ->
        requireDraft(state)
        val pages = state.pageObjects()
        require(pages.isNotEmpty()) { "请先拍下至少一页作业" }
        require(pages.all { it.optString("status") == "ready" }) { "还有照片没有处理完，请删除出错的页后再提交" }
        require(pages.size == confirmedPages) { "页数已变化，请重新确认" }
        pages.forEach { page -> uploadTooLargeMessage(page.optLong("byte_size"))?.let { throw IllegalArgumentException(it) } }
        state.put("stage", "queued").put("submitted_at", System.currentTimeMillis()).put("confirmed_pages", confirmedPages).remove("error")
    }

    @Synchronized fun requestSync(id: String, retryFailed: Boolean): LocalCapture = update(id) { state ->
        if (retryFailed && state.optString("stage") == "failed") {
            if (state.optJSONObject("failed_job") != null) state.remove("recognition_job_id")
            val jobs = state.optJSONObject("check_jobs")
            state.optJSONArray("failed_checks")?.objects()?.forEach { failed -> jobs?.remove(failed.getString("case_id")) }
            state.remove("failed_job"); state.remove("failed_checks"); state.put("generation", state.optInt("generation") + 1)
            val uploaded = state.text("homework_id") != null && state.pageObjects().all(::isServerConfirmed)
            if (!state.optString("id").startsWith("remote_") && !uploaded) state.put("stage", "queued")
        }
        if (state.optString("stage") != "local") state.put("sync_requested", true)
        state.remove("error")
    }

    /** Recover a camera/import write interrupted after original bytes reached disk. */
    fun recoverInterruptedCaptures(server: String, actor: String, student: String) {
        val drafts = synchronized(this) {
            root.listFiles().orEmpty().filter { it.isDirectory && validId.matches(it.name) && File(it, "record.json").isFile }
                .mapNotNull { runCatching { read(it.name) }.getOrNull() }
                .filter { it.optString("server") == server && it.optString("actor_id") == actor && it.optString("student_id") == student && it.optString("stage") == "local" }
        }
        drafts.forEach { state ->
            val id = state.getString("id")
            state.pageObjects().filter { it.optString("status") == "capturing" }.forEach { page ->
                val pageId = page.getString("id")
                val original = page.text("original_file")?.let { file(id, it) }
                if (original == null || !original.isFile || original.length() == 0L) removePage(id, pageId)
                else try { completePage(id, pageId) }
                catch (_: Exception) { update(id) { page(it, pageId).put("status", "failed").put("error", "上次拍照中断，原文件已保留，请删除这页后重拍") } }
            }
        }
    }

    /** Persist exact key and payload before request; retain unknown outcomes. */
    fun postOnce(id: String, operation: String, api: BackendApi, path: String, proposed: JSONObject): JSONObject {
        val operationState = synchronized(this) {
            val state = read(id); val ops = state.getJSONObject("ops")
            prepareOperation(ops.optJSONObject(operation), path, proposed, UUID.randomUUID().toString()).also { ops.put(operation, it); write(id, state) }
        }
        operationState.optJSONObject("response")?.let { return it.copyJson() }
        require(operationState.getString("path") == path) { "待恢复操作与当前记录不一致，请联系老师" }
        try {
            val result = api.post(path, operationState.getJSONObject("body"), operationState.getString("key"))
            update(id) { it.getJSONObject("ops").getJSONObject(operation).put("response", result.copyJson()) }
            return result
        } catch (error: ApiFailure) {
            // A rejected mutation can be explicitly retried with fresh revisions.
            // Unknown outcomes (including network loss) retain the exact key/body.
            if (error.status in listOf(409, 422)) update(id) { it.getJSONObject("ops").remove(operation) }
            throw error
        }
    }

    @Synchronized fun importHomework(server: String, actor: String, student: String, homework: JSONObject): LocalCapture {
        val existing = listCaptures(server, actor, student).firstOrNull { it.state.text("homework_id") == homework.getString("id") && it.state.optJSONObject("correction") == null }
        if (existing != null) return update(existing.id) { it.put("homework", homework.copyJson()) }
        val id = "remote_" + UUID.randomUUID().toString()
        return write(id, JSONObject().put("schema_version", LEDGER_VERSION).put("id", id).put("server", server).put("actor_id", actor).put("student_id", student)
            .put("captured_at", runCatching { java.time.Instant.parse(homework.getString("created_at")).toEpochMilli() }.getOrDefault(System.currentTimeMillis()))
            .put("homework_id", homework.getString("id")).put("homework", homework.copyJson()).put("title", homework.text("title") ?: "学校作业")
            .put("stage", "remote").put("ops", JSONObject()).put("generation", 0).put("pages", JSONArray()))
    }

    private fun requireDraft(state: JSONObject) = require(state.optString("stage") == "local") { "这份作业已提交，不能再修改照片" }

    companion object {
        fun page(state: JSONObject, pageId: String): JSONObject =
            state.pageObjects().firstOrNull { it.getString("id") == pageId } ?: throw IllegalArgumentException("这一页已不存在")
    }
}

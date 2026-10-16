package cn.xueban.companion.data

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.util.AtomicFile
import androidx.exifinterface.media.ExifInterface
import java.io.File
import java.security.MessageDigest
import java.util.UUID
import org.json.JSONObject

data class LocalCapture(val id: String, val file: File, val capturedAt: Long, val state: JSONObject) {
    val title: String get() = state.optString("title", "我的作业")
    val stage: String get() = state.optString("stage", "local")
    val error: String? get() = state.text("error")
    val cases: List<JSONObject> get() = state.optJSONArray("cases")?.objects().orEmpty()
}

/** Atomic journal + private originals. Old unassigned development photos are not uploaded. */
class LocalCaptureRepository(context: Context) {
    private val root = File(context.noBackupFilesDir, "correction-v2").apply { mkdirs() }
    private val validId = Regex("[a-zA-Z0-9_-]{1,100}")
    private fun directory(id: String): File { require(validId.matches(id)); return File(root, id) }
    private fun journal(id: String) = AtomicFile(File(directory(id), "record.json"))
    @Synchronized fun read(id: String): JSONObject = JSONObject(String(journal(id).readFully(), Charsets.UTF_8))
    @Synchronized fun write(id: String, value: JSONObject): LocalCapture {
        check(directory(id).isDirectory || directory(id).mkdirs()) { "本机空间不足，无法保存记录" }
        val file = journal(id); val stream = file.startWrite()
        try { stream.write(value.toString().toByteArray(Charsets.UTF_8)); file.finishWrite(stream) }
        catch (error: Exception) { file.failWrite(stream); throw error }
        return capture(id, value)
    }
    @Synchronized fun update(id: String, action: (JSONObject) -> Unit): LocalCapture = read(id).let { action(it); write(id, it) }
    private fun capture(id: String, value: JSONObject) = LocalCapture(id, File(directory(id), "upload.jpg"), value.optLong("captured_at"), value)
    @Synchronized fun listCaptures(server: String, actor: String, student: String): List<LocalCapture> = root.listFiles().orEmpty()
        .filter { it.isDirectory && (File(it, "record.json").exists() || File(it, "record.json.bak").exists()) }
        .map { capture(it.name, read(it.name)) }
        .filter { it.state.optString("server") == server && it.state.optString("actor_id") == actor && it.state.optString("student_id") == student && it.stage != "capturing" }
        .sortedByDescending { it.capturedAt }

    fun beginCapture(server: String, actor: String, student: String, correction: JSONObject?): File {
        val id = UUID.randomUUID().toString()
        val state = JSONObject().put("id", id).put("server", server).put("actor_id", actor).put("student_id", student)
            .put("captured_at", System.currentTimeMillis()).put("stage", "capturing").put("ops", JSONObject()).put("generation", 0)
            .put("title", if (correction == null) "学校作业" else "新的订正")
        correction?.let { state.put("correction", it.copyJson()).put("homework_id", it.getString("homework_id")) }
        write(id, state)
        return File(directory(id), "original.jpg")
    }

    fun completeCapture(pending: File): LocalCapture {
        val id = pending.parentFile?.name ?: error("照片路径无效")
        require(pending.canonicalFile == File(directory(id), "original.jpg").canonicalFile)
        require(pending.isFile && pending.length() > 0) { "相机未保存照片" }
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(pending.path, bounds)
        require(bounds.outWidth > 0 && bounds.outHeight > 0) { "照片无法读取" }
        require(pending.length() <= 20_000_000 && bounds.outWidth.toLong() * bounds.outHeight <= 50_000_000) { "照片过大，请选择较小的单页照片" }
        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 4800) sample *= 2
        val bitmap = BitmapFactory.decodeFile(pending.path, BitmapFactory.Options().apply { inSampleSize = sample }) ?: error("照片无法读取")
        val exif = ExifInterface(pending)
        val matrix = Matrix().apply { if (exif.isFlipped) postScale(-1f, 1f); postRotate(exif.rotationDegrees.toFloat()) }
        val oriented = Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true)
        if (oriented !== bitmap) bitmap.recycle()
        val scale = minOf(1f, 2400f / maxOf(oriented.width, oriented.height))
        val upload = if (scale < 1f) Bitmap.createScaledBitmap(oriented, (oriented.width * scale).toInt().coerceAtLeast(1), (oriented.height * scale).toInt().coerceAtLeast(1), true) else oriented
        if (upload !== oriented) oriented.recycle()
        val target = AtomicFile(File(directory(id), "upload.jpg")); val output = target.startWrite()
        try { check(upload.compress(Bitmap.CompressFormat.JPEG, 92, output)); target.finishWrite(output) }
        catch (error: Exception) { target.failWrite(output); throw error }
        val width = upload.width; val height = upload.height; upload.recycle()
        val file = target.baseFile
        val sha = MessageDigest.getInstance("SHA-256").apply { file.inputStream().use { input -> val buffer = ByteArray(16384); while (true) { val count = input.read(buffer); if (count < 0) break; update(buffer, 0, count) } } }.digest().joinToString("") { "%02x".format(it) }
        return update(id) { it.put("width", width).put("height", height).put("byte_size", file.length()).put("sha256", sha).put("stage", "local") }
    }

    fun abandonCapture(pending: File) {
        val dir = pending.parentFile ?: return
        if (dir.parentFile?.canonicalFile == root.canonicalFile && read(dir.name).optString("stage") == "capturing") dir.deleteRecursively()
    }

    /** Recover a camera/import write interrupted after original bytes reached disk. */
    fun recoverInterruptedCaptures(server: String, actor: String, student: String) {
        root.listFiles().orEmpty().filter { it.isDirectory && File(it, "record.json").isFile }.forEach { dir ->
            val state = read(dir.name)
            if (state.optString("stage") == "capturing" && state.optString("server") == server && state.optString("actor_id") == actor && state.optString("student_id") == student) {
                val original = File(dir, "original.jpg")
                if (original.isFile && original.length() > 0) {
                    try { completeCapture(original) }
                    catch (_: Exception) { update(dir.name) { it.put("stage", "capture_failed").put("error", "上次拍照中断，原文件已保留，请重新拍照。") } }
                }
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

    fun importHomework(server: String, actor: String, student: String, homework: JSONObject): LocalCapture {
        val existing = listCaptures(server, actor, student).firstOrNull { it.state.text("homework_id") == homework.getString("id") && it.state.optJSONObject("correction") == null }
        if (existing != null) return update(existing.id) { it.put("homework", homework.copyJson()) }
        val id = "remote_" + UUID.randomUUID().toString()
        return write(id, JSONObject().put("id", id).put("server", server).put("actor_id", actor).put("student_id", student)
            .put("captured_at", runCatching { java.time.Instant.parse(homework.getString("created_at")).toEpochMilli() }.getOrDefault(System.currentTimeMillis()))
            .put("homework_id", homework.getString("id")).put("homework", homework.copyJson()).put("title", homework.text("title") ?: "学校作业")
            .put("stage", "remote").put("ops", JSONObject()).put("generation", 0))
    }
}

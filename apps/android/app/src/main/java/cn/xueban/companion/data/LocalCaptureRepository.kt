package cn.xueban.companion.data

import android.content.Context
import java.io.File
import java.net.URI
import java.util.UUID

/** Local, unassigned development images. These are never student homework records. */
data class LocalCapture(val id: String, val file: File, val capturedAt: Long)

class LocalCaptureRepository(context: Context) {
    private val root = File(context.noBackupFilesDir, "development-captures")
    private val completed = File(root, "completed")
    private val inflight = File(root, "inflight")
    private val preferences = context.getSharedPreferences("development-settings", Context.MODE_PRIVATE)
    private val validId = Regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

    fun beginCapture(): File {
        check(inflight.isDirectory || inflight.mkdirs()) { "无法创建私有采集目录" }
        return File(inflight, "${UUID.randomUUID()}.jpg")
    }

    fun completeCapture(pending: File): LocalCapture {
        require(pending.parentFile?.canonicalFile == inflight.canonicalFile)
        require(pending.isFile && pending.length() > 0) { "相机没有写入完整照片" }
        check(completed.isDirectory || completed.mkdirs()) { "无法创建私有草稿目录" }
        val target = File(completed, pending.name)
        check(!target.exists() && pending.renameTo(target)) { "照片保存失败，请检查可用空间" }
        return LocalCapture(target.nameWithoutExtension, target, target.lastModified())
    }

    fun abandonCapture(pending: File) {
        require(pending.parentFile?.canonicalFile == inflight.canonicalFile)
        pending.delete()
    }

    fun listCaptures(): List<LocalCapture> = completed.listFiles()
        .orEmpty()
        .filter { it.isFile && it.extension == "jpg" && validId.matches(it.nameWithoutExtension) && it.length() > 0 }
        .map { LocalCapture(it.nameWithoutExtension, it, it.lastModified()) }
        .sortedByDescending { it.capturedAt }

    fun deleteCapture(id: String) {
        require(validId.matches(id)) { "无效的本地记录" }
        val target = File(completed, "$id.jpg")
        check(!target.exists() || target.delete()) { "删除失败，请重试" }
    }

    fun backendAddress(): String = preferences.getString("backend-address", "").orEmpty()

    fun saveBackendAddress(value: String) {
        val trimmed = value.trim()
        if (trimmed.isNotEmpty()) {
            val uri = URI(trimmed)
            require(uri.scheme == "https" && !uri.host.isNullOrEmpty() &&
                uri.userInfo == null && uri.query == null && uri.fragment == null) {
                "请输入 HTTPS 服务地址，不含账号、查询参数或密钥"
            }
        }
        check(preferences.edit().putString("backend-address", trimmed).commit()) { "地址保存失败" }
    }
}

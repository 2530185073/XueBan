package cn.xueban.companion.data

import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject

class ApiFailure(val status: Int, val code: String, message: String) : IOException(message)

class BackendApi(server: String, private val token: String?, private val debug: Boolean) {
    val server = UrlPolicy.server(server, debug)
    private val client = sharedClient

    fun get(path: String): JSONObject = json("GET", path)
    fun post(path: String, body: JSONObject, key: String): JSONObject = json("POST", path, body, key)

    private fun json(method: String, path: String, body: JSONObject? = null, key: String? = null): JSONObject {
        require(path.startsWith("/v1/") && !path.contains("..")) { "请求地址无效" }
        val request = Request.Builder().url(server + path).header("Accept", "application/json")
        token?.let { request.header("Authorization", "Bearer $it") }
        key?.let { request.header("Idempotency-Key", it) }
        if (method == "POST") request.post(requireNotNull(body).toString().toRequestBody("application/json".toMediaType()))
        client.newCall(request.build()).execute().use { response ->
            if (!response.isSuccessful) throw failure(response.code, problemCode(response))
            val source = response.body?.source() ?: throw ApiFailure(0, "invalid_response", "服务器没有返回结果")
            if (source.request(2_000_001)) throw ApiFailure(0, "response_too_large", "返回结果过大，请联系老师")
            return try { JSONObject(source.readUtf8()) } catch (_: Exception) { throw ApiFailure(0, "invalid_response", "服务器返回格式无法读取") }
        }
    }

    /** Presigned transfer uses only ticket headers; never propagates the app's Bearer token. */
    fun upload(url: String, headers: JSONObject, file: File) {
        val request = Request.Builder().url(UrlPolicy.storage(url, server, debug))
            .put(file.asRequestBody("image/jpeg".toMediaType()))
        headers.keys().forEach { key ->
            require(key.lowercase() !in listOf("host", "cookie", "connection", "content-length")) { "上传票据格式无效" }
            request.header(key, headers.getString(key))
        }
        client.newCall(request.build()).execute().use { response ->
            if (!response.isSuccessful) throw ApiFailure(response.code, "upload_failed", "图片上传未完成，请重试；原图已保留")
        }
    }

    fun download(url: String, target: File) {
        val request = Request.Builder().url(UrlPolicy.storage(url, server, debug)).build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw ApiFailure(response.code, "image_unavailable", "原图暂时无法打开")
            val body = response.body ?: throw IOException("图片读取失败")
            val temporary = File(target.parentFile, target.name + ".part")
            try {
                body.byteStream().use { input -> temporary.outputStream().use { output ->
                    val buffer = ByteArray(16384); var total = 0L
                    while (true) { val n = input.read(buffer); if (n < 0) break; total += n; require(total <= 20_000_000) { "图片过大" }; output.write(buffer, 0, n) }
                } }
                check(temporary.renameTo(target)) { "图片缓存失败" }
            } finally { temporary.delete() }
        }
    }

    /** Only the machine-readable Problem code is read; server prose never reaches the screen. */
    private fun problemCode(response: Response): String? {
        val source = response.body?.source() ?: return null
        return try {
            if (source.request(65_537)) null
            else JSONObject(source.readUtf8()).optString("code").takeIf { Regex("[a-z][a-z0-9_]{1,63}").matches(it) }
        } catch (_: Exception) { null }
    }

    private fun failure(status: Int, code: String?): ApiFailure = ApiFailure(status, code ?: "http_$status", when (code) {
        "invalid_credentials" -> "账号或密码不正确，请再检查一下"
        "login_rate_limited", "rate_limited" -> "尝试次数较多，请稍后再试"
        "password_change_required" -> "请先设置自己的新密码，再打开作业"
        "authentication_required" -> "登录已过期，请重新登录"
        "idempotency_conflict" -> "上次提交的结果还未确认，请用相同内容重试"
        "image_too_large" -> "照片超过服务器上传上限（4 MB、2400 万像素），请裁掉多余部分或重拍"
        else -> when (status) {
            401 -> "登录已失效，请重新登录"
            403 -> "当前账号无权查看或处理这份作业"
            404 -> "这项服务或记录暂时不可用"
            409 -> "记录已更新，请刷新后重试"
            422 -> "这次提交无法处理，请联系老师"
            429 -> "正在处理的作业较多，请稍后重试"
            in 300..399 -> "服务器地址发生跳转，请联系老师检查配置"
            else -> "服务暂时不可用，请稍后重试"
        }
    })

    private companion object {
        val sharedClient: OkHttpClient = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
            .retryOnConnectionFailure(false).connectTimeout(12, TimeUnit.SECONDS).readTimeout(45, TimeUnit.SECONDS)
            .writeTimeout(45, TimeUnit.SECONDS).callTimeout(60, TimeUnit.SECONDS).build()
    }
}

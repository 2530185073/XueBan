package cn.xueban.companion.data

import java.io.File
import java.security.MessageDigest
import java.time.Instant
import java.util.concurrent.CopyOnWriteArrayList
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.json.JSONArray
import org.json.JSONObject

class MemorySessionStore(initial: JSONObject? = null) : SessionStore {
    var value: JSONObject? = initial?.copyJson()
    override fun load(): JSONObject? = value?.copyJson()
    override fun save(value: JSONObject) { this.value = value.copyJson() }
    override fun clear() { value = null }
}

fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

/** Copies the original as the derived upload and records the requested edit, standing in for Bitmap work. */
class CopyRenderer : PageRenderer {
    val edits = mutableListOf<PageEdit>()
    override fun render(original: File, target: File, edit: PageEdit): RenderedPage {
        require(original.isFile && original.length() > 0) { "相机未保存照片" }
        edits += edit
        val bytes = original.readBytes() + "|${edit.rotation}|${edit.crop}".toByteArray()
        target.writeBytes(bytes)
        return RenderedPage(1200 + edit.rotation, 1600, bytes.size.toLong(), sha256(bytes), QualityReport(128.0, 500.0, 0.0))
    }
}

fun canonical(value: Any?): String = when (value) {
    is JSONObject -> value.keys().asSequence().sorted().joinToString(",", "{", "}") { "\"$it\":" + canonical(value.get(it)) }
    is JSONArray -> (0 until value.length()).joinToString(",", "[", "]") { canonical(value.get(it)) }
    is String -> JSONObject.quote(value)
    else -> value.toString()
}

fun tempDir(): File = File.createTempFile("xueban-test", "").apply { delete(); mkdirs(); deleteOnExit() }

/** Minimal stateful stand-in for the phase-one API: revisions, idempotent replays, tickets and jobs. */
class FakeBackend : Dispatcher() {
    data class Call(val method: String, val path: String, val key: String?, val auth: String?, val body: String) {
        val json: JSONObject get() = JSONObject(body)
    }
    lateinit var base: String
    val calls = CopyOnWriteArrayList<Call>()
    var password = "synthetic-initial-password"
    var mustChange = false
    var studentId: String? = "student_a"
    var tokenCounter = 0
    val validTokens = mutableSetOf("token-a")
    var homeworkRevision = 1
    val assets = linkedMapOf<String, JSONObject>()
    val stored = mutableMapOf<String, ByteArray>()
    var recognitionAssets: List<String>? = null
    private val replays = mutableMapOf<String, String>()
    private val failures = mutableMapOf<String, Pair<Int, String>>()
    private val drops = mutableSetOf<String>()

    fun failNext(path: String, status: Int, code: String) { failures[path] = status to code }
    fun dropResponseOnce(path: String) { drops += path }
    fun posts(path: String) = calls.filter { it.method == "POST" && it.path == path }

    fun start(): MockWebServer = MockWebServer().also { it.dispatcher = this; it.start(); base = "http://127.0.0.1:${it.port}" }

    @Synchronized override fun dispatch(request: RecordedRequest): MockResponse {
        val path = requireNotNull(request.path); val method = requireNotNull(request.method)
        val raw = request.body.readByteArray(); val body = String(raw, Charsets.UTF_8)
        val key = request.getHeader("Idempotency-Key"); val auth = request.getHeader("Authorization")
        calls += Call(method, path, key, auth, body)
        failures.remove(path)?.let { (status, code) -> return problem(status, code) }
        val replayKey = "$path|$key"
        if (method == "POST" && key != null) replays[replayKey]?.let { return respond(path, it) }
        val public = path == "/v1/auth/login" || path.contains("/content")
        if (!public && auth?.removePrefix("Bearer ") !in validTokens) return problem(401, "authentication_required")
        if (mustChange && path.startsWith("/v1/") && !path.startsWith("/v1/auth/")) return problem(403, "password_change_required")
        val result: Any = route(method, path, body, raw) ?: return problem(404, "not_found")
        if (result is MockResponse) return result
        val text = result.toString()
        if (method == "POST" && key != null) replays[replayKey] = text
        return respond(path, text)
    }

    private fun respond(path: String, text: String): MockResponse =
        if (drops.remove(path)) MockResponse().setBody(text).setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST) else MockResponse().setBody(text)

    private fun problem(status: Int, code: String) = MockResponse().setResponseCode(status).setBody(JSONObject().put("code", code).put("detail", "server prose").toString())

    private fun session(): JSONObject {
        val token = "token-${++tokenCounter + 1}"
        validTokens += token
        return JSONObject().put("access_token", token).put("expires_at", Instant.now().plusSeconds(3600).toString()).put("must_change_password", mustChange)
    }

    fun homework(): JSONObject = JSONObject().put("id", "homework_a").put("revision", homeworkRevision).put("asset_ids", JSONArray(assets.keys.toList()))
        .put("case_ids", JSONArray()).put("job_ids", JSONArray(listOfNotNull("job_recognize".takeIf { recognitionAssets != null })))

    private fun route(method: String, path: String, body: String, raw: ByteArray): Any? {
        val input = if (body.isNotEmpty() && body.startsWith("{")) JSONObject(body) else JSONObject()
        fun expect(revision: Int): MockResponse? = if (input.optInt("expected_revision") != revision) problem(409, "stale_input") else null
        val assetMatch = Regex("/v1/assets/(asset_\\d+)(/content|/complete)?").matchEntire(path)
        return when {
            method == "POST" && path == "/v1/auth/login" ->
                if (input.optString("password") == password && input.optString("username") == "student.one") session() else problem(401, "invalid_credentials")
            method == "GET" && path == "/v1/auth/account" -> JSONObject().put("username", "student.one").put("must_change_password", mustChange).put("student_id", studentId ?: JSONObject.NULL)
            method == "POST" && path == "/v1/auth/change-password" -> {
                if (input.optString("current_password") != password) problem(401, "invalid_credentials")
                else { password = input.getString("new_password"); mustChange = false; validTokens.clear(); session() }
            }
            method == "POST" && path == "/v1/auth/logout" -> JSONObject().put("logged_out", true)
            method == "GET" && path == "/v1/me" -> JSONObject().put("actor_id", "actor_a").put("roles", JSONArray().put("student"))
                .put("authorized_students", JSONArray().put(JSONObject().put("student_id", "student_a").put("display_name", "合成学生").put("campus_id", "campus_a")))
            method == "POST" && path == "/v1/students/student_a/homeworks" -> homework()
            method == "GET" && path == "/v1/homeworks/homework_a" -> homework()
            method == "POST" && path == "/v1/homeworks/homework_a/uploads" -> expect(homeworkRevision) ?: run {
                val id = "asset_${assets.size + 1}"
                val asset = JSONObject().put("id", id).put("revision", 1).put("homework_id", "homework_a").put("status", "awaiting_upload")
                    .put("page_index", input.getInt("page_index")).put("sha256", input.getString("sha256")).put("byte_size", input.getLong("byte_size"))
                    .put("width", input.getInt("width")).put("height", input.getInt("height"))
                assets[id] = asset; homeworkRevision++
                JSONObject().put("asset", asset).put("put_url", "$base/v1/assets/$id/content").put("expires_at", Instant.now().plusSeconds(600).toString())
                    .put("required_headers", JSONObject().put("X-Upload-Token", "ticket-$id"))
            }
            assetMatch != null && method == "GET" && assetMatch.groupValues[2].isEmpty() -> assets[assetMatch.groupValues[1]]
            assetMatch != null && method == "PUT" && assetMatch.groupValues[2] == "/content" -> {
                stored[assetMatch.groupValues[1]] = raw; MockResponse().setResponseCode(204)
            }
            assetMatch != null && method == "POST" && assetMatch.groupValues[2] == "/complete" -> {
                val asset = assets.getValue(assetMatch.groupValues[1])
                expect(asset.getInt("revision")) ?: if (input.getString("sha256") != asset.getString("sha256")) problem(422, "asset_integrity_mismatch") else {
                    asset.put("status", "uploaded").put("revision", asset.getInt("revision") + 1); homeworkRevision++; asset
                }
            }
            method == "POST" && path == "/v1/homeworks/homework_a/recognitions" -> expect(homeworkRevision) ?: run {
                recognitionAssets = input.getJSONArray("asset_ids").let { ids -> (0 until ids.length()).map(ids::getString) }
                homeworkRevision++
                JSONObject().put("job", JSONObject().put("id", "job_recognize").put("status", "queued"))
            }
            method == "GET" && path == "/v1/jobs/job_recognize" -> JSONObject().put("id", "job_recognize").put("kind", "recognize_homework").put("status", "succeeded")
            else -> null
        }
    }
}

fun signedInStore(server: String, token: String = "token-a"): MemorySessionStore = MemorySessionStore(
    JSONObject().put("format", SESSION_FORMAT).put("kind", "account").put("server", server).put("username", "student.one")
        .put("access_token", token).put("expires_at", Instant.now().plusSeconds(3600).toString()).put("must_change_password", false)
        .put("actor_id", "actor_a").put("student_id", "student_a").put("student_name", "合成学生")
)

/** A submitted draft with the given page payloads, in that order. */
fun submittedRecord(repository: LocalCaptureRepository, server: String, pages: List<String>): String {
    val id = repository.createDraft(server, "actor_a", "student_a", null)
    pages.forEach { content ->
        val (pageId, original) = repository.beginPage(id, "camera")
        original.writeText(content)
        repository.completePage(id, pageId)
    }
    repository.submit(id, pages.size)
    return id
}

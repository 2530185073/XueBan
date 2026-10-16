package cn.xueban.companion.data

import java.time.Instant
import java.util.UUID
import org.json.JSONObject

interface SessionStore {
    fun load(): JSONObject?
    fun save(value: JSONObject)
    fun clear()
}

data class Account(val server: String, val username: String, val actorId: String, val studentId: String, val studentName: String)
data class ActiveSession(val server: String, val token: String, val actorId: String, val studentId: String)

sealed interface AuthResult {
    data class Ready(val account: Account) : AuthResult
    data class PasswordChangeRequired(val username: String) : AuthResult
}

/**
 * One student = one username/password account shared with the web app. Login and password-change
 * keys are saved before sending; passwords never are. The backend only compares a replayed body
 * after verifying the password, so re-entering the same credentials replays the original session.
 */
class AccountAuth(private val store: SessionStore, private val debug: Boolean, private val newKey: () -> String = { UUID.randomUUID().toString() }) {
    private val definite = listOf(400, 401, 403, 409, 422, 429)

    @Synchronized fun saved(): JSONObject? {
        val raw = try { store.load() } catch (_: Exception) { store.clear(); null }
        val migrated = migrateSession(raw)
        if (migrated == null) { if (raw != null) store.clear(); return null }
        if (migrated !== raw) store.save(migrated)
        return migrated
    }

    fun hasToken(): Boolean = saved()?.let { tokenUsable(it) } == true

    fun cachedAccount(): Account? {
        val saved = saved()?.takeIf { tokenUsable(it) && !it.optBoolean("must_change_password") } ?: return null
        return account(saved)
    }

    fun activeSession(): ActiveSession? {
        val saved = saved()?.takeIf { tokenUsable(it) && !it.optBoolean("must_change_password") } ?: return null
        val actor = saved.text("actor_id") ?: return null
        val student = saved.text("student_id") ?: return null
        return ActiveSession(saved.getString("server"), saved.getString("access_token"), actor, student)
    }

    fun login(address: String, username: String, password: String): AuthResult {
        val base = UrlPolicy.server(address, debug)
        val name = normalizeUsername(username)
        require(password.isNotEmpty() && password.codePointCount(0, password.length) <= 128 && password.toByteArray(Charsets.UTF_8).size <= 512) { "请填写密码" }
        val saved = synchronized(this) {
            val current = saved() ?: JSONObject().put("format", SESSION_FORMAT).put("kind", "account")
            val pending = current.optJSONObject("pending_login")?.takeIf { it.optString("server") == base && it.optString("username") == name }
                ?: JSONObject().put("server", base).put("username", name).put("key", newKey())
            signedOut(current).put("server", base).put("username", name).put("pending_login", pending).also { store.save(it) }
        }
        val pending = saved.getJSONObject("pending_login")
        val result = try {
            BackendApi(base, null, debug).post("/v1/auth/login", JSONObject().put("username", name).put("password", password), pending.getString("key"))
        } catch (error: ApiFailure) {
            if (error.status in definite) update { it.remove("pending_login") }
            throw error
        }
        acceptSession(result) { it.remove("pending_login") }
        return verify()
    }

    fun verify(): AuthResult {
        val saved = saved()?.takeIf(::tokenUsable) ?: throw ApiFailure(401, "session_missing", "请先登录")
        val api = BackendApi(saved.getString("server"), saved.getString("access_token"), debug)
        val account = api.get("/v1/auth/account")
        val username = account.text("username") ?: throw ApiFailure(0, "invalid_response", "服务器返回格式无法读取")
        if (account.optBoolean("must_change_password")) {
            update { it.put("username", username).put("must_change_password", true).remove("identity") }
            return AuthResult.PasswordChangeRequired(username)
        }
        val studentId = account.text("student_id") ?: throw ApiFailure(403, "not_student_account", "学伴平板仅供学生本人账号登录，请使用老师发放的学生账号")
        val me = api.get("/v1/me")
        val student = me.getJSONArray("authorized_students").objects().firstOrNull { it.optString("student_id") == studentId }
            ?: throw ApiFailure(403, "access_denied", "该账号尚未授权学生，请联系老师")
        val next = update {
            it.put("username", username).put("must_change_password", false).put("actor_id", me.getString("actor_id"))
                .put("student_id", studentId).put("student_name", student.optString("display_name", username)).put("identity", me.copyJson())
        }
        return AuthResult.Ready(requireNotNull(account(next)))
    }

    fun changePassword(current: String, new: String): AuthResult {
        require(current.isNotEmpty() && current.toByteArray(Charsets.UTF_8).size <= 512) { "请填写当前密码" }
        require(new.codePointCount(0, new.length) in 15..128 && new.toByteArray(Charsets.UTF_8).size <= 512) { "新密码需要 15–128 个字符，可以用一句好记的话" }
        require(new != current) { "新密码必须与当前密码不同" }
        val saved = synchronized(this) {
            val value = saved()?.takeIf(::tokenUsable) ?: throw ApiFailure(401, "session_missing", "请先登录")
            if (value.text("pending_change_key") == null) { value.put("pending_change_key", newKey()); store.save(value) }
            value
        }
        val result = try {
            BackendApi(saved.getString("server"), saved.getString("access_token"), debug)
                .post("/v1/auth/change-password", JSONObject().put("current_password", current).put("new_password", new), saved.getString("pending_change_key"))
        } catch (error: ApiFailure) {
            if (error.status in definite) update { it.remove("pending_change_key") }
            if (error.status == 401) throw ApiFailure(422, "current_password_rejected", "当前密码不正确；如果刚才已改成功，请退出后用新密码登录")
            if (error.code == "idempotency_conflict") throw ApiFailure(409, error.code, "上次修改密码的结果未确认：请退出后先用新密码登录，不行再用原密码登录")
            throw error
        }
        acceptSession(result) { it.remove("pending_change_key") }
        return verify()
    }

    /** Returns whether the server confirmed revocation; local credentials are wiped either way. */
    fun logout(): Boolean {
        val saved = saved()?.takeIf(::tokenUsable)
        return try {
            if (saved == null) true
            else { BackendApi(saved.getString("server"), saved.getString("access_token"), debug).post("/v1/auth/logout", JSONObject(), newKey()); true }
        } catch (error: ApiFailure) { error.status == 401 }
        catch (_: Exception) { false }
        finally { expire() }
    }

    /** Ends the local session but keeps server/username for the form; local homework stays on disk. */
    @Synchronized fun expire() { saved()?.let { store.save(signedOut(it)) } }

    @Synchronized fun markPasswordChangeRequired() { saved()?.let { store.save(it.put("must_change_password", true)) } }

    private fun acceptSession(result: JSONObject, extra: (JSONObject) -> Unit) {
        val token = result.text("access_token") ?: throw ApiFailure(0, "invalid_response", "服务器返回格式无法读取")
        val expires = result.text("expires_at") ?: throw ApiFailure(0, "invalid_response", "服务器返回格式无法读取")
        if (!runCatching { Instant.parse(expires).isAfter(Instant.now()) }.getOrDefault(false)) throw ApiFailure(401, "expired", "登录已过期，请重新登录")
        update { extra(it); it.put("access_token", token).put("expires_at", expires).put("must_change_password", result.optBoolean("must_change_password")) }
    }

    @Synchronized private fun update(action: (JSONObject) -> Unit): JSONObject {
        val value = saved() ?: throw ApiFailure(401, "session_missing", "请先登录")
        action(value); store.save(value); return value
    }

    private fun signedOut(value: JSONObject): JSONObject {
        val next = JSONObject().put("format", SESSION_FORMAT).put("kind", "account")
        listOf("server", "username", "pending_login").forEach { key -> if (value.has(key)) next.put(key, value.get(key)) }
        return next
    }

    private fun tokenUsable(value: JSONObject): Boolean {
        val expiry = value.text("expires_at") ?: return false
        return value.text("access_token") != null && runCatching { Instant.parse(expiry).isAfter(Instant.now()) }.getOrDefault(false)
    }

    private fun account(value: JSONObject): Account? {
        val fields = listOf("server", "username", "actor_id", "student_id", "student_name").map { value.text(it) ?: return null }
        return Account(fields[0], fields[1], fields[2], fields[3], fields[4])
    }
}

fun normalizeUsername(value: String): String {
    val name = value.trim().lowercase()
    require(Regex("[a-z0-9][a-z0-9._-]{2,63}").matches(name)) { "请填写老师发放的账号（3–64 位英文字母、数字、点、下划线或连字符）" }
    return name
}

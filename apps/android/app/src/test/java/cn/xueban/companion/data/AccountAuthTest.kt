package cn.xueban.companion.data

import java.io.IOException
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class AccountAuthTest {
    private val backend = FakeBackend()
    private lateinit var server: MockWebServer
    private val store = MemorySessionStore()
    private var keys = 0
    private val auth by lazy { AccountAuth(store, debug = true) { "synthetic-key-${++keys}" } }

    @Before fun start() { server = backend.start() }
    @After fun stop() { server.shutdown() }

    @Test fun loginReusesWebAccountAndSelectsItsOwnStudent() {
        val result = auth.login(backend.base + "/", "  Student.One ", backend.password)
        val account = (result as AuthResult.Ready).account
        assertEquals("student_a", account.studentId)
        assertEquals("actor_a", account.actorId)
        val login = backend.posts("/v1/auth/login").single()
        assertNull(login.auth)
        assertTrue(Regex("[A-Za-z0-9._:-]{8,128}").matches(requireNotNull(login.key)))
        assertEquals("student.one", login.json.getString("username"))
        assertEquals(listOf("/v1/auth/login", "/v1/auth/account", "/v1/me"), backend.calls.map { it.path })
        assertTrue(backend.calls.drop(1).all { it.auth == "Bearer token-2" })
        val saved = requireNotNull(store.value)
        assertFalse(saved.has("pending_login"))
        assertFalse(saved.toString().contains(backend.password))
        assertEquals(ActiveSession(backend.base, "token-2", "actor_a", "student_a"), auth.activeSession())
    }

    @Test fun lostLoginResponseIsRetriedWithTheSameKeyAndBodyWithoutStoringThePassword() {
        backend.dropResponseOnce("/v1/auth/login")
        assertThrows(IOException::class.java) { auth.login(backend.base, "student.one", backend.password) }
        val pending = requireNotNull(store.value).getJSONObject("pending_login")
        assertEquals("synthetic-key-1", pending.getString("key"))
        assertFalse(store.value.toString().contains(backend.password))
        assertNull(auth.activeSession())

        auth.login(backend.base, "student.one", backend.password)
        val attempts = backend.posts("/v1/auth/login")
        assertEquals(2, attempts.size)
        assertEquals(attempts[0].key, attempts[1].key)
        assertEquals(attempts[0].body, attempts[1].body)
        assertEquals(1, backend.tokenCounter)
        assertFalse(requireNotNull(store.value).has("pending_login"))
    }

    @Test fun changingAccountOrServerStartsANewLoginKey() {
        backend.dropResponseOnce("/v1/auth/login")
        assertThrows(IOException::class.java) { auth.login(backend.base, "student.one", backend.password) }
        val error = assertThrows(ApiFailure::class.java) { auth.login(backend.base, "student.two", "whatever") }
        assertEquals("invalid_credentials", error.code)
        val keys = backend.posts("/v1/auth/login").map { it.key }
        assertNotEquals(keys[0], keys[1])
        assertFalse(requireNotNull(store.value).has("pending_login"))
        assertEquals("student.two", store.value?.getString("username"))
    }

    @Test fun forcedPasswordChangeBlocksTheAppUntilTheNewSessionIsVerified() {
        backend.mustChange = true
        val first = auth.login(backend.base, "student.one", backend.password)
        assertEquals(AuthResult.PasswordChangeRequired("student.one"), first)
        assertNull(auth.activeSession())
        assertEquals(listOf("/v1/auth/login", "/v1/auth/account"), backend.calls.map { it.path })

        val fresh = "a long synthetic sentence password"
        assertThrows(IllegalArgumentException::class.java) { auth.changePassword(backend.password, "too short") }
        val result = auth.changePassword(backend.password, fresh) as AuthResult.Ready
        assertEquals("student_a", result.account.studentId)
        val change = backend.posts("/v1/auth/change-password").single()
        assertEquals("Bearer token-2", change.auth)
        assertEquals(fresh, change.json.getString("new_password"))
        val saved = requireNotNull(store.value)
        assertEquals("token-3", saved.getString("access_token"))
        assertFalse(saved.optBoolean("must_change_password"))
        assertFalse(saved.has("pending_change_key"))
        assertFalse(saved.toString().contains(fresh))
    }

    @Test fun unknownPasswordChangeOutcomeReplaysTheSameKey() {
        backend.mustChange = true
        auth.login(backend.base, "student.one", backend.password)
        val initial = backend.password
        backend.dropResponseOnce("/v1/auth/change-password")
        assertThrows(IOException::class.java) { auth.changePassword(initial, "a long synthetic sentence password") }
        auth.changePassword(initial, "a long synthetic sentence password")
        val attempts = backend.posts("/v1/auth/change-password")
        assertEquals(attempts[0].key, attempts[1].key)
        assertEquals("token-2", attempts[1].auth?.removePrefix("Bearer "))
        assertNotNull(auth.activeSession())
    }

    @Test fun wrongCurrentPasswordDoesNotSignOut() {
        backend.mustChange = true
        auth.login(backend.base, "student.one", backend.password)
        val error = assertThrows(ApiFailure::class.java) { auth.changePassword("not the password", "a long synthetic sentence password") }
        assertEquals(FailureKind.Other, classifyFailure(error))
        assertTrue(auth.hasToken())
        assertFalse(requireNotNull(store.value).has("pending_change_key"))
    }

    @Test fun logoutRevokesOnServerAndWipesOnlyLocalCredentials() {
        auth.login(backend.base, "student.one", backend.password)
        assertTrue(auth.logout())
        val logout = backend.posts("/v1/auth/logout").single()
        assertEquals("Bearer token-2", logout.auth)
        assertNotNull(logout.key)
        assertEquals("{}", logout.body)
        val saved = requireNotNull(store.value)
        assertEquals(setOf("format", "kind", "server", "username"), saved.keys().asSequence().toSet())
        assertNull(auth.activeSession())
    }

    @Test fun unconfirmedLogoutStillWipesLocalCredentials() {
        auth.login(backend.base, "student.one", backend.password)
        backend.dropResponseOnce("/v1/auth/logout")
        assertFalse(auth.logout())
        assertFalse(auth.hasToken())
    }

    @Test fun expiredSessionKeepsServerAndUsernameForTheLoginForm() {
        auth.login(backend.base, "student.one", backend.password)
        auth.expire()
        val saved = requireNotNull(auth.saved())
        assertEquals(backend.base, saved.getString("server"))
        assertEquals("student.one", saved.getString("username"))
        assertFalse(saved.has("access_token"))
        assertNull(auth.cachedAccount())
        assertEquals("session_missing", assertThrows(ApiFailure::class.java) { auth.verify() }.code)
    }

    @Test fun oldPairingSessionIsNotAnAccountPath() {
        store.save(JSONObject().put("server", "https://homework.example.com").put("pairing_code", "PAIR-123456").put("pairing_key", "pair-key")
            .put("access_token", "paired-token").put("expires_at", "2099-01-01T00:00:00Z").put("student_id", "student_a"))
        val saved = requireNotNull(auth.saved())
        assertEquals("https://homework.example.com", saved.getString("server"))
        assertFalse(store.value.toString().contains("paired-token"))
        assertFalse(store.value.toString().contains("PAIR-123456"))
        assertFalse(auth.hasToken())
    }

    @Test fun staffAccountsAreRejectedOnTheStudentTablet() {
        backend.studentId = null
        val error = assertThrows(ApiFailure::class.java) { auth.login(backend.base, "student.one", backend.password) }
        assertEquals("not_student_account", error.code)
        assertEquals(FailureKind.AccessDenied, classifyFailure(error))
        assertNull(auth.activeSession())
    }

    @Test fun revokedSessionIsReportedAsExpired() {
        val local = AccountAuth(signedInStore(backend.base, token = "revoked-token"), true)
        val error = assertThrows(ApiFailure::class.java) { local.verify() }
        assertEquals("authentication_required", error.code)
        assertEquals(FailureKind.SessionExpired, classifyFailure(error))
        assertFalse(error.message.orEmpty().contains("server prose"))
    }

    @Test fun usernamesFollowTheBackendRule() {
        assertEquals("student.one", normalizeUsername(" Student.One "))
        listOf("ab", ".student", "student one", "学生").forEach { assertThrows(IllegalArgumentException::class.java) { normalizeUsername(it) } }
    }
}

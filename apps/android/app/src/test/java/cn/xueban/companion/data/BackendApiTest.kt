package cn.xueban.companion.data

import java.io.File
import java.util.concurrent.TimeUnit
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class BackendApiTest {
    private fun withServer(test: (MockWebServer, String) -> Unit) {
        val server = MockWebServer(); server.start()
        try { test(server, "http://127.0.0.1:${server.port}") } finally { server.shutdown() }
    }
    @Test fun backendTokenAndIdempotencyHeaderReachOnlyBusinessRequest() = withServer { server, base ->
        server.enqueue(MockResponse().setBody("{\"id\":\"homework_a\"}"))
        BackendApi(base, "synthetic-app-token", true).post("/v1/students/student_a/homeworks", JSONObject().put("title", "合成作业"), "synthetic-operation-key")
        val sent = server.takeRequest(2, TimeUnit.SECONDS)!!
        assertEquals("Bearer synthetic-app-token", sent.getHeader("Authorization"))
        assertEquals("synthetic-operation-key", sent.getHeader("Idempotency-Key"))
        assertEquals("合成作业", JSONObject(sent.body.readUtf8()).getString("title"))
    }
    @Test fun privateUploadUsesTicketHeadersWithoutBusinessBearer() = withServer { server, base ->
        server.enqueue(MockResponse().setResponseCode(204))
        val file = File.createTempFile("synthetic", ".jpg")
        try {
            file.writeBytes(byteArrayOf(1, 2, 3))
            BackendApi(base, "synthetic-app-token", true).upload("$base/private/upload", JSONObject().put("X-Upload-Ticket", "synthetic-ticket"), file)
            val sent = server.takeRequest(2, TimeUnit.SECONDS)!!
            assertEquals("PUT", sent.method)
            assertNull(sent.getHeader("Authorization"))
            assertEquals("synthetic-ticket", sent.getHeader("X-Upload-Ticket"))
        } finally { file.delete() }
    }
    @Test fun redirectsAreNotFollowedAndFailuresDoNotBecomeSuccess() = withServer { server, base ->
        val api = BackendApi(base, "synthetic-app-token", true)
        listOf(302, 401, 403, 409, 503).forEach { status ->
            server.enqueue(MockResponse().setResponseCode(status).addHeader("Location", "$base/v1/other").setBody("secret upstream body must not appear"))
            val error = assertThrows(ApiFailure::class.java) { api.get("/v1/me") }
            assertEquals(status, error.status)
            assertFalse(error.message.orEmpty().contains("secret upstream"))
        }
        assertEquals(5, server.requestCount)
    }
    @Test fun problemCodeIsReadButServerProseIsNot() = withServer { server, base ->
        val api = BackendApi(base, "synthetic-app-token", true)
        server.enqueue(MockResponse().setResponseCode(403).setBody("{\"code\":\"password_change_required\",\"detail\":\"secret upstream detail\"}"))
        val change = assertThrows(ApiFailure::class.java) { api.get("/v1/me") }
        assertEquals("password_change_required", change.code)
        assertFalse(change.message.orEmpty().contains("secret upstream"))
        server.enqueue(MockResponse().setResponseCode(429).setBody("{\"code\":\"login_rate_limited\"}"))
        assertEquals("login_rate_limited", assertThrows(ApiFailure::class.java) { api.post("/v1/auth/login", JSONObject(), "synthetic-key") }.code)
        server.enqueue(MockResponse().setResponseCode(500).setBody("{\"code\":\"Not A Code<script>\"}"))
        assertEquals("http_500", assertThrows(ApiFailure::class.java) { api.get("/v1/me") }.code)
    }
}

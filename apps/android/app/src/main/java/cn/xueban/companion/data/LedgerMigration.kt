package cn.xueban.companion.data

import org.json.JSONArray
import org.json.JSONObject

const val LEDGER_VERSION = 2
const val MIGRATED_PAGE_ID = "page_0"
private val pageFields = listOf("width", "height", "byte_size", "sha256", "asset_id", "asset", "upload_ticket", "ticket_generation")

/**
 * Version 1 (0.2.0) kept one photo per record at top level. Version 2 keeps an ordered page list.
 * Operation names are rewritten per page while each saved key, path, body and response is kept
 * byte-for-byte, so an unconfirmed write is replayed with the same idempotency key after an update.
 */
fun migrateLedger(record: JSONObject): JSONObject {
    val version = record.optInt("schema_version", 1)
    require(version <= LEDGER_VERSION) { "本机记录来自更新版本的 App，请升级后查看" }
    if (version == LEDGER_VERSION) return record
    val next = record.copyJson()
    val stage = next.optString("stage", "local")
    val page = JSONObject().put("id", MIGRATED_PAGE_ID).put("original_file", "original.jpg").put("upload_file", "upload.jpg")
        .put("rotation", 0).put("crop", JSONObject.NULL)
        .put("status", when (stage) { "capturing" -> "capturing"; "capture_failed" -> "failed"; else -> "ready" })
    pageFields.forEach { key -> if (next.has(key)) page.put(key, next.get(key)); next.remove(key) }
    if (next.optString("id").startsWith("remote_")) page.remove("original_file")
    val hasPage = page.has("sha256") || page.has("asset_id") || stage in listOf("capturing", "capture_failed")
    val ops = next.optJSONObject("ops") ?: JSONObject()
    val renamed = JSONObject()
    ops.keys().forEach { name ->
        val target = when {
            name == "upload" -> "upload-$MIGRATED_PAGE_ID"
            name == "complete" -> "complete-$MIGRATED_PAGE_ID"
            name.startsWith("renew-") -> "renew-$MIGRATED_PAGE_ID-" + name.removePrefix("renew-")
            else -> name
        }
        renamed.put(target, ops.get(name))
    }
    if (stage == "capture_failed") page.put("error", next.optString("error", "上次拍照中断，原文件已保留，请删除这页后重拍"))
    return next.put("ops", renamed).put("pages", if (hasPage) JSONArray().put(page) else JSONArray())
        .put("stage", if (stage in listOf("capturing", "capture_failed")) "local" else stage)
        .put("schema_version", LEDGER_VERSION)
}

const val SESSION_FORMAT = 2

/** 0.2.0 pairing sessions are not an account path: only the server address is kept for the login form. */
fun migrateSession(saved: JSONObject?): JSONObject? {
    if (saved == null) return null
    if (saved.optInt("format") == SESSION_FORMAT && saved.optString("kind") == "account") return saved
    val server = saved.text("server") ?: return null
    return JSONObject().put("format", SESSION_FORMAT).put("kind", "account").put("server", server)
}

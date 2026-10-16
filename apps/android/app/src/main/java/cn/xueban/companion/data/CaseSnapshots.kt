package cn.xueban.companion.data

import org.json.JSONArray
import org.json.JSONObject

/** Copy only the current case view; original photos, operations and attempt evidence stay intact. */
fun mergeCaseSnapshot(target: JSONObject, source: JSONObject, incoming: JSONObject, insertForSource: Boolean = false): JSONObject? {
    val partition = listOf("server", "actor_id", "student_id", "homework_id")
    if (partition.any { source.text(it) == null || source.text(it) != target.text(it) }) return null
    val nextCase = incoming.getJSONObject("case")
    if (nextCase.text("homework_id") != target.text("homework_id")) return null
    val nextScope = nextCase.getJSONObject("scope")
    if (nextScope.text("student_id") != target.text("student_id")) return null
    fun sameScope(scope: JSONObject) = listOf("tenant_id", "campus_id", "student_id").all { scope.text(it) != null && scope.text(it) == nextScope.text(it) }
    target.optJSONObject("homework")?.optJSONObject("scope")?.let { if (!sameScope(it)) return null }
    val rows = target.optJSONArray("cases")?.objects().orEmpty().toMutableList()
    val index = rows.indexOfFirst { it.getJSONObject("case").getString("id") == nextCase.getString("id") }
    if (index < 0) {
        if (!insertForSource || target.text("id") != source.text("id")) return null
        rows.add(incoming.copyJson())
    } else {
        val previous = rows[index].getJSONObject("case")
        if (!sameScope(previous.getJSONObject("scope")) || previous.getLong("revision") >= nextCase.getLong("revision")) return null
        rows[index] = incoming.copyJson()
    }
    return target.copyJson().put("cases", JSONArray(rows))
}

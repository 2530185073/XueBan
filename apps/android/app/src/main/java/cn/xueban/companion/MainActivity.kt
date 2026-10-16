package cn.xueban.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.*
import cn.xueban.companion.data.*
import cn.xueban.companion.ui.CameraScreen
import cn.xueban.companion.ui.LocalPhoto
import java.text.DateFormat
import java.util.Date
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MaterialTheme(colorScheme = lightColorScheme(primary = Color(0xFF175D4E), background = Color(0xFFF8F8F1), surface = Color(0xFFFFFEF8), secondaryContainer = Color(0xFFE7EDE4))) {
                CompanionApp()
            }
        }
    }
}

@Composable
private fun CompanionApp(model: CaptureViewModel = viewModel()) {
    val navigation = rememberNavController()
    val lifecycle = LocalLifecycleOwner.current
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_START) model.setForeground(true)
            if (event == Lifecycle.Event.ON_STOP) model.setForeground(false)
        }
        lifecycle.lifecycle.addObserver(observer)
        onDispose { lifecycle.lifecycle.removeObserver(observer) }
    }
    Scaffold { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(horizontal = 20.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("学伴", style = MaterialTheme.typography.headlineMedium, color = MaterialTheme.colorScheme.primary)
                if (model.identity != null) Text(model.studentName, style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 8.dp))
            }
            model.message?.let { message ->
                Card(Modifier.fillMaxWidth()) {
                    Row(Modifier.padding(12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(message, Modifier.weight(1f))
                        TextButton(onClick = model::clearMessage) { Text("知道了") }
                    }
                }
            }
            if (model.loading) { CircularProgressIndicator(); Text("正在恢复本机记录…") }
            else if (model.identity == null) PairingScreen(model)
            else {
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    TextButton(onClick = { navigation.navigate("home") { launchSingleTop = true } }) { Text("首页") }
                    TextButton(onClick = { navigation.navigate("history") { launchSingleTop = true } }) { Text("历史作业") }
                }
                NavHost(navigation, startDestination = "home", modifier = Modifier.weight(1f)) {
                    composable("home") {
                        Page {
                            Text("先完成作业，再一起检查", style = MaterialTheme.typography.headlineSmall)
                            Text("拍下你自己的作答，学伴帮你找到需要再想一想的地方。")
                            Button(enabled = model.canCapture, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp), onClick = { model.prepareCamera(); navigation.navigate("camera") }) { Text("拍作业 / 从相册选择") }
                            if (model.busy) { LinearProgressIndicator(Modifier.fillMaxWidth()); Text("正在处理作业，可以到历史查看进度。") }
                            val unfinished = model.captures.firstOrNull { it.stage !in listOf("complete", "remote") }
                            unfinished?.let { record ->
                                Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Text("继续上次作业", style = MaterialTheme.typography.titleMedium)
                                    Text(stageLabel(record))
                                    OutlinedButton(onClick = { navigation.navigate("result/${record.id}") }) { Text("查看这份作业") }
                                } }
                            }
                            HorizontalDivider()
                            Text("当前孩子：${model.studentName}", style = MaterialTheme.typography.titleMedium)
                            if (model.students.size > 1) {
                                Text("请坐班老师确认后切换孩子。")
                                model.students.forEach { student ->
                                    OutlinedButton(enabled = !model.busy, onClick = { model.selectStudent(student.getString("student_id")) }) { Text(student.getString("display_name")) }
                                }
                            }
                            TextButton(enabled = !model.busy, onClick = { model.unpair() }) { Text("退出本机配对") }
                        }
                    }
                    composable("camera") { Page { CameraScreen(model) { navigation.navigate("progress/$it") } } }
                    composable("progress/{captureId}") { entry ->
                        val id = entry.arguments?.getString("captureId").orEmpty()
                        LaunchedEffect(id) { model.process(id) }
                        Page { RecordContent(model, id) { record ->
                            Text(if (model.busy) "正在陪你检查" else "查看这次处理结果", style = MaterialTheme.typography.headlineSmall)
                            if (model.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
                            Text(stageLabel(record))
                            record.state.optJSONObject("last_job")?.let { Text(jobState(it)) }
                            record.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                            Text("原图和进度已保留。离开页面后，可以从历史继续查看。")
                            Button(onClick = { navigation.navigate("result/$id") }) { Text("查看已有结果") }
                            if (!model.busy && record.stage != "complete") OutlinedButton(onClick = { model.process(id, record.stage == "failed") }) { Text("继续 / 重试未完成部分") }
                        } }
                    }
                    composable("result/{captureId}") { entry ->
                        val id = entry.arguments?.getString("captureId").orEmpty()
                        Page { RecordContent(model, id) { record ->
                            Text(record.title, style = MaterialTheme.typography.headlineSmall)
                            Text(stageLabel(record))
                            if (record.file.isFile) LocalPhoto(record.file, Modifier.fillMaxWidth().heightIn(min = 240.dp, max = 450.dp))
                            record.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                            if (record.cases.any { it.getJSONObject("attempt").optString("recognition_status") in listOf("needs_revision", "failed") }) {
                                Text("未读清的题目尚未检查，不代表答对。请把题干与作答一起拍清楚，或请坐班老师核实。")
                            } else record.state.text("notice")?.let { Text(it) }
                            if (model.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
                            Button(enabled = !model.busy, onClick = { model.process(id, record.stage == "failed") }) { Text(if (record.stage == "local") "上传并检查" else if (record.stage == "failed") "重试未完成部分" else "刷新并继续检查") }
                            if (record.cases.isEmpty()) Text("尚无逐题结果。照片未读清的部分不会被当作正确。")
                            else {
                                Text("逐题查看", style = MaterialTheme.typography.titleLarge)
                                Text("以下是已读到的题目，可能未覆盖照片中的所有作答。")
                                record.cases.forEachIndexed { index, row ->
                                    val case = row.getJSONObject("case")
                                    Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                        Text("识别条目 ${index + 1}", style = MaterialTheme.typography.labelLarge)
                                        Text(row.getJSONObject("question").text("stem_text") ?: "题干暂未读清", maxLines = 3)
                                        Text(caseResultLabel(row), color = MaterialTheme.colorScheme.primary)
                                        OutlinedButton(onClick = { navigation.navigate("correction/$id/${case.getString("id")}") }) { Text("看原题与订正") }
                                    } }
                                }
                            }
                        } }
                    }
                    composable("correction/{captureId}/{caseId}") { entry ->
                        val id = entry.arguments?.getString("captureId").orEmpty()
                        val caseId = entry.arguments?.getString("caseId").orEmpty()
                        Page { RecordContent(model, id) { record ->
                            val row = record.cases.firstOrNull { it.getJSONObject("case").getString("id") == caseId }
                            if (row == null) Text("这道题已更新，请返回整页结果刷新。")
                            else QuestionDetail(model, record, row, onCapture = { model.prepareCamera(id, caseId); navigation.navigate("camera") })
                        } }
                    }
                    composable("history") {
                        Page {
                            Text("我的作业", style = MaterialTheme.typography.headlineSmall)
                            OutlinedButton(enabled = !model.busy, onClick = { model.refreshHistory() }) { Text("同步服务器历史") }
                            if (model.captures.isEmpty()) Text("还没有作业照片，拍下第一份作业吧。")
                            model.captures.forEach { record ->
                                Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Text(record.title, style = MaterialTheme.typography.titleMedium)
                                    Text(DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(Date(record.capturedAt)))
                                    Text(stageLabel(record))
                                    Button(onClick = { navigation.navigate("result/${record.id}") }) { Text("打开作业") }
                                } }
                            }
                            if (model.historyCursor != null) OutlinedButton(enabled = !model.busy, onClick = { model.refreshHistory(true) }) { Text("加载更多历史") }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun PairingScreen(model: CaptureViewModel) {
    var address by rememberSaveable(model.backendAddress) { mutableStateOf(model.backendAddress) }
    // Pairing code never enters saved instance state; pending retries live only in encrypted vault.
    var code by remember { mutableStateOf("") }
    Page {
        Text("让老师帮你配对", style = MaterialTheme.typography.headlineSmall)
        Text("完成配对后，作业会保存到已授权的孩子名下。")
        if (model.canReconnect) OutlinedButton(enabled = !model.busy, onClick = model::reconnect) { Text("恢复已有配对连接") }
        OutlinedTextField(value = address, onValueChange = { address = it }, label = { Text("服务器地址") }, singleLine = true, modifier = Modifier.fillMaxWidth(), enabled = !model.busy)
        OutlinedTextField(value = code, onValueChange = { code = it }, label = { Text("老师提供的配对码") }, visualTransformation = PasswordVisualTransformation(), singleLine = true, modifier = Modifier.fillMaxWidth(), enabled = !model.busy)
        if (BuildConfig.DEBUG && address.startsWith("http:")) Text("内网联调连接：数据未使用 HTTPS 加密，仅用于已确认的本地测试环境。", color = MaterialTheme.colorScheme.error)
        Button(enabled = !model.busy, onClick = { model.pair(address, code) }) { Text(if (model.busy) "正在配对…" else if (model.pairingPending) "继续上次配对" else "配对设备") }
        if (model.pairingPending) TextButton(enabled = !model.busy, onClick = { code = ""; model.unpair() }) { Text("退出上次配对，重新开始") }
    }
}

@Composable
private fun QuestionDetail(model: CaptureViewModel, record: LocalCapture, row: JSONObject, onCapture: () -> Unit) {
    val case = row.getJSONObject("case"); val question = row.getJSONObject("question"); val attempt = row.getJSONObject("attempt")
    val caseId = case.getString("id"); val decision = case.optJSONObject("current_decision")
    Text("看清这一题", style = MaterialTheme.typography.headlineSmall)
    val availablePhotos = model.captures.filter { it.file.isFile }
    val currentPhoto = currentAttemptPhotoIndex(attempt.strings("asset_ids"), availablePhotos.map { it.state.text("asset_id") })?.let { availablePhotos[it] }
    val displayPhoto = currentPhoto ?: record.takeIf { it.file.isFile }
    if (displayPhoto != null) {
        Text(if (currentPhoto != null) "本次作答照片" else "原题照片，本次作答图片未加载")
        val regions = (question.optJSONArray("regions")?.objects().orEmpty() + attempt.optJSONArray("answer_regions")?.objects().orEmpty())
            .filter { it.optString("asset_id") == displayPhoto.state.text("asset_id") }
        LocalPhoto(displayPhoto.file, Modifier.fillMaxWidth().heightIn(min = 240.dp, max = 450.dp), regions = regions,
            originalWidth = displayPhoto.state.optInt("width"), originalHeight = displayPhoto.state.optInt("height"))
        if (regions.isNotEmpty()) Text("绿色框为 AI 推测区域，请结合原图核对。", style = MaterialTheme.typography.bodySmall)
    } else Text("本次作答图片未加载，请刷新记录或请老师核实。")
    Text("题目", style = MaterialTheme.typography.titleMedium)
    Text(question.text("stem_text") ?: "暂时没有读清题干，请对照照片。")
    Text("这次作答 · 识别文字", style = MaterialTheme.typography.titleMedium)
    Text(attempt.text("answer_text") ?: "暂时没有读清作答，请对照照片。")
    Text(caseResultLabel(row), style = MaterialTheme.typography.titleLarge, color = MaterialTheme.colorScheme.primary)
    if (attempt.optString("recognition_status") in listOf("needs_revision", "failed")) Text("这道题尚未完成检查。请重拍同一道题的题干和作答，或请坐班老师核实。")
    if (decision?.optString("verdict") == "unable_to_judge") Text("题目、作答或过程可能没有看清，请对照照片，必要时请老师核实。")
    val hint = record.state.optJSONObject("hints")?.optJSONObject(caseId)
    if (hint != null && hint.optString("validity") == "active" && hint.optString("attempt_id") == case.getString("current_attempt_id") && hint.optString("source_decision_id") == decision?.optString("id") && hint.optInt("source_decision_revision") == decision?.optInt("revision")) {
        LaunchedEffect(hint.getString("id")) { model.markHintShown(record.id, hint) }
        Card { Text(hint.getString("content"), Modifier.padding(16.dp)) }
    }
    Button(enabled = !model.busy && decision != null, onClick = { model.hint(record.id, caseId, "direction") }) { Text("给我一点方向") }
    OutlinedButton(enabled = !model.busy && decision != null, onClick = { model.hint(record.id, caseId, "key_step") }) { Text("我还卡住了，看看关键一步") }
    Button(enabled = model.canCapture, onClick = onCapture) { Text("我已在纸上订正 · 拍新作答") }
    Text("请把同一道题的题干和新作答一起拍清楚。新订正会另存记录，保留原来的作答。")
    OutlinedButton(enabled = !model.busy, onClick = { model.help(record.id, caseId, false) }) { Text("请坐班老师帮助") }
    TextButton(enabled = !model.busy, onClick = { model.help(record.id, caseId, true) }) { Text("识别文字与原图不一样") }
    if (case.strings("help_request_ids").isNotEmpty()) Text("已有求助记录，请等老师过来一起看。")
    Text("已有 ${case.strings("attempt_ids").size} 次作答记录。当前结果可能仍需老师核实。", style = MaterialTheme.typography.bodySmall)
}

private fun stageLabel(record: LocalCapture): String = when (record.stage) {
    "local" -> "照片已保留 · 等待开始检查"
    "creating" -> "正在保存作业记录"
    "uploading" -> "正在上传并核验照片"
    "recognizing" -> "正在读题与作答"
    "checking" -> "正在逐题检查"
    "complete" -> completedPageLabel(record.cases)
    "failed" -> "部分处理未完成 · 原图已保留"
    "remote" -> "服务器历史 · 打开后可刷新结果"
    else -> "进度已保留"
}

@Composable
private fun Page(content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(16.dp), content = content)
}

@Composable
private fun RecordContent(model: CaptureViewModel, id: String, content: @Composable (LocalCapture) -> Unit) {
    val record = model.captures.firstOrNull { it.id == id }
    if (record == null) Text("这份记录不在当前孩子的本机历史中，请返回历史同步。") else content(record)
}

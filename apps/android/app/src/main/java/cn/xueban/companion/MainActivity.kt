package cn.xueban.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.KeyboardType
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
import cn.xueban.companion.ui.PageEditor
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
    val lifecycle = LocalLifecycleOwner.current
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_START) model.onForeground() }
        lifecycle.lifecycle.addObserver(observer)
        onDispose { lifecycle.lifecycle.removeObserver(observer) }
    }
    Scaffold { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(horizontal = 20.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("学伴", style = MaterialTheme.typography.headlineMedium, color = MaterialTheme.colorScheme.primary)
                model.account?.let { Text(it.studentName, style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 8.dp)) }
            }
            model.message?.let { message ->
                Card(Modifier.fillMaxWidth()) {
                    Row(Modifier.padding(12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(message, Modifier.weight(1f))
                        TextButton(onClick = model::clearMessage) { Text("知道了") }
                    }
                }
            }
            when (model.screen) {
                Screen.Loading -> { CircularProgressIndicator(); Text("正在恢复登录和本机记录…") }
                Screen.Login -> LoginScreen(model)
                Screen.ChangePassword -> ChangePasswordScreen(model)
                Screen.Home -> SignedIn(model)
            }
        }
    }
}

@Composable
private fun LoginScreen(model: CaptureViewModel) {
    var address by rememberSaveable(model.savedServer) { mutableStateOf(model.savedServer) }
    var username by rememberSaveable(model.savedUsername) { mutableStateOf(model.savedUsername) }
    // Passwords never enter saved instance state or the encrypted vault.
    var password by remember { mutableStateOf("") }
    Page {
        Text("登录学伴", style = MaterialTheme.typography.headlineSmall)
        Text("使用老师发放的学生账号登录。和网页版是同一个账号，作业记录也是同一份。")
        OutlinedTextField(value = address, onValueChange = { address = it }, label = { Text("服务器地址") }, singleLine = true, modifier = Modifier.fillMaxWidth(), enabled = !model.busy,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
        OutlinedTextField(value = username, onValueChange = { username = it }, label = { Text("账号") }, singleLine = true, modifier = Modifier.fillMaxWidth(), enabled = !model.busy,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Ascii, autoCorrectEnabled = false))
        OutlinedTextField(value = password, onValueChange = { password = it }, label = { Text("密码") }, visualTransformation = PasswordVisualTransformation(), singleLine = true,
            modifier = Modifier.fillMaxWidth(), enabled = !model.busy, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false))
        if (BuildConfig.DEBUG && address.trim().startsWith("http:")) Text("内网联调连接：数据未使用 HTTPS 加密，仅用于已确认的本地测试环境。", color = MaterialTheme.colorScheme.error)
        Button(enabled = !model.busy && username.isNotBlank() && password.isNotEmpty(), modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp), onClick = { model.login(address, username, password) }) {
            Text(if (model.busy) "正在登录…" else "登录")
        }
        Text("忘记密码请找老师重置。共用平板用完请退出登录。", style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun ChangePasswordScreen(model: CaptureViewModel) {
    var current by remember { mutableStateOf("") }
    var next by remember { mutableStateOf("") }
    var repeat by remember { mutableStateOf("") }
    val length = next.codePointCount(0, next.length)
    Page {
        Text("设置自己的新密码", style = MaterialTheme.typography.headlineSmall)
        Text("${model.savedUsername} 第一次登录或密码被老师重置后，需要先换成只有自己知道的新密码。")
        Text("新密码 15–128 个字符，可以用一句好记的话，例如“我每天放学先写数学作业”。不要告诉同学。", style = MaterialTheme.typography.bodySmall)
        OutlinedTextField(value = current, onValueChange = { current = it }, label = { Text("当前密码（老师给的初始密码）") }, visualTransformation = PasswordVisualTransformation(), singleLine = true,
            modifier = Modifier.fillMaxWidth(), enabled = !model.busy, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false))
        OutlinedTextField(value = next, onValueChange = { next = it }, label = { Text("新密码") }, visualTransformation = PasswordVisualTransformation(), singleLine = true,
            modifier = Modifier.fillMaxWidth(), enabled = !model.busy, supportingText = { Text("已输入 $length 个字符") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false))
        OutlinedTextField(value = repeat, onValueChange = { repeat = it }, label = { Text("再输入一次新密码") }, visualTransformation = PasswordVisualTransformation(), singleLine = true,
            modifier = Modifier.fillMaxWidth(), enabled = !model.busy, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false))
        if (repeat.isNotEmpty() && repeat != next) Text("两次输入的新密码不一样", color = MaterialTheme.colorScheme.error)
        Button(enabled = !model.busy && current.isNotEmpty() && length >= 15 && next == repeat, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp), onClick = { model.changePassword(current, next) }) {
            Text(if (model.busy) "正在保存…" else "保存新密码")
        }
        TextButton(enabled = !model.busy, onClick = model::logout) { Text("退出登录") }
    }
}

@Composable
private fun ColumnScope.SignedIn(model: CaptureViewModel) {
    val navigation = rememberNavController()
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        TextButton(onClick = { navigation.navigate("home") { launchSingleTop = true } }) { Text("首页") }
        TextButton(onClick = { navigation.navigate("history") { launchSingleTop = true } }) { Text("历史作业") }
    }
    NavHost(navigation, startDestination = "home", modifier = Modifier.weight(1f)) {
        composable("home") { Page { Home(model, navigation::navigate) } }
        composable("capture") {
            Page { CameraScreen(model, onEdit = { navigation.navigate("edit/$it") }, onSubmitted = { id -> navigation.navigate("progress/$id") { popUpTo("home") } }, onDiscarded = { navigation.popBackStack() }) }
        }
        composable("edit/{pageId}") { entry ->
            Page { PageEditor(model, entry.arguments?.getString("pageId").orEmpty()) { navigation.popBackStack() } }
        }
        composable("progress/{captureId}") { entry ->
            val id = entry.arguments?.getString("captureId").orEmpty()
            Page { RecordContent(model, id) { record ->
                val working = canResumeAutomatically(record.stage)
                Text(if (working) "正在陪你检查" else "查看这次处理结果", style = MaterialTheme.typography.headlineSmall)
                if (working && model.syncRunning) LinearProgressIndicator(Modifier.fillMaxWidth())
                Text(stageLabel(record))
                SyncNote(model, record)
                record.state.optJSONObject("last_job")?.let { Text(jobState(it)) }
                record.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                Text("原图和进度已保留在本机。离开页面、切到后台或重启平板后，联网时会自动继续。")
                Button(onClick = { navigation.navigate("result/$id") }) { Text("查看已有结果") }
                if (record.stage != "complete") OutlinedButton(enabled = !model.busy, onClick = { model.process(id, record.stage == "failed") }) { Text("立即继续 / 重试未完成部分") }
            } }
        }
        composable("result/{captureId}") { entry ->
            val id = entry.arguments?.getString("captureId").orEmpty()
            Page { RecordContent(model, id) { record ->
                Text(record.title, style = MaterialTheme.typography.headlineSmall)
                Text(stageLabel(record))
                SyncNote(model, record)
                if (record.isDraft) {
                    Text("这份作业还没有上传。")
                    Button(onClick = { model.openDraft(id); navigation.navigate("capture") }) { Text("继续编辑草稿") }
                } else ResultBody(model, record) { navigation.navigate(it) }
            } }
        }
        composable("correction/{captureId}/{caseId}") { entry ->
            val id = entry.arguments?.getString("captureId").orEmpty()
            val caseId = entry.arguments?.getString("caseId").orEmpty()
            Page { RecordContent(model, id) { record ->
                val row = record.cases.firstOrNull { it.getJSONObject("case").getString("id") == caseId }
                if (row == null) Text("这道题已更新，请返回整页结果刷新。")
                else QuestionDetail(model, record, row, onCapture = { model.prepareCapture(id, caseId); navigation.navigate("capture") })
            } }
        }
        composable("history") {
            Page {
                Text("我的作业", style = MaterialTheme.typography.headlineSmall)
                OutlinedButton(enabled = !model.busy && !model.offline, onClick = { model.refreshHistory() }) { Text("同步服务器历史") }
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

@Composable
private fun ResultBody(model: CaptureViewModel, record: LocalCapture, navigate: (String) -> Unit) {
    val photos = record.pages.filter { it.upload.isFile }
    photos.forEachIndexed { index, page ->
        if (photos.size > 1) Text("第 ${index + 1} 页", style = MaterialTheme.typography.labelLarge)
        LocalPhoto(page.upload, Modifier.fillMaxWidth().heightIn(min = 240.dp, max = 450.dp), version = page.state.optString("sha256"))
    }
    record.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    if (record.cases.any { it.getJSONObject("attempt").optString("recognition_status") in listOf("needs_revision", "failed") }) {
        Text("未读清的题目尚未检查，不代表答对。请把题干与作答一起拍清楚，或请坐班老师核实。")
    } else record.state.text("notice")?.let { Text(it) }
    if (canResumeAutomatically(record.stage) && model.syncRunning) LinearProgressIndicator(Modifier.fillMaxWidth())
    Button(enabled = !model.busy, onClick = { model.process(record.id, record.stage == "failed") }) { Text(if (record.stage == "failed") "重试未完成部分" else "刷新并继续检查") }
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
                OutlinedButton(onClick = { navigate("correction/${record.id}/${case.getString("id")}") }) { Text("看原题与订正") }
            } }
        }
    }
}

@Composable
private fun ColumnScope.Home(model: CaptureViewModel, navigate: (String) -> Unit) {
    var confirmLogout by remember { mutableStateOf(false) }
    Text("先完成作业，再一起检查", style = MaterialTheme.typography.headlineSmall)
    Text("拍下你自己的作答，学伴帮你找到需要再想一想的地方。")
    if (model.offline) Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("暂时未连接服务器", style = MaterialTheme.typography.titleMedium)
        Text("可以继续拍照保存，联网后自动上传。检查结果需要联网后查看。")
        OutlinedButton(enabled = !model.busy, onClick = model::reconnect) { Text("重新连接") }
    } }
    Button(enabled = model.canCapture, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp), onClick = { model.prepareCapture(); navigate("capture") }) { Text("拍作业 / 从相册选择") }
    model.captures.firstOrNull { it.isDraft }?.let { draft ->
        Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("有一份草稿还没提交", style = MaterialTheme.typography.titleMedium)
            Text(stageLabel(draft))
            OutlinedButton(onClick = { model.openDraft(draft.id); navigate("capture") }) { Text("继续拍 / 提交草稿") }
        } }
    }
    model.captures.firstOrNull { !it.isDraft && it.stage !in listOf("complete", "remote") }?.let { record ->
        Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("继续上次作业", style = MaterialTheme.typography.titleMedium)
            Text(stageLabel(record))
            SyncNote(model, record)
            OutlinedButton(onClick = { navigate("progress/${record.id}") }) { Text("查看这份作业") }
        } }
    }
    HorizontalDivider()
    model.account?.let { Text("当前账号：${it.username}（${it.studentName}）", style = MaterialTheme.typography.titleMedium) }
    TextButton(enabled = !model.busy, onClick = { if (model.unsentCount > 0) confirmLogout = true else model.logout() }) { Text("退出登录") }
    if (confirmLogout) AlertDialog(
        onDismissRequest = { confirmLogout = false },
        title = { Text("还有 ${model.unsentCount} 份作业没有上传完") },
        text = { Text("退出后会暂停上传。照片会保留在本机，只有重新登录同一账号才能看到并继续上传。") },
        confirmButton = { TextButton(onClick = { confirmLogout = false; model.logout() }) { Text("仍然退出") } },
        dismissButton = { Button(onClick = { confirmLogout = false }) { Text("先不退出") } },
    )
}

@Composable
private fun SyncNote(model: CaptureViewModel, record: LocalCapture) {
    if (!canResumeAutomatically(record.stage) && !record.state.optBoolean("sync_requested")) return
    Text(when {
        model.syncRunning -> "正在后台处理，可以离开这个页面。"
        model.syncWaiting -> "等待网络或稍后自动重试，原图和进度已保留。"
        else -> "已暂停，点“立即继续”或重新打开 App 后继续。"
    }, style = MaterialTheme.typography.bodySmall)
}

@Composable
private fun QuestionDetail(model: CaptureViewModel, record: LocalCapture, row: JSONObject, onCapture: () -> Unit) {
    val case = row.getJSONObject("case"); val question = row.getJSONObject("question"); val attempt = row.getJSONObject("attempt")
    val caseId = case.getString("id"); val decision = case.optJSONObject("current_decision")
    Text("看清这一题", style = MaterialTheme.typography.headlineSmall)
    val availablePages = model.captures.flatMap { it.pages }.filter { it.upload.isFile && it.assetId != null }
    val currentPage = currentAttemptPhotoIndex(attempt.strings("asset_ids"), availablePages.map { it.assetId })?.let { availablePages[it] }
    val questionAssets = question.optJSONArray("regions")?.objects().orEmpty().mapNotNull { it.text("asset_id") }
    val displayPage = currentPage ?: record.pages.firstOrNull { it.upload.isFile && it.assetId in questionAssets } ?: record.pages.firstOrNull { it.upload.isFile }
    if (displayPage != null) {
        Text(if (currentPage != null) "本次作答照片" else "原题照片，本次作答图片未加载")
        val regions = (question.optJSONArray("regions")?.objects().orEmpty() + attempt.optJSONArray("answer_regions")?.objects().orEmpty())
            .filter { it.optString("asset_id") == displayPage.assetId }
        LocalPhoto(displayPage.upload, Modifier.fillMaxWidth().heightIn(min = 240.dp, max = 450.dp), regions = regions,
            originalWidth = displayPage.state.optInt("width"), originalHeight = displayPage.state.optInt("height"), version = displayPage.state.optString("sha256"))
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

private fun stageLabel(record: LocalCapture): String = recordStageLabel(record.state, record.cases)

@Composable
private fun Page(content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(16.dp), content = content)
}

@Composable
private fun RecordContent(model: CaptureViewModel, id: String, content: @Composable (LocalCapture) -> Unit) {
    val record = model.captures.firstOrNull { it.id == id }
    if (record == null) Text("这份记录不在当前账号的本机历史中，请返回历史同步。") else content(record)
}

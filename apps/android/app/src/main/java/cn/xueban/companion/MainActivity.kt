package cn.xueban.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import cn.xueban.companion.data.LocalCapture
import cn.xueban.companion.ui.CameraScreen
import cn.xueban.companion.ui.LocalPhoto
import java.text.DateFormat
import java.util.Date

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MaterialTheme(colorScheme = lightColorScheme(primary = Color(0xFF146457), background = Color(0xFFF6F8F5))) {
                CompanionApp()
            }
        }
    }
}

@Composable
private fun CompanionApp(model: CaptureViewModel = viewModel()) {
    val navigation = rememberNavController()
    Scaffold { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(horizontal = 20.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("学伴 · 拍作业，自己改", style = MaterialTheme.typography.headlineSmall)
            Card(Modifier.fillMaxWidth()) {
                Text("开发采集模式｜未登录学生｜照片仅存本机｜AI 尚未接入", Modifier.padding(12.dp), style = MaterialTheme.typography.bodyMedium)
            }
            TextButton(onClick = { navigation.navigate("home") { popUpTo("home") { inclusive = true } } }) { Text("回首页") }
            model.message?.let { message ->
                Text(message)
                TextButton(onClick = model::clearMessage) { Text("知道了") }
            }
            NavHost(navigation, startDestination = "home", modifier = Modifier.weight(1f)) {
                composable("home") {
                    Page {
                        Text("初中 · 人教版", style = MaterialTheme.typography.titleLarge)
                        Text("当前只验证真实拍照和本地保存。年级、学期与首批题型由后续授权配置，不据此假定已覆盖全部初中课程。")
                        Button(enabled = BuildConfig.DEBUG, onClick = { navigation.navigate("camera") }) { Text("拍作业 · 本地采集") }
                        OutlinedButton(onClick = { navigation.navigate("history") }) { Text("本地采集历史 (${model.captures.size})") }
                        Text("继续订正：尚无经过识别和确认的题目。")
                        BackendSettings(model)
                    }
                }
                composable("camera") { Page { CameraScreen(model) { navigation.navigate("progress/$it") } } }
                composable("progress/{captureId}") { entry ->
                    Page {
                        Text("上传与检查进度", style = MaterialTheme.typography.titleLarge)
                        CaptureContent(model, entry.arguments?.getString("captureId")) { capture ->
                            Text("采集：本机私有照片已保存")
                            Text("上传：未执行，尚无服务端身份授权")
                            Text("识别与检查：未执行，OCR / 模型适配器待接入")
                            Text("这里只展示实际状态，不生成虚拟进度或检查结果。")
                            Button(onClick = { navigation.navigate("result/${capture.id}") }) { Text("查看原图与结果状态") }
                        }
                    }
                }
                composable("result/{captureId}") { entry ->
                    Page {
                        Text("整页结果", style = MaterialTheme.typography.titleLarge)
                        CaptureContent(model, entry.arguments?.getString("captureId")) { capture ->
                            LocalPhoto(capture.file, Modifier.fillMaxWidth().heightIn(min = 240.dp, max = 480.dp))
                            Text("尚未识别题区，尚无对错结论。不能据此计算正确率。")
                            OutlinedButton(onClick = { navigation.navigate("correction/${capture.id}") }) { Text("查看订正入口状态") }
                        }
                    }
                }
                composable("correction/{captureId}") { entry ->
                    Page {
                        Text("单题订正", style = MaterialTheme.typography.titleLarge)
                        CaptureContent(model, entry.arguments?.getString("captureId")) {
                            Text("尚未形成题目和纠错记录，当前无法启动订正或再检查。")
                            Text("接入后在这里展示原作答、错误位置、分层提示、老师求助，以及每次订正的真实检查状态。")
                            Button(enabled = false, onClick = {}) { Text("拍订正 · 等待有效题目") }
                            OutlinedButton(enabled = false, onClick = {}) { Text("请坐班老师帮助 · 待接入") }
                            Text("按钮点击不会生成已解决记录；当前也没有向老师发送请求。")
                        }
                    }
                }
                composable("history") {
                    Page {
                        Text("本地采集历史", style = MaterialTheme.typography.titleLarge)
                        Text("这里是设备开发草稿，不归属任何学生。清除应用数据或卸载会删除；不会变成生产作业记录。")
                        if (model.loading) Text("正在读取本机记录…")
                        else if (model.captures.isEmpty()) Text("还没有本地照片。")
                        model.captures.forEach { capture ->
                            Card(Modifier.fillMaxWidth()) {
                                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Text(DateFormat.getDateTimeInstance().format(Date(capture.capturedAt)))
                                    Text("仅本地保存 · 尚未检查")
                                    Button(onClick = { navigation.navigate("result/${capture.id}") }) { Text("查看照片") }
                                    TextButton(onClick = { model.deleteCapture(capture.id) }) { Text("删除这张开发草稿") }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun Page(content: @Composable () -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(PaddingValues(bottom = 24.dp)), verticalArrangement = Arrangement.spacedBy(16.dp)) { content() }
}

@Composable
private fun CaptureContent(model: CaptureViewModel, captureId: String?, content: @Composable (LocalCapture) -> Unit) {
    val capture = model.captures.firstOrNull { it.id == captureId }
    when {
        model.loading -> Text("正在读取本机记录…")
        capture == null -> Text("这张本地照片不存在或已删除，请返回历史。")
        else -> content(capture)
    }
}

@Composable
private fun BackendSettings(model: CaptureViewModel) {
    var address by rememberSaveable(model.backendAddress) { mutableStateOf(model.backendAddress) }
    Text("开发服务配置", style = MaterialTheme.typography.titleMedium)
    OutlinedTextField(value = address, onValueChange = { address = it }, label = { Text("后端 HTTPS 地址（可留空）") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
    OutlinedButton(onClick = { model.saveBackendAddress(address) }) { Text("保存服务地址") }
    Text(if (model.backendAddress.isBlank()) "后端未配置。" else "已保存地址；当前没有网络客户端，也没有服务端授权。")
}

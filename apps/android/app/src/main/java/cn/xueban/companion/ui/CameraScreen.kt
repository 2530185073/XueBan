package cn.xueban.companion.ui

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.content.pm.PackageManager
import android.provider.Settings
import android.view.Surface
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.net.toUri
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import cn.xueban.companion.CaptureViewModel
import cn.xueban.companion.data.CameraAccess
import cn.xueban.companion.data.LocalPage
import cn.xueban.companion.data.cameraAccess

private const val MAX_PICKED_PAGES = 10

@Composable
fun CameraScreen(model: CaptureViewModel, onEdit: (String) -> Unit, onSubmitted: (String) -> Unit, onDiscarded: () -> Unit) {
    val context = LocalContext.current
    val activity = remember(context) { context.findActivity() }
    val lifecycleOwner = LocalLifecycleOwner.current
    fun hasCamera() = ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
    fun rationale() = activity?.let { ActivityCompat.shouldShowRequestPermissionRationale(it, Manifest.permission.CAMERA) } ?: false
    var granted by remember { mutableStateOf(hasCamera()) }
    var asked by rememberSaveable { mutableStateOf(false) }
    var showRationale by remember { mutableStateOf(rationale()) }
    var error by remember { mutableStateOf<String?>(null) }
    var saving by remember { mutableStateOf(false) }
    var confirming by remember { mutableStateOf(false) }
    var discarding by remember { mutableStateOf(false) }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { result ->
        granted = result; asked = true; showRationale = rationale()
    }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.PickMultipleVisualMedia(MAX_PICKED_PAGES)) { uris -> model.importPages(uris) }
    val pages = model.draft?.pages.orEmpty()

    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) { granted = hasCamera(); showRationale = rationale() }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }
    BackHandler(enabled = saving) { /* Keep this capture visible until CameraX reports a result. */ }

    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(if (model.isCorrectionCapture) "拍新的订正" else "拍作业 · 可以拍多页", style = MaterialTheme.typography.headlineSmall)
        Text(if (model.isCorrectionCapture) "请拍同一道题的题干和新的作答，保留题目完整内容。" else "一页拍一张，拍清题目和自己的作答，注意反光和缺角。拍完所有页再一起上传。")
        when (cameraAccess(granted, asked, showRationale)) {
            CameraAccess.Granted -> CameraCapture(model, saving, onSaving = { saving = it }, onError = { error = it })
            CameraAccess.NotRequested -> {
                Text("拍作业需要使用相机。照片只保存在本机应用内，提交后才会上传给老师和学伴检查。")
                Button(onClick = { permission.launch(Manifest.permission.CAMERA) }) { Text("允许使用相机") }
            }
            CameraAccess.Denied -> {
                Text("没有相机权限就不能直接拍照。你可以再允许一次，或者先从相册选择已经拍好的作业照片。")
                Button(onClick = { permission.launch(Manifest.permission.CAMERA) }) { Text("再次允许相机") }
            }
            CameraAccess.PermanentlyDenied -> {
                Text("相机权限已被关闭，需要到系统设置的“权限”里打开相机。也可以先从相册选择照片。")
                Button(onClick = {
                    context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, "package:${context.packageName}".toUri()))
                }) { Text("打开系统设置") }
            }
        }
        OutlinedButton(enabled = !saving && !model.busy, onClick = { picker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) }) {
            Text("从相册添加（可多选）")
        }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        if (pages.isNotEmpty()) {
            Text("已拍 ${pages.size} 页 · 未上传", style = MaterialTheme.typography.titleMedium)
            pages.forEachIndexed { index, page -> PageRow(model, page, index, pages.size, onEdit) }
            Button(enabled = !saving && !model.busy && pages.all { it.ready }, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp), onClick = { confirming = true }) {
                Text("拍完了，确认页数并上传")
            }
        }
        if (model.draft != null) TextButton(enabled = !saving && !model.busy, onClick = { discarding = true }) { Text("放弃这份草稿") }
    }

    if (confirming) AlertDialog(
        onDismissRequest = { confirming = false },
        title = { Text("这份作业一共 ${pages.size} 页吗？") },
        text = { Text("请翻一下作业本，确认要检查的每一页都拍到了。没拍到的页不会被检查。提交后照片不能再修改，会在联网时自动上传。") },
        confirmButton = { Button(onClick = { confirming = false; model.submitDraft(pages.size, onSubmitted) }) { Text("确认 ${pages.size} 页，上传检查") } },
        dismissButton = { TextButton(onClick = { confirming = false }) { Text("还有漏拍，继续拍") } },
    )
    if (discarding) AlertDialog(
        onDismissRequest = { discarding = false },
        title = { Text("放弃这份草稿？") },
        text = { Text("这份草稿还没有上传，放弃后本机照片会被删除。") },
        confirmButton = { TextButton(onClick = { discarding = false; model.discardDraft(onDiscarded) }) { Text("删除草稿") } },
        dismissButton = { Button(onClick = { discarding = false }) { Text("继续编辑") } },
    )
}

@Composable
private fun PageRow(model: CaptureViewModel, page: LocalPage, index: Int, count: Int, onEdit: (String) -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            if (page.ready) LocalPhoto(page.upload, Modifier.size(width = 120.dp, height = 160.dp), version = page.state.optString("sha256"), maxSide = 480)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("第 ${index + 1} 页", style = MaterialTheme.typography.titleMedium)
                when {
                    page.status == "capturing" -> Text("正在保存这一页…")
                    !page.ready -> Text(page.error ?: "这一页没有处理成功，请删除后重拍", color = MaterialTheme.colorScheme.error)
                    else -> page.quality?.warnings()?.forEach { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
                }
                if (page.ready && (page.edit.rotation != 0 || page.edit.crop != null)) Text("已旋转/裁切，原图保留", style = MaterialTheme.typography.bodySmall)
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    TextButton(enabled = !model.busy && index > 0, onClick = { model.movePage(page.id, -1) }) { Text("上移") }
                    TextButton(enabled = !model.busy && index < count - 1, onClick = { model.movePage(page.id, 1) }) { Text("下移") }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    TextButton(enabled = !model.busy && page.ready, onClick = { onEdit(page.id) }) { Text("旋转/裁切") }
                    TextButton(enabled = !model.busy && page.status != "capturing", onClick = { model.removePage(page.id) }) { Text("删除") }
                }
            }
        }
    }
}

@Composable
private fun CameraCapture(model: CaptureViewModel, saving: Boolean, onSaving: (Boolean) -> Unit, onError: (String?) -> Unit) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val previewView = remember(context) {
        PreviewView(context).apply {
            scaleType = PreviewView.ScaleType.FIT_CENTER
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
        }
    }
    val imageCapture = remember { ImageCapture.Builder().setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY).build() }
    var ready by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf(false) }
    DisposableEffect(lifecycleOwner, previewView) {
        var disposed = false
        var provider: ProcessCameraProvider? = null
        val preview = Preview.Builder().build().also { it.setSurfaceProvider(previewView.surfaceProvider) }
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            if (!disposed) {
                runCatching {
                    val cameraProvider = future.get()
                    provider = cameraProvider
                    check(cameraProvider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA)) { "设备没有可用的后置相机，请从相册选择" }
                    cameraProvider.bindToLifecycle(lifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA, preview, imageCapture)
                    ready = true
                }.onFailure { failed = true; onError(it.message ?: "相机启动失败，请从相册选择") }
            }
        }, ContextCompat.getMainExecutor(context))
        onDispose {
            disposed = true
            provider?.unbind(preview, imageCapture)
        }
    }
    AndroidView(factory = { previewView }, modifier = Modifier.fillMaxWidth().heightIn(min = 280.dp, max = 480.dp))
    Button(enabled = ready && !saving && model.canCapture, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp), onClick = {
        onError(null); onSaving(true)
        model.beginPage { pending ->
            if (pending == null) { onSaving(false); return@beginPage }
            imageCapture.targetRotation = previewView.display?.rotation ?: Surface.ROTATION_0
            runCatching { imageCapture.takePicture(
                ImageCapture.OutputFileOptions.Builder(pending.file).build(),
                ContextCompat.getMainExecutor(context),
                object : ImageCapture.OnImageSavedCallback {
                    override fun onImageSaved(output: ImageCapture.OutputFileResults) { model.finishPage(pending) { onSaving(false) } }
                    override fun onError(exception: ImageCaptureException) {
                        model.abandonPage(pending); onSaving(false)
                        onError(if (exception.imageCaptureError == ImageCapture.ERROR_FILE_IO) "照片没能保存，可能是存储空间不足，请清理后重试" else "拍照失败，请重试：${exception.message.orEmpty()}")
                    }
                },
            ) }.onFailure {
                model.abandonPage(pending); onSaving(false)
                onError("相机未能开始拍照，请重试：${it.message.orEmpty()}")
            }
        }
    }) { Text(if (saving) "正在保存这一页…" else if (ready) "拍这一页" else if (failed) "相机未就绪，可从相册选择" else "正在启动相机…") }
}

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

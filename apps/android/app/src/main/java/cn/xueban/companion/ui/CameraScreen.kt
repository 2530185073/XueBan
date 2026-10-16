package cn.xueban.companion.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import android.view.Surface
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
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
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import cn.xueban.companion.BuildConfig
import cn.xueban.companion.CaptureViewModel

@Composable
fun CameraScreen(model: CaptureViewModel, onSaved: (String) -> Unit) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    var granted by remember { mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) }
    var error by remember { mutableStateOf<String?>(null) }
    var saving by remember { mutableStateOf(false) }
    var savedId by rememberSaveable { mutableStateOf<String?>(null) }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted = it }
    val saved = model.captures.firstOrNull { it.id == savedId }

    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                granted = ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }
    BackHandler(enabled = saving) { /* Keep this capture visible until CameraX reports a result. */ }

    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("拍照与预览")
        Text("只采集已获准使用的开发样张。当前没有学生身份，不会上传或批改。")
        if (!BuildConfig.DEBUG) {
            Text("正式身份授权尚未接入，此版本不开放采集。请使用 debug 开发构建。")
        } else if (saved != null) {
            LocalPhoto(saved.file, Modifier.fillMaxWidth().heightIn(min = 240.dp, max = 480.dp))
            Text("照片已保存到本机私有草稿。检查、旋转编辑与裁切待接入。")
            Button(onClick = { onSaved(saved.id) }) { Text("查看当前状态") }
            OutlinedButton(onClick = { savedId = null }) { Text("继续拍一张") }
        } else if (!granted) {
            Text("需要相机权限才能拍照。拒绝权限不会创建记录。")
            Button(onClick = { permission.launch(Manifest.permission.CAMERA) }) { Text("允许使用相机") }
            OutlinedButton(onClick = {
                context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${context.packageName}")))
            }) { Text("打开权限设置") }
        } else {
            val previewView = remember(context) {
                PreviewView(context).apply {
                    scaleType = PreviewView.ScaleType.FIT_CENTER
                    implementationMode = PreviewView.ImplementationMode.COMPATIBLE
                }
            }
            val imageCapture = remember { ImageCapture.Builder().setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY).build() }
            var ready by remember { mutableStateOf(false) }
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
                            check(cameraProvider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA)) { "设备没有可用的后置相机" }
                            cameraProvider.bindToLifecycle(lifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA, preview, imageCapture)
                            ready = true
                        }.onFailure { error = it.message ?: "相机启动失败" }
                    }
                }, ContextCompat.getMainExecutor(context))
                onDispose {
                    disposed = true
                    provider?.unbind(preview, imageCapture)
                }
            }
            AndroidView(factory = { previewView }, modifier = Modifier.fillMaxWidth().heightIn(min = 280.dp, max = 480.dp))
            Button(enabled = ready && !saving, onClick = {
                error = null
                val pending = runCatching { model.beginCapture() }.getOrElse {
                    error = it.message ?: "无法创建采集文件"
                    return@Button
                }
                saving = true
                imageCapture.targetRotation = previewView.display?.rotation ?: Surface.ROTATION_0
                runCatching { imageCapture.takePicture(
                    ImageCapture.OutputFileOptions.Builder(pending).build(),
                    ContextCompat.getMainExecutor(context),
                    object : ImageCapture.OnImageSavedCallback {
                        override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                            model.finishCapture(pending) { capture -> savedId = capture?.id; saving = false }
                        }
                        override fun onError(exception: ImageCaptureException) {
                            model.abandonCapture(pending)
                            saving = false
                            error = "拍照失败，请重试：${exception.message.orEmpty()}"
                        }
                    },
                ) }.onFailure {
                    model.abandonCapture(pending)
                    saving = false
                    error = "相机未能开始拍照，请重试：${it.message.orEmpty()}"
                }
            }) { Text(if (saving) "正在保存照片…" else if (ready) "拍照并保存在本机" else if (error != null) "相机未就绪，请返回后重试" else "正在启动相机…") }
        }
        error?.let { Text(it) }
    }
}

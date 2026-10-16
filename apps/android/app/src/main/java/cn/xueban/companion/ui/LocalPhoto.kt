package cn.xueban.companion.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import androidx.compose.foundation.Image
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.exifinterface.media.ExifInterface
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

@Composable
fun LocalPhoto(file: File, modifier: Modifier = Modifier, regions: List<JSONObject> = emptyList(), originalWidth: Int = 0, originalHeight: Int = 0) {
    val state by produceState<PhotoState>(initialValue = PhotoState.Loading, key1 = file.absolutePath) {
        value = withContext(Dispatchers.IO) {
            runCatching { decodePreview(file) }
                .fold(onSuccess = { it?.let { bitmap -> PhotoState.Ready(bitmap) } ?: PhotoState.Unreadable }, onFailure = { PhotoState.Unreadable })
        }
    }
    when (val current = state) {
        PhotoState.Loading -> Text("正在读取本机照片…", modifier)
        PhotoState.Unreadable -> Text("照片预览读取失败。原文件仍保留，可返回历史后重试或重新拍照。", modifier)
        is PhotoState.Ready -> Box(modifier) {
            Image(current.bitmap, "作业照片；绿色框为 AI 推测区域，请结合原图核对", Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
            if (originalWidth > 0 && originalHeight > 0) Canvas(Modifier.matchParentSize()) {
                val fit = minOf(size.width / current.bitmap.width, size.height / current.bitmap.height)
                val width = current.bitmap.width * fit; val height = current.bitmap.height * fit
                val left = (size.width - width) / 2; val top = (size.height - height) / 2
                regions.forEach { region ->
                    if (listOf("x", "y", "width", "height").any { !region.optDouble(it).isFinite() }) return@forEach
                    val x = region.optDouble("x", 0.0).toFloat().coerceIn(0f, originalWidth.toFloat())
                    val y = region.optDouble("y", 0.0).toFloat().coerceIn(0f, originalHeight.toFloat())
                    val w = region.optDouble("width", 0.0).toFloat().coerceIn(0f, originalWidth - x)
                    val h = region.optDouble("height", 0.0).toFloat().coerceIn(0f, originalHeight - y)
                    drawRect(Color(0xFF17846C), Offset(left + x / originalWidth * width, top + y / originalHeight * height), Size(w / originalWidth * width, h / originalHeight * height), style = Stroke(3f))
                }
            }
        }
    }
}

private sealed interface PhotoState {
    data object Loading : PhotoState
    data object Unreadable : PhotoState
    data class Ready(val bitmap: ImageBitmap) : PhotoState
}

private fun decodePreview(file: File): ImageBitmap? {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeFile(file.absolutePath, bounds)
    var sample = 1
    while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 1600) sample *= 2
    val original = BitmapFactory.decodeFile(file.absolutePath, BitmapFactory.Options().apply { inSampleSize = sample })
        ?: return null
    val exif = ExifInterface(file)
    val transform = Matrix().apply {
        if (exif.isFlipped) postScale(-1f, 1f)
        postRotate(exif.rotationDegrees.toFloat())
    }
    val preview = Bitmap.createBitmap(original, 0, 0, original.width, original.height, transform, true)
    if (preview !== original) original.recycle()
    return preview.asImageBitmap()
}

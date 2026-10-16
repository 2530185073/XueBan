package cn.xueban.companion.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import androidx.compose.foundation.Image
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.exifinterface.media.ExifInterface
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@Composable
fun LocalPhoto(file: File, modifier: Modifier = Modifier) {
    val state by produceState<PhotoState>(initialValue = PhotoState.Loading, key1 = file.absolutePath) {
        value = withContext(Dispatchers.IO) {
            runCatching { decodePreview(file) }
                .fold(onSuccess = { it?.let { bitmap -> PhotoState.Ready(bitmap) } ?: PhotoState.Unreadable }, onFailure = { PhotoState.Unreadable })
        }
    }
    when (val current = state) {
        PhotoState.Loading -> Text("正在读取本机照片…", modifier)
        PhotoState.Unreadable -> Text("照片预览读取失败。原文件仍保留，可返回历史后重试或重新拍照。", modifier)
        is PhotoState.Ready -> Image(current.bitmap, "本地保存的作业原图预览，尚未检查", modifier, contentScale = ContentScale.Fit)
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

package cn.xueban.companion.data

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import androidx.core.graphics.scale
import androidx.core.util.AtomicFile
import androidx.exifinterface.media.ExifInterface
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest

/** EXIF orientation, then the student's rotation and crop, then a bounded JPEG for upload. */
class BitmapPageRenderer : PageRenderer {
    override fun render(original: File, target: File, edit: PageEdit): RenderedPage {
        require(original.isFile && original.length() > 0) { "相机未保存照片" }
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(original.path, bounds)
        sourceImageProblem(original.length(), bounds.outWidth, bounds.outHeight)?.let { throw IllegalArgumentException(it) }
        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 4800) sample *= 2
        val decoded = BitmapFactory.decodeFile(original.path, BitmapFactory.Options().apply { inSampleSize = sample }) ?: error("照片无法读取")
        val exif = ExifInterface(original)
        val matrix = Matrix().apply { if (exif.isFlipped) postScale(-1f, 1f); postRotate((exif.rotationDegrees + edit.rotation).toFloat()) }
        val oriented = Bitmap.createBitmap(decoded, 0, 0, decoded.width, decoded.height, matrix, true)
        if (oriented !== decoded) decoded.recycle()
        val area = cropPixels(oriented.width, oriented.height, edit.crop ?: NormRect.FULL)
        val cropped = Bitmap.createBitmap(oriented, area.x, area.y, area.width, area.height)
        if (cropped !== oriented) oriented.recycle()
        val (width, height) = scaledToMaxSide(cropped.width, cropped.height)
        val upload = if (width != cropped.width || height != cropped.height) cropped.scale(width, height) else cropped
        if (upload !== cropped) cropped.recycle()
        try {
            val quality = analyse(upload)
            val bytes = ImageLimits.JPEG_QUALITIES.asSequence().map { level -> ByteArrayOutputStream().also { check(upload.compress(Bitmap.CompressFormat.JPEG, level, it)) }.toByteArray() }
                .firstOrNull { it.size <= ImageLimits.UPLOAD_MAX_BYTES } ?: throw IllegalArgumentException(uploadTooLargeMessage(Long.MAX_VALUE))
            val file = AtomicFile(target); val output = file.startWrite()
            try { output.write(bytes); file.finishWrite(output) } catch (error: Exception) { file.failWrite(output); throw error }
            val sha = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
            return RenderedPage(upload.width, upload.height, bytes.size.toLong(), sha, quality)
        } finally { upload.recycle() }
    }

    private fun analyse(bitmap: Bitmap): QualityReport {
        val (width, height) = scaledToMaxSide(bitmap.width, bitmap.height, QualityReport.ANALYSIS_MAX_SIDE)
        val small = bitmap.scale(width, height)
        val pixels = IntArray(width * height)
        small.getPixels(pixels, 0, width, 0, 0, width, height)
        if (small !== bitmap) small.recycle()
        return assessQuality(IntArray(pixels.size) { lumaOf(pixels[it]) }, width, height)
    }
}

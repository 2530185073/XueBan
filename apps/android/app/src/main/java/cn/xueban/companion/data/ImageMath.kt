package cn.xueban.companion.data

import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.roundToInt
import org.json.JSONObject

/** Crop rectangle normalised to the page after EXIF orientation and the user's rotation. */
data class NormRect(val left: Float, val top: Float, val right: Float, val bottom: Float) {
    val width: Float get() = right - left
    val height: Float get() = bottom - top
    val isFull: Boolean get() = left <= 0f && top <= 0f && right >= 1f && bottom >= 1f
    fun toJson(): JSONObject = JSONObject().put("left", left.toDouble()).put("top", top.toDouble()).put("right", right.toDouble()).put("bottom", bottom.toDouble())

    companion object {
        val FULL = NormRect(0f, 0f, 1f, 1f)
        const val MIN_SIZE = 0.1f
        fun fromJson(value: JSONObject?): NormRect? = value?.let {
            clampCrop(NormRect(it.optDouble("left", 0.0).toFloat(), it.optDouble("top", 0.0).toFloat(), it.optDouble("right", 1.0).toFloat(), it.optDouble("bottom", 1.0).toFloat()))
        }
    }
}

data class PageEdit(val rotation: Int = 0, val crop: NormRect? = null) {
    init { require(rotation in listOf(0, 90, 180, 270)) { "旋转角度无效" } }
}

data class PixelRect(val x: Int, val y: Int, val width: Int, val height: Int)
enum class Corner { TopLeft, TopRight, BottomLeft, BottomRight }

fun normalizeRotation(degrees: Int): Int {
    require(degrees % 90 == 0) { "只能按 90° 旋转" }
    return ((degrees % 360) + 360) % 360
}

/** A point (x, y) on the page moves to (1 - y, x) when the page turns 90° clockwise. */
fun rotateCropClockwise(rect: NormRect): NormRect = NormRect(1f - rect.bottom, rect.left, 1f - rect.top, rect.right)

fun rotateCrop(rect: NormRect, fromRotation: Int, toRotation: Int): NormRect {
    val turns = normalizeRotation(toRotation - fromRotation) / 90
    return (0 until turns).fold(rect) { current, _ -> rotateCropClockwise(current) }
}

fun clampCrop(rect: NormRect, minSize: Float = NormRect.MIN_SIZE): NormRect {
    fun finite(value: Float, fallback: Float) = if (value.isFinite()) value.coerceIn(0f, 1f) else fallback
    var left = minOf(finite(rect.left, 0f), finite(rect.right, 1f)); var right = maxOf(finite(rect.left, 0f), finite(rect.right, 1f))
    var top = minOf(finite(rect.top, 0f), finite(rect.bottom, 1f)); var bottom = maxOf(finite(rect.top, 0f), finite(rect.bottom, 1f))
    if (right - left < minSize) { val center = ((left + right) / 2).coerceIn(minSize / 2, 1 - minSize / 2); left = center - minSize / 2; right = center + minSize / 2 }
    if (bottom - top < minSize) { val center = ((top + bottom) / 2).coerceIn(minSize / 2, 1 - minSize / 2); top = center - minSize / 2; bottom = center + minSize / 2 }
    return NormRect(left, top, right, bottom)
}

fun dragCorner(rect: NormRect, corner: Corner, dx: Float, dy: Float, minSize: Float = NormRect.MIN_SIZE): NormRect = when (corner) {
    Corner.TopLeft -> rect.copy(left = (rect.left + dx).coerceIn(0f, rect.right - minSize), top = (rect.top + dy).coerceIn(0f, rect.bottom - minSize))
    Corner.TopRight -> rect.copy(right = (rect.right + dx).coerceIn(rect.left + minSize, 1f), top = (rect.top + dy).coerceIn(0f, rect.bottom - minSize))
    Corner.BottomLeft -> rect.copy(left = (rect.left + dx).coerceIn(0f, rect.right - minSize), bottom = (rect.bottom + dy).coerceIn(rect.top + minSize, 1f))
    Corner.BottomRight -> rect.copy(right = (rect.right + dx).coerceIn(rect.left + minSize, 1f), bottom = (rect.bottom + dy).coerceIn(rect.top + minSize, 1f))
}

fun moveCrop(rect: NormRect, dx: Float, dy: Float): NormRect {
    val x = dx.coerceIn(-rect.left, 1f - rect.right); val y = dy.coerceIn(-rect.top, 1f - rect.bottom)
    return NormRect(rect.left + x, rect.top + y, rect.right + x, rect.bottom + y)
}

/** Pixel rectangle inside a width x height bitmap; rounding never shrinks the student's selection. */
fun cropPixels(width: Int, height: Int, rect: NormRect): PixelRect {
    require(width > 0 && height > 0)
    val safe = clampCrop(rect, 0f)
    val left = floor(safe.left * width).toInt().coerceIn(0, width - 1)
    val top = floor(safe.top * height).toInt().coerceIn(0, height - 1)
    val right = ceil(safe.right * width).toInt().coerceIn(left + 1, width)
    val bottom = ceil(safe.bottom * height).toInt().coerceIn(top + 1, height)
    return PixelRect(left, top, right - left, bottom - top)
}

fun scaledToMaxSide(width: Int, height: Int, maxSide: Int = ImageLimits.UPLOAD_MAX_SIDE): Pair<Int, Int> {
    val scale = minOf(1f, maxSide.toFloat() / maxOf(width, height))
    return (width * scale).roundToInt().coerceAtLeast(1) to (height * scale).roundToInt().coerceAtLeast(1)
}

object ImageLimits {
    const val SOURCE_MAX_BYTES = 20_000_000L
    const val SOURCE_MAX_PIXELS = 50_000_000L
    const val UPLOAD_MAX_BYTES = 4L * 1024 * 1024
    const val UPLOAD_MAX_SIDE = 2400
    const val MIN_FREE_BYTES = 64L * 1024 * 1024
    val JPEG_QUALITIES = listOf(92, 85, 75, 65)
}

fun sourceImageProblem(byteSize: Long, width: Int, height: Int): String? = when {
    byteSize <= 0 || width <= 0 || height <= 0 -> "照片无法读取，请重新拍照或换一张"
    byteSize > ImageLimits.SOURCE_MAX_BYTES -> "照片大于 20 MB，请换一张较小的照片或直接用相机拍"
    width.toLong() * height > ImageLimits.SOURCE_MAX_PIXELS -> "照片超过 5000 万像素，请换一张较小的照片或直接用相机拍"
    else -> null
}

fun uploadTooLargeMessage(byteSize: Long): String? =
    if (byteSize > ImageLimits.UPLOAD_MAX_BYTES) "这页压缩后仍超过 4 MB 上传上限，请裁掉空白部分或重拍" else null

fun storageProblem(usableBytes: Long, neededBytes: Long = ImageLimits.MIN_FREE_BYTES): String? =
    if (usableBytes < neededBytes) "平板存储空间不足（剩余约 ${usableBytes / 1_048_576} MB），请先清理空间再拍照；已保存的作业不受影响" else null

data class QualityReport(val meanLuma: Double, val sharpness: Double, val brightFraction: Double) {
    val tooDark: Boolean get() = meanLuma < DARK_MEAN
    val blurry: Boolean get() = sharpness < BLUR_VARIANCE
    val glare: Boolean get() = brightFraction > GLARE_FRACTION
    fun warnings(): List<String> = listOfNotNull(
        "照片偏暗，题目可能看不清，建议开灯或换个位置重拍".takeIf { tooDark },
        "照片可能模糊，建议拿稳后重拍".takeIf { blurry },
        "照片可能有反光或过曝，建议避开灯光重拍".takeIf { glare },
    )
    fun toJson(): JSONObject = JSONObject().put("mean_luma", meanLuma).put("sharpness", sharpness).put("bright_fraction", brightFraction)

    companion object {
        const val DARK_MEAN = 60.0
        const val BLUR_VARIANCE = 40.0
        const val GLARE_FRACTION = 0.35
        const val ANALYSIS_MAX_SIDE = 512
        fun fromJson(value: JSONObject?): QualityReport? = value?.let { QualityReport(it.optDouble("mean_luma"), it.optDouble("sharpness"), it.optDouble("bright_fraction")) }
    }
}

fun lumaOf(argb: Int): Int = (299 * ((argb shr 16) and 0xFF) + 587 * ((argb shr 8) and 0xFF) + 114 * (argb and 0xFF)) / 1000

/** Warning-only heuristic: mean luminance plus variance of the 4-neighbour Laplacian on a downscaled image. */
fun assessQuality(luma: IntArray, width: Int, height: Int): QualityReport {
    require(width > 0 && height > 0 && luma.size == width * height)
    val mean = luma.average()
    val bright = luma.count { it >= 250 }.toDouble() / luma.size
    if (width < 3 || height < 3) return QualityReport(mean, 0.0, bright)
    var sum = 0.0; var squares = 0.0; var count = 0
    for (y in 1 until height - 1) for (x in 1 until width - 1) {
        val i = y * width + x
        val value = (4 * luma[i] - luma[i - 1] - luma[i + 1] - luma[i - width] - luma[i + width]).toDouble()
        sum += value; squares += value * value; count++
    }
    val average = sum / count
    return QualityReport(mean, squares / count - average * average, bright)
}

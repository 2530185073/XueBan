package cn.xueban.companion.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ImageMathTest {
    private fun near(expected: NormRect, actual: NormRect) {
        listOf(expected.left to actual.left, expected.top to actual.top, expected.right to actual.right, expected.bottom to actual.bottom)
            .forEach { (e, a) -> assertEquals(e, a, 1e-5f) }
    }

    @Test fun cropFollowsThePageWhenRotatedInQuarterTurns() {
        val crop = NormRect(0.1f, 0.2f, 0.5f, 0.6f)
        near(NormRect(0.4f, 0.1f, 0.8f, 0.5f), rotateCropClockwise(crop))
        near(crop, rotateCrop(crop, 0, 360))
        near(crop, rotateCrop(rotateCrop(crop, 0, 90), 90, 0))
        near(rotateCrop(crop, 0, 270), rotateCrop(crop, 90, 0))
        near(NormRect(0.5f, 0.4f, 0.9f, 0.8f), rotateCrop(crop, 0, 180))
        val width = 400; val height = 300
        val point = 150f to 120f
        val rotated = (height - point.second) to point.first
        val turned = rotateCropClockwise(crop)
        assertTrue(point.first / width in crop.left..crop.right && point.second / height in crop.top..crop.bottom)
        assertTrue(rotated.first / height in turned.left..turned.right && rotated.second / width in turned.top..turned.bottom)
    }

    @Test fun cropPixelsNeverShrinkTheSelectionAndStayInsideTheBitmap() {
        assertEquals(PixelRect(100, 200, 450, 600), cropPixels(1000, 800, NormRect(0.1f, 0.25f, 0.55f, 1f)))
        assertEquals(PixelRect(0, 0, 1000, 800), cropPixels(1000, 800, NormRect.FULL))
        assertEquals(PixelRect(33, 0, 34, 10), cropPixels(100, 10, NormRect(0.333f, -1f, 0.667f, 2f)))
        val tiny = cropPixels(3, 3, NormRect(0.999f, 0.999f, 1f, 1f))
        assertTrue(tiny.width >= 1 && tiny.height >= 1 && tiny.x + tiny.width <= 3 && tiny.y + tiny.height <= 3)
    }

    @Test fun cropEditingKeepsAMinimumSizeAndStaysOnThePage() {
        near(NormRect(0.2f, 0.1f, 0.6f, 0.9f), clampCrop(NormRect(0.6f, 0.9f, 0.2f, 0.1f)))
        near(NormRect(0f, 0f, 1f, 1f), clampCrop(NormRect(Float.NaN, -3f, 4f, Float.POSITIVE_INFINITY)))
        val squeezed = clampCrop(NormRect(0.98f, 0.5f, 0.99f, 0.5f))
        assertEquals(NormRect.MIN_SIZE, squeezed.width, 1e-5f); assertEquals(1f, squeezed.right, 1e-5f)
        val dragged = dragCorner(NormRect.FULL, Corner.TopLeft, 0.95f, 0.3f)
        near(NormRect(0.9f, 0.3f, 1f, 1f), dragged)
        near(NormRect(0f, 0f, 0.1f, 1f), dragCorner(NormRect.FULL, Corner.BottomRight, -2f, 0.5f))
        near(NormRect(0.6f, 0f, 1f, 0.4f), moveCrop(NormRect(0.3f, 0.2f, 0.7f, 0.6f), 0.5f, -0.5f))
        assertEquals(NormRect(0.1f, 0.2f, 0.3f, 0.4f), NormRect.fromJson(NormRect(0.1f, 0.2f, 0.3f, 0.4f).toJson()))
        assertTrue(NormRect.FULL.isFull)
    }

    @Test fun rotationAndScalingSizes() {
        assertEquals(270, normalizeRotation(-90)); assertEquals(0, normalizeRotation(720))
        assertThrows(IllegalArgumentException::class.java) { normalizeRotation(45) }
        assertThrows(IllegalArgumentException::class.java) { PageEdit(rotation = 45) }
        assertEquals(2400 to 1800, scaledToMaxSide(4000, 3000))
        assertEquals(1800 to 2400, scaledToMaxSide(3000, 4000))
        assertEquals(800 to 600, scaledToMaxSide(800, 600))
    }

    private fun image(width: Int, height: Int, value: (Int, Int) -> Int) = IntArray(width * height) { value(it % width, it / width) }
    private fun checkerboard(size: Int = 64, cell: Int = 4) = image(size, size) { x, y -> if ((x / cell + y / cell) % 2 == 0) 30 else 220 }
    private fun boxBlur(source: IntArray, size: Int, radius: Int) = image(size, size) { x, y ->
        var sum = 0; var count = 0
        for (dy in -radius..radius) for (dx in -radius..radius) {
            val nx = x + dx; val ny = y + dy
            if (nx in 0 until size && ny in 0 until size) { sum += source[ny * size + nx]; count++ }
        }
        sum / count
    }

    @Test fun blurDarknessAndGlareHeuristicsOnSyntheticPixels() {
        val sharp = assessQuality(checkerboard(), 64, 64)
        assertFalse(sharp.blurry); assertFalse(sharp.tooDark); assertFalse(sharp.glare)
        assertTrue(sharp.warnings().isEmpty())
        val blurred = assessQuality(boxBlur(checkerboard(cell = 8), 64, 6), 64, 64)
        assertTrue(blurred.sharpness < sharp.sharpness)
        assertTrue(blurred.blurry)
        val flat = assessQuality(IntArray(64 * 64) { 128 }, 64, 64)
        assertEquals(0.0, flat.sharpness, 1e-9); assertTrue(flat.blurry)
        val dark = assessQuality(checkerboard().map { it / 6 }.toIntArray(), 64, 64)
        assertTrue(dark.tooDark); assertFalse(dark.blurry)
        assertEquals(listOf("照片偏暗，题目可能看不清，建议开灯或换个位置重拍", "照片可能模糊，建议拿稳后重拍"), dark.warnings() + flat.warnings())
        val glare = assessQuality(image(64, 64) { x, y -> if (x < 40) 255 else if ((x + y) % 2 == 0) 0 else 200 }, 64, 64)
        assertTrue(glare.glare)
        assertEquals(sharp, QualityReport.fromJson(sharp.toJson()))
        assertEquals(255, lumaOf(0xFFFFFFFF.toInt())); assertEquals(0, lumaOf(0xFF000000.toInt())); assertEquals(76, lumaOf(0xFFFF0000.toInt()))
        assertThrows(IllegalArgumentException::class.java) { assessQuality(IntArray(3), 2, 2) }
    }

    @Test fun sizeAndStorageMessagesAreSpecific() {
        assertNull(sourceImageProblem(5_000_000, 4000, 3000))
        assertTrue(sourceImageProblem(20_000_001, 4000, 3000)!!.contains("20 MB"))
        assertTrue(sourceImageProblem(5_000_000, 10_000, 6_000)!!.contains("5000 万像素"))
        assertTrue(sourceImageProblem(0, 0, 0)!!.contains("无法读取"))
        assertNull(uploadTooLargeMessage(ImageLimits.UPLOAD_MAX_BYTES))
        assertTrue(uploadTooLargeMessage(ImageLimits.UPLOAD_MAX_BYTES + 1)!!.contains("4 MB"))
        assertNull(storageProblem(ImageLimits.MIN_FREE_BYTES))
        assertTrue(storageProblem(10L * 1_048_576)!!.contains("剩余约 10 MB"))
    }

    @Test fun cameraPermissionStatesOfferTheRightRecovery() {
        assertEquals(CameraAccess.Granted, cameraAccess(granted = true, askedBefore = true, showRationale = false))
        assertEquals(CameraAccess.NotRequested, cameraAccess(granted = false, askedBefore = false, showRationale = false))
        assertEquals(CameraAccess.Denied, cameraAccess(granted = false, askedBefore = true, showRationale = true))
        assertEquals(CameraAccess.Denied, cameraAccess(granted = false, askedBefore = false, showRationale = true))
        assertEquals(CameraAccess.PermanentlyDenied, cameraAccess(granted = false, askedBefore = true, showRationale = false))
    }

    @Test fun failuresMapToSessionActionsExceptImageScopedTicketErrors() {
        assertEquals(FailureKind.SessionExpired, classifyFailure(ApiFailure(401, "authentication_required", "")))
        assertEquals(FailureKind.PasswordChangeRequired, classifyFailure(ApiFailure(403, "password_change_required", "")))
        assertEquals(FailureKind.AccessDenied, classifyFailure(ApiFailure(403, "access_denied", "")))
        assertEquals(FailureKind.Other, classifyFailure(ApiFailure(401, "upload_failed", "")))
        assertEquals(FailureKind.Other, classifyFailure(ApiFailure(401, "image_unavailable", "")))
        assertEquals(FailureKind.Other, classifyFailure(ApiFailure(503, "http_503", "")))
        assertEquals(FailureKind.Other, classifyFailure(java.io.IOException("offline")))
    }

    @Test fun onlyServerConfirmedPagesCountAsUploaded() {
        fun page(confirmed: Boolean) = JSONObject().put("id", "p").apply { if (confirmed) put("asset", JSONObject().put("status", "uploaded")) else put("asset", JSONObject().put("status", "awaiting_upload")) }
        val state = JSONObject().put("id", "record").put("pages", JSONArray().put(page(true)).put(page(false)))
        listOf("local", "queued", "creating").forEach { assertTrue(it, recordStageLabel(state.put("stage", it), emptyList()).startsWith("未上传")) }
        assertEquals("上传中 · 服务器已确认 1/2 页", recordStageLabel(state.put("stage", "uploading"), emptyList()))
        assertTrue(recordStageLabel(state.put("stage", "failed").put("homework_id", "homework_a"), emptyList()).startsWith("未上传完成"))
        state.put("pages", JSONArray().put(page(true)))
        assertEquals("部分处理未完成 · 原图已保留", recordStageLabel(state, emptyList()))
        assertEquals("部分处理未完成 · 原图已保留", recordStageLabel(JSONObject().put("id", "remote_x").put("stage", "failed").put("homework_id", "homework_a"), emptyList()))
    }
}

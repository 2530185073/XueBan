package cn.xueban.companion.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import cn.xueban.companion.CaptureViewModel
import cn.xueban.companion.data.Corner
import cn.xueban.companion.data.NormRect
import cn.xueban.companion.data.PageEdit
import cn.xueban.companion.data.dragCorner
import cn.xueban.companion.data.moveCrop
import cn.xueban.companion.data.normalizeRotation
import cn.xueban.companion.data.rotateCrop
import kotlin.math.hypot
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@Composable
fun PageEditor(model: CaptureViewModel, pageId: String, onDone: () -> Unit) {
    val page = model.draft?.pages?.firstOrNull { it.id == pageId }
    val original = page?.original
    if (page == null || original == null || !page.ready) {
        Text("这一页已不存在或还在处理，请返回草稿。")
        TextButton(onClick = onDone) { Text("返回") }
        return
    }
    var rotation by remember(pageId) { mutableIntStateOf(page.edit.rotation) }
    var crop by remember(pageId) { mutableStateOf(page.edit.crop ?: NormRect.FULL) }
    val preview by produceState<ImageBitmap?>(null, original.path, rotation) {
        value = withContext(Dispatchers.IO) { runCatching { decodePreview(original, 1600, rotation) }.getOrNull() }
    }
    fun turn(degrees: Int) {
        val next = normalizeRotation(rotation + degrees)
        crop = rotateCrop(crop, rotation, next); rotation = next
    }

    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("旋转和裁切", style = MaterialTheme.typography.headlineSmall)
        Text("原图会原样保留；上传检查的是调整后的这一页。拖动四个角只保留作业区域，拖动框内可以整体移动。")
        BoxWithConstraints(Modifier.fillMaxWidth().height(480.dp)) {
            val bitmap = preview
            if (bitmap == null) Text("正在读取原图…")
            else {
                val density = LocalDensity.current
                val boxWidth = with(density) { maxWidth.toPx() }; val boxHeight = with(density) { maxHeight.toPx() }
                val fit = minOf(boxWidth / bitmap.width, boxHeight / bitmap.height)
                val imageWidth = bitmap.width * fit; val imageHeight = bitmap.height * fit
                val left = (boxWidth - imageWidth) / 2; val top = (boxHeight - imageHeight) / 2
                val touch = with(density) { 40.dp.toPx() }
                Box(Modifier.fillMaxSize()) {
                    Image(bitmap, "待裁切的作业照片", Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
                    Canvas(Modifier.fillMaxSize().pointerInput(bitmap, rotation, imageWidth, imageHeight) {
                        var target: Corner? = null; var moving = false
                        detectDragGestures(
                            onDragStart = { start ->
                                val corners = mapOf(
                                    Corner.TopLeft to Offset(left + crop.left * imageWidth, top + crop.top * imageHeight),
                                    Corner.TopRight to Offset(left + crop.right * imageWidth, top + crop.top * imageHeight),
                                    Corner.BottomLeft to Offset(left + crop.left * imageWidth, top + crop.bottom * imageHeight),
                                    Corner.BottomRight to Offset(left + crop.right * imageWidth, top + crop.bottom * imageHeight),
                                )
                                target = corners.minByOrNull { (_, point) -> hypot(point.x - start.x, point.y - start.y) }
                                    ?.takeIf { (_, point) -> hypot(point.x - start.x, point.y - start.y) <= touch }?.key
                                val x = (start.x - left) / imageWidth; val y = (start.y - top) / imageHeight
                                moving = target == null && x in crop.left..crop.right && y in crop.top..crop.bottom
                            },
                            onDragEnd = { target = null; moving = false },
                            onDrag = { change, amount ->
                                change.consume()
                                val dx = amount.x / imageWidth; val dy = amount.y / imageHeight
                                val corner = target
                                if (corner != null) crop = dragCorner(crop, corner, dx, dy) else if (moving) crop = moveCrop(crop, dx, dy)
                            },
                        )
                    }) {
                        val rectLeft = left + crop.left * imageWidth; val rectTop = top + crop.top * imageHeight
                        val rectWidth = crop.width * imageWidth; val rectHeight = crop.height * imageHeight
                        val shade = Color(0x88000000)
                        drawRect(shade, Offset(left, top), Size(imageWidth, rectTop - top))
                        drawRect(shade, Offset(left, rectTop + rectHeight), Size(imageWidth, top + imageHeight - rectTop - rectHeight))
                        drawRect(shade, Offset(left, rectTop), Size(rectLeft - left, rectHeight))
                        drawRect(shade, Offset(rectLeft + rectWidth, rectTop), Size(left + imageWidth - rectLeft - rectWidth, rectHeight))
                        drawRect(Color.White, Offset(rectLeft, rectTop), Size(rectWidth, rectHeight), style = Stroke(3f))
                        listOf(Offset(rectLeft, rectTop), Offset(rectLeft + rectWidth, rectTop), Offset(rectLeft, rectTop + rectHeight), Offset(rectLeft + rectWidth, rectTop + rectHeight))
                            .forEach { drawCircle(Color.White, radius = 14f, center = it) }
                    }
                }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(enabled = !model.busy, onClick = { turn(-90) }) { Text("向左转") }
            OutlinedButton(enabled = !model.busy, onClick = { turn(90) }) { Text("向右转") }
            TextButton(enabled = !model.busy, onClick = { crop = NormRect.FULL }) { Text("不裁切") }
        }
        Button(enabled = !model.busy && preview != null, onClick = { model.editPage(pageId, PageEdit(rotation, crop.takeUnless { it.isFull }), onDone) }) {
            Text(if (model.busy) "正在保存…" else "保存这一页")
        }
        TextButton(enabled = !model.busy, onClick = onDone) { Text("不保存，返回") }
    }
}

package cn.xueban.companion

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import cn.xueban.companion.data.LocalCapture
import cn.xueban.companion.data.LocalCaptureRepository
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class CaptureViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = LocalCaptureRepository(application)
    var captures by mutableStateOf<List<LocalCapture>>(emptyList())
        private set
    var loading by mutableStateOf(true)
        private set
    var message by mutableStateOf<String?>(null)
        private set
    var backendAddress by mutableStateOf(repository.backendAddress())
        private set

    init { refresh() }

    fun refresh() = viewModelScope.launch {
        loading = true
        runCatching { withContext(Dispatchers.IO) { repository.listCaptures() } }
            .onSuccess { captures = it }
            .onFailure { message = it.message ?: "本地记录读取失败" }
        loading = false
    }

    fun beginCapture(): File {
        check(BuildConfig.DEBUG) { "当前版本未开放采集" }
        return repository.beginCapture()
    }

    fun finishCapture(file: File, onComplete: (LocalCapture?) -> Unit) = viewModelScope.launch {
        runCatching { withContext(Dispatchers.IO) { repository.completeCapture(file) } }
            .onSuccess { capture ->
                captures = (captures.filterNot { it.id == capture.id } + capture).sortedByDescending { it.capturedAt }
                onComplete(capture)
            }
            .onFailure { message = it.message ?: "照片保存失败"; onComplete(null) }
    }

    fun abandonCapture(file: File) = viewModelScope.launch(Dispatchers.IO) {
        runCatching { repository.abandonCapture(file) }
    }

    fun deleteCapture(id: String) = viewModelScope.launch {
        runCatching { withContext(Dispatchers.IO) { repository.deleteCapture(id) } }
            .onSuccess { captures = captures.filterNot { it.id == id } }
            .onFailure { message = it.message ?: "删除失败" }
    }

    fun saveBackendAddress(value: String) = viewModelScope.launch {
        runCatching { withContext(Dispatchers.IO) { repository.saveBackendAddress(value) } }
            .onSuccess { backendAddress = value.trim(); message = "地址已保存；身份授权和联网尚未接入，不会上传" }
            .onFailure { message = it.message ?: "地址保存失败" }
    }

    fun clearMessage() { message = null }
}

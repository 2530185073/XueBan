# 学伴 Android 0.2.0 联调版

安卓平板学生端，支持 CameraX 拍照和系统“从相册选择”，使用已授权身份把学生自己的作业提交给后端真实模型处理。没有内置供应商 Key、假检查结果或本地 OCR；一期走服务端多模态模型，专用 OCR 留后续阶段。

## 本轮实现

- 服务器地址和一次性配对码 → `POST /v1/device-sessions` → `GET /v1/me`。只能选择服务端授权学生，无自由输入 studentId 登录。启动时需重新读取 me 确认授权；断网时可点击恢复已有配对连接，不强迫消耗新配对码。配对请求首次发送前持久保存随机幂等键；网络结果未知时沿原码、原键恢复。交换成功先保存 token，再读 me；me 成功后清除配对码和待请求键。
- Android Keystore AES/GCM 加密私有凭据文件，禁止系统备份。401/到期要求重新配对；无自动刷新 token。退出配对清本机凭据与可见身份，原图继续保留，重新授权同一账号/学生后可见。服务端 session 撤销由服务端管理。
- CameraX 相机权限、后摄预览、拍照；系统 OpenDocument 支持 JPEG/PNG，读取后复制进应用私有区。20 MB/5000 万像素上限。原始字节保留；上传派生图按 EXIF 正向、最长边 2400、JPEG 92% 生成，以派生图尺寸/哈希为服务端坐标基础。
- 原图 → 创建作业 → 私有上传票据 → PUT → complete 核验 → recognize job → 逐 case checks job → 轮询 → 整页/单题原图、题区、识别文字、AI 初判、不确定和失败状态。任务成功不表示答对；只有服务端 staff/confirmed 才显示老师确认。学生端不直接展开可能含解法的 basis；帮助通过有曝光记录的提示接口提供。
- 提示的真实 job、实际显示后的 views 记录；本地 hint_used 保留为证据，提交订正时按契约映射为 unknown（兼容已有草稿），由服务端已确认 views 继承提示使用，不能自行宣称无帮助；重新拍订正建立新 Attempt 并再次检查。识别有问题通过单独的 recognition 求助入口，不伪造成孩子新作答。
- 私有 `AtomicFile` JSON 操作账本先保存精确 body 和幂等键，再发送；成功响应也存档。网络/超时未知结果保留原操作，409/422 明确拒绝后才能在用户重试时换键/刷新版本。照片、作业/图片/任务 ID 和阶段可重启恢复。400/409/422 等确定拒绝会标记 failed 并清除自动恢复目标，返回前台不抢占新照片导入；网络结果未知、408/429/5xx 保留原请求恢复。终态 failed 只在用户点击后创建新任务；retryable_failed 由服务端有限重试，客户端继续轮询。检查恢复先比较题目/作答版本并复用服务端现有任务。
- 本机历史与服务器分页历史；不同 server/actor/student 分区可见。相机写原图后中断可在重启授权后恢复处理。

## 启动与配对

1. 启动后端 API、持久 worker、数据库，并启用管理员签发配对码能力；模型凭据只配置在后端。
2. 管理员为已授权账号生成一次性配对码。App 填服务器根地址（不含 `/v1`）、配对码，确认孩子。
3. 从相机或相册选择一页作业，预览后点“上传并检查”。也可从历史恢复。

Release 只允许 HTTPS。Debug 额外允许字面 RFC1918/loopback IP 的内网 HTTP（如模拟器 `http://10.0.2.2:8000`；adb reverse 后 `http://127.0.0.1:8000`；真机填 Mac 内网 IP），设置页有醒目联调提醒。HTTP 域名包括 localhost 不允许；禁止 URL userinfo/query/fragment。图片票据必须与 API origin 完全一致；不跟随重定向，不向存储请求自动附加业务 Bearer。后端 PUBLIC_BASE_URL 必须是设备可达的同一 origin。

## 后端接口依赖

沿用 `contracts/phase1`：

| 动作 | 接口 / 返回 |
| --- | --- |
| 配对 | POST `/v1/device-sessions` `{pairing_code}` + `Idempotency-Key` → `{access_token,expires_at}` |
| 身份 | GET `/v1/me` → `IdentityContext` |
| 作业/历史 | POST/GET `/v1/students/{id}/homeworks`；GET `/v1/homeworks/{id}` |
| 图片 | POST `/v1/homeworks/{id}/uploads` → `UploadTicket`；PUT 票据 URL；GET asset、POST complete、POST upload-ticket 续期、GET access |
| 识别 | POST `/v1/homeworks/{id}/recognitions` → `Accepted.job`；GET `/v1/jobs/{id}` |
| 逐题检查 | GET cases/questions/attempts/checks；POST cases/{id}/checks → job；当前有效结论读取 case.current_decision |
| 提示 | POST cases/{id}/hints → job → GET hints/{id}；实际展示后 POST hints/{id}/views |
| 新订正 | 上传到原 homework 后 POST cases/{id}/attempts → recognize_attempt job → 最新 case/attempt → checks |
| 求助 | POST cases/{id}/help-requests；只有服务端写入成功才显示求助记录 |

所有业务写操作持久幂等，使用当前聚合/识别 revision。未实现的接口或失败均展示真实失败，不虚构完成。

## 构建与测试

JDK 17、Android SDK Platform 35、AGP 8.9.2、Kotlin 2.1.20、Gradle Wrapper 8.11.1。新增网络库 OkHttp 4.12.0，无本地模型依赖。

```sh
cd apps/android
./gradlew :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
# APK: app/build/outputs/apk/debug/app-debug.apk
```

2026-10-17 已在本机 JDK 17 / SDK 35 执行上述三项任务，最终增量构建 `BUILD SUCCESSFUL`，13 项 JVM 测试全部通过。Lint 无错误，14 项警告主要为依赖升级提示，另含备份声明、应用图标与 KTX 建议；本轮未为消除升级提示改变已验证的工具链。

纯 JVM 测试覆盖 URL/同源规则、模型候选和确认区分、识别待核实/空题/未检查状态、幂等请求恢复、题目/作答版本隔离、直接读取仓库契约核对 reported_help 枚举及旧草稿兼容，以及 MockWebServer 鉴权/票据隔离/HTTP 错误/不跟随跳转。Keystore、CameraX、系统选图、真实断网/杀进程恢复仍需模拟器/真机验证。实际联调证据由主任务补充，不能把构建或 mock 测试当成完整验收。

## 当前边界

这是 0.2.0 联调版本，不是一期完整验收完成。协调器在前台运行；切后台暂停，重回前台/重启后恢复，不承诺系统后台持续上传（尚未加入 WorkManager）。网络调用有 60 秒整体上限；任务轮询单轮最多 300 次，此后保留任务等待手动继续。单次拍照作为一份作业；目前无多页合批、手工裁切、识别文本直接编辑、完整的跨设备多页图切换。服务器历史先下载首张照片；本机仍保留每次订正图片。单题页依据当前 Attempt.asset_ids 选择本机作答图及对应题区；未加载本次图片时明确标注原题照片，避免旧图冒充新作答。原图与规范化上传图均需按后续正式保留/删除策略清理，当前无学生自行删除服务端记录入口。

Android Keystore 与网络安全依据：[Keystore](https://developer.android.com/privacy-and-security/keystore)、[Network security configuration](https://developer.android.com/privacy-and-security/security-config)。

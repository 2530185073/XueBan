# 学伴 Android 0.3.0 联调版

安卓平板学生端：学生用自己的账号密码登录（与学生网页端同一账号、同一 `student_id`），用 CameraX 或系统照片选择器把一份作业的多页照片整理好、旋转裁切、确认页数后提交；上传、识别和逐题检查由 WorkManager 在后台按持久操作账本继续。没有内置供应商 Key、假检查结果或本地 OCR；一期走服务端多模态模型。

**0.3.0 是工程交付，不是一期验收完成。** 下文“待真机验收”列出的项目全部没有在设备上运行过；本机没有模拟器或真机，构建、JVM 测试和 Lint 不能代替真机验证。

## 本轮实现（P1-03 / P1-13 / P1-16 工程部分）

### 账号登录（替代配对，P1-02 产品决定）

- 登录页：服务器地址 + 用户名 + 密码 → `POST /v1/auth/login` → `GET /v1/auth/account` → `GET /v1/me`。只接受绑定了 `student_id` 的学生账号，并要求该学生在 `/v1/me.authorized_students` 中；老师/管理员账号在平板上被拒绝。界面不再提供切换学生入口。
- 服务器地址规则不变：Release 只允许 HTTPS；Debug 额外允许字面 RFC1918/loopback IP 的 HTTP，并显示联调提醒；禁止 userinfo/query/fragment/路径。
- 幂等登录：发送前把 `{server, username, Idempotency-Key}` 写入 Keystore 加密的 `SessionVault`，**不保存密码**。网络结果未知时，用户以相同账号密码再点登录即沿用原键原正文，服务端重放同一会话；换账号或服务器则换新键；400/401/403/409/422/429 等确定结果清除待定键。后端只在密码校验通过后比对请求指纹，所以“同键 + 正确密码”必然是同一正文。
- 首次登录/被重置后强制改密：登录返回或 `/v1/auth/account` 显示 `must_change_password` 时进入改密页（15–128 字符，两次输入一致，新旧不同）。改密键同样先持久化、不存密码；结果未知可用相同内容重放；服务端改密后旧会话全部撤销，App 改用新 token 并重新核对账号与学生。
- 退出登录：先 `POST /v1/auth/logout`（Bearer + 幂等键 + `{}`），无论服务器是否确认都清除本机 token/身份，仅保留服务器地址和用户名用于下次填表；未确认时明确提示“服务器尚未确认会话撤销”。有未上传作业时先弹窗说明“退出会暂停上传”。
- 会话失效：任何业务接口（前台或后台）返回 401 → 本机结束会话回到登录页，提示本机未上传的作业已保留；403 `password_change_required` → 改密页；其他 403 → 结束会话。图片票据/存储失败（`upload_failed`/`image_unavailable`）只影响那一页，不会登出。
- 离线启动：token 未过期、服务器暂时不可达时，用上次核对过的账号进入离线模式，可继续拍照保存，提交后等联网自动上传；检查结果明确需要联网。
- 本机照片仍按 server/actor/student 分区保存。退出、过期都不删除本机作业；重新登录同一账号后可见并继续上传，其他账号看不到。
- 迁移：0.2.0 的配对会话（含待用配对码）在首次启动时被丢弃，只保留服务器地址；**配对不再是正式入口，代码与界面已移除**。若后端把原配对身份原位绑定到账号（见 `docs/ACCOUNT_FOUNDATION.md`），actor/student 不变，旧本机记录登录后照常可见。

### 多页作业

- 一份作业可拍多页（相机逐页拍，或照片选择器一次多选最多 10 张），草稿中可上移/下移、删除、逐页旋转裁切。每页显示质量提醒。
- 提交前弹窗确认“这份作业一共 N 页吗？”，提醒翻一下作业本防漏拍；页数变化后必须重新确认。提交后照片锁定不可再改。
- 上传时按草稿顺序逐页：创建上传票据（`page_index` = 基准 + 位置；新作业基准为 0，订正追加到原作业时基准为原作业现有图片数，首次计算后固化在账本）→ PUT → `complete` 核验，每页独立的幂等操作 `upload-<页ID>` / `renew-<页ID>-n` / `complete-<页ID>`。全部页确认后，`recognitions`（或订正的 `CreateCorrection`）一次带上全部 `asset_ids`，顺序与页序一致。
- 每页原图原样保留在应用私有目录（`<页ID>-original.jpg`），上传的是派生图（`<页ID>-upload.jpg`），页关系、旋转裁切参数、尺寸、SHA-256、服务端 asset 状态都写在账本中。服务器历史作业按服务器页序最多缓存 12 页。

### 预览旋转与裁切

- 草稿每页可 90° 左右旋转、拖四角或整体移动矩形裁切框、一键取消裁切。保存后从**未改动的原图**重新生成上传图：EXIF 方向 → 学生旋转 → 裁切 → 最长边 2400 → JPEG（92/85/75/65 逐级压到 ≤4 MiB），尺寸与哈希随之更新，服务端题区坐标以该派生图为准。
- 选型：使用约 150 行 Compose 实现，没有引入 CanHub/vanniktech Android-Image-Cropper（4.6.0）。理由：该库基于 View 并带入 AppCompat 等依赖，自有解码/输出管线，裁切结果需再与本项目“原图不动、派生图记录裁切参数与哈希”的账本对齐；我们只需要矩形裁切和 90° 旋转，坐标数学（旋转时裁切框跟随、像素取整不缩小选区、最小尺寸、边界钳制）全部是纯函数并有单元测试。

### 拍摄质量与失败恢复

- 相机权限：首次说明用途；拒绝后可再次请求；“不再询问”/系统关闭时引导打开系统设置；三种状态下都可改用照片选择器（照片选择器无需存储权限）。设备无后置相机或相机启动失败时同样提示改用相册。
- 存储不足：新建草稿或每拍一页前用 `StorageManager.getAllocatableBytes` 检查，少于 64 MB 时提示剩余空间并拒绝写入；CameraX 写文件失败（`ERROR_FILE_IO`）也提示可能存储不足。
- 模糊/过暗/反光：派生图缩到最长边 512 后计算平均亮度与 4 邻域拉普拉斯方差，以及近饱和像素比例；偏暗（均值 <60）、可能模糊（方差 <40）、可能反光（≥250 的像素 >35%）只作提醒，**从不阻止提交**。阈值是经验初值，需在真机样本上校准。
- 超大图片：原图 >20 MB 或 >5000 万像素、派生图压到 65 质量仍 >4 MiB（服务端上限）都会给出具体提示；服务端 `image_too_large` 也映射为明确文案。
- 中断恢复：每页先在账本登记再写原图；进程被杀后，下次登录时原图已落盘的页自动补完派生图，未落盘的页条目被移除。

### 后台继续（WorkManager）

- 唯一后台任务 `correction-sync`（`CoroutineWorker`，需联网约束，指数退避起始 30 秒）。提交草稿、点“立即继续/重试”、登录成功、回到前台时调度；切后台或进程被杀后由 WorkManager 恢复执行。
- 执行逻辑在纯 JVM 类 `BackgroundSync` 中：只处理已提交（非草稿）的本账号记录；沿用账本中已保存的幂等键与正文；只有服务端确认的响应才推进阶段；单次运行 8 分钟预算，轮询未完成则交给 WorkManager 稍后重试。网络中断/超时/408/425/429/5xx → 保留原操作并重试；409/422 等确定拒绝 → 标记失败等待学生点重试（届时才换键/刷新版本）；401/403 → 结束本机会话并停止后台任务，待重新登录后继续。
- 前台不再直接跑上传/识别协调器，只调度同一个后台任务并观察账本变化，因此前后台行为一致，不会两处同时写。提示、求助、提示曝光记录仍在前台即时执行。仓库/凭据在进程内单例，WorkManager 与界面共用同一把日志锁；同进程内多个 Worker 实例由互斥锁串行。
- 状态文案：未经服务端确认的记录一律标“未上传”（草稿、已提交待上传、正在建立作业记录、上传未完成的失败）；上传中显示“服务器已确认 x/N 页”。

### 账本版本化

- 每条记录 `record.json` 带 `schema_version`（当前 2）。读取 0.2.0 的旧格式（隐式版本 1，单张照片字段在顶层）时自动迁移为第 1 页 `page_0`：文件仍指向原 `original.jpg`/`upload.jpg`，`upload`/`complete`/`renew-n` 操作改名为对应页的操作名，**幂等键、路径、正文、已存响应逐字保留**，迁移后会用原键继续未确认的写入，不会重复创建。迁移幂等；遇到更新版本 App 写入的记录会拒绝读取而不是误改。
- 会话文件同样带 `format`，0.2.0 配对会话按上文规则迁移。

### Release 构建

- `versionCode 3` / `versionName 0.3.0`。Release 开启 R8 代码压缩与资源压缩（`proguard-android-optimize.txt` + `proguard-rules.pro`，仅保留行号用于定位崩溃）。OkHttp/Okio、Kotlin 协程、WorkManager、Compose 自带 consumer 规则；`org.json` 来自系统框架，无需规则；已核对 R8 seeds 中保留了 `CorrectionSyncWorker(Context, WorkerParameters)`。
- 签名从环境变量或 Gradle 属性读取：`XUEBAN_KEYSTORE_PATH`、`XUEBAN_KEYSTORE_PASSWORD`、`XUEBAN_KEY_ALIAS`、`XUEBAN_KEY_PASSWORD`。四项齐全才签名，否则产出 `app-release-unsigned.apk`。密钥不入仓库（`.gitignore` 已排除 `*.jks`/`*.keystore`）。
- 备份：`allowBackup=false`，并新增 `dataExtractionRules`/`fullBackupContent` 全部排除，Android 12+ 设备迁移也不带走凭据与作业。

## 启动与登录

1. 启动后端 API、持久 worker、数据库；模型凭据只配置在后端。管理员在学员账号页开户（见 `docs/ACCOUNT_FOUNDATION.md`）。
2. App 填服务器根地址（不含 `/v1`）、学生用户名与临时密码登录，按提示设置新密码。
3. 首页“拍作业 / 从相册选择”→ 逐页拍或多选 → 必要时旋转裁切 → “拍完了，确认页数并上传”。可直接离开页面或切到后台，进度在历史中查看。
4. 共用平板用完点“退出登录”。

后端 `PUBLIC_BASE_URL` 必须是设备可达、与 App 填写地址同一 origin；图片票据必须与 API origin 完全一致，不跟随重定向，不向存储请求附加业务 Bearer。

## 后端接口依赖

纠错接口沿用 `contracts/phase1`；账号接口由后端 `account_auth.py` 提供（尚未写入 `contracts/phase1`）。

| 动作 | 接口 / 返回 |
| --- | --- |
| 登录 | POST `/v1/auth/login` `{username,password}` + `Idempotency-Key` → `{access_token,expires_at,must_change_password}` |
| 账号状态 | GET `/v1/auth/account` → `{username,must_change_password,student_id}` |
| 改密 | POST `/v1/auth/change-password` `{current_password,new_password}` + 键 → 新会话 |
| 退出 | POST `/v1/auth/logout` `{}` + 键 → `{logged_out:true}` |
| 身份 | GET `/v1/me` → `IdentityContext` |
| 作业/历史 | POST/GET `/v1/students/{id}/homeworks`；GET `/v1/homeworks/{id}` |
| 图片 | 每页 POST `/v1/homeworks/{id}/uploads`（`page_index`）→ `UploadTicket`；PUT 票据 URL；GET asset、POST complete、POST upload-ticket 续期、GET access |
| 识别 | POST `/v1/homeworks/{id}/recognitions`（全部 `asset_ids`）→ `Accepted.job`；GET `/v1/jobs/{id}` |
| 逐题检查 | GET cases/questions/attempts/checks；POST cases/{id}/checks → job |
| 提示 | POST cases/{id}/hints → job → GET hints/{id}；实际展示后 POST hints/{id}/views |
| 新订正 | 各页上传到原 homework 后 POST cases/{id}/attempts（全部 `asset_ids`）→ recognize_attempt job → checks |
| 求助 | POST cases/{id}/help-requests；只有服务端写入成功才显示求助记录 |

所有业务写操作持久幂等，使用当前聚合/识别 revision。失败一律展示真实状态，不虚构完成。

## 构建与测试

JDK 17、Android SDK Platform 35、AGP 8.9.2、Kotlin 2.1.20、Gradle Wrapper 8.11.1。本轮新增依赖：

| 依赖 | 版本 | 用途 |
| --- | --- | --- |
| `androidx.work:work-runtime-ktx` | 2.10.5 | 后台续传/轮询；2.10.x 是兼容 compileSdk 35 的稳定线 |
| `androidx.core:core-ktx` | 1.13.1 | 原已作为传递依赖解析到同一版本；显式声明以使用 `androidx.core.util.AtomicFile`（与框架 AtomicFile 文件格式兼容、可在 JVM 测试中运行）和 KTX 扩展 |

未引入 Room：账本已是带版本号的原子 JSON 文件，改用 Room 需要另一套迁移，本轮以版本化 JSON 满足“可迁移”要求。未引入 `work-testing`：它在 JVM 上需要 Robolectric 或仪器测试；Worker 只做结果映射，调度逻辑放在 `BackgroundSync` 中用 MockWebServer 测试。

```sh
export JAVA_HOME=~/.local/share/xueban/toolchain/jdk/Contents/Home
export ANDROID_HOME=~/.local/share/xueban/toolchain/sdk
cd apps/android
./gradlew --no-daemon :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
./gradlew --no-daemon :app:assembleRelease          # 未设签名变量 → app-release-unsigned.apk
XUEBAN_KEYSTORE_PATH=/secure/xueban.jks XUEBAN_KEYSTORE_PASSWORD=… XUEBAN_KEY_ALIAS=… XUEBAN_KEY_PASSWORD=… \
  ./gradlew --no-daemon :app:assembleRelease        # → app-release.apk（签名）
```

2026-10-17 本机执行结果：上述任务 `BUILD SUCCESSFUL`；JVM 测试 58 项全部通过；Lint 0 错误、13 警告（12 项依赖升级提示、1 项缺少应用图标，本轮未改动已验证的工具链版本）；Debug APK 约 13.0 MB，未签名 Release APK 约 2.3 MB；另用临时自签密钥验证了四个签名变量齐全时产出经 `apksigner verify` 通过的签名 APK（临时密钥已删除）。CI（`.github/workflows/android-checks.yml`）新增未签名 Release 构建并归档 APK 与 R8 mapping。

JVM 测试覆盖：账号登录/核对学生/拒绝非学生账号、丢失登录响应后同键同正文重放且不存密码、换账号换键、强制改密与改密重放、错误当前密码不登出、退出（服务器确认/未确认）、会话过期保留表单信息、0.2.0 配对会话迁移；多页按确认顺序上传（`page_index`、`expected_revision`、SHA-256、PUT 字节和无 Bearer）、识别带全部 asset、丢失上传响应后重放不重复建 asset；0.2.0 账本迁移（操作改名但键/正文不变、幂等、拒绝新版本、中断拍摄、服务器缓存）及迁移后用原键续传；后台同步（草稿不上传、401 停止且保留未上传记录并在重新登录后同键续传、强制改密暂停、未知结果重试且不标完成、确定拒绝标失败并可重试、预算耗尽请求重试、未登录不动作）；草稿编辑锁定、存储不足、中断恢复、身份分区；裁切/旋转坐标、模糊/过暗/反光启发式（合成像素）、尺寸与存储提示、相机权限状态、错误分类、“未上传”文案；以及 0.2.0 已有的 URL/同源、提示与订正契约、版本隔离测试。

## 待真机验收

以下均**未在设备上验证**，需在声明的平板上逐项执行并记录：

- 在声明设备上安装 Debug 与签名 Release，覆盖安装 0.2.0 → 0.3.0（同签名）后账本与会话迁移、旧照片仍可见并能续传。
- CameraX 在目标硬件上的预览、对焦、拍摄方向/EXIF、横竖屏旋转；照片选择器（含无 Google Play 照片选择器时回退到系统文档选择）多选。
- 相机权限三种状态的系统行为（含厂商 ROM 的“不再询问”）与跳转设置后返回。
- Android Keystore 加解密在目标硬件上的表现（含锁屏/重启后）；Keystore 不可用时会清除会话要求重新登录。
- 真实弱网、断网恢复、切后台、划掉进程、重启平板后 WorkManager 的恢复时机，厂商后台限制/省电策略对任务的影响。
- 存储空间不足、超大照片、真实模糊/暗光/反光样本下提醒阈值是否合适（当前阈值仅为初值）。
- 旋转裁切交互在平板触屏上的手感，与裁切后服务端题区坐标显示是否吻合。
- R8 压缩后的 Release 全流程（登录、改密、多页上传、识别、检查、提示、订正、退出）。

## 当前边界

- 不是一期完整验收。识别文本直接编辑、学生自行删除服务端记录、正式的原图/派生图保留与清理策略尚未实现。
- 后台任务需要至少一次登录；退出登录会暂停上传，重新登录同一账号继续。WorkManager 不保证立即执行，前台显示“等待网络或稍后自动重试”。
- 服务端只保存派生图（契约中 `CreateUpload` 无法声明 `source_asset_id`/`source_region`），原图只在本机；裁切参数记录在本机账本。
- 页数确认只在本机记录（`confirmed_pages`）；创建作业仍按契约提交 `collection_scope: partial`。
- 绿色框仅为 AI 推测区域，不视为精确错点定位；未加载本次作答图片时明确标注原题照片。
- 质量启发式只提醒不拦截，也不替代服务端“看不清不判错”的规则。

Android 依据：[Keystore](https://developer.android.com/privacy-and-security/keystore)、[Network security configuration](https://developer.android.com/privacy-and-security/security-config)、[WorkManager](https://developer.android.com/develop/background-work/background-tasks/persistent)、[Photo picker](https://developer.android.com/training/data-storage/shared/photopicker)、[App signing](https://developer.android.com/studio/publish/app-signing)、[Shrink, obfuscate, and optimize](https://developer.android.com/build/shrink-code)。

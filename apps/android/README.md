# 安卓拍照纠错 App 工程初稿

本目录落实 [安卓一期框架](../../ANDROID_PHASE1.md) 中的安卓端起点。首发已确认**初中、人教版、安卓平板**。当前只实现本地开发采集与六屏骨架，**不是完成的一期 App，也没有完成 C0 / R1 / R2 验收**。没有固定答案、模拟模型、假上传或虚构检查进度。

## 已写入的行为

- Kotlin、Compose、ViewModel、CameraX 工程与六条导航：首页、拍照预览、进度、整页结果、单题订正、历史。
- debug 构建明确显示“开发采集模式、未登录学生”。没有任意输入 studentId 的入口；release 构建不开放采集。
- 点击后申请相机权限，使用真实后置相机预览和 JPEG 拍照；失败显示错误，不生成检查结果。
- 照片先写应用私有临时目录，仅 CameraX 保存回调成功后移入完成目录。历史从完成目录恢复；原图不覆盖，预览单独缩采样并按 EXIF 显示方向。
- 本地草稿可回看和删除。文件在 `noBackupFilesDir/development-captures/`，不写系统相册；不申请共享存储权限，不提供文件分享组件。
- 后端 HTTPS 地址可以本地保存或清空。**保存地址不代表连接成功**，当前没有网络客户端，Manifest 也不声明 `INTERNET` 权限，不会上传。
- 后续页面明确“尚未识别 / 没有结果 / 尚未授权”；订正和老师求助按钮禁用，不发送请求，不假设已正确或已解决。

本机草稿没有学生归属，不得在未来登录后自动迁入某名孩子名下。首次生产接入必须重新从经授权的学生会话创建作业并采集。

## 固定构建版本

| 项目 | 固定版本 |
|---|---|
| JDK | 17 |
| Gradle Wrapper | 8.11.1 |
| Android Gradle Plugin | 8.9.2 |
| Kotlin / Compose Compiler plugin | 2.1.20 / 2.1.20 |
| compileSdk / targetSdk / minSdk | 35 / 35 / 26 |
| Build Tools | 35.0.0（AGP 默认） |
| Compose BOM | 2025.04.01 |
| CameraX | 1.4.2 |

这是可复现起点，未声称所有依赖为最新版或已适配最终平板。官方 [AGP 8.9 兼容说明](https://developer.android.com/build/releases/agp-8-9-0-release-notes) 对应 Gradle 8.11.1、JDK 17、SDK 35；[Kotlin 支持表](https://developer.android.com/build/kotlin-support) 可核对最低 AGP。Compose 编译插件与 Kotlin 保持同版本，依赖由 [Compose BOM](https://developer.android.com/develop/ui/compose/bom/bom-mapping) 统一。相机遵循 [CameraX 文件拍摄 API](https://developer.android.com/media/camera/camerax/take-photo)；[CameraX 发行记录](https://developer.android.com/jetpack/androidx/releases/camera) 可查固定版本。

Wrapper 脚本和 jar 来自 Gradle 官方 `v8.11.1` 标签。jar SHA-256 为 `2db75c40782f5e8ba1fc278a5574bab070adccb2d21ca5a6e5ed840888448046`，与 [官方 wrapper 校验值](https://services.gradle.org/distributions/gradle-8.11.1-wrapper.jar.sha256) 对照。发行包 SHA-256 已固定在 wrapper properties；本轮只下载了小型 Wrapper 文件，没有安装 JDK / SDK 或下载 Gradle 发行包。

## 构建和验证

本机 2026-10-16 只读检查结果：`/usr/bin/java` 与 `javac` 是 macOS 启动器；`java_home -V` 返回 `Unable to locate a Java Runtime`。未找到 Gradle、sdkmanager、adb；默认 Android SDK、Android Studio、常见 Homebrew JDK 路径不存在，也没有 JAVA_HOME / ANDROID_HOME / ANDROID_SDK_ROOT 配置。因此**本机编译被 JDK 与 SDK 阻塞，未生成 APK，未执行真机检查**。

在具有 JDK 17 与 SDK 35 的研发机 / CI 上：

```sh
cd apps/android
sh scripts/check-environment.sh
# SDK 安装位置可由 ANDROID_HOME 或未入库的 local.properties 中 sdk.dir 指定。
# 需要 platforms;android-35、build-tools;35.0.0 和 platform-tools。
./gradlew :app:assembleDebug :app:lintDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

首次 Gradle 调用会下载锁定的发行包与 Maven 依赖；上面的步骤供已有开发环境执行，本轮没有自行安装全局工具。远程 CI 构建应单独记录运行结果，**初稿交付时远程 CI 待验证**。编译 / lint 通过也不等于 CameraX 在目标平板上通过验收。

真机需逐项验证并记录机型 / 系统：授权、拒绝与系统设置撤权；没有后置相机；连续拍照；横竖屏与旋转时拍照；存储不足；返回及杀进程后历史原图仍可打开；图片方向；删除；禁止联网时依旧采集；release 模式无法进入采集；所有后续页面始终不伪装检查成功。预览只为采集人工检查，尚无模糊、反光和完整性自动检测。

## 与后端的接入约束

1. 服务端授权应返回可操作的学生 / 设备范围；App 选择当前学生必须依据该范围，不接受任意 studentId。短期令牌及安全存储后续实现，不放进 URL、日志或普通设置。
2. 只有有授权的作业批次才能申请上传。每张图绑定批次与页序，上传完成须校验完整性并取得服务端 asset ID；本地路径不能当成云端 ID。
3. 异步任务返回真实 job ID 和状态；上传、识别、检查分别呈现，部分失败和无法判断不算成功，离页之后仍可恢复。
4. 单题订正依赖有效 question revision、correction case、父 / 根 attempt；没有关联时不能独立创建“已订正”。提示与老师求助也需关联有效来源版本。
5. 改判、撤销、输入版本变化后按服务端有效版本刷新。客户端不能自行写“已确认正确”或“已解决”。

当前故意不引入 Retrofit、Room、WorkManager 或模型 SDK：没有运行中的上传任务可交给它们。下一步接正式授权 / 上传契约后，再以 Room + WorkManager 实现受控的本地任务、幂等重试和恢复；此处的文件列表不冒充生产队列。

## 未完成

正式身份授权与设备绑定；多页批次 / 裁切 / 编辑旋转 / 质量检测；上传与后端适配；持久任务与后台续传；OCR / 切题 / 判题 / 提示；题区叠加；真正的订正再拍与复查；老师接手 / 申诉；生产历史与审计；数据加密和授权删除联动；升级签名 / 分发 / 崩溃监控；真机与负载验收。拍摄中断留下的 `inflight` 文件不会显示为成功记录，但自动清理与恢复策略仍待实现。

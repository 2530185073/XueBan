# Mac 本地 Android 联调

本文对应 `services/backend/scripts/mac_local.py`、`mac_demo.py` 与 Android Debug App。用途是开发联调：独立 Python 虚拟环境、独立 PostgreSQL 数据目录、API、持久 Worker 和 Android 模拟器。识别、检查、提示、再检走服务端 Ark 模型；没有本地假判题或腾讯/百度 OCR。

当前默认模型为 `deepseek-v4-1-flash-260910`。合成图片可验证接口和流程，不能代表真实手写准确率或一期验收通过。本轮实际联调结果见 [验证记录](../VALIDATION.md)，完整后端合成订正闭环已真实调用通过；其他项目逐项记录。

## 已配置机器的快捷入口

仓库根目录双击 **`启动本地体验.command`**，或在仓库根目录执行：

```sh
services/backend/.venv/bin/python services/backend/scripts/mac_demo.py
```

入口要求后端 runtime 已初始化、模型私有配置可读、Debug APK 已构建、Android SDK 和 `XueBan_Tablet` AVD 已创建。它会启动数据库/API/Worker，等待模拟器启动，设置 `adb reverse`，安装现有 Debug APK，放入两张合成测试图，并打开 App。它不会自动构建新 APK、注册学生、生成配对码或替用户提交作业。

当前入口固定使用 `emulator-5554` 和 AVD `XueBan_Tablet`；若该端口属于另一 AVD，会拒绝安装。已运行的 API/Worker 会被保留；更新代码或配置后须显式 `stop`、`start`。首次配置按下文进行。

## 一次性准备

所有命令从仓库根目录开始。Python 使用 3.11 或更新版本；本轮测试环境为 Python 3.14。为项目创建独立虚拟环境，不向系统 Python 或 Codex 共享解释器安装依赖：

```sh
python3 --version
python3 -m venv services/backend/.venv
services/backend/.venv/bin/python -m pip install -r services/backend/requirements.txt
```

已有该虚拟环境时直接使用。安装 PostgreSQL 17，并确认 `initdb`、`pg_ctl`、`createdb` 可用；Homebrew 示例：

```sh
brew install postgresql@17
export XUEBAN_PG_BIN="$(brew --prefix postgresql@17)/bin"
"$XUEBAN_PG_BIN/pg_ctl" --version
```

后续脚本使用独立集群和 `55432` 端口，无须启动 Homebrew 默认数据库服务。Apple Silicon Homebrew 默认位置为 `/opt/homebrew/opt/postgresql@17/bin`；其他安装位置通过 `--pg-bin` 指定。

模型配置由管理员存放在仓库外的 **0600 普通文件**中。下方仅为格式，须在私有文件中填写有效值，不能把占位文本当作凭据：

```text
ARK_BASE_URL=https://ark.ap-southeast.bytepluses.com/api/v3
ARK_API_KEY=<管理员在私有文件中填写>
ARK_MODEL=deepseek-v4-1-flash-260910
```

`ARK_BASE_URL` 不含 `/chat/completions`。文件按简单 `NAME=value` 解析，不加 `export`，不加包围引号，不执行 shell 展开。该 API Key 只留在后端；Android 只使用设备会话。

```sh
export XUEBAN_RUNTIME="$HOME/.local/share/xueban/local-dev"
export XUEBAN_MODEL_CONFIG="$HOME/.config/xueban/ark.env"
```

将 `XUEBAN_MODEL_CONFIG` 改为管理员实际提供的私有文件路径。不要将模型配置、runtime、数据库、照片、签名私钥或配对文件复制进仓库。

## 初始化、启动和停止后端

`init` 只执行一次，要求目标 runtime 尚不存在。它会生成本机签名密钥、随机数据库口令、独立 PostgreSQL 集群与数据库，并启动数据库；不会删除或重置已有目录。

```sh
services/backend/.venv/bin/python services/backend/scripts/mac_local.py \
  --runtime "$XUEBAN_RUNTIME" init \
  --pg-bin "$XUEBAN_PG_BIN" \
  --model-config "$XUEBAN_MODEL_CONFIG" \
  --base-url http://127.0.0.1:8000 \
  --host 127.0.0.1 --pg-port 55432

services/backend/.venv/bin/python services/backend/scripts/mac_local.py \
  --runtime "$XUEBAN_RUNTIME" start

services/backend/.venv/bin/python services/backend/scripts/mac_local.py \
  --runtime "$XUEBAN_RUNTIME" status
```

`start` 自动执行 Alembic 迁移后启动 API 与 Worker。`status` 检查进程存活，不表示模型、数据库业务或完整纠错已经验收。需要只迁移时使用同一入口的 `migrate`。

```sh
services/backend/.venv/bin/python services/backend/scripts/mac_local.py \
  --runtime "$XUEBAN_RUNTIME" stop
```

`stop` 保留数据和照片，先停止 Worker/API，再停止数据库。进程在 15 秒内未退出时保留其 PID 和数据库并明确报错；不要直接删除 PID 文件冒充停机成功。更新后再次 `start`。停止、查看状态和管理员撤权不依赖模型配置文件可读。

运行日志位于 runtime 的 `logs/`：`api.log`、`worker.log`、`postgres.log`、`postgres-control.log`。排错时只摘取必要错误信息，勿整包分享 runtime；其中包含本机身份材料和业务数据。

## 注册授权学生、签发配对码

以下均为合成联调身份。学生、账号和授权由管理员创建；App 不能自行指定任意学生 ID。先执行 `start` 或 `migrate`，确保数据库表已建立。

```sh
services/backend/.venv/bin/python services/backend/scripts/mac_local.py \
  --runtime "$XUEBAN_RUNTIME" admin enroll \
  --tenant-id tenant_demo --campus-id campus_demo \
  --student-id student_demo --student-name 合成联调学生 \
  --principal-id actor_demo --subject student-demo --role student

export XUEBAN_PAIR_FILE="$XUEBAN_RUNTIME/pair-student-$(date +%Y%m%d-%H%M%S).json"
services/backend/.venv/bin/python services/backend/scripts/mac_local.py \
  --runtime "$XUEBAN_RUNTIME" admin pair \
  --principal-id actor_demo --output "$XUEBAN_PAIR_FILE" --ttl 900
```

配对文件以 0600 权限独占创建，不覆盖已有文件。仅在本机私下打开，输入其中 `pairing_code`；它是短期凭据，不进入截图、日志或 Git。默认配对码 15 分钟有效，设备会话 8 小时；到期后重新签发配对码。网络结果未知时由 App 保存并复用原配对幂等键；已兑换后的同键恢复受原会话有效期和当前授权约束。

App 中填写服务器 **`http://127.0.0.1:8000`**，不加 `/v1`，输入配对码，再选择服务端返回的授权学生。已有身份不支持借 `enroll` 改角色或迁移校区；坐班老师应使用独立账号和明确授权。

撤销已知设备会话使用管理员入口，`--session-id` 填待撤销的真实会话标识，不填整个 Bearer Token：

```sh
services/backend/.venv/bin/python services/backend/scripts/mac_local.py \
  --runtime "$XUEBAN_RUNTIME" admin revoke-session --session-id '<设备会话标识>'
```

## Android SDK、构建与模拟器

本轮 Mac 使用以下环境位置。它们是开发工具路径，不包含模型凭据；其他机器替换为自己的 SDK/JDK 安装位置。

```sh
export ANDROID_HOME="$HOME/.local/share/xueban/android-sdk"
export ANDROID_AVD_HOME="$HOME/.local/share/xueban/android-avd"
export JAVA_HOME="$HOME/.local/share/xueban/java/zulu17.68.203-ca-jdk17.0.20.1-macosx_aarch64/Contents/Home"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
java -version
adb devices
```

项目要求 JDK 17、Android Platform 35、Build Tools 35.0.0。本轮 AVD 为 `XueBan_Tablet`，设备配置 `pixel_tablet`，系统镜像 `system-images;android-35;google_apis;arm64-v8a`。该镜像用于 Apple Silicon；Intel Mac 应选择与宿主匹配的镜像，并自行验证。若 SDK/AVD 尚未准备，先通过 Android Studio 或 SDK 命令行工具安装对应组件、按提示处理许可并创建 AVD。

```sh
cd apps/android
./gradlew :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
cd ../..
```

APK 输出为 `apps/android/app/build/outputs/apk/debug/app-debug.apk`，应用 ID 为 `cn.xueban.companion.dev`。之后可使用上方双击入口，或手动启动模拟器与安装：

```sh
"$ANDROID_HOME/emulator/emulator" -avd XueBan_Tablet -port 5554 -no-snapshot -no-boot-anim
```

等待模拟器开机后，在另一终端从仓库根目录执行：

```sh
adb -s emulator-5554 reverse tcp:8000 tcp:8000
adb -s emulator-5554 install -r apps/android/app/build/outputs/apk/debug/app-debug.apk
adb -s emulator-5554 shell am start -n cn.xueban.companion.dev/cn.xueban.companion.MainActivity
```

`adb reverse` 将模拟器的 `127.0.0.1:8000` 转到 Mac，因此 App 地址、API 地址与后端生成的图片票据 origin 一致。模拟器重启后重新执行 reverse。手动使用 `10.0.2.2` 时也必须同步调整后端 `PUBLIC_BASE_URL`；不能只修改 App 地址。

## 同网络真机

USB 真机可用 `adb -s <设备序列号> reverse tcp:8000 tcp:8000`，继续使用回环地址。通过 Wi-Fi 访问时，Mac 与平板须在可互通的局域网；后端绑定 `0.0.0.0`，`--base-url` 与 App 地址均使用 **同一个 Mac 内网 IP 和端口**。例如初始化一个独立的真机联调 runtime：

```sh
services/backend/.venv/bin/python services/backend/scripts/mac_local.py \
  --runtime "$HOME/.local/share/xueban/local-device-dev" init \
  --pg-bin "$XUEBAN_PG_BIN" --model-config "$XUEBAN_MODEL_CONFIG" \
  --host 0.0.0.0 --base-url http://192.168.1.20:8000 --pg-port 55433
```

将示例 IP 替换为该 Mac 实际内网 IP；这套 runtime 的后续 `start/status/admin/stop` 都要传同一路径。使用同一 API 8000 端口前先停止原 runtime；新 runtime 也须注册身份并签发自己的配对码。不要对已存在 runtime 重跑 `init`。

Debug 才允许回环/RFC1918 IP 的 HTTP，`localhost` 域名不在例外内；Release 要求 HTTPS。`0.0.0.0` 仅是监听地址，不能填到 App 或票据地址。局域网联调按需允许 Mac 防火墙的该端口访问，不将开发服务直接暴露到公网。

## 最小联调路径与验收边界

1. 先配对并读到授权学生。快捷入口放入相册的 `xueban-original.png`、`xueban-corrected.png` 来自仓库合成 fixture。
2. 从相册选择原错题图，预览后上传。确认上传、识别、逐题检查分别显示真实状态；任务成功不能被显示成学生答对。
3. 查看具体题与原图，打开一级提示；选择对应订正图提交新作答，再等待再检。错拍别题、看不清或无法定位应保留待核实状态。
4. 模型输出始终是 AI 初判；坐班老师确认须走受权人工审核，不能因模型返回 `correct` 自动显示“老师已确认”。
5. 检查历史恢复、失败重试、断网/重启恢复、设备撤权；这些项目须记录实际结果，不能用单元测试替代模拟器/真机验收。

点击上传、提示或再检会触发配置好的真实模型服务；合成数据用于协议与流程验证，用量不等于零成本。首次现场试点前仍需真实授权手写数据评测、真机相机与网络恢复测试，以及坐班老师工作流验收。本文不预先宣称上述项目通过。

## 显式运行真实模型闭环验证

在 API/Worker 已启动的本机，以下命令会创建独立合成身份与作业，运行五个模型任务（上游可按有限重试策略执行）。不会使用真实学生照片，不会自动确认掌握或老师已复核。

```sh
services/backend/.venv/bin/python services/backend/scripts/live_local_smoke.py --run-live
```

完整报告以0600权限保存在 runtime 中，提交意图和已受理任务立即原子保存。报告仅用于联调；真实费用以服务账单为准。单个任务失败不会冒充成功，也不会清理此前失败记录。

## 删除清单与隔离恢复

新建 runtime 会在数据库之外建立私有 `ledger/deletions.jsonl`；已有 runtime 不会静默补一个空清单。先运行 `audit-deletion-ledger` 盘点，再显式用 `configure-deletion-ledger --ledger-path <已有私有清单>` 绑定；缺失的历史删除记录必须先找回，不据空清单追认过去备份。

完整备份需停止 API/Worker 等写入者但保留 PostgreSQL；恢复默认创建新数据库和新资产目录，校验后由运维明确切换。命令、维护窗口和历史边界见 [备份与恢复](runbooks/BACKUP_RESTORE.md)，真实本机验收见 [恢复演练](evidence/recovery-20261021/README.md)。

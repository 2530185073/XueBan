# 学生网页端 · Mac 本地运行

2026-10-17 按项目负责人最新决定：**先交付可运行的学生网页端，App 后续封装；一学员一账号密码。** 拍照纠错、自己订正和再检的核心顺序不变；网页端共用业务 API、模型 Worker、数据库和历史证据。新登录绑定固定学员ID，详见 [账号与长期学习档案](ACCOUNT_FOUNDATION.md)。

## 这台已配置 Mac

在项目目录双击 `启动网页版.command`。它会构建学生网页，启动现有本地后端，并在默认浏览器打开：

**http://127.0.0.1:8000/learn/**

此入口不需要 Android SDK、模拟器或 APK。需要已配置的 Python 虚拟环境、Node.js 22.12+、PostgreSQL runtime 及服务端模型接口；原环境准备见 [Mac 环境说明](MAC_LOCAL_RUN.md)。

```sh
services/backend/.venv/bin/python services/backend/scripts/mac_web.py
```

启动器打开普通登录页，不自动登录、不选择孩子、不生成配对码。学员输入个人账号密码；管理员账号进入学员管理页。退出或会话过期后重新登录自己的账号，学习记录继续保存在原学员名下。

首次临时密码及管理员重置后的临时密码，需先更换再访问作业。忘记密码请管理员在管理页重置。`--no-open` 仅启动服务而不打开浏览器，不生成任何身份凭据。

首次部署由管理员在服务端创建受控管理账号，不需要先创建占位学员。密码文件必须0600、归当前用户所有且保存在仓库外，密码不能作为命令行参数：

```sh
services/backend/.venv/bin/python services/backend/scripts/mac_local.py admin \
  bootstrap-admin --tenant-id '<托管运营方ID>' --campus-id '<校区ID>' \
  --principal-id '<管理员主体ID>' --subject '<管理员身份标识>' \
  --username '<管理员用户名>' --password-file '<私有密码文件绝对路径>'
```

首次初始化同时创建管理员、运营方/校区及显式管理授权，拒绝覆盖已有账号。已有主体则使用以下绑定命令；校区授权只授予已有管理员角色：

```sh
services/backend/.venv/bin/python services/backend/scripts/mac_local.py admin \
  set-password-account --principal-id '<已有主体ID>' \
  --username '<登录用户名>' --password-file '<私有密码文件绝对路径>'

services/backend/.venv/bin/python services/backend/scripts/mac_local.py admin \
  grant-campus --principal-id '<已有管理员主体ID>' --campus-id '<所属校区ID>'
```

为已有学生主体绑定账号时还需指定 `--student-id`，其值必须与原学员授权一致；不能重建一个新学生来替代旧数据。新增普通学员优先使用管理员网页创建。管理员角色、校区授权及已有身份初始化仍由受控CLI完成，不提供公开管理员注册。

浏览器只保存短期会话，不保存密码。模型密钥始终在后端。这个启动器限定本机回环地址，不将密码登录服务自动开放到局域网或公网。

## 使用流程

1. 学员登录自己的账号，确认姓名，选择自己的作业照片。电脑使用文件选择；支持的平板浏览器可从系统拍照入口上传。
2. 查看上传、识别与检查的实际进度。原图、识别条目和判定一起查看；待核实和未读清不代表答对。
3. 需要订正时逐级查看提示，在纸上改好，再提交包含同一道题题干的新照片，等待再检。
4. 拿不准时提交坐班求助；历史可打开之前的作业并查看当前结果。原作答、订正父链和提示使用证据已由后端保留，网页尚未提供逐版本时间线。求助受理不等于老师已经确认结论。

点击上传、提示、检查或再检会实际调用已配置的模型，可能产生接口费用。浏览器本身不运行假判题，也不要求填写模型密钥。

## 整页双模型纠错（当前推荐配置）

设置 `CORRECTION_MODE=two_model` 后，DeepSeek 与 GPT 分别读取同一页原图并独立判题；两路并行，不预先交换题干、学生答案或结论。服务器逐个作答项比较两路结果，在同一个持久任务中保存题目、原作答和候选判断。网页直接显示整页结果，不再为已有结果的每道题追加检查请求。两路存在实质分歧或一方失败时，对应题目待核，学生可以先看其他题。

本机配置保留原 Ark 私有文件，另指定 OpenAI 兼容网关私有文件；它必须是权限 `0600` 的普通文件，内容为 `ZYAI_BASE_URL=https://api.zyai.online/v1` 和 `ZYAI_API_KEY=...`。密钥仅给 Worker，不给网页构建或公开 API 进程。

```sh
services/backend/.venv/bin/python services/backend/scripts/mac_local.py configure-models \
  --mode two_model --gateway-config "$HOME/.config/xueban/zyai.env" \
  --review-model gpt-6-astra

services/backend/.venv/bin/python services/backend/scripts/mac_local.py stop
services/backend/.venv/bin/python services/backend/scripts/mac_web.py
```

配置命令仅保存私有文件路径与模型开关，不复制密钥，不改变账号或历史记录。它为本地 Worker 设置至少 600 秒租约，覆盖有界的双读与复核阶段。切换模式后必须重启 Worker；已经运行的进程不会自动加载新代码或配置。回到原单模型路径使用 `configure-models --mode single`，再重启服务。

模型结果仍有 `candidate` 和 `needs_review` 的区别：识读一致不等于人工确认或知识点掌握。原图、每次调用的候选与用量、分歧、当前作答版本都保留审计；网关不可用或复核超时不会伪装成多模型通过。正常作答和订正照片仍绑定原学员账号。坐班老师的原有人工切分修订入口保留。

双模型配置使用 Ark 的 DeepSeek 和兼容网关 Responses 接口的 Astra，每页各一次调用，各自最多 24576 输出 tokens，DeepSeek 请求预算 200 秒、Astra 为 118 秒，每个供应商最多四次并发。`matched` 要求题目、可见作答、作答状态和结论一致；错误项还需要一致的正确答案及各自的改正依据。它仍然只是 AI 初判，不能替代老师确认或独立掌握证据。超时不会自动重新发起付费请求；失败可在网页明确重试，未知用量不按零费用记账。

旧的三模型实验路径保留在 `CORRECTION_MODE=multimodel`：DeepSeek 批改、Kimi 独立只读、Astra 处理分歧。需要复现实验时可显式切换，该路径不等同于两模型独立判题。旧单模型配置 `single` 也继续兼容。

## 开发与部署结构

学生端源码在 `apps/student-web`。构建后由 FastAPI 的 `/learn/` 提供静态资源，API 在同源 `/v1/`；图片通过短期授权票据下载为 Blob，模型密钥不进入构建。网页不加载第三方脚本或外部字体，不缓存私人API到Service Worker。

```sh
cd apps/student-web
npm ci
npm test
npm run build
```

已有后端可设置 `STUDENT_WEB_DIST` 为构建产物 `dist` 的绝对路径，再启动 API。本机 `mac_local.py` 会发现已构建目录并启用；旧API进程尚未加载网页挂载时，网页启动器仅重启该API，保留数据库与模型Worker。

代码或后端配置发生其他变化时，仍须按原文档明确重启对应服务。学生网页、坐班工作台和后端保持独立目录与CI，后续 App 封装复用这套网页与接口，并补设备权限、文件选择、相机方向、安装升级和后台恢复的真机验收。

## 当前边界

这是本地联网开发版，不是公网部署或正式学生试点。网页能否正常运行、模型是否返回候选、整页分题与纠错是否准确是不同检查项；切换到网页不会自动修复当前模型的多空合并、读回不一致或坐标偏移问题。质量记录继续以 [VALIDATION](../VALIDATION.md) 为准。

多模型模式下，整页上传并受理后，识读、核验与候选保存均在服务端任务中继续执行，浏览器重新打开后恢复查询；尚未上传成功的照片仍需客户端恢复。原单模型兼容路径仍有客户端逐题协调。拍照入口由浏览器和设备提供，Mac文件选择通过不等于安卓/苹果平板实机拍照验收。正式使用前仍需质量评测、身份运营、删除与恢复、坐班联合流程及现场设备验证。

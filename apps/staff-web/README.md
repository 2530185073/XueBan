# 坐班 Web 工作台

React + TypeScript + Vite 的一期坐班老师工作台，由后端同源托管在 **`/staff/`**（与学生网页 `/learn/` 同一方式）。老师用账号登录，在一张桌面上处理孩子的求助与申请重查、核实 AI 候选判断、修订识别结果和切题错误。所有请求都走同源 `/v1/`，没有跨域配置，也不再需要手工粘贴服务地址或访问令牌。

## 运行

需要 Node.js 22.12+；依赖和 lockfile 已固定。

```sh
cd apps/staff-web
npm ci
npm test          # vitest：API 客户端、框选坐标换算、队列合并、冲突与任务轮询、学情接口校验与周期/链接规则
npm run build     # 契约漂移检查 + tsc 类型检查 + 生产构建到 dist/
```

构建后由后端提供：`install_staff_web` 优先读取环境变量 `STAFF_WEB_DIST`，未设置时若仓库内 `apps/staff-web/dist/index.html` 存在则自动使用。启动后端后打开 `http://127.0.0.1:8000/staff/`（以实际 `PUBLIC_BASE_URL` 为准）。容器镜像（`infra/Dockerfile`）已同时构建两个网页并设置 `STAFF_WEB_DIST`。

`/staff/` 响应带严格 CSP（`script-src 'self'`、`connect-src 'self'`、`img-src 'self' blob: data:`、禁止嵌入）、`no-referrer` 和 `nosniff`；静态目录不提供点文件、source map 或符号链接。因为 CSP 禁止 eval，契约校验器在构建期预编译（见下文），界面也不加载任何外部字体或脚本。

本机联调可用 `npm run dev`（`http://127.0.0.1:5173/staff/`，把 `/v1` 代理到 `127.0.0.1:8000`）；仅开发服务器会把本机后端返回的原图地址改走同源代理。`npm run preview` 不做这项改写，正式使用请以后端托管的 `/staff/` 为准。

## 登录与会话

- 校区管理员可在 `/learn/` 登录后管理学员与老师账号，包括创建、重置密码和老师授权状态。
- 账号由校区管理员通过 `POST /v1/admin/staff-accounts` 开通（角色 `sitting_teacher`，可另加 `data_steward`）。登录调用 `POST /v1/auth/login`，带幂等键；网络中断后重试沿用同一个键。
- 首次登录（`must_change_password`）先进入改密页，`POST /v1/auth/change-password` 返回新会话后才读取 `/v1/me`。新密码 15–128 个字符。
- `/v1/me` 必须含 `sitting_teacher` 或 `campus_admin` 角色，否则立即退出。有校区授权的老师可以看到该校区所有学员。
- 访问令牌**只在内存中**：不写 localStorage / sessionStorage / URL。刷新或关闭页面需要重新登录。到期时间一到、或任一请求返回 401，都会清空会话、队列、原图缓存（blob URL 会被释放）并回到登录页。
- “退出登录”调用 `POST /v1/auth/logout`；即使服务器没有确认撤销，本页也会清除会话并提示。

## 队列

左侧三个标签页：

| 标签 | 来源 | 说明 |
|---|---|---|
| 求助 | `GET /v1/help-requests` | 等待中 / 处理中的求助，含“申请重查”（`kind: appeal`）；申请重查排最前，其余按等待时间排序；可按状态筛选、按学生姓名或内容搜索 |
| 待核实 | `GET /v1/review-queue` | 明确需要看原图、识读修订或老师修订后的后续处理；清晰可用的 AI 初判不要求老师逐题确认 |
| 全部题 | `GET /v1/review-queue?view=all` | 查看授权范围内全部有效题目，区分 AI 初判、AI 核对未完成和老师已核实；可随时打开原图主动复核 |

三个列表都按游标“加载更多”，追加时按 id 合并、不重复；刷新替换首页。求助条目里的学生姓名由 case 的 scope 对照 `/v1/me` 的授权学员补出。“处理中”只在服务器返回 `claimed` 时显示。

模型接口故障、格式分歧或尚未完成的模型核对，不会标成孩子字迹看不清，也不会因为从待核实列表移出而升为老师确认。它们仍在全部题中显示“AI 核对未完成”，该标签不承诺已有任务正在运行或会自动重试。孩子的求助和申请重查始终进入独立求助队列。

## 题目工作区

选中任一条目后读取 `GET /v1/cases/{id}`，再并行读取作业、题目（按 case 记录的版本 `?revision=N`）、全部作答、提示、当前判断及其检查记录、全部求助和复核记录，以及作业的图片元数据。

- **原图**：每张图先 `GET /v1/assets/{id}/access` 换短期地址，再同源下载为 blob 显示。只接受同源 `/v1/assets/{id}/content` 地址（本机 http 也可以），外部来源一律拒绝。图上叠加题区（绿色虚线）和答案区（蓝色），可放大、展开、翻页。
- **当前判断**：AI 候选为琥珀色虚线卡片并标“AI 候选 · 未经老师确认”；老师确认为深绿色实线卡片并显示确认时间。依据逐条标出来源（AI 推测 / 老师观察 等）。
- **作答过程**：从原始作答沿 `parent_attempt_id` 排出每次订正，标当前作答、识别状态和帮助情况；点选任一次即可切换到它的照片和答案区。
- **提示记录**：级别、是否含答案、孩子是否已看到（`first_shown_at`）、是否基于 AI 候选、是否已失效。
- **求助与申请重查**：列出这道题的全部求助、谁接手、帮助记录。

## 处理操作

所有写入都带 `Idempotency-Key` 和 `expected_revision`，请求体先按共享契约校验再发送。

| 操作 | 接口 | 前提 |
|---|---|---|
| 接手求助 | `POST /v1/help-requests/{id}/claim` | 状态为待接手 |
| 帮助已结束 / 仍需帮助 | `POST /v1/help-requests/{id}/complete` | 由自己接手；不等于题目答对 |
| 确认 / 更正 / 撤销 / 保留待核实 / 人工初次核实 | `POST /v1/cases/{id}/reviews` | 有孩子在求助时须先接手；判断依据版本必须仍有效 |
| 请求重新检查 | `POST /v1/cases/{id}/checks` | 当前无有效判断且识别完成；完成后自动刷新 |
| 修订题目 | `POST /v1/questions/{id}/revisions` | 修改题干，可在原图上重新框题区 |
| 修订答案 | `POST /v1/attempts/{id}/recognition-revisions` | 修改当前作答的识别文字（或标“看不清/未作答”），可重新框答案区（只能框在这次作答的照片上） |
| 补题 / 拆合 | `POST /v1/homeworks/{id}/layout-revisions` | 见下 |

复核和三种修订都遵守同一道门槛：这道题有孩子在等待帮助时要先接手；已由其他老师接手、题目已被替代、作业已停止处理时不能提交。

**补题 / 拆合**：在原图上按住拖动画矩形（鼠标、触控笔、手指均可），矩形按显示尺寸换算成原图像素（`coordinate_space: original_pixels`）并取整、夹在图片范围内。可添加多道新题，每道可跨多块区域、可选填题干；勾选要被替代的现有题（漏题不勾、拆分勾一题画多题、合并勾多题画一题），填写原因后提交。接口返回 202 和任务，工作台每两秒左右轮询 `GET /v1/jobs/{id}`，完成后列出新生成的题目，可直接打开；被替代的题显示“已被替代”并链接到新题。若显示的图片比例与记录尺寸不一致（例如 EXIF 旋转），会禁止在该图上框选。

## 学情工作区（后期功能）

页头下方的导航切换六个区域：坐班工作台（上文）与下列五个学情区域。它们不在 `contracts/phase1` 内，响应由 `src/api/learningTypes.ts` 手写校验（不符即视为 502），请求经 `src/api/learning.ts` 走同一会话、同一幂等键与待确认写入锁；PUT/PATCH 也一样。写入带 `expected_revision` / `expected_link_id` / `expected_condition_id`，409 时提示并重新读取。掌握情况只显示“当前需要帮助 / 正在巩固 / 阶段性较稳 / 暂不判断”，任何地方都不显示百分比或分数。

| 区域 | 接口 | 要点 |
|---|---|---|
| 知识点映射确认 | `GET /v1/knowledge-link-queue`、`GET /v1/curriculum/graph`、`GET/POST /v1/questions/{id}/knowledge-links` | 老师已核实但未确认知识点的题；AI 候选显示理由；确认 / 驳回 / 替换、主要或次要；一道题最多一个主要知识点（本地先拦）。题目工作台右栏也有同一编辑器 |
| 帮助情况（在题目工作台） | `GET`/`POST /v1/attempts/{id}/condition` | 按作答记录帮助程度；看过提示或答案的作答不能选“独立”，订正也不能。只有老师亲眼核实才勾选独立 |
| 学员画像 | `GET /v1/students/{id}/knowledge`、`…/knowledge/{kid}?include_invalidated=true`、诊断 `POST …/diagnoses`、`POST /v1/diagnoses/{id}/retract`、`GET/PUT …/learning-profile` | 章节只计数不平均；`projection.pending` 显示“正在汇总最新改判”；证据区分有效/失效；“可能原因（未核实）”与“已核实薄弱范围”（至少两条证据）；档案维护年级、学期与当前课次 |
| 学伴记忆 | `GET …/memories?include_inactive=true`、`POST …/memories`、`POST /v1/memories/{id}/confirm|retract`、`POST/GET …/agent-runs` | 来源、状态、有效期与引用证据；学科困难必须引用该生当前有效证据；AI 学情摘要排队后自动轮询，分别显示证据不足、需要老师判断、已过期、失败；AI 提议的记忆进入“待确认” |
| 半月报告 | `GET /v1/reports?status=draft|published`、`GET/POST …/reports`、`GET/PATCH /v1/reports/{id}`、`POST …/publish`、分享链接 | 只能生成已结束的半月（北京时间）；快照显示真实分母、帮助条件、证据卡片、还不知道的、额外活动和局限说明；更正版本必须写更正说明；`projection_pending` / `report_changed` 给出中文提示；家长链接只显示一次（基于服务地址拼出 `/r/{token}`），可撤销 |
| 题库审核与选做 | `GET/POST /v1/practice-items`、`POST …/review`、`POST /v1/practice-items/generate`、`PUT …/optional-window`、`GET …/optional-activities`、`GET …/practice-sessions` | AI 只生成候选，老师通过（可先修改）后才会出现；老师录入直接生效。今日选做窗口：额外活动默认 0 分钟，必须孩子愿意、学校任务完成、老师确认有余力；不做不记欠账。选做记录只读 |

合成样例预览不包含这些区域。

## 冲突与未知结果

| 情况 | 工作台行为 |
|---|---|
| 409 版本冲突 | 提示“记录已发生变化”，自动重新读取队列和当前题目；已填写的说明保留，需老师核对后再提交 |
| 网络中断 / 超时 / 5xx | 不显示完成；表单锁定，只能点“重试原操作”，沿用原内容与原幂等键 |
| 401 | 清空会话与已加载资料，回到登录页 |
| 403 | 显示无权限；`password_change_required` 转到改密页 |
| 422 | 提示内容不符合要求，表单内容保留 |
| 成功响应不符合契约 | 视为 502，不当作成功 |

待确认写入同样只在内存中，刷新页面后无法从本页恢复。

## 合成样例预览

登录页的“合成样例预览”是独立的只读模式：整页标注、人物/题目/原图均为合成（`public/sample-homework.svg`），不创建会话、不调用后台，所有提交按钮禁用。

## 契约

`scripts/generate-contract-types.mjs` 从 `contracts/phase1` 生成 `src/api/contracts.generated.ts`（学生网页也引用这份类型）以及预编译的 `validators.generated.js/.d.ts`（Ajv standalone，无运行时编译）。`npm run contracts:check` 在构建时检查三者是否与共享 Schema 一致；契约更新后执行 `npm run contracts:generate` 并复核测试。`GET /v1/review-queue` 和 `/v1/auth/*` 的响应不在共享契约内，由 `src/api/types.ts` 手写校验。

## 仍待完成

- 求助列表不含学生姓名和作业标题，需逐条读取 case 补出；数量大时会较慢。
- 复核依据的区域目前固定使用当前题区，还不能单独框选依据区域。
- 多页大作业、真实设备照片（含 EXIF 方向）和现场平板可用性仍需联合验收。
- 学情接口中，记忆、诊断、选做开始与作答按 `Idempotency-Key` 去重；题库录入与分享链接不去重（分享链接的令牌不落库，无法重放），结果未知时“重试原操作”可能多出一条，可在列表中撤销或下架。

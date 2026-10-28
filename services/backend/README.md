# 本机纠错服务与持久 Worker

这里实现学生网页端和安卓共用的真实后端：授权学生 → 作业 → 私有照片上传 → 识别任务 → 逐题原作答 → 候选检查 → 分层提示 → 新订正 → 再检查；同时提供坐班求助与受权老师核实。全链路使用多模态模型，不接专用 OCR。模型输出始终是候选，只有受权老师复核能产生 `confirmed`；模型判断正确也不会自动关闭订正。

纠错业务请求和返回按 `contracts/phase1/` 的 JSON Schema 校验；长期账号接口在服务端使用严格字段模型校验，见文末。未知题库 ID、知识点映射保留 `null`。已实现人工布局修订、学生数据删除、证据驱动的个人图谱、记忆和家长报告；当前交付范围及真实验收证据见 [持续交付记录](../../docs/CONTINUOUS_DELIVERY.md)。工程测试和合成流程通过不代表所有真实整页识读、在校使用或安卓硬件验收通过。

纠错可靠性边界另见 [无效对齐预检](../../docs/RECOGNITION_ALIGNMENT_DISPATCH.md)、[逐项格式隔离](../../docs/RECOGNITION_PARTIAL_OUTPUT_PLAN.md) 和 [多空作答范围检查](../../docs/RECOGNITION_SCOPE_GUARD.md)。服务或范围未完成不得写成学生薄弱点。

## 启动

Mac 联调可使用仓库根目录的学生网页启动器；账号通过下述受控 CLI 开通。早期安卓模拟器的 [本地运行指南](../../docs/MAC_LOCAL_RUN.md) 保留设备连接说明；已有长期账号不再接受配对登录。

Python 3.14、PostgreSQL 17。命令从仓库根执行；依赖只装进本目录虚拟环境：

```sh
python3 -m venv services/backend/.venv
services/backend/.venv/bin/python -m pip install -r services/backend/requirements.txt
services/backend/.venv/bin/alembic -c services/backend/alembic.ini upgrade head
PYTHONPATH=services/backend services/backend/.venv/bin/uvicorn xueban_backend.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

另开终端，以同一数据库和私有图片目录启动 Worker：

```sh
PYTHONPATH=services/backend services/backend/.venv/bin/python -m xueban_backend.worker
# 只领取并处理一次可用任务：
PYTHONPATH=services/backend services/backend/.venv/bin/python -m xueban_backend.worker --once
```

服务不会自动读取 `.env`，不会自动建库或创建身份。配置从受控运行环境注入：

| 配置 | 说明 |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://...`；本机测试替身须另显式启用 `ALLOW_SQLITE_FOR_TESTS=1` |
| `OIDC_ISSUER`、`OIDC_AUDIENCE` | 受信任签发方与受众 |
| `JWT_PUBLIC_KEY_FILE` 或 `OIDC_JWKS_URL` | 二选一；固定 RS256，验证签名、issuer、audience、exp、iat、sub |
| `SIGNING_PRIVATE_KEY_FILE` | 账号会话签发需要；保存在仓库外并限制权限，缺失时登录返回503 |
| `ASSET_STORAGE_DIR` | 仓库外专用私有目录；本机 API 和 Worker 必须共享，禁止设为静态站点目录 |
| `PUBLIC_BASE_URL` | 如 `http://127.0.0.1:8000`；必须与安卓配置的 API origin 一致，无额外路径/查询；本机 HTTP 仅用于受控联调 |
| `UPLOAD_TICKET_TTL_SECONDS` | 默认900秒，服务端限定30—1800秒；读取票据固定120秒 |
| `WORKER_LEASE_SECONDS` | 默认300秒；进程中断后到期可恢复 |
| `READ_CHECKPOINT_ENABLED` | 默认 `0`；API 与 Worker 同时设 `1` 时，普通整页任务可保存及读取未核实首路草稿；不是结果、学习证据或中断续跑许可，学生网页另需构建期开关 |
| `INITIAL_READ_RECOVERY_ENABLED` | 默认 `0`；新普通整页任务的完整首读可安全复用；要求首路草稿开启、answer_slot、结构导航关闭、各角色适配器单次请求。未知请求或已进入后续复核的任务不自动续跑，见 [恢复边界](../../docs/INITIAL_READ_RECOVERY.md) |
| `CORS_ALLOWED_ORIGINS` | 精确来源列表，默认关闭；不允许 `*`，不启用 Cookie 凭证 |
| `ARK_BASE_URL`、`ARK_API_KEY`、`ARK_MODEL`、`ARK_FALLBACK_MODEL` | 仅 Worker 使用；密钥不进入安卓/Web/响应/日志。兜底模型可选；配置 `gpt-6-astra` 时走 Responses API |

模型字段与当前默认模型见 [providers](xueban_backend/providers/README.md)。没有模型凭据时，任务明确失败；不会产生演示答案。`scripts/ark_smoke.py` 与 `scripts/vision_benchmark.py` 是独立、显式付费的评测脚本，不是生产任务入口。

图片读取票据存在 URL 查询内，运行示例关闭 Uvicorn access log；反向代理和观测系统也应排除完整图片查询 URL、访问令牌及请求/响应正文。服务自己只生成安全错误代码，不打印原图、答案或模型错误正文。

## 已实现接口

除以下长期账号接口外，已实现原32项纠错契约全部操作（含人工重切/拆合题目与学生数据删除），并有坐班队列、运维与学习层扩展接口：

| 路径 | 行为 |
|---|---|
| `GET /v1/me` | 当前真实身份、数据库角色与授权学生 |
| `POST /v1/students/{id}/homeworks`；`GET /v1/students/{id}/homeworks`；`GET /v1/homeworks/{id}` | 建作业、授权历史分页、完整资源索引 |
| `POST /v1/homeworks/{id}/uploads` | 创建私有资产和上传票据 |
| `GET /v1/assets/{id}`；`POST /v1/assets/{id}/upload-ticket`；`POST /v1/assets/{id}/complete`；`GET /v1/assets/{id}/access` | 状态、同资产续票、完整性确认、短时读取票据 |
| `POST /v1/homeworks/{id}/recognitions`；`GET /v1/jobs/{id}` | 持久识别任务、真实执行状态 |
| `GET /v1/jobs/{id}/recognition-progress` | 与任务相同授权；运行中的普通整页识读仅返回题号、题干、作答原文；终态隐藏草稿，旧任务不回填 |
| `GET /v1/questions/{id}`；`GET /v1/attempts/{id}`；`GET /v1/cases/{id}` | 题目、原/新作答和纠错状态 |
| `POST /v1/cases/{id}/checks`；`GET /v1/checks/{id}`；`GET /v1/decisions/{id}` | 发起检查、模型版本、候选依据 |
| `POST /v1/cases/{id}/hints`；`GET /v1/hints/{id}`；`POST /v1/hints/{id}/views` | 分层提示、真实展示记录 |
| `POST /v1/cases/{id}/solution-view` | 学生主动打开当前解答；提交 `attempt_id`、`source_decision_id`、`source_decision_revision` 与幂等键，校验当前版本并记录带时间的答案展示，重检中拒绝旧解答 |
| `POST /v1/cases/{id}/attempts` | 新订正照片生成新 Attempt，先识别，再明确发起 checks |
| `POST /v1/cases/{id}/help-requests`；`GET /v1/help-requests`；`POST /v1/help-requests/{id}/claim`；`POST /v1/help-requests/{id}/complete` | 学生求助、老师授权待办、领取和处理 |
| `POST /v1/cases/{id}/reviews` | 受权老师确认/替换/撤回判断，模型失败时可人工初判 |
| `POST /v1/questions/{id}/revisions`；`POST /v1/attempts/{id}/recognition-revisions` | 老师修正题干/题区或答案识别，保存旧版本并使旧依据失效 |
| `POST /v1/cases/{id}/progress` | 暂停/继续，不能据此确认完成；继续后错题恢复为待订正 |

后续阶段学习层（设计与规则见 [LEARNING_LAYER](../../docs/LEARNING_LAYER.md)）：

| 路径 | 行为 |
|---|---|
| `GET /v1/curriculum/graph`；`GET/PUT /v1/students/{id}/learning-profile` | 已发布课程图谱；年级、学期与当前课次 |
| `GET /v1/students/{id}/knowledge`；`GET /v1/students/{id}/knowledge/{kid}` | 个人知识状态（无百分比）、章节汇总、证据与前置知识 |
| `GET/POST /v1/questions/{id}/knowledge-links`；`GET /v1/knowledge-link-queue` | 当前作答的机器归因与老师确认/驳回的本题关联 |
| `POST /v1/attempts/{id}/condition`；`POST /v1/students/{id}/knowledge/{kid}/diagnoses`；`POST /v1/diagnoses/{id}/retract` | 老师记录帮助/独立条件与诊断 |
| `GET/POST /v1/students/{id}/memories`；`POST /v1/memories/{id}/confirm`、`/retract` | 专属 AI 记忆：有证据、可确认、可撤回、证据撤销即失效 |
| `GET /v1/cases/{id}/agent-context`；`POST/GET /v1/students/{id}/agent-runs` | 当前题“学伴记得”提醒；引用证据的坐班学情摘要 |
| `GET/POST /v1/students/{id}/reports`；`GET /v1/reports`；`GET/PATCH /v1/reports/{id}`；`POST /v1/reports/{id}/publish` | 半月报告草稿、编辑、发布与更正版本 |
| `POST/GET /v1/reports/{id}/share-links`；`POST /v1/report-share-links/{id}/revoke`；`GET /r/{token}` | 家长分享链接与只读报告页 |
| `GET/POST /v1/practice-items`；`POST /v1/practice-items/{id}/review`；`POST /v1/practice-items/generate` | 已审核题库、模型候选与老师审核 |
| `PUT /v1/students/{id}/optional-window`；`GET /v1/students/{id}/optional-activities` | 今日余力与额外分钟数；三项门禁状态与可选活动 |
| `POST/GET /v1/students/{id}/practice-sessions`；`GET /v1/practice-sessions/{id}`；`…/responses`、`…/items/{item}/hint`、`…/exit` | 自愿举一反三、预习与小测，随时结束不记欠账 |

`GET question/attempt/decision` 支持 `?revision=N`，返回历史快照及当前有效性。`GET homework` 返回实际 `asset_ids/question_ids/case_ids/job_ids`；从历史即可恢复，不能用安卓本地演示数据代替服务端结果。

学生端首次改密须同时提交初始密码与新密码；开始新作业前先核对年级与可用教材。打开解答只追加带时间的帮助记录，不倒改之前独立作答的条件；之后的新订正继承已看答案状态。日报将原答正确、错题订正、空白补做分别处理，`completions` 列出待完成与补做，`corrected_resolved` 只统计确有错误祖先的新订正。

上传/读取票据另外指向 `PUT/GET /v1/assets/{id}/content`，这是 `UploadTicket`/`AssetAccess` 中的对象传输地址。上传客户端只发送 `required_headers`，不把业务 Bearer 扩散到 URL。票据绑定资产、签发主体及有效期；每次传输重新核验主体、角色和学生授权。原图只写私有目录，文件0600、目录创建0700；原图不可覆盖。PUT 成功仍为 `awaiting_upload`，`complete` 验证真实 SHA256、大小、格式、完整解码和尺寸后才变为 `uploaded` 并撤销上传票据。同票据同内容可在完成前重传，过期通过原资产续票恢复。

上传照片须先把 EXIF 方向归一到实际像素（安卓采集模块已处理）；每张最多4 MiB、2400万像素；每个模型任务最多4张、总计8 MiB。只接静态 JPEG/PNG/WebP，拒绝伪造格式、损坏图片和声明不一致。未读出题目时保留覆盖说明，客户端提示重拍或核实，不得把空题列表当作全对。

本机测试启用 `CORRECTION_MODE=user_original`：DeepSeek 从整张照片提取题目、选项和作答候选；Astra 随后只看原图及题号定位线索，不接收首轮学生答案或判定，独立核对笔迹、空白、负号、印刷示例与填空数，再保存题目和作答版本。两次原始观察保留在私有任务审计中；独立读图失败不会静默沿用首轮答案。不先定位题框、不叠加网格、不逐题裁图，原文件保持不变。题目关联的全页 `regions` 只表示来源照片，不表示已定位单题。`annotations.reading_order` 保存模型输出顺序，不能按随机 ID 或纸面题号重排。

`user_original` 保存识读后先由 DeepSeek 判卷，复杂或需要复核的结论交给 Astra。v22 在独立读图内容绑定有效且已配置 Astra 复核时，关闭 DeepSeek 首轮深度思考；同一作业、同页的简单题可合并最多两题请求，响应按私有 ID 逐题绑定，合并调用的用量只计一次。只有题干、学生全部计算步骤和参考答案通过本地精确有理数验算，且无识读疑点时，才省略重复的 Astra 判题；图形、应用、证明、不支持的算式、模糊转写、模型冲突或缺少纠正答案仍复核，不以模型自报信心放行。调用身份以请求配置记录，不能用中转站返回的模型别名误触发第二次 Astra 复核。若判题发现明确的原图与作答差异，不把错误转写判为正确，也不悄悄改写学生答案。

独立读图凭据只保存于私有 annotations，并绑定原图、题干、选项、实际作答及逐空内容；版本变更、人工编辑或历史记录缺少凭据时，不使用快速通道。`WORKER_CHECK_CONCURRENCY` 默认 3、允许 1–8，实际还受主模型和复核模型并发上限约束（当前各为 2）。空位完成后可立即补下一题，每轮最多领取 8 题、领取时间窗口 30 秒；已经开始的调用等待完成后才轮到学习等其他任务。只并行模型核验，数据库会话与保存串行；SQLite 仍只启动一个 worker 进程。已完成题目增量显示，纸面顺序不受完成先后影响；外部模型耗时仍可能延迟剩余任务，不能保证固定时延。`--once` 保留单次串行语义。其他模式的行为见 [provider 文档](xueban_backend/providers/README.md)，不能用其验收结论代替当前模式。

模型返回的知识点标签先与学生已授权、已审核的教材图谱精确对应；无法明确对应时使用带原文依据的语义映射。只有可用的识读与判断可产生机器学习证据，所有证据保留 AI 来源。没有合适教材节点时累计原始作答，不伪造图谱归属。学生看到的掌握状态还取决于作答帮助条件、同题关系和独立迁移表现，单次订正不能证明独立掌握。

逐空识别记录同时绑定题目和作答版本，任一版本变化即不复用；模型调用与失败保留计费尝试审计，截断不自动降级为无依据答案。整图识别并不意味着整份作业只调用模型一次，也不保证识别或判题准确率。真实照片仍需独立标注和重复验收；单次通过不能用于宣称商用准确率。

填空变式按可见空位与声明数量建立生成契约，逐空检查字段和答案，失败反馈具体字段并最多额外生成一次；独立解题核验不能跳过。打开失败记录不会自动重试，只有明确点击重试才重新调用。缺少已审核教材图谱时，知识页面显示真实学科样本与独立列出的练习记录，不伪造节点；首次未记录应用内帮助、帮助后作答和同题重做分开统计，练习记录不直接升级正式掌握状态。

普通数值填空等式另有有理数代入校验：正负号、分数和小数按原式运算，明确不成立的参考答案不能发布，即使出题与验算模型一致。首轮核验失败后的第二轮，可使用已配置的 Astra 独立验算，仍受原有两轮与总时限限制；不增加第三层模型。该校验不是通用数学求解器，复杂或不支持的语法仍依赖模型审核。历史已验证题也在读取、提交、拍照提交和反馈统计时重新检查；失效参考的原始记录保留，但不继续判学生对错或计入统计，学生需明确重新生成。

## 事务、版本与恢复边界

纠错命令使用 `Idempotency-Key`，作用域为真实主体、授权学生、实际操作路径；同键同载荷重放原响应，同键不同载荷409。纠错、图谱和练习跨域保存先按学员串行，再锁任务/作业/题目并重新核验授权与版本；不在外部推理期间占用这些写锁。学习层追加接口另使用明确的行 ID、题目/图谱版本进行 CAS。客户端应在新的操作前读取最新状态；重试旧操作保持原键和原载荷。

数据库通过显式迁移演进，当前新增 `0016_student_registration`，前序覆盖账号、纠错、学习层、教材绑定与举一反三。应用不使用 `create_all` 绕过迁移。授权由服务层执行，数据库保留归属外键；这里没有宣称已部署 PostgreSQL RLS。

### 临时学生注册（默认关闭）

启用前执行迁移，并设置 `SELF_REGISTRATION_ENABLED=1`、`SELF_REGISTRATION_TENANT_ID` 和 `SELF_REGISTRATION_CAMPUS_ID`；必须指向已存在且相互匹配的租户与校区，同时需要正常的账号签名配置。本地试用仅监听回环地址，不能把这个短密码注册入口未经评估直接开放到公网。

- `GET /v1/auth/registration`：公开返回 `enabled`、`username_preview`、`grades`，关闭时为 `false`、`null`、`[]`。
- `POST /v1/auth/register`：公开接口，必须提供 `Idempotency-Key`；请求仅允许 `password`、`confirm_password`、整数 `grade`（5、6、7、8）。成功返回 201 与 `username`、`student_id`、`grade`，不自动签发登录令牌。
- 服务端分配「荣耀学员001」起的编号，取已有同前缀最大数字和持久计数器的较大者再加一；同一事务写入主体、学生、唯一学生授权、密码哈希和年级档案。并发分配串行化，同键同内容恢复原结果，同键不同内容409。
- 密码沿用学生的3–15位 ASCII 字母/数字规则，必须填写两遍。仅保存 scrypt 哈希与带服务端秘密的请求摘要，不保存明文；注册请求恢复记录随账号删除，编号计数器保留。每IP每分钟最多20次新注册；已成功请求可用原键恢复，恢复查询另按请求键每分钟限20次，错误密码或变更载荷同样计入。客户端遇限流或暂时关闭仍保留原注册键，稍后重试不会重复开户。
- 年级档案初始化为 `grade` 与 `textbook_revision=publisher_latest`，不替学生强选具体教材，也不生成虚假学习证据。知识图谱仍遵循既有教材范围与审核状态。

机器知识关联的 `observation_bindings` 绑定 `attempt_id + attempt_recognition_revision + decision_id`；老师关联为 `null`，适用于本题。0015只从现有有效证据中回填可验证的旧机器作用域，并标为 `legacy_evidence`，不声明模型独立读取过这些历史作答。来源不可验证的旧关联继续留档，但不参与新证据，也不会因升级自动触发批量付费回填。新作答与新识读照常映射。R1仅撤回被替换观察的机器归因，其他作答的证据 ID 和原文保持不变。

升级前停止写入并备份；0015降级要求全部待删除绑定都可由当前有效证据完整重建。已有新直接绑定、识读变更或撤回记录时会拒绝降级，不能通过强制删列回滚；应使用经验证的升级前备份进行隔离恢复，再审核切换。

任务表是事务持久队列。PG通过 `FOR UPDATE SKIP LOCKED` 领取，提交租约后再执行模型，模型调用期间不持有数据库事务。进程崩溃、重启后可重新领取过期租约；完成时校验租约 token 和期限，旧 Worker 不能覆盖新结果。最多执行3次；429/5xx等暂时失败有限退避，超时保留费用未知并终止，等待用户明确重试。重启恢复可能重复一次已计费推理，不能保证外部模型恰好调用一次；每次执行的开始、元数据及费用未知记录持久保存，业务结果不会重复生成。

题目 revision、答案 recognition_revision、原图哈希及当前 Attempt 构成计算输入。`case_revision_at_submission` 只记录提交时CAS，不因提示、求助或暂停而误杀有效计算。真正内容变化后，进行中的旧任务变为 `superseded`，旧判断和提示失效，旧照片/订正链和历史版本保留。新订正照片须含同一道题的完整题干；单一完整题干不要求自由标签逐字一致，同一作业内存在多个相同题干时额外核对小问标记，指数和数字间隔不归并，不一致就待老师核实。新订正沿父链继承已知提示/老师帮助，不能用客户端 `none_reported` 抹掉真实帮助记录。

三种失败分别处理：输入不合法422或版本/幂等冲突409；外部执行失败为 `failed/retryable_failed` Job；模型无法可靠判断是成功 Job 中的 `unable_to_judge/needs_review`。求助被处理也不等于答对。只有受权老师新建的 `origin=staff, validity=confirmed, verdict=correct` 判断才关闭为 `verified_resolved`。

## 测试

完全离线、本地 SQLite 替身：

```sh
services/backend/.venv/bin/python -m pytest services/backend/tests -q
```

实际 PG 的专用测试库（不能用真实学生库）：

```sh
# DATABASE_URL 用于独立迁移检查；BACKEND_TEST_DATABASE_URL 用于临时测试schema。
services/backend/.venv/bin/alembic -c services/backend/alembic.ini upgrade head
services/backend/.venv/bin/alembic -c services/backend/alembic.ini upgrade head
services/backend/.venv/bin/python -m pytest services/backend/tests -q --require-postgres
```

`BACKEND_TEST_DATABASE_URL` 未配置时 `--require-postgres` 会失败。PG夹具为每项用例建立随机 schema、执行正式迁移，结束仅删自己的 schema；签名私钥在测试运行时生成。`test_correction_flow.py` 使用真实合成 JPEG、HTTP 路由与持久 Worker，模型端口注入受控替身，没有外网请求。覆盖私有票据、哈希/解码错误、隔离/撤权、幂等/回滚、任务租约重启与旧Worker隔离、未知定位、三类失败、输入失效、提示→订正→再检、老师独占确认、求助与确认分离，以及 PG 并发同键/双Worker领取。

离线合成通过只证明工程边界，不证明真实照片准确率、模型成本或真机体验。实际本机 PG、真实模型、安卓设备联合验收由集成阶段另行记录。


## 长期账号与管理员开通

学员以稳定用户名和密码登录，一账号绑定一个既有 `student_id` / `principal_id`。用户名不作为作业或学习证据主键；即使误添其他学生授权，学生账号仍只能访问绑定学员。初始密码和管理员重置的临时密码用于登录及改密时的当前密码验证，必须改密后才可读取作业；普通后续登录不强制重复改密。账号建立后禁止使用旧配对码兑换会话。

| 接口 | 请求 / 返回 |
|---|---|
| `POST /v1/auth/login` | `{username,password}` → `{access_token,expires_at,must_change_password}` |
| `GET /v1/auth/account` | `{username,must_change_password,student_id}`，管理员 `student_id=null` |
| `POST /v1/auth/change-password` | `{current_password,new_password}` → 新会话，撤销原主体全部旧会话与已签发图片票据 |
| `POST /v1/auth/logout` | `{}` → `{logged_out:true}`，撤销当前会话 |
| `GET /v1/admin/student-accounts` | `{items:[{student_id,student_name,username,active,must_change_password,campus_id,principal_id}],campuses:[{campus_id}]}` |
| `POST /v1/admin/student-accounts` | `{username,student_name,campus_id,initial_password}` → 201，直接返回新账号item |
| `POST /v1/admin/student-accounts/{student_id}/reset-password` | `{new_password}` → `{student_id,must_change_password:true}`，撤销目标学员全部旧会话与图片票据 |

所有POST需要 `Idempotency-Key`。密码字段只存在于请求处理内存；数据库保存随机盐的 scrypt 摘要，以及由服务器密钥和独立随机盐生成的幂等HMAC摘要，不保存密码、Bearer或可离线枚举的裸快速请求哈希。创建/重置的重放会检查密码版本，已被后续改密则409 `stale_input`，不能继续交付旧初始密码。改密响应丢失时，可用**原token、原key、完全相同请求**恢复先前生成的新会话；旧token不能发起其他新操作。刷新丢失内存密码后需重新输入，不应把密码写进浏览器持久存储。

用户名去两端空格、转小写，允许3—64位ASCII字母数字及 `._-`，首位必须字母或数字；姓名1—80字符且不能纯空白。学生自行设置的新密码须为3–15位ASCII英文字母或数字（`A–Z`、`a–z`、`0–9`），可以全数字、全字母或混合，不接受空白、中文或符号；该规则仅校验学生的新密码，原有长密码仍可登录并用于验证 `current_password`。管理员自身密码及管理员签发的初始/重置临时密码继续使用15–128字符、UTF-8不超过512字节的规则，保留空白及Unicode，不要求字符类别混合。密码使用标准库scrypt `N=2^17,r=8,p=1`、每次随机16字节盐和恒时比较，遵循 [OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) 参数。每账号连续5次失败锁定15分钟；数据库持久限流同时限制请求IP和不存在的用户名，重启不会重置。对外身份来自已配置的RSA issuer/audience/有效期及数据库授权，不接受请求声明角色。

管理员必须拥有显式 `principal_campus_grants`，只能列出/创建/重置该租户授权校区的学员；学生授权不等于校区管理授权。创建学员不会向管理员自动增加学习数据授权。没有公共自注册接口。

首次部署无需创建占位学生。先配置真实issuer与仓库外签名文件，并执行迁移，再由有数据库权限的本机操作员运行：

```sh
# 密码写入自己拥有的0600普通UTF-8文件；拒绝符号链接，不传命令行明文。
PYTHONPATH=services/backend python -m xueban_backend.admin bootstrap-admin \
  --tenant-id tenant_example --campus-id campus_example \
  --principal-id admin_example --subject admin-example \
  --username example-admin --password-file /private/path/admin-password.txt

# 给已有学员主体绑定长期账号，保留原student/principal及历史。
PYTHONPATH=services/backend python -m xueban_backend.admin set-password-account \
  --principal-id actor_example --student-id student_example \
  --username example-student --password-file /private/path/student-password.txt

# 仅向已有同租户campus_admin显式授予校区管理范围。
PYTHONPATH=services/backend python -m xueban_backend.admin grant-campus \
  --principal-id admin_example --campus-id campus_example
```

`bootstrap-admin`建立独立管理员及校区授权，默认首次改密，拒绝覆盖已有不同角色/归属或重置既有账号。`set-password-account`允许明确重置既有同用户名同绑定账号，默认首次改密；只有本机操作员明确加 `--no-must-change` 才不要求首次修改。CLI不显示密码或token。公网部署仍需HTTPS与部署层请求体/访问日志控制，当前本机启动器只用于受控联调。

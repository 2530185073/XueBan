# 服务端 Ark 候选适配器

本包提供同步 Worker 调用组件；公开 API、授权、作业状态机和数据落库由后端 Worker 负责。导入不启用模型；无凭据或无明确 base URL 时拒绝调用。部署端负责注入已核实的服务域名与凭据，不从学生客户端接受这些配置。

## 当前整页双模型路径（v5.2）

`CORRECTION_MODE=two_model` 使用 `TwoModelWholePagePipeline`。默认按 `answer_slot`
记录，`CORRECTION_TARGET_UNIT=question` 是尚未通过原图验收的实验选项。
首轮每张完整原图并行交给两路模型；每路都独立读取题面、学生笔迹并判断，
没有先做像素切题。主路使用 `ARK_*`；第二路使用 `REVIEW_API`、`REVIEW_MODEL`、
`REVIEW_THINKING` 和 `ZYAI_*`（缺省凭据可回退 ARK），不能仅据默认值推断部署型号。

完整题面、选项、作答和判定一致且无实质疑点时，形成明确的 AI 候选；不升级为老师核实。
格式、表述和判定差异可进入批量图像复核。每批最多六个目标，单批失败不使整页失效。
真实笔迹或必要条件分歧另作独立原图核读；实际判题分歧由 `VISUAL_REVIEW_MODEL`
指定的模型裁决。它不是简单多数票，也不能用相同答案补写原笔迹。

v5.2 允许明确的计算指令翻译及完整计算题头差异由 AI 核对；必须保留完整相同算式，
不消除数字、正负号、变量、单位、声调、运算要求或题号冲突。复核选源绑定原始题面、
作答、引文和来源哈希；选第三路观察时还需与实际原题绑定。未知、超时和看不清分别记录，
不会据此形成确定薄弱点，也不自动创建老师求助。

`items[].assessment.verification` 记录 matched / reviewed / conflict / degraded 等状态、
实际原因及独立核读依据。完整观察和调用账本保留在内部审计中，公开接口不泄露模型配置。
图像与同题订正仍由持久化层的资产、版本、权限和当前作答指针约束。

同一作答的原图重核由 `original_recheck.py` 限定一个原有目标，只传题号、位置与单元提示，
不传旧答案或旧判定。服务层每组作答原图最多派发两次；恢复成功修订识读版本，
保留原作答 ID、照片、时间、帮助记录及历史快照。任务完成不等于恢复已应用，
客户端必须读取真实 `last_outcome`。学生重新作答仍走新照片订正流程。

整页初读每方最多一次实际供应商请求，不隐式重试。主路总截止配置为 200 秒，
第二路和独立视觉客户端为 118 秒；后续复核另有调度预算，因此这些数值不是整页 SLA。
Responses 默认 SSE，须收到完整终结响应。服务失败与已经派发的计费状态分别审计，
不能推断超时免费。线上效果与工程测试分别验收，现有原图质量和时延阻断见
[持续交付记录](../../../../docs/CONTINUOUS_DELIVERY.md)。

### 结构导航实验（默认关闭）

只有显式设置 `STRUCTURE_NAVIGATION_ENABLED=1` 才启用。每页第一路完成后，第二路接收
仅含 `label + target_unit` 的导航并重新通读原图；多页按各自完成顺序派发，不等最慢页。
不传题干、选项、学生答案、判定、空数或内容锚点。导航不合法或第一路失败时，第二路
只执行一次普通整页请求；已派发的第二路失败不会再收费重试。R1 原图重核始终不用此模式。

额外返回题目保留，找不到的导航另存报告，不按清单删除、补题或声称覆盖完整。
模型仍需通过原有内容和题源检查。审计记录导航/来源哈希、结构共享和内容未共享；
实际共享导航时 `independent_before_comparison=false`。实验结果增加独立协议后缀。

已有缓存显示 P07 的结构对应改善；P19 完整小题实验仍有真实内容和范围分歧。
一次新的覆盖探针（删真实题号、加假题号并反转顺序）成功找回真实题且拒绝虚构题，
但单路耗时 116.094 秒。未证明整体提速、保留集准确率或真实双栏/重复题号的覆盖，
不得默认启用到学生体验环境。

### 整页格式坏项隔离（默认关闭）

`PARTIAL_PAGE_OUTPUT_ENABLED=1` 只作用于非 R1、未启用结构导航的 `answer_slot`
初读。完整 JSON 中的坏项按原 schema 隔离，有效项仍须两路实际观察一致才可使用。
坏项不修补、不补第二票；部分页不再追加对齐或付费复核，服务未完成不会显示成字迹模糊。
页壳、截断、传输、密钥和 JSON 错误仍整路拒绝；Responses 失败尚不支持逐项保全。

原请求账本仍保留 `invalid_output` 与实际用量，不增加尝试。开关只接受 `0/1`，
不影响已有记录；回退设置为 `0` 并在队列空闲时重启 Worker。实现及证据边界见
[逐项输出隔离](../../../../docs/RECOGNITION_PARTIAL_OUTPUT_PLAN.md)。

## 旧单路与三模型适配器

`CORRECTION_MODE=multimodel` 和 `single` 保留兼容路径。下方逐题 API、默认提示版本和
重试参数属于旧适配器，不能据此推断上面的整页模式行为。底层工厂未设置模式时仍为
`single`；运行脚本必须显式选择。

```python
from xueban_backend.providers import ArkClient, ArkSettings

# verified_base_url / server_api_key 来自受控服务端配置，不写入代码或日志。
with ArkClient(ArkSettings(base_url=verified_base_url, api_key=server_api_key)) as client:
    candidate = client.generate(
        role="recognition",
        text="只识别这张合成作业图中的原始题目和作答。",
        images=(synthetic_png_data_url,),
        output_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        thinking=False,
        max_tokens=512,
    )
```

默认主模型为 `deepseek-v4-1-flash-260910`，使用 Chat Completions。Worker 可配置 `ARK_FALLBACK_MODEL=gpt-6-astra`，通过 Responses API 复核。提示版本为 `photo-correction.v19-two-stage-review`：DS 判为 `correct` 必须交 GPT 核对；主模型判为无法判断、有实质疑点、缺少错题答案/改法，以及图形、应用、证明、多空题均升级。上游失败、格式不合法或逐空绑定失效也进入有限兜底，所有实际调用保留审计。

DS 与 GPT 的有效结论分歧时不得放行 `correct`。GPT 无法确认、缺少正确答案或改法、服务失败或审核未完成时，返回 `unable_to_judge`/`needs_review`，不猜测为错题、不生成未经依据支持的纠正内容，并保留重试路径。只有 GPT 明确判错且证据充分时，才允许带红色 `*` 输出错误。系统不依赖或展示模型概率估计。所有凭证仅传给 Worker。商用教材范围由服务端学生档案和授权出版社确定；范围未命中先补充信息，已确定范围但无已审核知识点候选时严格返回空标签。

`recognition / check / hint / recheck / knowledge_extract` 各有独立系统提示；知识点角色只是接口能力，不会部署或更新知识图谱。旧 Ark 适配器默认思考开启、`max_tokens=4096`，可单次显式覆盖，最大16384（包含该模型思考 token）；整页有界适配器上限32768，本路径每方配置24576。Astra 使用 Responses API、`store=false`、结构化 JSON Schema 输出；因 Responses strict 子集限制，服务端会在解析后用原始业务 Schema 再校验。两类接口都不发送未经确认的温度或 top-p 参数。

使用 Chat Completions 的 `response_format={"type":"json_object"}`，另在服务端做 JSON Schema 校验；不依赖上游严格 schema 输出。只接受一个完整 JSON 对象，拒绝重复键、NaN/Infinity、Markdown 包装、输出截断、工具调用、额外未允许字段和不匹配的数据类型。Schema 仅接受内联定义和本地引用，不检索远程 Schema。

`CandidateResult.status` 恒为 `candidate`，`data` 是已通过结构验证的候选；结构验证不证明事实正确。`metadata` 保留请求模型、实际返回模型、request/response ID、usage、总耗时和每次尝试。模型输出不会触发已确认判定、学生掌握度或订正完成。

连接仅接受显式 HTTPS URL，禁止 URL 凭据、查询和片段，不跟随重定向，不读取代理环境变量。仅接受 PNG/JPEG/WebP 的 base64 data URL，不替调用方下载图片；限制单张4MiB、共8MiB、最多4张。图片仍需上游完成文件解码/安全检查与授权。本适配器的魔数检查不能代替完整图片校验。

默认并发2、排队最多1秒、单次 HTTP 各 I/O 阶段超时30秒（连接最多5秒），最多3次尝试。`total_timeout_seconds=90` 设置整次调用预算，覆盖排队、退避、请求和读循环；每次请求的各 I/O 超时按剩余预算缩短，每个读取块之后检查 monotonic 截止时间。到期返回 `deadline_exceeded`、保留已经发出的尝试记录并关闭响应、释放槽位，所以持续小块响应不能无限占槽。

这不是绝对墙钟硬中断：同步 HTTPX 无法中断已经开始的阻塞 I/O，该阶段仍使用进入时的超时；普通读体检查可能晚最多一个已启动 I/O 阶段的超时（另有操作系统调度误差）。HTTPX 内部的响应头解析或跨多个阶段的请求还不能由外层逐块检查，因此不承诺所有传输形态都满足该偏差上界。本实现不启动超时后继续消耗资源的后台线程。生产任务仍须由 Worker 的进程/任务级执行期限管理；超时不表示上游停止计算或不计费。

仅429/5xx有限重试，等待最多2秒且不超过剩余总预算；超时或传输失败不自动重试。推理调用重试没有写入幂等承诺，每次 `charge_status` 都是 `unknown`，即使失败也不能推断免费。调用层须持久保存尝试记录并设置费用限额。

`ProviderError` 只携带固定安全错误码与经过筛选的尝试元数据，不包含请求、响应正文、密钥或原始异常消息。本包不记录日志；调用者不能把输入、`data`、配置对象展开后的密钥或完整异常局部变量写入日志。`repr` 隐藏配置中的 URL/密钥与候选正文。

离线验证：`python -m pytest services/backend/tests/test_providers.py -q`。测试全部使用 `httpx.MockTransport` 与合成图片/凭据，不读取部署密钥、不访问 API、不证明真实照片识别质量或生产费用。

## 纯模型照片纠错流水线

`correction_pipeline.py` 复用上述 Ark 适配器，当前不调用腾讯或百度 OCR。识别及首轮判题使用主模型，符合复核门禁时由 GPT 复核；不同角色调用不构成独立模型验证。

```python
from xueban_backend.providers.correction_pipeline import CorrectionPipeline, PipelineImage

# image_data_url 和尺寸由 Worker 从已授权且完成解码校验的原始资产取得。
image = PipelineImage("asset_example", image_data_url, width_px, height_px)
with CorrectionPipeline.from_env() as pipeline:
    recognition = pipeline.recognize(images=(image,))
    # Worker 保存每空候选、生成数据库 ID/版本，并提交不可变快照后再调用：
    # pipeline.check(images=(image,), question=question, attempt=attempt, input_versions=versions)
    # pipeline.hint(..., decision=decision, level="direction")
    # pipeline.recheck(images=(original_image, corrected_image), ...)
```

`from_env()` 只读取显式环境配置，不查找或打开 `.env` 文件；未配置 base URL/key 时保持禁用。生产主/兜底客户端默认最多一次尝试、单 I/O 180 秒、total 200 秒。Worker 也可注入自行配置的客户端。识别最多输出 80 个独立空位，识别输出预算 16384，判题/再检 8192，其余角色 4096。整页逐题调用的总次数、总费用与任务期限由 Worker 控制；单次限制不是整页预算。

稳定接口：

| 方法 | 输入 | `PipelineResult.data` |
|---|---|---|
| `recognize(*, images)` | 1–4 个 `PipelineImage(asset_id, data_url, width_px, height_px)` | `items` 每题／每空一项；`coverage` 保留漏识别与范围未知 |
| `check(*, images, question, attempt, input_versions)` | 已保存题目和当前作答快照，以及契约 `InputVersions` | `Decision` 的业务字段与内部 `readback`、`uncertainties`；不生成记录 ID／时间 |
| `recheck(...)` | 与 check 相同；图片必须包含题干资产和本次作答资产 | 只检查当前作答，不接收旧判定或旧提示作为依据 |
| `hint(*, images, question, attempt, input_versions, decision, level)` | 同一内容版本的来源判定；层级 `direction/key_step/staff_handoff` | 受控提示内容、来源判定 ID／版本、是否需要坐班帮助，不含标准答案 |

`PipelineResult.status` 恒为 `candidate`，另有 `prompt_version` 和 Ark `metadata`。`metadata.model` 为上游返回型号，可为 null；`requested_model` 为请求型号；`attempts` 保存尝试和费用未知状态。本地安全提示回退不调用模型，`attempts=()`、`model=None`，不能当成一次模型成功。

自 `photo-correction.v3` 起，固定任务规则通过 `ArkClient.generate(instructions=...)` 放入 system；该参数仅接受服务端代码定义的规则，不能传入图片文字、学生输入或客户端策略。图片与候选快照只出现在 user JSON 的 `images/task_input`。候选 `correct/incorrect` 合法，禁止的是自行 `confirmed`。`uncertainties` 只容纳影响识别、题目对应或数学结论的实质歧义；印刷/合成属性或无法确认身份不是拒判理由。没有非必需步骤时仍可核验终答，明确要求的证明缺失则保留不确定。若模型仍违规返回无关 uncertainties，代码继续降级并保留原文，不通过后处理删除警告放行。旧版调用与审计保留，提示词改动的真实效果须重新实测。

自 `photo-correction.v4` 起，再检明确只读取 `attempt_asset_ids` 对应的当前作答，旧答案与新答案不同本身不是冲突。当前图自身的涂改、多个作答或错题对应歧义仍保留。识别字段只移除明确的 `Question:`／`Student answer:` 等字段前缀；`normalize_field_for_comparison(value, field_name)` 让 Worker 题干匹配和 readback 比较共用同一保守规则。它不删除数字题号、不做模糊匹配、不合并数字间隔、不消除指数、括号或负号；readback 原文保留供审计。

`question` 至少含 `question_id/revision/stem_text/regions`，`label` 可选；`attempt` 至少含 `id/question_id/captured_question_revision/recognition_revision/answer_text/answer_regions/recognition_status/asset_ids`。`answer_status` 可显式为 `present/blank/unclear`；省略时非空文字按 present、null 按 unclear，不能把未识别当空白。`input_versions` 使用完整契约字段。来源内容版本必须一致；仅 `case_revision_at_submission` 改变不会使提示失效。Worker 仍须在调用前授权并在落库／展示前再次校验当前版本、作答指针和来源有效性；此组件不能查询数据库完成这些检查。

识别项包含 `label/asset_id/stem_text/answer_text/answer_status/regions/answer_regions/uncertainties`，并由代码补充 `recognition_status=ready|needs_revision`。原始笔迹与题干分开；空白、看不清、涂改分别记录，不用标准解法补笔迹。`coverage` 仅为 partial／unknown，模型不负责声明整页无漏题；空 items 也不表示整页正确。

区域严格使用对应资产的 `original_pixels`，拒绝未知资产、负数、零尺寸、越界、把旧作答图位置用于新作答等情况。图片尺寸必须与实际发送的图像像素一致；旋转或缩放需要由上游维护资产版本与坐标关系。

检查和再检使用首轮保存的题干和学生作答；原图用于核对图形、表格和明确缺失条件，不允许用推导答案覆盖学生笔迹。逐空结果绑定保存的原始作答，顶层与逐空正确性矛盾时不能放行全对。`readback` 保留原输入供内部审计；`origin=model`、确认字段恒为 null，依据类型为 `model_hypothesis`。原始审核记录不直接进入公共 Decision；Worker 只选择公共字段，前端仅显示受控审核摘要。

提示不依赖模型自报“没有泄露答案”。模型只能选择符号、分数、分配律、单位、代入等有限提示类型和可选关注区域，服务端用固定模板形成学生可见内容。自由文本、标准答案字段被 Schema 拒绝；不确定则转为坐班核实。正确候选的提示请求只返回读题自检，不人为制造教师任务。这个版本的提示表达能力受模板限制，后续扩充模板需单独评测。

技术故障通过 `ProviderError` 抛出，不伪装成无法判断或空识别；截断、429、超时、结构不合法和后处理发现的越界均保留可得调用记录／用量。代码不会生成 confirmed、掌握度、图谱更新或订正完成状态。

流水线离线验证：`python -m pytest services/backend/tests/test_correction_pipeline.py services/backend/tests/test_providers.py -q`。全部为合成图片与 MockTransport，覆盖逐空拆项、看不清、漏定位、负号／上标／字母歧义、版本与新作答绑定、提示泄露防护、越界和技术失败。它们证明协议与代码约束，不证明照片识别、定位或数学判断的真实质量。真实调用与授权学生样本评测由集成流程另行执行。

`photo-correction.v5` 将判题结论要求限定为 check/recheck；recognition 只读题与作答，同题号多个独立求值目标也须分项并保留共享条件。拆分仍是模型候选，`coverage=partial/unknown` 不表示整页已无遗漏。

`photo-correction.v6` 进一步明确识别输出的顶层只能是 `items` 与 `coverage`，提供合法空结构示例，并区分模型字段与服务端计算的状态字段。不得添加状态/元数据或包装层；服务端仍拒绝所有契约外字段，不以删字段、自动解包或修补 JSON 代替校验。JSON mode 无法保证 Schema 遵循，这些提示改善仍需整页实测验证。

`photo-correction.v7` 不再让模型定位题目。识别改为简短提示词，只按原样抄录每个小问/填空的 `page`（第几张图）、`label`、题干、学生作答、`answer_status` 和 `uncertainties`；服务端把 `page` 换成 `asset_id`，题区记为整张图（只表示题目在哪一页，不代表位置），`answer_regions` 为空，每道读出的题都建立题目记录。check/recheck 不再返回 `first_error_region`，hint 不再返回 `focus_region`，二者恒为 null；模型输入只给题干/作答所在图片的 ID，不给坐标。两个网页不绘制整页大小的框。老师在工作台手动框选的区域仍照常保存和显示。

输出失败在 `attempts[].diagnostic` 保存层级（外层 JSON/响应结构、候选 JSON/Schema/业务语义）、规则码和已知字段路径；数组只记录索引，未知字段替换为 `*`。不保存校验错误原文、实例值、Schema 值或响应正文。Worker 现有调用账本会保留这些诊断与 usage/请求 ID，外部失败码和候选/人工确认边界不变。部署前未记录诊断的旧失败不能凭后来一次成功重放倒推出原因；诊断也不等于自动重试授权。

## 同平台知识资料向量

向量模型独立于对话模型，当前使用 `skylark-embedding-vision-251215` 的 `/embeddings/multimodal` 接口。没有把对话回复伪装成向量。配置与调用为：

```python
from xueban_backend.providers.embeddings import EmbeddingClient, EmbeddingSettings

with EmbeddingClient(EmbeddingSettings(
    base_url=verified_base_url, api_key=server_api_key, dimensions=2048,
)) as client:
    document = client.embed_document("乘法分配律：括号外的因数与括号内的每一项相乘。")
    # 建索引时保存document.space；后续独立进程从索引配置恢复，禁止临时重选。
    index_space = document.space
    query = client.embed_query("展开括号时漏乘了一项，需要检查什么？", index_space=index_space)
    document.require_compatible(index_space)
```

使用相同配置入库时也传 `embed_document(text, index_space=index_space)`。完整空间包含模型、维度、以及绑定端点和查询/语料指令对的profile；换任意一项须建立新索引。1024/2048维可选，本轮固定2048；输出经稳定L2归一化，拒绝空、非有限或零向量。每个请求处理一段文字，不把多段独立文档误当一个batch。单段不超过100,000 UTF-8字节，另保留更保守的字符限制；8,000 token仍需提供方校验。

向量不自动重试，错误只含安全错误码/请求元数据，默认I/O20秒、总预算30秒。与聊天组件相同，同步HTTPX预算不是生产Worker的硬中断保证。费用保持未知。没有创建索引、数据库或更新掌握度；相关性分数只是资料召回依据。知识库Rerank是另一服务及权限体系，本轮未接入。

2026-10-16在同一有效账号下完成中文语料/查询两次真实调用，均2048维；聊天五角色也完成合成图片实调。见 [实测证据](../../../../docs/evidence/ark-20261016/README.md) 和 [服务选型](../../../../MODEL_SERVICE_PLAN.md)。复验优先使用离线测试；真实调用必须显式选择、保存请求及费用证据，不能在CI中自动消耗配额。

## 语义对齐派发预检

两路未配对来源仅在所有跨路候选均被既有印刷条件/完整选项规则排除时跳过对齐请求。任一兼容边存在就发送完整原输入；异常保留原路径。`multimodel_audit.alignment_dispatches` 只记调度原因、数量及来源哈希，没有虚构调用或用量。兼容只是必要条件，不能代替模型对齐和后续核验。当前 provisional/split 仅审计；未来消费这些结果时须重新评估预检。见 [证据与边界](../../../../docs/RECOGNITION_ALIGNMENT_DISPATCH.md)。

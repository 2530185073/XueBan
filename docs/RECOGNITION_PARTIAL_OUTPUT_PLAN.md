# 整页返回的逐项隔离

## 状态与证据边界

实现已通过独立审查并整合到开发分支。仓库默认关闭，本机显式开启及验收状态见 [持续交付记录](CONTINUOUS_DELIVERY.md)。本阶段未调用真实模型，未重跑 P02 付费测试。

P02 既有实验仅能确认一次 79.244 秒、HTTP 200 的请求，在 `items[11].answer_text` 触发 `candidate_schema/anyOf`。拒绝的正文没有保存，不能知道错误类型、总题数或多少题可保全。本候选的合成故障注入证明控制流行为，不证明 P02 已修复、识图准确率提高或真实链路加速。

## 目标与不变量

完整 JSON 的一个无效返回项，不应丢弃同一照片中其他已经满足原协议且有两路证据的项。无效项必须留下服务未完成记录，不能被算为正确、错误、未作答或知识点薄弱；不把服务格式问题称为字迹模糊。

- 完整上游提示与 JSON Schema 保持不变；每个合格项仍通过原 item schema。
- 不补答案，不将数字强转字符串，不截短答案，不借另一模型的题目/笔迹修复坏项。
- 不新增请求或自动重试。部分页不进入后续语义对齐、视觉重读或裁决；其他有效项如仍有分歧也保持未完成。缺少一侧有效观察的题不会 ready。
- 原始请求的 `invalid_output`、usage、request ID 和尝试次数保留。业务侧另记 `partial_output`，不把整个请求改记成功。
- 每个 accepted 项保留原始 source_index；每个 rejected 项只公开固定错误路径/规则，不臆造其目标题号。
- 同一路 canonical_label 冲突时整组隔离；坏行即使同时 schema 失败也参与冲突判定。部分页不做跨题号自动/语义对齐，避免丢弃坏行后误认为题干唯一。
- 坏 JSON、冲突重复键、截断/未完成、拒绝、工具调用、错误 envelope、传输失败和密钥回显，仍整路拒绝。

## 当前最小纵切

### 私有失败通道

`providers/partial_output.py` 的 `capture_page_failures(enabled)` 使用 ContextVar，仅在单次初读调用期间允许捕获。线程之间不共享开关，异常退出后恢复原状态。

`BoundedChatClient` 只在完成 envelope/JSON/本 provider 密钥校验后、确认为 schema ValidationError 时创建 `PageSchemaFailure`。此异常仍是 ProviderError，字符串和 repr 只有固定 code；候选正文是一次性私有字段，不在 metadata、日志、API 或 page audit 中。只留 assistant final 的已解析 JSON，不留整个 envelope 或 reasoning。

`take_candidate(schema, secrets)` 检查同一 schema，并重查所有配置 provider 的密钥；取出后即清除 payload。初读、复核、视觉识读、语义对齐四类调用使用同一个完整密钥集合，包含实际调用方及全部配置 provider；成功内容和普通 ProviderError 的 request ID/model 都要检查。命中时整路拒绝、清理 request ID/model 并保留次数与用量，默认关闭部分输出时也保留此安全修复。异常对象的 `__dict__`、pickle 不可进入持久化/日志。未来若归档失败正文，必须另建明确的私有存储与保留期限，不能默认序列化异常；此候选尚未做正文持久化。

### 纯分类

`partition_page(data, schema)` 首先严格验证原页壳，只把 item 内部验证延后：原 array 类型、maxItems、页顶 required/additionalProperties 都保留。它不截数组、不删除未知页字段。页壳失败仍整路失败。

随后按原始顺序校验每项。结果为 `Partition(source_count, accepted, rejected)`；accepted 元素含 source_index 与未修复的深拷贝 item；rejected 仅含 source_index、固定 layer/rule/path。缺失/无效题号、无可用题干及 canonical 题号冲突均不能产生有效观察。`Partition.audit()` 不含题干、答案、失败原值或原始 label。

### 两路组装

`TwoModelWholePagePipeline(..., partial_output_enabled=False)` 默认不改变行为。环境开关是 `PARTIAL_PAGE_OUTPUT_ENABLED=0`，只接受 0/1。

实验开启时，有效项继续进入原有逐项比较逻辑。只有来自两个实际有效 source 的同一题满足原有核验条件，才可 ready。若另一侧本页部分未完成，单路结果用现有 `provider_unavailable` 展示服务未完成，另附 `provider_item_invalid` 细码；不增加 `visual_unreadable`，不通过坏项补齐第二票。

坏项在两路均不可用时，系统只记录页面覆盖缺口，不制造题目/答案。页面提示统计“模型返回项”，不把重复/身份不明项误称为若干道题。其他有效题可继续使用，coverage 始终非 declared_complete。两路全部没有有效项时保持整页服务失败。

## 当前明确未覆盖的路径

实验只作用于 **answer_slot、非 R1 原图复核、非结构导航** 的整页初读。`_partial_output_active()` 对其他路径关闭；R1 的 schema 和原记录回写规则完全沿用原路径，不声称已验证 R1 局部恢复。

只有 BoundedChat 的失败候选通道已实现，包括通过该客户端调用的 Ark Chat 接口。Responses 的 schema 失败仍整路失败；不能因为它提供有效另一侧就宣称失败 Responses 得到保全。未改 Ark 通用适配器、Responses、worker、数据库、API 和 UI。

复用现有服务错误呈现原因，未增加公共协议字段。若以后允许两路均全坏但任务仍返回空成功，则必须先修 worker 的空结果文案覆盖及 UI 空页状态；当前不允许这种成功回写。页壳坏/响应未完成不能走该分支。

## 离线验收

合成响应通过 httpx.MockTransport 调用真实 BoundedChat 解码、schema 检验和 two_model 汇合。提供原生测试 `tests/test_partial_output.py`、`tests/test_partial_output_boundaries.py`；持久化回归为 `tests/test_partial_output_persistence.py`，覆盖真实 HTTP、worker、SQLite/PostgreSQL、重新创建 API 客户端读取、知识投影和报告。

核心用例：一坏多好；双侧坏项不造题；canonical 同组全隔离；默认关闭保持旧行为；页壳错误；无效身份；私有一次性候选；截断/坏 JSON/冲突重复键/拒绝/工具调用/多 choices/HTTP 失败；本 provider 和另一 provider 的密钥出现在坏项时仍整体拒绝；并发开关复位；usage/attempts 仅计一次；没有额外 review 调用；R1 实验开关不生效。

独立边界审查的 27 项测试已整理为 `tests/test_partial_output_boundaries.py`；独立凭据审查的 10 项原生回归已整理为 `tests/test_two_model_credential_boundaries.py`，均无临时目录绝对路径依赖。另有 15 项私有载荷、传输和上下文边界探针通过。主实现测试覆盖严格配置、partial 页全页禁止额外 review、单次用量，以及默认关闭语义。

候选合并上述可提交测试及相关既有 provider/导航/整题/原图复核测试，离线结果为 **681 passed**，仅有既有 Starlette/AnyIO 弃用警告。另有 SQLite 与 PostgreSQL 合计 **4 项持久化回归通过**。整合时已核对基线、补丁和独立审查所验源码 SHA；正式分支回归结果另记持续交付记录。以上使用 MockTransport，并非真实模型识读，不代表准确率验收。

## 后续实证和整合门槛

1. 已完成独立审查；默认关闭回归保持旧行为，故障注入未发现违反上述不变量的情况。
2. 默认关闭入库，本机可单独开启供自然新上传验证；已有记录不会重跑。记录实际配置、激活提交和回退方法。
3. 若要支持 Responses/R1/导航，分别补同等级安全与范围测试，不能隐式复用捕获。
4. 在自然发生的新上传中做受控诊断归档，对同一次响应离线比较“整路拒绝”和“逐项隔离”；无需为补旧 P02 数据重复收费。
5. 无真实失败正文时，只报告此次实现提高了故障隔离能力；不宣称识图更准、更快或坏项已解决。

# 模型服务路线与最小申请清单

**最新执行决定（2026-10-17）：按用户要求，一期优先纯模型完成识别、检查、提示与订正再检；腾讯/百度等专用 OCR 调整到二期、三期辅助评测。本次决定覆盖此前同日“将 OCR 纳入下一批”的安排。首版先在 Mac 独立运行环境及安卓模拟器联调。**

核验与集成状态日期：2026-10-16。用户已选择 **BytePlus AP 官方 ModelArk 的 `deepseek-v4-1-flash-260910`**，作为一期识别、检查、提示、订正再检、知识抽取五个角色的统一底座。主路线已完成真实 API 合成样例联调：五阶段均返回 HTTP 200，角色流程与本地结构校验通过。此结果只证明当前配置能够调用并走通合成流程，**不代表真实手写准确率、数学批改质量或上线验收通过**。

## 1 当前固定配置与证据边界

| 项目 | 当前决定 |
|---|---|
| 官方平台 | BytePlus ModelArk，AP 资源；区域标识 `ap-southeast-1`，马来西亚柔佛（Johor） |
| Base URL | `https://ark.ap-southeast.bytepluses.com/api/v3` |
| 聊天与图片接口 | `POST /chat/completions`；完整端点 `https://ark.ap-southeast.bytepluses.com/api/v3/chat/completions` |
| 五角色统一模型 | `deepseek-v4-1-flash-260910`；角色为 `recognition`、`check`、`hint`、`recheck`、`knowledge_extract` |
| 输出约束 | `response_format: {"type":"json_object"}`，服务端再执行严格 JSON 解析和本地 JSON Schema 校验；不宣称模型原生严格 Schema 保证 |
| 当前真实调用证据 | 自制印刷图片的识别、检查、提示、订正再检，以及自编文本的知识抽取，五阶段均通过；样例标为 `synthetic_only`，发布验收仍未评估 |
| 结果权限 | 所有模型输出均为候选；不能直接确认对错、完成订正或把知识点标为已掌握 |

`bytepluses.com` 的拼写来自官方文档，不是依据用户描述推定。该配置不应称为“中国内地火山端点”或“固定新加坡节点”。官方区域说明允许根据资源调度把部分请求路由到其他区域；区域、数据处理和留存条件需与真实资料使用范围一起核实。[官方快速开始](https://docs.byteplus.com/en/docs/modelark/quick-start)、[区域与跨区路由](https://docs.byteplus.com/en/docs/modelark/region-availability)

最新模型目录对该型号写明仅支持 `json_object` 模式；不要用通用 Chat API 的 `json_schema` 参数列表或旧语言版本说明替代具体型号能力。`thinking.type` 可为 `enabled`／`disabled`，`reasoning_effort` 支持 `none/low/high/max`。此型号的输出预算包含思考与答案；默认预算在目录和专页间有不同表述，项目必须显式指定预算、超时与尝试次数，并记录是否截断。[模型目录](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/model-list)、[型号与参数](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/deepseek-v4-1-flash)

五个角色使用不同提示和证据输入，但仍是同一个模型；第二次提示不等于独立模型验证。HTTP 200 也不等于结果正确，还须通过完整输出、结构和业务证据检查。合成样例只覆盖协议及角色流程，没有测量学生手写、模糊照片、复杂证明或真实现场吞吐。

## 2 现在需要什么资源

| 优先级 | 资源与当前状态 | 需要完成的事项 |
|---|---|---|
| 已接通，继续使用 | AP `deepseek-v4-1-flash-260910` | 沿用当前服务端配置；确认项目实际额度、账单和预算告警，按授权样本继续评测，无需再次索取一批聊天密钥 |
| 已接通纯文本向量 | AP `skylark-embedding-vision-251215` | 当前账号的中文合成语料／查询两次真实调用均 HTTP 200，返回 2048 维并使用相同空间配置；图文调用、索引、检索质量和数据库接入尚未验证 |
| 可后置，未接入未测 | Knowledge Base `base-multilingual-rerank`，备选 `m3-v2-rerank` | 仅在召回排序效果有明确需求时开通知识库服务、专用鉴权和额度；不阻塞拍照纠错 |
| 二期/三期辅助，尚未接通 | 腾讯 `QuestionSplitOCR`、百度试卷切题与识别（多模态） | 一期不以其开通或适配为依赖；后续按纯模型实测暴露的题框、漏题、题答绑定和手写问题开展对照 |
| 专项对照，按需追加 | 百炼 `qwen3.5-ocr` 等其他 OCR | 已有两家切题候选与直接多模态基线之后，再按具体识别缺口追加，避免同时扩展过多路线 |

模型激活状态和 API Key 按区域隔离，同一账号已有聊天权限不代表向量或知识库服务已开通。只需交接非密钥信息：**平台与区域 → 准确模型或 Action → 开通状态 → 实际 RPM/TPM/QPS → 额度与计费 → 数据处理安排**。[模型激活与 API Key](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/quick-start)

密钥仅进入后端环境或密钥管理，不写进安卓 App、Web 包、GitHub 或聊天正文。研发 Agent 的 Coding 套餐与学生 App 使用的标准业务 API 分别配置。本轮使用官方业务接口；保留适配能力，不以中转网关为默认前提。

以下是后续对照候选，不能混用账号、端点或模型名，也不自动成为当前回退路线。**型号更新不等于更适合本项目**，替换须由相同样本上的误伤、漏检、覆盖、延迟与成本结果决定。

| 平台 | 可选准确 ID | 账号与用途边界 |
|---|---|---|
| 当前 BytePlus AP 平台 | `dola-seed-2-1-turbo-260628`、`glm-5-3-flash-260828` | 同平台模型对照，仍需分别激活并确认账户权限；不能套用国内豆包或 Z.AI 的型号拼写。[AP 模型目录](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/model-list) |
| 中国火山方舟账号 | `doubao-seed-2-1-pro-260915` | 属于另一个账号／区域与接入配置，不是 AP `dola-seed` 的可互换名字；按需追加多模态实验组。[中国方舟模型目录](https://docs.volcengine.com/docs/ark/model-list?lang=zh&redirect=1) |
| 阿里云百炼账号 | `qwen3.8-max-0902`、`qwen3.5-ocr` | 前者用于多模态对照，后者用于文字与文档提取；确认 Workspace、地域和独立权限。OCR 提取不等于数学批改。[Max 型号](https://help.aliyun.com/zh/model-studio/qwen3-8-max)、[OCR 型号](https://help.aliyun.com/zh/model-studio/qwen3-5-ocr) |

## 3 OCR 与成品批改的区别

以下保留专用服务的能力核验，供二期/三期选型。一期由模型输出题区和识别候选，仍须检查坐标、题答绑定和漏题，不把“使用纯模型”等同于免除这些验收。腾讯、百度目前均未在本项目实际调用。

| 服务 | 已核实内容 | 必须试验的缺口 |
|---|---|---|
| 腾讯 `QuestionSplitOCR` | 切题、文字及坐标；默认 2 QPS。UseNewModel 为 true 时强化框选与手写答案定位，但不返回选项和题型；API 与演示默认值不同 | 完整题干、手写步骤和答案绑定不能只看演示。分别测新旧模型，再选参数。[接口](https://cloud.tencent.com/document/api/866/115930) |
| 百度多模态切题识别 | 切题、手写、公式、题干与答案识别，异步创建/查询；提交 2 QPS、查询 10 QPS | only_split=false 的 OCR 模式不返回手写答案框 ans_location；only_split=true 才返回。若两次调用，须验证对应关系和费用。[接口](https://ai.baidu.com/ai-doc/OCR/Cmn8k7ihq) |
| 腾讯试题批改 Agent | `SubmitQuestionMarkAgentJob` / `DescribeQuestionMarkAgentJob`；K12 整卷/单题，含判断、手写坐标及可选步骤级批改；默认 10 张/分钟 | 初中方程、函数、证明、合法不同解法需专项验证；不是已经核实七至九年级全部题型。输出仅作候选。[提交接口](https://cloud.tencent.com/document/api/866/128273)、[查询接口](https://cloud.tencent.com/document/api/866/128274) |
| 阿里教育 OCR | RecognizeEduPaperCut / RecognizeEduPaperOcr / RecognizeEduFormula | 官方切题和公式说明以扫描印刷体为主，不能替代对学生手写过程的验证；可作印刷题干基线。[切题](https://help.aliyun.com/en/ocr/developer-reference/api-ocr-api-2021-07-07-recognizeedupapercut)、[公式](https://help.aliyun.com/zh/ocr/developer-reference/api-ocr-api-2021-07-07-recognizeeduformula) |
| Mathpix `/v3/text` | 手写/印刷数学、LaTeX 和行级位置 | 行框不是语义切题，识别不是批改；适合裁切公式专项对照。[接口](https://docs.mathpix.com/reference/post-v3-text) |

腾讯 `EduPaperOCR` 旧数学试题识别 3.0 接口按官方通知于 2026-09-15 下线，本项目不以旧教程作为新接入依据。[迁移公告](https://cloud.tencent.com/announce/detail/2313)

百度本次核对的多模态接口为 `/rest/2.0/ocr/v1/paper_cut_edu_vlm/create_task` 与 `/rest/2.0/ocr/v1/paper_cut_edu_vlm/get_result`。`only_split=false` 创建异步识别任务；`only_split=true` 同步返回题区与手写位置。后者不是同一次识别结果的免费附加字段，两模式如组合调用，需实测坐标、题目关联和计费。不得混用普通 `paper_cut_edu` 或作业批改接口的返回契约。[百度官方接口](https://ai.baidu.com/ai-doc/OCR/Cmn8k7ihq)

二期/三期接入时归一两家适配器的题区、原图坐标、题干/学生作答、缺失字段、供应商请求/任务 ID 与识别版本。增强或裁切后的坐标必须能映射回原图，OCR 自身结果仍待核验。固定开发样本比较三条路线：腾讯切题识别＋模型核验、百度切题识别＋模型核验、整页多模态基线。评测后选默认路线，第二供应商仅按已验证的失败/争议策略调用；不默认每页串行调用两家。服务未开通时可完成适配与模拟回归，但不能记为实测接通。

OCR 不能只统计整段文字相似度。负号、分数线、根号、上下标、单位与几何图中的标记一旦识别错，可能改变整道题。必须同时检查漏题、题答绑定、坐标回显和符号级错误；低质量图片明确补拍或待核实。

## 4 模型如何分工

一期把统一 DeepSeek 的纯模型纠错闭环接实；专用切题识别安排在后续阶段，保留适配边界：

1. **已接通的模型底座与直接读图基线**：原图／题区交给 `deepseek-v4-1-flash-260910`，分角色生成原始作答识别、判题候选、逐级提示与本次订正再检结果；知识抽取也使用同一型号。当前接通不等于整页识别路线已通过生产验收。
2. **二期/三期辅助识别路线**：腾讯或百度生成文字、公式和题区候选，保存原图位置与版本；将 OCR 结果及对应图像交给模型核验，避免把 OCR 错误当成孩子错误。尚未接通实测，不占用首版联网 App 的开发批次。
3. **可追加对照路线**：同 AP 的其他模型或成品批改服务，仅在固定开发样本上比较；结果归一化到同一证据结构，不能直接覆盖老师决定或学生画像。

先独立读题/求解，再对照学生作答；能用确定性算术、符号等价或已核实答案校验时增加校验器。另一个模型复核应尽量独立读取题目，避免先喂第一个模型的结论造成附和。原始图像、识别和判题版本均保留。不同模型一致不等于事实已确认；当前契约仍规定模型仅提出候选。

儿童可见内容仅展示当前题必要错误位置和逐级提示，不返回长篇内部推理或整页答案。提示依据一旦过期，后续提示重新生成。订正照片独立识别并重新检查，不能沿用“原题已错”或按钮点击作为判定。

所有模型走服务端适配器。统一输出 Schema、超时/退避、速率限制、请求 ID、版本、用量及成本；识别失败、服务失败、无法判断和正确分别表示。仅凭兼容协议不假定图片、JSON、思考参数和缓存行为相同。批改内容当数据处理，作业里的文字不能变成给模型的系统指令或工具权限。

## 5 知识图谱使用强模型的方式

课程结构与孩子个人证据分开保存：

`已核版本教材 → 按页识别并保留坐标 → 强模型提取概念/关系候选 → 校验与审核 → 发布课程版本`

`孩子的照片/作答/提示/订正/复核 → 有效纠错证据 → 映射知识点 → 后续更新个人状态与智能体记忆`

教材结构、概念与关系候选当前统一由 `deepseek-v4-1-flash-260910` 的 `knowledge_extract` 角色提取。每个节点和关系附教材版本、PDF 页码、印刷页码、原文区域和抽取版本；实际初中人教版教材须核实年级、册次与版次。章节先后不自动等于知识前置关系，找不到来源的关系保持候选。每个孩子的掌握状态根据有效纠错证据、独立性、帮助条件与时间计算，不能由一次模型回答直接改成“已掌握”。本轮合成规则引用通过不等于真实教材图谱已经建立。

知识抽取、Embedding 和 Rerank 是不同职责。Embedding 只帮助召回教材段落与相似题目，Rerank 只排序候选证据；相似度和相关性分数都不等于孩子掌握度。

| 检索能力 | 当前选型与接口 | 边界 |
|---|---|---|
| 文本与多模态 Embedding | `skylark-embedding-vision-251215`；`POST https://ark.ap-southeast.bytepluses.com/api/v3/embeddings/multimodal` | 纯文本使用 `input` 中的 `text` 项，图文用对应类型组合；一组混合输入整体返回一个向量，不是自动批量切段。`dimensions` 仅 1024／2048，默认 2048；纯文本可选稀疏向量。本轮已验证中文合成文本的 2048 维调用，图文与稀疏输出未实测。[API](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/multimodal-vectorization-api)、[教程](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/vectorization) |
| 可后置的 KB Rerank | `base-multilingual-rerank`，可选 `m3-v2-rerank`；`POST https://api-knowledgebase.mlp.ap-southeast-1.bytepluses.com/api/knowledge/service/rerank` | 柔佛 Knowledge Base 独立服务，最多 200 个候选；需知识库专用 Key／适用的 IAM API key 或 AK/SK 权限，不能假设普通聊天 Key 通用。当前未接入、未实测，账号权限未核实。[接口](https://docs.byteplus.com/en/docs/RAG_Cloud/Rerank)、[区域和鉴权](https://docs.byteplus.com/en/docs/RAG_Cloud/Signature_authentication) |

固定 Embedding 型号、维度、切段规则和索引版本；更换后重建对应索引。首轮可先使用稠密检索与可追溯来源，不把独立知识库或 Rerank 的开通设为纠错前置。

一期可以用 PostgreSQL 保存节点、关系与来源，不先引入复杂图平台。Docling 可作带页码和区域来源的解析候选；仍需核验中文公式。[文档模型](https://docling-project.github.io/docling/reference/docling_document/)。Microsoft GraphRAG 当前仓库说明主要处于维护状态；本项目不把它设为一期前置，可在后续跨文档探索时评估。[官方仓库](https://github.com/microsoft/graphrag)

## 6 费用与配额核对

价格是 2026-10-16 官方页面快照，不是供应商报价或项目成本实测。币种、缓存、思考输出、图片 token、失败/重试与账号地域必须按实际账单核对。

| 服务 | 本次核到的公开单位价格 | 边界 |
|---|---|---|
| BytePlus AP DeepSeek V4.1 Flash | 高峰每百万 token：非缓存输入 $0.30、缓存命中输入 $0.006、输出 $1.20；非高峰分别 $0.15、$0.003、$0.60 | 当前平台官方价格，不套用 DeepSeek 直营或其他转售平台账单。高峰为 UTC+8 周一至周五 09:00–12:00、14:00–18:00；图片、思考、缓存存储另按实际用量核对。[官方价格，2026-10-13 更新](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/model-pricing) |
| BytePlus `skylark-embedding-vision` | 每百万输入 token：文本 $0.125、图片 $0.325 | 不把单次返回的维度当作计费单位。[官方价格](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/model-pricing) |
| Knowledge Base `rerank_knowledge` | 每千输入 token $0.00004 | 属知识库计费项；知识库上传文档后还可能持续产生计算资源费用，不能把此价格当成整套服务成本；独立接口实际计费待核。[知识库价格](https://ai.byteplus.com/ark/region:ap-southeast-1/docs/knowledge-base-pricing) |
| 腾讯切题 / 批改 Agent | 后付费分别 ¥0.24/次、¥0.15/题 | 十题照片的批改按题计费，非一张一次；失败规则另核。[计费](https://cloud.tencent.com/document/product/866/17619) |
| 百度多模态切题识别 | 本次未核实准确单价，申请商业报价 | 不能套普通 OCR 或普通切题价格 |
| Mathpix | 首百万图档 $0.002/图 | 超过12行可能按PDF页价计费，不能套整页预算。[价格](https://website.mathpix.com/pricing/api) |

用实际调用记录计算：`每次作业成本 = OCR/切题 + 模型输入输出 + 必要复核 + 订正再检 + 失败重试 + 存储`。按供应商请求 ID 去重分摊整页费用，不对同一请求产生的每道题重复计费。成功和失败尝试都记录请求标识、模型版本、延迟及可得用量；超时／失败的费用保持未知，不能当成免费。当前合成联调的用量不代表真实整页成本，须用授权开发样本测出实际用量与等待后再调整峰值额度。

腾讯、百度具体教育接口的完整留存/训练使用/处理地域，本次未核实，取得供应商答复后再配置真实资料流程。Mathpix 的图片与结果留存受 `metadata.improve_mathpix` 等设置影响，第一轮优先无身份的自制公式样本。[Mathpix 留存说明](https://docs.mathpix.com/concepts/data-retention)

## 7 分阶段执行与当前交付

1. **已完成的接入证据**：统一 DeepSeek 五角色真实合成联调全部 HTTP 200，识别／检查／提示／订正再检／知识抽取流程通过；没有真实学生数据或手写准确率结论。实现入口为 `services/backend/scripts/ark_smoke.py`，合成图片位于 `evals/correction/fixtures/ark-smoke/`。只保存可审查的脱敏结论，不把密钥或私有报告位置写入仓库。
2. **向量合成协议实测已通过**：同一有效账号调用 `skylark-embedding-vision-251215`，中文合成语料与查询均 HTTP 200、2048 维，使用相同空间配置。语料请求 38 tokens／2655ms，查询请求 49 tokens／1545ms；这是两次观测，不是延迟基准，费用未核账且不能写为零。索引构建、检索质量与数据库接入仍未完成；图文／稀疏向量和 Rerank 尚未实测。后续继续完成错误保存、预算与适配器测试；模型优化依据评测，不以“最新”作为替换理由。
3. **上线前仍须完成**：把模型候选接入真实上传、版本化任务、坐班核实与订正再检流程，使用获准作业和可信标注评测。按题目与拍摄组隔离开发／保留集，保留集不得用于选模型或调参。按 C0/R1 验收进入试点；正确题误伤、错题漏检、覆盖率、仍错订正错误放行及分位延迟分别报告，来源失效、技术失败、漏题和缺输出不能从分母消失。
4. **后续建设**：用有效纠错记录完善个人知识图谱和专属智能体。教材抽取可提前离线验证，图谱完整度与知识库重排序开通不阻塞一期纠错，也不为填充图谱增加学生练习压力。

资源之外还需要：实际教材版本、第一批可获准使用的真实作业和可信标注、目标安卓平板，以及后端部署环境与身份系统。可先用自制样例开发，不能用合成测试成绩证明学生作业识别效果。

## 8 用户指定兼容网关对照（2026-10-17更新）

用户后续提供独立兼容网关，并授权用同一张作业原图测试 `glm-5.3`、`kimi-k3`、`gpt-6-astra`、`gpt-6.1-sol`。四者最终均取得有效候选；Sol包含一次非流式184秒超时和一次174秒流式成功，失败继续保留。GPT按OpenAI官方Responses协议接入，GLM/Kimi按Chat Completions接入。结果、参数及边界见 [匿名实测摘要](docs/evidence/gateway-vision-20261017/README.md)。

原图与逐题模型结果只保存在本机私有目录；模型调用不更新学生状态、不改变当前DeepSeek主路线。单页结果已经发现“判定正确但解释错误”和“用标准解法补原始笔迹”的问题，下一批评测应分别检查识别忠实性、解释质量和学生误伤。该网关的Kimi返回型号为 `FW-Kimi-K3`；网关自报名不能独立证明模型来源，不把此结果称为厂商直营基准。

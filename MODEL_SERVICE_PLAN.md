# 外部 AI 接口选型与申请清单

核验日期：2026-10-16。用户已授权优先使用高能力外部 OCR、多模态与推理模型，并愿意协助取得接口资源。本文件记录官方文档核验与工程选型建议；尚未获得项目凭据，未调用付费推理，未测得任何真实作业准确率。服务商能力说明、项目候选方案和实测结果必须分开。用户随后已明确选择官方 API，首轮按各供应商官方标准业务接口接入。

## 1 先申请什么

| 优先级 | 具体资源 | 开通要求 | 在项目中的用途 |
|---|---|---|---|
| 第一批 | DeepSeek 标准 API：`deepseek-flash` | 可调用图片与思考模式的标准 API Key、按量额度及实际并发限制 | 作业视觉理解、逐题候选检查、提示的主路线候选 |
| 第一批 | Kimi 标准 API：`kimi-k3` | 标准开放平台权限、图片输入、结构化输出、试验额度 | 困难题、视觉争议与不同模型家族的质量对照 |
| 第一批 | 腾讯云 `QuestionSplitOCR` | OCR 试卷切题权限、独立服务账号、测试次数；显式评估 UseNewModel | 整页切题、文字及坐标候选 |
| 第一批 | 百度“试卷切题与识别（多模态）” | `paper_cut_edu_vlm/create_task` 与 `get_result` 的明确权限、测试额度及报价 | 手写、公式与整页切题的第二条识别路线 |
| 第二批，可同时申请 | GLM：`glm-5.3`、`glm-5.3-flash`，可选 `glm-ocr` | 确认所用国内/国际平台的准确型号、标准 API 权限及地域 | 文本推理与课程结构抽取；Flash/OCR 作为视觉对照 |
| 第二批，可同时申请 | 阿里百炼：`qwen3.8-max-0902`、`qwen3.5-ocr` | 项目/Workspace、地域、模型权限和试验额度 | 强多模态与专用文档识别对照 |
| 建教材检索时 | 百炼 `qwen3.7-text-embedding`、`qwen3.7-text-rerank` | 分别开通向量与重排接口，提供实际端点与维度限制 | 找到对应教材段落与知识点候选，不直接计算掌握度 |
| 按需对照 | 腾讯试题批改 Agent；Mathpix；豆包 Seed2.1 Pro | 独立试用额度、明确题型与数据处理条件 | 成品批改基线、手写公式专项、另一多模态候选 |

第一批两家识别、两个不同模型家族足以开始主路线对比；更多候选可以并行申请，不要求每道题同时调用所有服务。首轮额度建议按每家约 200 页开发/校准样本、1000—2000 道题申请，包含同页重试及不同路线的额外调用。这是试验申请量，不是已采购或发布样本数；独立保留集另行冻结。

用户提供的非密钥信息可以按此格式交接：**官方供应商/账号平台 → API 文档 → Base URL → 准确 model ID 或 Action → 已开通能力 → 地域/项目 → RPM、TPM、并发或 QPS → 计费单位/试用余额 → 数据留存与训练使用安排**。记录实际模型版本是否会自动切换；本轮不依赖统一中转网关。

密钥仅进入后端环境或密钥管理，不写进安卓 App、Web 包、GitHub 或聊天正文。研发 Agent 的 Coding 套餐与给学生 App 使用的标准业务 API 分别配置；账号能登录不代表全部接口已开通。

## 2 用户指定的模型已核实

| 名称 | 正确调用标识与能力 | 建议职责与注意点 |
|---|---|---|
| GLM 5.3 | `glm-5.3`；官方明确纯文本、1M 上下文，思考始终开启，low/high/max | OCR 后的复杂推理、教材概念与关系候选抽取；不能直接读取照片。[官方模型页](https://docs.z.ai/guides/llm/glm-5.3) |
| GLM 视觉系列 | `glm-5.3-flash`、`glm-5.3-flashx` 支持图片等输入；`glm-ocr` 是专门识别候选 | 与 DeepSeek/Kimi 使用同一批照片比较，不能因为名称带 Flash 就认定能力弱。[Flash](https://docs.z.ai/guides/vlm/glm-5.3-flash)、[OCR](https://docs.z.ai/guides/vlm/glm-ocr) |
| Kimi K3 | 标准 API 为 `kimi-k3`，原生视觉、1M 上下文，官方例子含 strict JSON Schema | 复杂题与结构化输出对照；Kimi Code 的 `k3`/`k3-256k` 属另一配置体系。[标准 K3 文档](https://platform.kimi.com/docs/guide/kimi-k3-quickstart)、[Code 配置](https://www.kimi.com/help/kimi-code/models) |
| DeepSeek V4.1 Flash | 标准 API 为 `deepseek-flash`；图片/文本、1M 上下文、JSON 和工具调用 | 日常纠错主候选，仍须服务端校验结构和数学结论。[规格](https://api-docs.deepseek.com/quick_start/pricing/)、[视觉接口](https://api-docs.deepseek.com/guides/vision/) |

国内智谱文档本次多次超时，GLM 以上能力来自官方 Z.AI 文档；不能据此推定国内账号权限、人民币价格或处理地域。Z.AI 模型页的协议表与标准调用样例地址也需按实际账号复核，不把 Coding 端点直接写成生产默认值。

DeepSeek 旧 Flash 型号名会转发到 V4.1 Flash；关于 `deepseek-v4-pro`，发布公告与当前价目页仍有不同表述。不能用两个名字就认定为两个独立模型，独立复核优先跨供应商家族。[发布公告](https://deepseek.com/news/deepseek-v4-1-flash/)

阿里官方当前视觉列表包含 `qwen3.8-max-0902`，`qwen3.5-ocr` 面向文档、试卷和手写提取；火山官方列有 `doubao-seed-2-1-pro-260915`。它们可作为追加实验组，是否对项目账户开放仍需控制台验证。[百炼视觉模型](https://help.aliyun.com/zh/model-studio/vision-model)、[方舟模型列表](https://docs.volcengine.com/docs/ark/model-list?lang=zh&redirect=1)

## 3 OCR 与成品批改的区别

| 服务 | 已核实内容 | 必须试验的缺口 |
|---|---|---|
| 腾讯 `QuestionSplitOCR` | 切题、文字及坐标；默认 2 QPS。UseNewModel 为 true 时强化框选与手写答案定位，但不返回选项和题型；API 与演示默认值不同 | 完整题干、手写步骤和答案绑定不能只看演示。分别测新旧模型，再选参数。[接口](https://cloud.tencent.com/document/api/866/115930) |
| 百度多模态切题识别 | 切题、手写、公式、题干与答案识别，异步创建/查询；提交 2 QPS、查询 10 QPS | only_split=false 的 OCR 模式不返回手写答案框 ans_location；only_split=true 才返回。若两次调用，须验证对应关系和费用。[接口](https://ai.baidu.com/ai-doc/OCR/Cmn8k7ihq) |
| 腾讯试题批改 Agent | `SubmitQuestionMarkAgentJob` / `DescribeQuestionMarkAgentJob`；K12 整卷/单题，含判断、手写坐标及可选步骤级批改；默认 10 张/分钟 | 初中方程、函数、证明、合法不同解法需专项验证；不是已经核实七至九年级全部题型。输出仅作候选。[提交接口](https://cloud.tencent.com/document/api/866/128273)、[查询接口](https://cloud.tencent.com/document/api/866/128274) |
| 阿里教育 OCR | RecognizeEduPaperCut / RecognizeEduPaperOcr / RecognizeEduFormula | 官方切题和公式说明以扫描印刷体为主，不能替代对学生手写过程的验证；可作印刷题干基线。[切题](https://help.aliyun.com/en/ocr/developer-reference/api-ocr-api-2021-07-07-recognizeedupapercut)、[公式](https://help.aliyun.com/zh/ocr/developer-reference/api-ocr-api-2021-07-07-recognizeeduformula) |
| Mathpix `/v3/text` | 手写/印刷数学、LaTeX 和行级位置 | 行框不是语义切题，识别不是批改；适合裁切公式专项对照。[接口](https://docs.mathpix.com/reference/post-v3-text) |

腾讯 `EduPaperOCR` 旧数学试题识别 3.0 接口按官方通知于 2026-09-15 下线，本项目不以旧教程作为新接入依据。[迁移公告](https://cloud.tencent.com/announce/detail/2313)

OCR 不能只统计整段文字相似度。负号、分数线、根号、上下标、单位与几何图中的标记一旦识别错，可能改变整道题。必须同时检查漏题、题答绑定、坐标回显和符号级错误；低质量图片明确补拍或待核实。

## 4 模型如何分工

三条路线在同一开发样本上比较：

1. **多模态直读**：原图/题区交给 DeepSeek Flash、Kimi K3 等，分别输出题干、孩子作答与检查依据。
2. **专用识别加核验**：OCR 保存文字、公式、区域与版本，再交强推理模型核验；核验时必要时补原图，避免把 OCR 错误当成孩子错误。
3. **成品批改对照**：腾讯批改 Agent 等单独评测完整流程；输出归一化到同一证据结构，不能直接覆盖老师决定或学生画像。

先独立读题/求解，再对照学生作答；能用确定性算术、符号等价或已核实答案校验时增加校验器。另一个模型复核应尽量独立读取题目，避免先喂第一个模型的结论造成附和。原始图像、识别和判题版本均保留。不同模型一致不等于事实已确认；当前契约仍规定模型仅提出候选。

儿童可见内容仅展示当前题必要错误位置和逐级提示，不返回长篇内部推理或整页答案。提示依据一旦过期，后续提示重新生成。订正照片独立识别并重新检查，不能沿用“原题已错”或按钮点击作为判定。

所有模型走服务端适配器。统一输出 Schema、超时/退避、速率限制、请求 ID、版本、用量及成本；识别失败、服务失败、无法判断和正确分别表示。仅凭兼容协议不假定图片、JSON、思考参数和缓存行为相同。批改内容当数据处理，作业里的文字不能变成给模型的系统指令或工具权限。

## 5 知识图谱使用强模型的方式

课程结构与孩子个人证据分开保存：

`已核版本教材 → 按页识别并保留坐标 → 强模型提取概念/关系候选 → 校验与审核 → 发布课程版本`

`孩子的照片/作答/提示/订正/复核 → 有效纠错证据 → 映射知识点 → 后续更新个人状态与智能体记忆`

GLM 5.3、Kimi K3 可用于离线教材结构与关系抽取，DeepSeek/其他模型作为对照。每个节点和关系附教材版本、PDF页码、印刷页码、原文区域和抽取版本。章节先后不自动等于知识前置关系；找不到来源的关系保持候选。每个孩子的掌握状态根据有效证据、独立性、帮助条件与时间计算，不能由一次模型回答直接改成“已掌握”。

Embedding 帮助召回教材段落，Rerank 对候选证据排序；相关性分数不等于孩子掌握度。建议首测 `qwen3.7-text-embedding` 与 `qwen3.7-text-rerank`，固定模型/维度/索引版本；换模型重建相应索引。[Embedding](https://help.aliyun.com/zh/model-studio/embedding-interfaces-compatible-with-openai/)、[Rerank](https://help.aliyun.com/zh/model-studio/text-rerank-api)

一期可以用 PostgreSQL 保存节点、关系与来源，不先引入复杂图平台。Docling 可作带页码和区域来源的解析候选；仍需核验中文公式。[文档模型](https://docling-project.github.io/docling/reference/docling_document/)。Microsoft GraphRAG 当前仓库说明主要处于维护状态；本项目不把它设为一期前置，可在后续跨文档探索时评估。[官方仓库](https://github.com/microsoft/graphrag)

## 6 费用与配额核对

价格是 2026-10-16 官方页面快照，不是供应商报价或项目成本实测。币种、缓存、思考输出、图片 token、失败/重试与账号地域必须按实际账单核对。

| 服务 | 本次核到的公开单位价格 | 边界 |
|---|---|---|
| DeepSeek Flash | 高峰每百万 token：非缓存输入 $0.30、缓存输入 $0.006、输出 $1.20；闲时半价 | 图片与思考用量要计入；并发上限不保证实际等待时间。[官方价格](https://api-docs.deepseek.com/quick_start/pricing/) |
| Kimi K3 | 每百万 token：输入 ¥20、输出 ¥100、缓存命中 ¥2 | 标准 API 开通条件、累计充值等级配额与缓存写入另核。[价格](https://platform.kimi.com/docs/pricing/chat)、[额度](https://platform.kimi.com/docs/pricing/limits) |
| Z.AI GLM 5.3 / Flash | 每百万 token：5.3 输入 $1.40、输出 $4.40；Flash 输入 $0.15、输出 $0.50 | 国际站价格，不能替代国内 BigModel 报价；缓存价另列。[价格](https://docs.z.ai/guides/overview/pricing) |
| 腾讯切题 / 批改 Agent | 后付费分别 ¥0.24/次、¥0.15/题 | 十题照片的批改按题计费，非一张一次；失败规则另核。[计费](https://cloud.tencent.com/document/product/866/17619) |
| 百度多模态切题识别 | 本次未核实准确单价，申请商业报价 | 不能套普通 OCR 或普通切题价格 |
| Mathpix | 首百万图档 $0.002/图 | 超过12行可能按PDF页价计费，不能套整页预算。[价格](https://website.mathpix.com/pricing/api) |

用实际调用记录计算：`每次作业成本 = OCR/切题 + 模型输入输出 + 必要复核 + 订正再检 + 失败重试 + 存储`。按供应商请求 ID 去重分摊整页费用，不对同一请求产生的每道题重复计费。先对 100—200 页测出实际用量与等待，再决定主路线与峰值额度。

腾讯、百度具体教育接口的完整留存/训练使用/处理地域，本次未核实，取得供应商答复后再配置真实资料流程。Mathpix 的图片与结果留存受 `metadata.improve_mathpix` 等设置影响，第一轮优先无身份的自制公式样本。[Mathpix 留存说明](https://docs.mathpix.com/concepts/data-retention)

## 7 分阶段执行与当前交付

第一阶段：完成本申请清单、供应商能力核验、离线评测结构；同时开发不依赖外部密钥的后端身份/授权/作业持久化和坐班页面。当前已分别派给三个 Agent，主 Agent 负责集成与契约审查。

第二阶段：取得接口后，先用合成题验证授权、图片、格式、故障和计费，再用授权开发样本比较路线。固定参数、题目分组和版本，独立保留集不用于选择模型或调参。

第三阶段：选定主路线与必要备选，接入真实上传、任务与订正再检查。按项目 C0/R1 验收，再进入真实现场。来源失效、技术失败和无法判断不能从分母消失。

第四阶段：使用一期有效纠错记录完善个人知识图谱和专属智能体；教材抽取可提前离线验证，图谱完整度不阻塞一期纠错。

资源之外还需要：实际教材版本、第一批可获准使用的真实作业和可信标注、目标安卓平板，以及后端部署环境与身份系统。可先用自制样例开发，不能用合成测试成绩证明学生作业识别效果。

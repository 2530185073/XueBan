# 纠错离线评测 A05

这是可运行的**离线评测基础设施**，不是已接入模型的纠错服务。当前没有 API Key、真实学生照片、真实标签或供应商调用结果。`examples/` 全部标记 `synthetic_only=true`；它验证计数和约束，不能证明准确率、速度、成本、初中题型覆盖或可发布性。

首轮接入采用项目选定的**厂商官方 API**。本目录不调用任何网络接口，不开通付费，不含云端密钥；供应商适配器以后把实际响应转换成这里的契约。适配接口不依赖某一家厂商，但本轮没有接入第三方转发服务。具体接口和采购资料由主项目资源登记维护。

## 运行

Python 3.11+，依赖与现有一期契约一致，只有 `jsonschema==4.26.0`（及它的安装依赖）。Schema 解析仅使用本地注册表，不访问 `$id` 中的域名。

```sh
python3 -m pip install -r evals/correction/requirements.txt
python3 -m unittest discover -s evals/correction/tests -v

python3 evals/correction/evaluate.py \
  evals/correction/examples/manifest.synthetic.json \
  evals/correction/examples/predictions.synthetic.json \
  --human-decisions evals/correction/examples/human-decisions.synthetic.json \
  --allow-missing \
  --output /tmp/xueban-synthetic-eval.json
```

不传 `--output` 时向 stdout 输出 JSON。真实评测文件必须保存在授权的私有位置，不放入本目录示例；输出可放临时目录或本目录已忽略的 `outputs/`。程序不会读取 `asset_ref` 指向的照片或访问远程 URL。

示例特意遗漏一条预测，展示固定分母。去掉 `--allow-missing` 将以退出码 **2** 拒绝计分；加上它则明确报告 `missing_prediction_ids` 并保留缺失题。输入无效同样退出 2；成功输出退出 0。成功退出只代表评测文件合法并完成运算，不代表达到发布门槛。

## 文件契约

| 文件 | 用途 |
|---|---|
| `schemas/manifest.schema.json` | 独立于供应商输出冻结的题目/作答清单、分组、split、授权和真值 |
| `schemas/predictions.schema.json` | 一次模型候选运行，含版本、逐题结果和请求级遥测 |
| `schemas/human-decisions.schema.json` | 同一模型运行之后的人工最终决定；不会覆盖模型结果 |
| `schemas/common.schema.json` | 本地公共类型、输入版本和费用字段 |
| `NORMALIZATION.md` | 官方供应商响应转入统一结果的要求 |
| `evaluate.py` | CLI、语义约束、切片指标和报告 |
| `tests/test_evaluate.py` | 分母、数据泄漏、版本、重复、费用归属和人工隔离测试 |
| `examples/*.synthetic.json` | 无照片、无教材正文、无学生数据、无真实调用的合成样例 |

### 数据集：先冻结预期题目，后运行模型

每个 `sample_id` 表示**一道题的一次实际作答**，不是整张照片，也不是一次 API 请求。整张照片里所有需要检查的题都必须预先入清单，包括后来没切出的题。不能从供应商返回了几题反向生成分母。冻结后变更题目清单或标签必须增加 `dataset_version`，报告同时保存输入文件 SHA-256。

- `question_group_id` 把同源题、题目变体、拍摄变体和订正链放在同组；`capture_group_id` 把同张照片、重拍、裁切及图像增强变体放在同组。这两类关系的传递连接应在分组阶段合并，不能只按文件名随机划分。
- 全 manifest 检查上述分组、`question_id`、输入指纹、已知原图哈希是否跨 `development/calibration/holdout`；即使本次只测一个 split，也检查其他 split。组名正确性仍需要数据管理员核实，脚本无法从两个不同组名推断未披露的同源题。
- `curriculum` 保留初中、人教版背景、年级、上下册、教材版次；不确定版次使用 `null`，年级/册次可为 `unknown`。`question_type` 与 `image_conditions` 使用冻结词表，建议覆盖方程、函数、几何证明、等价表达、缺步骤、多小题、分数、模糊、反光、倾斜、遮挡、漏切。
- `authorization.status` 为 `authorized` 的真实样本才可计分，必须有授权依据引用、私有原图引用和 SHA-256；`pending/withdrawn` 会拒绝运行，不会偷偷移出分母。真实、合成数据不得混为同一个 manifest。
- `label.status=reviewed` 才是已定标真值。真实标签必须引用独立学科审核者和可复验解法/审核过的参考依据；Schema 不接受“模型自评”作为标签依据。日常坐班答疑人员不被自动认定为内容审核者。审核流程和依据真实性仍须人在仓库外落实。
- 争议或未审标签保持 `verdict=uncertain`。它们计入总量和覆盖分母，按 `ground_truth_status` 单列；因为没有真值，不进入正确题/错题标签分母，报告保留 `unresolved_ground_truth`，不能据此宣称全量质量已验收。
- `stage=correction` 必须引用存在且同组、同 split、同题身份的父样本，新 `attempt_id` 不得复用父作答；循环父链被拒绝。`question_revision` 允许随订正链变化。单纯改 OCR 文字是识别版本变化，不伪造成学生重新作答。

`input_versions` 对齐一期契约里的题目身份/版本、作答身份/识别版本、内容指纹。此评测没有复制生产聚合和权限模型；`case_revision_at_submission` 是受理审计值，不是模型计算输入，故不作为离线结果过期条件。

### 模型候选与人工决定

模型文件只能有 `output_kind=model_candidate`。每次供应商/模型/配置比较用独立 run；保存 `provider`、`model`、`model_version`、`adapter_version`、请求配置 SHA-256。官方只暴露别名时版本可为 `null`，但必须说明原因，报告会警告无法保证模型版本可复现。多组件流水线需要在对应配置清单中冻结各 OCR/判题模型、提示词和参数的版本；配置散列不是其明文清单的替代物。

模型候选不可写 `confirmed`。人工文件必须引用对应的 `applies_to_model_run_id`，包含决定 ID、审核者和依据引用，单独输出 `human_final`。没有人工决定的题计为 `not_reviewed`，保持全量分母；不以“已经看过的几题”冒充整班人工完成率。这里的人工最终结果是系统服务结果，与独立评测真值是两个角色。

默认拒绝重复 sample、重复预测（即使内容相同）、重复请求、未知题/跨 split 输出、旧输入版本、模型结果与人工运行不对应、失败状态携带判对结果。选用 `--allow-missing` 只放宽缺预测，其他冲突仍拒绝。

## 指标口径

`incorrect/partially_correct/unanswered` 均属于本轮“需订正”类别。下列是正确/需订正二分类指标，不是逐步骤得分、数学证明正确性或知识掌握度指标。所有 rate 都同时给出分子、分母；分母为 0 时 rate 为 `null`。

| 报告字段 | 分子 / 分母 |
|---|---|
| `correct_false_flag_rate` | 真值正确却被候选判为需订正 / 全部已定标正确题 |
| `wrong_missed_rate` | 真值需订正却没有得到“需订正”候选 / 全部已定标需订正题；包括技术失败、未识别、无法判断、漏切、无输出、缺预测 |
| `wrong_false_correct_rate` | 真值需订正却明确被候选判对 / 全部已定标需订正题；与失败造成的未检出分开 |
| `decision_coverage` | 有有效判题候选的题 / 全部预期题；人工块改为有已确认人工决定的题 |
| `still_wrong_correction_false_pass_rate` | 订正后仍需订正却被候选判对 / 全部已定标仍需订正的订正作答 |
| `still_wrong_correction_not_detected_rate` | 订正后仍需订正却未得到需订正候选 / 上述同一分母，含失败 |
| `known_truth_decision_coverage` | 已定标题中有候选 / 全部已定标题 |
| `known_truth_success_rate` | 已定标题中正确/需订正二分类匹配的候选 / 全部已定标题；失败不消失 |

“错误放行”在模型块中表示**错误的判对候选**，不是生产系统真的给学生放行；目前生产契约仍要求人工确认。所有题都不判断会让误伤率看似低，因此必须同时读覆盖率、漏检率、未知真值数量和各类失败计数，不能只展示一个好看的百分比。

切片包括题型、初始/订正、年级、册次、教材版次、每个图像条件、真值审核状态。一次报告只比较一个 split；保留集不应用来选模型或调提示词。此处没有自动发布阈值，门槛及置信区间需在获得有效真实样本后另行冻结，不能用合成结果定门槛。

## 时延与费用

`requests[]` 是请求账本，一次整页请求可对应多个 `sample_id`，每条预测引用这些 request ID。重试必须使用独立的实际请求 ID，记录失败尝试的耗时与费用；同一次响应重复导入会被拒绝。不因为一次请求切出多题而把费用加多次。

- p50/p95 使用 **nearest-rank**，单位为已测得的供应商请求耗时毫秒，失败请求同样参与；未知值保持 `null`，同时报告已知/未知请求数及完全没有请求遥测的题。这不是学生端到端耗时，也不含未记录的排队/人工等待；异步接口应把提交至终态的作业耗时另作业务观测，不冒充单次 HTTP 请求耗时。
- `cost.amount=null` 表示未知，不能填写 0；0 仅表示确实免费。`known_cost_subtotal_by_currency` 只是已知费用小计，CNY/USD 不折算、不相加。估算费用与账单实扣不能混写在同次 run，采用哪种依据应保留在运行记录中；本目录未实现供应商计费核对。
- 每个切片显示与其相交的完整请求账本。一个整页请求可同时属于多个题型切片，因此**各切片时延/费用不能相加**；总体费用看 `all`。当前不推算无法归属的逐题成本。

## 当前交付边界

已提供可执行计分、输入防错、固定分母、合成演示与约束测试。尚未实现真实供应商适配器、题框匹配、识别文本/公式准确率评测、人工标注平台、统计置信区间、真实端到端时延、生产权限/留存/计费核对。框架不会自动访问数据或证明授权成立。后续真实评测至少需要官方 API 权限、独立授权样本、冻结分组/版本、独立学科标签和实际请求账本。

# 兼容网关单图验证摘要

日期：北京时间2026-10-16晚至10-17凌晨。用户授权通过自己的OpenAI兼容网关 `api.zyai.online` 测试一张原始作业照片。此公开摘要只记录接口与匿名结果；原图、原始作答、完整模型返回和逐题核读保留在本机私有目录，未进入仓库。

## 实际调用

`GET /v1/models` 成功，列出了用户指定的四个准确ID。随后同一原图和任务提示，先执行四次请求，Sol超时后另做一次流式诊断；均无自动重试。

| 请求模型 | 网关返回型号 | 接口 | 实际结果 | 单次耗时 | 报告总token |
|---|---|---|---|---:|---:|
| glm-5.3 | glm-5.3 | Chat Completions | 200，19项候选 | 34.668秒 | 5239 |
| kimi-k3 | FW-Kimi-K3 | Chat Completions | 200，19项候选 | 97.877秒 | 8440 |
| gpt-6-astra | gpt-6-astra | Responses | 200，completed，19项候选 | 48.050秒 | 5576 |
| gpt-6.1-sol，首次 | 未取得 | Responses，非流式 | ReadTimeout，无有效输出 | 184.002秒 | 未知 |
| gpt-6.1-sol，诊断 | gpt-6.1-sol | Responses，SSE | 200，completed，19项候选 | 174.139秒 | 12202 |

四个有效返回均把19个答题点标为correct；这描述模型输出，不是“100%准确率”。对照为独立AI辅助核读及数学复算，没有正式人工金标；有局部字形与涂改不确定。此次样本没有确认的学生数学错误，因此主要考察覆盖、误伤、忠实识别和解释质量，不能推断错题漏检率。

逐项审查发现GLM有一处明确分数换算讲解错误，另把涂改中的学生中间步骤用推断的标准解法补全。Kimi保留了一处关键字母歧义；Astra与Sol保留底部涂改不明，但未提示该字母歧义。Kimi即使记录歧义仍返回correct，产品层需要把“读法待核”与“该候选读法数学正确”分开，不能按票数确认。全部细节仅在私有报告记录。

这是一页、多次协议调用，不能排定模型总体优劣或测得P95。Sol成功不抹去首次超时，也不能证明流式是成功的原因。四个成功返回合计31457 token，未包含失败可能产生的用量；费用未核账，不能按0或官方直营价估算。返回型号和model列表仅是网关自报，实际后端身份/版本未独立验证。

## 复验与实现

入口 [vision_benchmark.py](../../../services/backend/scripts/vision_benchmark.py)，使用方法见 [工具说明](../../../services/backend/scripts/README.md)。没有修改生产路由、公开API或学生数据。GPT使用官方Responses结构，不能混用Chat的图片字段、response_format与max_tokens；严格Schema在text.format，REST最终内容从output中的message/output_text读取。[官方Responses](https://developers.openai.com/api/reference/resources/responses/methods/create)、[图片](https://developers.openai.com/api/docs/guides/images-vision)、[结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)

两GPT请求reasoning=medium、输出预算12288；Chat保留提供方默认思考、同等数字的max_tokens预算。同一提示不等于计算量一致。所有原始字节相同，没有裁切/重编码，没有把对照答案或题目数量喂给模型。后补的终态/凭据/异常解析加固仅做模拟回归，没有因此重复付费调用。

新增59项离线协议回归，覆盖Responses/Chat/SSE格式、完成/拒绝/截断、JSON Schema、原始与转义凭据回显、异常深度、断流、大小/时间预算和报告预检。本机完整后端 **261 passed、2 skipped、1 warning**；跳过仍是SQLite的PG并发场景，提示仍为Starlette/AnyIO弃用。CI不持有网关密钥，不调用真实图片或模型。生产上传、异步任务与完整安卓纠错仍需后续集成；本次不构成C0/R1/R2放行。

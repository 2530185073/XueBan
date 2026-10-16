# 显式模型验证工具

这些命令只供研发人员复验接口，不是公开纠错API、生产Worker或准确率评测。正常CI只运行MockTransport测试，不读取真实密钥、不发送作业、不调用付费模型。

## Ark合成流程

`ark_smoke.py` 使用仓库自制印刷题图，检查统一DeepSeek五角色的候选流程。配置、结果与命令见 [2026-10-16实测](../../../docs/evidence/ark-20261016/README.md)。

## 单图兼容网关对照

`vision_benchmark.py` 接受用户明确授权的JPEG原图。一个命令只发一次请求，不自动重试、不改换模型、不把图片裁切或重编码。输出文件必须位于仓库外；图片、原始答案和完整模型报告不得提交公共仓库。报告保留图片摘要、提示版本、请求/返回型号、协议参数、耗时、用量、失败和候选结果，不保留内部推理或密钥。

私有配置文件权限0600，内容为 `ZYAI_BASE_URL=https://api.zyai.online/v1` 和 `ZYAI_API_KEY`。实际密钥在服务端安全配置，不填写到下面命令、应用包或文档中。

```sh
# GPT系列使用Responses；每个报告路径必须是新的。
services/backend/.venv/bin/python services/backend/scripts/vision_benchmark.py \
  --live --env-file /secure/path/gateway.env --image /secure/path/authorized.jpg \
  --model gpt-6-astra --protocol responses --output /private/path/new-astra-report.json

# gpt-6.1-sol同样使用responses。
# glm-5.3、kimi-k3本轮按网关提供的准确ID用chat协议测试。
```

Responses采用 `POST /v1/responses`，图片为 `input[].content[].input_image`，`image_url`直接使用base64 data URL；Chat Completions为 `messages[].content[].image_url`，内含`url`。二者不能混写。[官方图片输入](https://developers.openai.com/api/docs/guides/images-vision)

GPT请求使用 `reasoning.effort=medium`、`max_output_tokens=12288`、`text.format`中的严格JSON Schema和`store=false`。Chat请求保留提供方默认思考配置、`max_tokens=12288`和JSON Object模式；所有结果再用同一Schema校验。因为控制参数和上游实现不同，不能声称这是一组推理计算量完全相同的实验。[Responses字段](https://developers.openai.com/api/reference/resources/responses/methods/create)、[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)、[Astra](https://developers.openai.com/api/docs/models/gpt-6-astra)、[Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)

Responses必须是completed、无error且无incomplete_details，逐项查找assistant message中的output_text；不能固定取output[0]或假设REST自带SDK的output_text便利属性。拒绝、截断、异常输出和JSON/Schema失败均记录为失败，HTTP200不代表判题完成。即使格式通过也只标candidate_received，不推断答案正确或学生已掌握。[官方文本输出](https://developers.openai.com/api/docs/guides/text)

Responses诊断可显式增加 `--stream`。SSE只读取终态事件里的完整response，要求事件类型与内部status一致；不把delta或DONE标记当作完整结果，不保存思考流。收到completed即关闭连接。一次流式诊断属于新请求，须使用新报告路径，并把先前超时计入记录；不能只展示重试成功的结果。

使用180秒I/O超时与读取循环240秒预算，同步HTTPX不能硬中断所有传输形态。操作人员应另用进程执行期限；超时/杀进程不表示上游没收费。诊断文件先预检写入，之后保存一次请求的最终报告；非事务性写入、进程终止或磁盘故障仍可能留下incomplete诊断。错误和失败不能从样本汇总消失。

网关列出型号、返回同名model，仅能证明网关自报，不能独立验证实际底层版本。`store=false`是请求字段，不保证第三方网关零留存。网关价格也不能按OpenAI直营价格换算；usage缺失和费用未核账均保留未知。[官方数据控制的适用范围](https://developers.openai.com/api/docs/guides/your-data)

本工具不加载对照答案，不自动生成“准确率”。同页多小问不是多个独立作业样本；AI辅助独立核读不是人工金标。最终答案、解释正确性、忠实识别、模糊/涂改处理和题目覆盖应分别核验；全对页主要验证误伤，不能估计漏检错题的能力。

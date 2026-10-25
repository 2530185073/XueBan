# 用户原始识图 provider

源代码取自本地最终提交 `d519e3b7d7e0a9079b8f2c2d962c3f2f513008bb`。
独立保存该提交的 `correction_pipeline.py`、`ark.py`、`responses.py`、
`page_detail.py`、`visual_questions.py` 和 `prompts.py`。

```python
from xueban_backend.providers.user_original import UserOriginalCorrectionPipeline

with UserOriginalCorrectionPipeline.from_env(env) as pipeline:
    result = pipeline.recognize(images=images)
```

## 接口与配置差异

- 入口类改名为 `UserOriginalCorrectionPipeline`，具有
  `uses_original_decision_policy = True`，供 worker 选择原始持久化语义。
  `recognize`、`check`、`recheck`、`hint`、`close` 和上下文管理接口保留。
  `client`、`fallback_client` 可检查；settings 的 URL/key 不出现在 repr。
- `from_env(environ=None)` 仅读取传入映射或进程环境，不读取密钥文件、
  不探测模型、不访问网络。DS 使用 `ARK_BASE_URL` / `ARK_API_KEY`，
  `ARK_MODEL` 缺省为且只允许 `deepseek-v4-1-flash-260910`。
- Astra 使用包内旧版 Responses 适配器，始终配置为 `gpt-6-astra`。
  `ARK_FALLBACK_MODEL` 缺省可省略，显式配置时必须等于 `gpt-6-astra`。
  URL/key **仅**取 `ZYAI_BASE_URL` / `ZYAI_API_KEY`，不回退 ARK。
  四项 URL/key 必须齐全，否则抛公共 `ProviderError("invalid_configuration")`；
  这是相对于原始 from_env（可禁用、允许任意主模型、复用 DS 凭据）的有意变更。
  保留单次请求、180 秒请求超时、200 秒总超时及并发上限 2。
- 直接复用公共 `PipelineImage` / `PipelineResult`，以及公共 `ArkSettings`、
  `ProviderError`、`CandidateResult`、`Metadata`、`Attempt`、`Usage`、
  `OutputDiagnostic`。公共 `PipelineResult` 多出的 `host_call_journal` 为 `None`，
  `Attempt.transport` 为 `None`；原始适配器不产生新的 transport/journal 数据。
  worker 传入的公共图片对象可通过原来的 isinstance 检查，异常仍由公共类型捕获。
  提示版本为 `photo-correction.v20-grade-level-solutions`：判卷步骤沿用原流程，
  错题输出调整为符合学生年级、可逐行照写的规范作答，而非抽象的纠错建议。
- 包内相对 import 保留；共享类型通过 `..ark` / `..correction_pipeline` 导入。
  不调用公共 factory，不依赖 `TwoModelWholePagePipeline`；外部 factory dispatch
  与 runtime 切换由主代理负责。

## 保留的原始行为

DS 首轮整图识别，必要时整图手写疑点复核；随后逐题第二轮判断。
图形、应用、证明、多空题，以及 correct、无法判断、实质疑点、缺少答案/改法、
逐空冲突等情况使用 Astra 独立复核；可恢复的上游失败保留原有有限兜底。
保留原识别提示词、视觉数学修复、单位“1”规则、逐空绑定、候选语义和调用计费记录。
第二轮及 Astra 兜底共用年级适配的正确解题步骤要求，继续使用
`correction_method` 字符串并保留换行，不增加模型调用或修改学生原始作答。
`visual_questions.py` 作为同提交配套工具保留，主流程不新增裁图或定位调用。
不移植当前共享 pipeline 后增的 `_dual_correct` 或 recognition_uncertainties
强制待核规则；worker 按上述能力标志保留原 persist 语义及其原有输入绑定校验。

不支持离线原图恢复/原图重读专用入口（没有 `recheck_original`、
`initial_read_contracts` 或 resume 接口），不回退到同事的 two_model。
普通 `recheck` 仍保留，表示对指定新作答进行原始第二轮判断。

离线测试使用合成图片、假凭据与 HTTP MockTransport，不读取本机密钥或调用真实模型。

## 接入当前项目

API 与 worker 均设置 `CORRECTION_MODE=user_original`；公共
`CorrectionPipeline.from_env` 会显式选择此包，不使用旧的双整页判卷。
worker 需同时配置上述两组凭据，API 只读取模式而不加载模型密钥。
保留 `AUTO_CHECK_AFTER_RECOGNITION=1`，让整图识别完成后自动排入逐题检查。
当前私有本地启动器也会校验此模式；不需要换库或重新初始化账号。

学生端 `RecognitionResults` / `ResultsSummary` 恢复同提交的精简题卡：
按原识别顺序连续编号，题干标题不含选项；展开显示题目、作答、知识点，
错题显示正确答案与正确解题步骤；不嵌入求助、提示、订正过程与历史记录。
主导航中的举一反三、知识图谱和账号功能继续保留。
前端仍等待已排入的逐题任务，不把首轮识读疑点当成整页处理完成。

数据库与公开契约继续沿用当前版本，不回退 migration，不删除照片、
作答、判定或学习证据，不自动重判存量作业。原图恢复入口在该模式下
返回符合现有契约的 `optional_gate_closed`，不会新建不受支持的任务；
其他模式及已有历史读取的兼容代码仍保留，但不参与本地活动识图流程。
清晰且有绑定的错题继续进入当前学习映射；未消解的识读疑点不伪造学习证据。

验收包括原始 provider 回归、当前 HTTP/worker/存储的串联测试、学习客户端
超时兼容、学生端任务轮询与组件测试。离线通过不等于真实照片准确率已验收。

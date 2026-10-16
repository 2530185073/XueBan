# 服务端 Ark 候选适配器

本包是同步 Worker 调用组件，尚未接入任何公开 API、作业状态机或数据落库。导入不启用模型；无凭据或无明确 base URL 时拒绝调用。部署端负责注入已核实的服务域名与凭据，不从学生客户端接受这些配置。

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

固定使用调用配置中的同一个模型，默认精确 ID 为 `deepseek-v4-1-flash-260910`。`recognition / check / hint / recheck / knowledge_extract` 各有独立系统提示；知识点角色只是接口能力，不会部署或更新知识图谱。默认思考开启、`max_tokens=4096`，可单次显式覆盖，最大8192（包含该模型思考 token）。不发送未经确认的温度或 top-p 参数。

使用 Chat Completions 的 `response_format={"type":"json_object"}`，另在服务端做 JSON Schema 校验；不依赖上游严格 schema 输出。只接受一个完整 JSON 对象，拒绝重复键、NaN/Infinity、Markdown 包装、输出截断、工具调用、额外未允许字段和不匹配的数据类型。Schema 仅接受内联定义和本地引用，不检索远程 Schema。

`CandidateResult.status` 恒为 `candidate`，`data` 是已通过结构验证的候选；结构验证不证明事实正确。`metadata` 保留请求模型、实际返回模型、request/response ID、usage、总耗时和每次尝试。模型输出不会触发已确认判定、学生掌握度或订正完成。

连接仅接受显式 HTTPS URL，禁止 URL 凭据、查询和片段，不跟随重定向，不读取代理环境变量。仅接受 PNG/JPEG/WebP 的 base64 data URL，不替调用方下载图片；限制单张4MiB、共8MiB、最多4张。图片仍需上游完成文件解码/安全检查与授权。本适配器的魔数检查不能代替完整图片校验。

默认并发2、排队最多1秒、单次 HTTP 各 I/O 阶段超时30秒（连接最多5秒），最多3次尝试。`total_timeout_seconds=90` 设置整次调用预算，覆盖排队、退避、请求和读循环；每次请求的各 I/O 超时按剩余预算缩短，每个读取块之后检查 monotonic 截止时间。到期返回 `deadline_exceeded`、保留已经发出的尝试记录并关闭响应、释放槽位，所以持续小块响应不能无限占槽。

这不是绝对墙钟硬中断：同步 HTTPX 无法中断已经开始的阻塞 I/O，该阶段仍使用进入时的超时；普通读体检查可能晚最多一个已启动 I/O 阶段的超时（另有操作系统调度误差）。HTTPX 内部的响应头解析或跨多个阶段的请求还不能由外层逐块检查，因此不承诺所有传输形态都满足该偏差上界。本实现不启动超时后继续消耗资源的后台线程。生产任务仍须由 Worker 的进程/任务级执行期限管理；超时不表示上游停止计算或不计费。

仅429/5xx有限重试，等待最多2秒且不超过剩余总预算；超时或传输失败不自动重试。推理调用重试没有写入幂等承诺，每次 `charge_status` 都是 `unknown`，即使失败也不能推断免费。调用层须持久保存尝试记录并设置费用限额。

`ProviderError` 只携带固定安全错误码与经过筛选的尝试元数据，不包含请求、响应正文、密钥或原始异常消息。本包不记录日志；调用者不能把输入、`data`、配置对象展开后的密钥或完整异常局部变量写入日志。`repr` 隐藏配置中的 URL/密钥与候选正文。

离线验证：`python -m pytest services/backend/tests/test_providers.py -q`。测试全部使用 `httpx.MockTransport` 与合成图片/凭据，不读取部署密钥、不访问 API、不证明真实照片识别质量或生产费用。

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

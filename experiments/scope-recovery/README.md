# 多空作答范围恢复：离线实验包

此包保存已冻结的协议、持久化预算、来源校验与专用回写候选，供后续技术接手复现。**仅使用合成数据和 `httpx.MockTransport`，未接入生产 worker，也未启用任何真实模型。测试通过不代表识图或数学判题准确。**

`candidate.patch` 基于完整提交 `03aa3a2cd58089d49fb30acadf7e0dc6005e0595`，包含 5 个新模块、5 个测试，以及 R1 双向排他和 `scope_recovery` Job kind 的必要改动。精确文件清单和 SHA-256 见 [manifest.json](manifest.json)。此目录本身不改变业务运行行为。

## 复现

需本地 Git 已有上述提交，并有安装了项目后端测试依赖的 Python 环境。运行器不联网安装依赖、不读取本机运行配置或凭据文件。目标目录的父目录必须存在；目标必须不存在，且位于所有已有 Git 工作树之外。失败后保留新建目录供检查，不清理其他目录。

在仓库根目录执行，只准备和校验源码：

```sh
python3 experiments/scope-recovery/run_offline.py \
  --python services/backend/.venv/bin/python \
  --destination ../scope-recovery-prepared-001 \
  --prepare-only
```

完整运行 SQLite 离线测试，使用另一个全新目录：

```sh
python3 experiments/scope-recovery/run_offline.py \
  --python services/backend/.venv/bin/python \
  --destination ../scope-recovery-sqlite-001
```

如显式设置了已有测试 PostgreSQL 的 `BACKEND_TEST_DATABASE_URL`，增加 `--require-postgres`，即可运行 SQLite 与 PostgreSQL 两套 fixture。每项 PostgreSQL 测试创建并删除自身的随机 schema；不清空既有表。未设置连接或连接类型不符时失败，不静默降级。默认即使父进程存在该变量也只运行 SQLite。不要在命令、报告或提交中写入真实连接密码。

运行器使用严格环境变量白名单，不传递模型 key、模型配置、代理、宿主 `PYTHONPATH` 或第三方 pytest 自动插件配置。只有显式 PostgreSQL 模式传递该一个数据库连接变量。新目录中生成 `scope-recovery-offline-result.json` 和经过数据库连接脱敏的测试日志。`--prepare-only` 明确记录未运行测试；不会把源码准备成功当成测试通过。

包自身的标准库安全测试：

```sh
python3 experiments/scope-recovery/test_run_offline.py
```

## 固定测试范围和已有证据

运行器只执行以下测试文件，不扫描或启动其他任务：

- `tests/test_scope_recovery_contract.py`
- `tests/test_scope_recovery_materializer.py`
- `tests/test_scope_recovery_durable.py`
- `tests/test_scope_recovery_dispatch.py`
- `tests/test_scope_recovery_apply.py`
- `tests/test_recognition_recheck.py`（基线已有，补丁不修改）

冻结源码的同一集合已完成 SQLite / PostgreSQL 联合回归：**316 passed，1 warning，47.58 秒；真实模型请求 0 次，6 个业务源码 SHA 在前后保持一致。** 这是冻结候选的既有证据，接手环境需自行复现。补丁应用后每个候选文件都会再次核对 SHA。

合成 fixture 只验证协议消费和事务。例如部分测试把 `(-12)÷3=____(12÷3)=____` 的 `-\n4` 配上固定的“两处 correct” Mock 复核，末值 `4` 在数学上是错误的；它不是数学 gold，不能把 Mock 输出当作正确答案。后续真实识图和判题验收必须使用独立人工核定样本。

## 已覆盖的机制与边界

- 原图、任务版本、目标、原文引用和新视觉来源绑定；typed 一致性不等于图片内容真实。
- 固定页级子任务，先持久化预约再允许发送；失联或结果未知的阶段不退额度、不自动重发。
- scope 分支最多 1 次盲读和 1 次复核。页级 `max_review_calls=2` 是此候选新增且未启用的总预算策略；生产旧信号量只限制并发，不能称旧系统已有该总上限。
- 实际 MockTransport 请求与响应摘要、单次成功 metadata、canonical final 均需匹配，不能只凭调用方的“已验证”布尔值晋级。
- R1 与 scope 在同一学生锁下双向排他。派发前任一目标过期，保守阻断整个固定请求；返回后的专用 apply 再逐题 CAS，可保留有效邻居。
- 专用 apply 重算协议和来源，保持原始资料，更新同一作答的识读版本，使用真实 homework ResultRef；无效、过期或未完成项不进入知识图谱证据。

仍缺生产 host route/receipt 台账生产者、真实网络 dispatcher、正式 worker 调度和真实图像准确率验收。旧档案缺完整可信台账时拒绝自动创建付费恢复，不能从模型输出回填“已派发”证明。只有受支持的单作答、无共享历史分支可清除本轮 scope 产生的特定提示；共享历史与其他疑点继续保持未完成。此包不得直接作为上线开关。

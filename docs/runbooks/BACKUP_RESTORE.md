# 私有备份、隔离恢复与删除重放

`backup.sh` 生成经过原图关联校验的归档；`restore.sh` **只创建全新的数据库和资产存储**，完成核验后停留在 `validated_staged`。恢复命令不停止原 API/Worker、不修改连接密钥、不切换资产挂载、不覆盖已有数据库或原图。生产切换是单独的运维步骤。

## 一致性与维护窗口

数据库事务快照不能同时冻结磁盘上的原图和删除清单。完整备份必须在维护窗口进行：停止 API、Worker、迁移任务及其他直接写数据库/资产的工具，从开始盘点到归档完成保持停止。脚本不会替操作者停服务；缺少 `--writers-quiesced`、检测到已运行的写入服务或其他数据库连接时会拒绝执行。

脚本在导出前后核对业务表记录数、迁移版本和上传原图的数据库元数据，每张归档原图必须匹配数据库中的字节数和 SHA-256。只保存状态为 `uploaded` 或 `deletion_pending` 的关联原图；未提交的临时文件、无数据库关联的文件及 `awaiting_upload/rejected/deleted` 记录不作为已上传原图恢复。维护窗口的外部写入约束仍需由运维保证，前后对比不是在线跨资源快照的替代品。

```sh
cd /srv/XueBan/infra
# 明确进入维护窗口；操作前确认无人正在提交作业。
docker compose stop api worker
# 其他迁移/外部写入者也必须已停止。
BACKUP_DIR=/srv/xueban-backups ./scripts/backup.sh --writers-quiesced
# 无论备份成功或失败，检查结果后由运维恢复原服务。
docker compose up -d --wait api worker
```

产物在唯一新目录中：`database.dump`、`assets.tar`、`database.json`、`deletion-ledger.snapshot.jsonl`、`manifest.json`、`SHA256SUMS`。目录为 0700，文件为 0600。只有 `manifest.json` 的状态为 `complete` 才可恢复；失败归档保留 `failure.json`，不得当成成功备份。SHA 用于检测损坏，不替代访问控制、归档来源核验或异地加密存储。

试点可采用每日一次、保留 14 天、每次升级前额外备份的安排；RPO 24 小时/RTO 2 小时只是待签收的运维目标。本次小型本机演练耗时不代表生产 RTO。排程必须覆盖上述维护窗口、失败处理及服务恢复，不能直接沿用旧版无停写的 cron 示例。

## 默认恢复到新副本

```sh
cd /srv/XueBan/infra
RESTORE_DIR=/srv/xueban-restores ./scripts/restore.sh /srv/xueban-backups/<备份目录>
```

命令执行：

1. 校验完整标记及四个归档文件 SHA；拒绝旧版缺少元数据的归档、损坏文件及符号链接。
2. 创建唯一 `xueban_restore_...` 数据库，记录数据库 OID 和专用身份标记。可用 `--target-database xueban_restore_<自定义名称>` 指定新名称；已有名称一律拒绝。
3. 创建唯一新资产卷（Compose）或新私有资产目录（Mac），不会复用原存储。
4. 用 `pg_restore --clean --if-exists --exit-on-error --single-transaction` 恢复到**刚创建的目标库**。`--clean` 不会指向原库。
5. 安全展开原图：只接受合法平面资产文件名、普通文件和合法大小，拒绝路径穿越、链接、重复文件及覆盖。
6. 核对恢复库各表记录数、迁移版本、原图元数据，并逐张验证原图 SHA/字节数。
7. 从当前独立保留的删除清单重新读取条目，在新数据库和新资产存储中重放，再检查剩余原图与数据库关联。
8. 写入私有 `stage.json`、`verified-database.json`，返回 `validated_staged`。不启动恢复副本的业务服务、不自动执行迁移或切换。

任何恢复步骤失败都会尝试回收本命令新建、且身份仍匹配的数据库和资产；数据库存在使用连接时不强制断开，身份改变时不删除。失败证据与清理状态保留在 staging 目录。不能把 `cleaned` 当作恢复通过，应同时检查 `previous_state`、`restore_completed` 和失败记录。

## 删除清单独立于备份

清单必须始终位于独立的 `ledger` 存储，并另行加密异地保存。备份中的 `deletion-ledger.snapshot.jsonl` 仅供审计；恢复时不得用它替换当前清单，否则备份之后被删除的学生可能重新出现。配置缺失、文件丢失、符号链接、非私有普通文件、存储未挂载或清单格式异常会阻断完整备份/恢复，不会自动假设历史为空。全新 Compose 实例需在首次部署时显式运行 `python -m scripts.recovery_payload initialize-ledger`（在 API 工具容器内）；该命令只在当前库没有删除请求或已删除学生、且清单文件不存在时建立当前基线，不会覆盖已有文件，也不证明过去备份的合规性。

恢复副本准备期间原系统可能继续产生删除。切换前必须停止所有原系统写入者，再次重放**最新**清单并复核：

```sh
python3 scripts/recovery.py revalidate /srv/xueban-restores/<staging目录> --writers-quiesced
```

重验只修改隔离副本；若副本已被应用连接使用，会拒绝操作。它不会同步备份之后的普通新增作业：恢复本质上回到备份恢复点，必须由负责人确认数据损失范围或先完成增量迁移。

## 明确切换和回退

`validated_staged` 是可检查副本，不是生产切换完成。切换前保留原数据库、原资产卷和原连接密钥，不删除它们。

- 核对 `stage.json` 中的 `migration_revisions` 与将启动的应用镜像一致。跨版本恢复需在隔离副本上按升级手册迁移、复验；默认命令不做隐式升级。
- 在维护窗口完成上面的 `revalidate`。抽查账号、作业/订正历史、原图、知识证据、练习记录及删除后的不可访问状态。
- 将数据库连接串写入**新的**私有 secret 文件（0600，Compose 应用读者 uid 10001），仅更改数据库名为 `target_database`；不要把完整连接串放进终端参数、日志或 Git。
- 运维明确修改 Compose override：`database_url` secret 指向新文件；`api/worker/migrate` 的 `/data/assets` 挂载统一改为 `stage.json` 的 `asset_target` 外部卷。保留 `/ledger` 最新独立卷。合并已有 override，不覆盖其他环境设置。
- 执行显式部署，检查 `/readyz`、登录、历史原图、订正和知识地图。新工具从应用实际连接 secret 获取当前数据库名，因此后续备份不会固定读取旧 `xueban` 库。
- 若尚未接受新写入，可恢复上一份 override/secret 引用并重新启动旧服务；接受新写入后不能直接切回旧数据，应先评估并迁移这些变化。

切换完成后的当前库禁止用 staging 清理命令删除。未切换且不再需要的副本可明确清理：

```sh
python3 scripts/recovery.py cleanup /srv/xueban-restores/<staging目录>
```

清理核对数据库 OID/标记及资产所有权，仅回收本工具创建的对象；不会使用 `DROP DATABASE ... FORCE`。归档与审计报告保留，按保留策略另行处理。

## Mac 本机与已有环境的清单盘点

新 `mac_local init` 自动建立 0700 `ledger/` 和 0600 `deletions.jsonl`。旧 runtime 的 `load/start/migrate` 不会静默创建清单。

先在 `services/backend` 目录执行只读盘点：

```sh
.venv/bin/python -m scripts.mac_local --runtime ~/.local/share/xueban/local-dev audit-deletion-ledger
# 找回并检查已有清单后，也可以先检查候选文件：
.venv/bin/python -m scripts.mac_local --runtime ~/.local/share/xueban/local-dev audit-deletion-ledger --ledger-path /私有目录/deletions.jsonl
```

输出只有计数和状态。已完成/服务器已执行/已标记删除落地的请求必须在清单中存在且归属一致；缺失时先从独立存储恢复和核查历史，不能临时创建空文件后宣称历史备份合规。即使当前数据库没有删除请求，也不能证明更早数据库或备份的历史，`historical_backup_compliance` 始终为 `unverified`。

完成盘点后，运维显式绑定一个**已存在**的 0600 清单文件及 0700 父目录：

```sh
.venv/bin/python -m scripts.mac_local --runtime ~/.local/share/xueban/local-dev configure-deletion-ledger --ledger-path /私有目录/deletions.jsonl
```

绑定命令不创建文件、不丢弃此前保留的清单条目；移动一个已配置清单的位置前还必须先停止 API/Worker，避免审计之后旧文件继续追加；缺已落地删除记录或发现无请求锚点的已删除学生时拒绝。成功后仅保存配置，需由运维在合适时机重启 API/Worker。没有历史删除证据的新环境可以经盘点后显式建立空清单，但不得据此追认过去备份。

Mac 完整备份需先按本机流程停止 API/Worker、保持 PostgreSQL 可用。使用后端虚拟环境 Python，不全局安装工具：

```sh
# 仓库根目录；--runtime 必须放在子命令之前。
services/backend/.venv/bin/python infra/scripts/recovery.py --runtime /私有runtime backup --writers-quiesced --backup-dir /私有备份目录
services/backend/.venv/bin/python infra/scripts/recovery.py --runtime /私有runtime restore /私有备份目录/<归档> --restore-dir /私有恢复目录
services/backend/.venv/bin/python infra/scripts/recovery.py --runtime /私有runtime revalidate /私有恢复目录/<staging> --writers-quiesced
services/backend/.venv/bin/python infra/scripts/recovery.py --runtime /私有runtime cleanup /私有恢复目录/<staging>
```

`mac_local stop` 会同时停止 PostgreSQL，不适用于需要数据库仍在线的备份窗口；应通过本机进程管理只停 API/Worker，确认 PID 对应当前服务，备份完成后再恢复服务。脚本发现活跃 PID 或其他数据库连接会拒绝备份。

## 已有证据与未覆盖项

[2026-10-21 本机恢复演练](../evidence/recovery-20261021/README.md) 覆盖 42 张业务表、57 张原图的隔离备份/恢复，以及备份之后新增删除清单的重放、原始数据不变和专用副本清理。Docker 部署冒烟已改为验证隔离恢复后原服务仍可用，并清理新副本；必须以实际 CI 成功记录作为 Compose 验收证据。真实生产维护窗口、异地取回、密钥恢复、人工切换及现场连续使用仍须单独执行。

# 本机稳定运行（launchd 守护 + 每日备份）

服务由当前用户的 launchd 管理，不需要 root，不改系统设置。代码目录是 `Documents/ChatGPT/学伴项目`，
运行数据在 `~/.local/share/xueban/local-dev/`（数据库、原图、账号、删除清单、备份），二者分开。

```sh
cd services/backend
.venv/bin/python scripts/mac_service.py install     # 写入 4 个 LaunchAgent 并启动
.venv/bin/python scripts/mac_service.py status      # 进程、/readyz、磁盘、最近备份；有问题时退出码为 1
.venv/bin/python scripts/mac_service.py backup      # 立即做一次静默备份（约 6 秒中断）
.venv/bin/python scripts/mac_service.py uninstall   # 卸载代理，数据保留
```

| 代理 | 作用 |
|---|---|
| `local.xueban.postgres` / `.api` / `.worker` | 前台运行，登录后自启，崩溃后约 10 秒内自动拉起 |
| `local.xueban.backup` | 每天 03:17 备份，保留最近 14 份，位于 `scheduled-backups/` |

plist 里不含任何密钥或配置；它只启动 `mac_service.py exec <name>`，在进程启动时读取私有配置。
模型密钥只传给 Worker，API 进程里没有（有测试覆盖）。改动模型配置或升级代码后重启对应代理：

```sh
launchctl kickstart -k gui/$(id -u)/local.xueban.worker
launchctl kickstart -k gui/$(id -u)/local.xueban.api
```

## 升级代码或网页

```sh
git pull --ff-only origin main
cd apps/student-web && VITE_READ_CHECKPOINT_ENABLED=1 VITE_HOMEWORK_DETAIL_SNAPSHOT_ENABLED=1 npm run build
cd ../staff-web && npm run build
cd ../../services/backend && .venv/bin/python scripts/mac_service.py backup   # 先备份
launchctl kickstart -k gui/$(id -u)/local.xueban.api && launchctl kickstart -k gui/$(id -u)/local.xueban.worker
```

迁移在 API 启动前由 `mac_local.py migrate` 执行；需要时手动运行，迁移只前进。

## 已验证（2026-10-22，本机）

- 切换到 launchd 前后，15 张业务表的行数与内容摘要相同；API/Worker 的工作目录是正式目录；没有进程引用 Codex worktree。
- `kill -9` API 和 Worker 后约 4 秒内 `/readyz` 恢复，PID 更换。
- 一次备份：约 4 秒，服务约 6 秒后恢复。
- 恢复演练：用该备份恢复到独立库，60 张原图全部校验；`homeworks`、`correction_entities`、`correction_tasks`、
  `correction_assets`、`question_knowledge_links`、`learning_evidence`、`student_knowledge_states`、`learning_reports`、
  `students`、`correction_events` 十张表行数与内容摘要与线上完全一致；演练库已清理，线上不受影响。

## 未验证 / 注意

- 尚未经历一次完整的 Mac 重启（`RunAtLoad` 与登录自启的真实表现）。Mac 睡眠时服务暂停；使用期间请接电源并避免睡眠。
- 备份与数据库在同一块磁盘上，防不了磁盘损坏或丢失；请定期把 `scheduled-backups/` 与 `governance/`（删除清单，须与备份分开保存）复制到另一块磁盘。
- 03:17 的计划备份在 Mac 睡眠时不会执行，唤醒后补跑一次。
- 这些只解决“能稳定运行”。真实手写整页识别质量、平板真机、现场使用仍未验收。

## 2026-10-22 切换记录

- PR #5（整页纠错与学习层，72 个提交）、#6（launchd 守护）、#7（定时备份与本文）已合并进 main；PR #3、#4 在此之前已合并，PR #1 是 10-16 的静态平板 UI 原型（`prototype/student-tablet-ui/`，4 个文件，main 里没有），保留未关闭，由你决定是否合入。
- 切换前备份：`scheduled-backups/20261022t035555z_…`；切换后 15 张业务表摘要与切换前完全一致；学生包仍是 `index-Czg2tERQ.js`。
- 切换时第一次备份曾失败（`subprocess_failed_2`）：当时 `mac_local.py stop` 已把 PostgreSQL 也停掉，备份工具连不上库。处理是先只启动 PostgreSQL 再备份；随后在 `install` 里不再强制要求“停止”命令成功（它在服务已停时会返回非零）。这个顺序问题写在这里，避免以后重复。
- 当时 Mac 在电池供电（35%）。电量耗尽或睡眠时服务会停止，这是代码无法解决的；长时间使用请接电源。

# 日常运维与故障处置

## 每日检查（值班人，5 分钟）

1. `curl -fsS https://<域名>/readyz` 返回 ready。
2. `docker compose ps` 中 `api`、`worker`、`postgres`、`caddy` 为 running/healthy。
3. 坐班工作台“待核实”队列没有超过 30 分钟未处理的项目；超出即通知坐班负责人。
4. 前一晚备份日志出现 `backup_written=`。

## 常见故障

| 现象 | 判断 | 处置 |
|---|---|---|
| 学生页面显示“识别/检查失败，可重试” | `docker compose logs worker --since 30m` 中的固定失败码：`provider_timeout`、`provider_unavailable`、`invalid_provider_output` | 模型服务故障或超时。学生可点重试；持续失败时告知坐班老师现场人工核实（`manual_initial` 初判），不要让孩子反复拍照。超时可能已计费，不会自动无限重试。 |
| 任务长时间“处理中” | Worker 停止或卡住 | `docker compose restart worker`。租约到期（默认 300 秒）后任务自动被重新领取；已提交的结果不会重复写入。 |
| `/readyz` 显示 `migration_pending` | 新版本镜像未完成迁移 | `docker compose run --rm migrate`，查看输出；失败时恢复到升级前备份并回到旧镜像。 |
| `/readyz` 显示 `asset_storage: unavailable` | 磁盘满或卷权限错误 | `df -h`；清理旧备份；不要删除 `assets` 卷中的文件。 |
| 登录返回“尝试过于频繁” | 账号或 IP 被限流 | 等待 15 分钟；确认不是撞库后，由管理员重置该学员密码。 |
| 数据库不可用 | `docker compose logs postgres` | 检查磁盘与内存；必要时按 [备份与恢复](BACKUP_RESTORE.md) 恢复。 |

## 模型或供应商变更

更换模型、提示词版本或扩大题型前，先在评测集上跑回归（见 [evals/correction](../../evals/correction/README.md)），与上一版本按切片对比误判、漏判与“无法判断”比例；任何切片劣于冻结阈值不得上线。新模型名称只在 `model.env` 中修改，API 容器不接触模型密钥。

## 学生数据删除

数据治理负责人在坐班工作台提交删除申请后：受理即停止处理并阻止在线读取；Worker 依次写删除清单、删除原图与全部学员记录。状态停留在 `waiting_devices` 表示服务端已清除，正在等待设备缓存保留期（默认 7 天）结束；之后变为 `completed`。查询：`GET /v1/deletion-requests/{id}`。

## 日志与隐私

日志只包含固定错误码和请求 ID，不包含作业内容、照片、密码、令牌或模型原文。排查时用页面上显示的请求 ID 对应服务器日志；不要导出数据库表到个人电脑。

# 备份、恢复与删除重放

## 目标与频率

试点建议：恢复点目标（RPO）24 小时，恢复时间目标（RTO）2 小时。每日定时执行一次备份，保留 14 天；每次升级前额外备份。正式目标须在试点前由验收负责人签收。

```sh
# crontab 示例（服务器本地时间 02:30）
30 2 * * * cd /srv/XueBan/infra && BACKUP_DIR=/srv/xueban-backups ./scripts/backup.sh >> /var/log/xueban-backup.log 2>&1
```

[backup.sh](../../infra/scripts/backup.sh) 产出 `database.dump`（`pg_dump` 自定义格式）、`assets.tar`（私有原图）和 `SHA256SUMS`。备份目录权限 0700，应再加密复制到异地（例如 `restic` 或对象存储的服务端加密）。

## 删除清单为什么单独保存

学生数据删除执行时，先把 `{deletion_id, student_id, asset_ids}` 追加写入卷 `ledger` 中的 `deletions.jsonl`，再删除数据库记录与原图。这个文件**不在**数据库备份里：如果从删除之前的备份恢复，数据库会“复活”已删学员的数据，恢复脚本随后读取当前清单再次清除。因此：

- `ledger` 卷要单独复制到异地，且只追加不覆盖；
- 恢复时始终使用**最新**的清单，而不是备份目录里的快照。

## 恢复步骤

```sh
cd /srv/XueBan/infra
./scripts/restore.sh /srv/xueban-backups/20261020T023000Z
```

[restore.sh](../../infra/scripts/restore.sh) 会：校验 SHA256 → 停止 API/Worker/代理 → 重建数据库并导入 → 替换原图 → 执行迁移 → `python -m xueban_backend.governance --reapply-ledger` 重放全部删除 → 重新启动。完成后访问 `/readyz`，并抽查一名学员的历史作业与原图。

## 演练记录

每月或每次重大升级做一次恢复演练，在 `docs/evidence/` 下登记：备份时间、恢复耗时、核对的记录数、删除重放数量、发现的问题。GitHub Actions 的部署冒烟每次推送都会执行一次空库备份→恢复→就绪检查，但它不能替代带真实数据量的现场演练。

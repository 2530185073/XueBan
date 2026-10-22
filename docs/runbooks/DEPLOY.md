# 部署手册（单机试点）

适用于 10—20 名学生的托管点试点：一台 Linux 服务器（2 vCPU / 4 GB 内存 / 50 GB 磁盘起），Docker Engine 25+ 与 Compose v2，一个指向该服务器的域名。所有组件在同一主机，用 [Caddy](https://caddyserver.com/docs/automatic-https) 自动申请和续期 HTTPS 证书。

| 组件 | 镜像 | 作用 |
|---|---|---|
| `postgres` | `postgres:17` | 业务数据库，数据卷 `pgdata` |
| `migrate` | 本仓库镜像 | 每次启动前执行 Alembic 迁移，成功后退出 |
| `api` | 本仓库镜像 | FastAPI；同源提供学生网页 `/learn/`、坐班工作台 `/staff/` 与 `/v1/` 接口 |
| `worker` | 本仓库镜像 | 持久任务：模型识别/检查/提示/补题，删除执行 |
| `caddy` | `caddy:2` | TLS 终止、反向代理、请求体上限 |

镜像构建见 [infra/Dockerfile](../../infra/Dockerfile)，编排见 [infra/compose.yaml](../../infra/compose.yaml)。应用容器以 uid 10001 运行，根文件系统只读、丢弃全部 Linux capability；模型密钥只注入 Worker。

## 首次部署

```sh
git clone https://github.com/2530185073/XueBan.git && cd XueBan/infra
cp xueban.env.example xueban.env      # 修改域名相关的 OIDC_ISSUER / PUBLIC_BASE_URL
cp model.env.example model.env && chmod 600 model.env   # 填入 ARK_API_KEY
sudo ./scripts/init-secrets.sh        # 生成数据库口令、连接串、会话签名密钥（不覆盖已有文件）
export XUEBAN_DOMAIN=xueban.example.org
docker compose up -d --build --wait
# 仅全新实例：盘点无删除请求后显式建立独立空清单，已有实例须按恢复手册找回旧清单。
docker compose run --rm --no-deps -T --entrypoint python api -m scripts.recovery_payload initialize-ledger
curl -fsS https://$XUEBAN_DOMAIN/readyz
```

`/readyz` 返回 `{"status":"ready"}` 才表示数据库已迁移到当前版本且私有图片目录可写；`/healthz` 只说明进程存活。

初始化首个校区管理员（密码通过标准输入写入容器内 0600 临时文件，不出现在命令行参数或日志中）：

```sh
read -rs ADMIN_PASSWORD && printf '%s\n' "$ADMIN_PASSWORD" | docker compose run --rm -T --no-deps --entrypoint sh api -c \
  'umask 077; cat > /tmp/p; python -m xueban_backend.admin bootstrap-admin --tenant-id tenant_main --campus-id campus_main --principal-id actor_admin_main --subject admin-main --username campus.admin --password-file /tmp/p'
unset ADMIN_PASSWORD
```

管理员首次登录 `/learn/` 后必须修改密码，然后在“学员账号”页开通学员；老师账号通过 `POST /v1/admin/staff-accounts`（坐班工作台的管理页）开通，角色为 `sitting_teacher`，需要处理删除申请的负责人另加 `data_steward`。

## 升级与回滚

```sh
git pull && docker compose build
docker compose stop api worker       # 明确维护窗口；其他写入者也须停止
./scripts/backup.sh --writers-quiesced
docker compose up -d --wait           # migrate 先执行，失败则 api/worker 不启动
```

迁移只前进；回滚代码前先把升级前的备份恢复到新数据库和新资产卷，经核验后明确切换（见 [备份与恢复](BACKUP_RESTORE.md)），不要对生产库执行 `alembic downgrade`。镜像可打标签 `XUEBAN_IMAGE=xueban-backend:<git-sha>` 以便回到上一版本。

## 安全基线

- 只开放 80/443；PostgreSQL 不映射到宿主机端口。
- `infra/secrets/`、`xueban.env`、`model.env` 不进入 Git（已在 `.gitignore`）。
- Caddy 只记录 WARN 及以上日志，避免把图片读取票据（URL 查询参数）写入访问日志；应用自身关闭了访问日志。
- 登录有按 IP 与账号的失败限流；上传单张 4 MiB、请求体 6 MB 上限由应用与 Caddy 双重限制。
- 学生数据删除依赖 `DELETION_LEDGER_PATH`（卷 `ledger`），该卷必须与数据库备份分开保存。

部署冒烟由 GitHub Actions [Deployment smoke](../../.github/workflows/deploy-smoke.yml) 自动执行：构建镜像、启动、经代理检查就绪、初始化管理员并登录、在维护窗口备份、恢复到独立副本，验证原服务仍就绪并清理测试副本。恢复命令默认不切换在线数据。

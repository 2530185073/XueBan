# 后端基础切片：身份与作业批次

此目录实现了可运行的第一批后端基础，不是完整纠错服务。仅包含下列 4 个操作；真实 OCR、照片上传、判题、提示、订正、老师复核、删除、OIDC 登录界面均未实现。

| 操作 | 行为 |
|---|---|
| `GET /v1/me` | 验证 Bearer 后读取数据库身份、角色和当前学生授权 |
| `POST /v1/students/{student_id}/homeworks` | 契约校验、服务端归属、事务内持久保存与幂等 |
| `GET /v1/homeworks/{homework_id}` | 再核当前学生授权后读取作业 |
| `GET /v1/students/{student_id}/homeworks` | 授权学生的历史列表，按创建时间倒序与游标分页 |

数据和请求以仓库 `contracts/phase1/` 为来源，运行时验证请求及成功响应；未采集照片时资源 ID 数组为空。未知年级/科目/册次可保留 `null`。未配置真实身份服务、未建立数据库授权或未执行迁移都拒绝访问，不回退演示身份。

## 启动与迁移

Python 3.14。以下命令从仓库根执行，安装仅作用于本目录虚拟环境：

```sh
python3 -m venv services/backend/.venv
services/backend/.venv/bin/python -m pip install -r services/backend/requirements.txt
```

按 `.env.example` 设置环境变量（本服务不会自动读取 `.env`）。`DATABASE_URL` 必须指向 PostgreSQL 并使用 `postgresql+psycopg://` 驱动；凭据通过部署环境注入，禁止写入 Git。应用启动不自动建表、不自动导入用户。

```sh
services/backend/.venv/bin/alembic -c services/backend/alembic.ini upgrade head
PYTHONPATH=services/backend services/backend/.venv/bin/uvicorn xueban_backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

推荐本地 API 端口8000，坐班 Web 端口5173。Vite 默认打开 `http://127.0.0.1:5173`，它与 `http://localhost:5173` 是不同来源；示例明确列出这两个开发来源。实际缺省为空列表，不支持通配符，不启用 Cookie 凭证。部署前将 `CORS_ALLOWED_ORIGINS` 改为实际受信任前端源。

`0001_identity_homeworks` 明确创建8张业务表；没有 `create_all` 启动捷径。`alembic upgrade head` 可重复执行。`downgrade base` 会删除这些表，仅用于一次性测试库或经正式数据处置批准的环境，不能当作生产发布回滚。

## 真实身份与授权映射

接受预先配置的 OIDC issuer 签发给 `OIDC_AUDIENCE` 的访问令牌，固定使用 RS256，校验签名、issuer、audience、有效期、签发时间和 subject。配置可信 HTTPS JWKS URL；或挂载 issuer 的公开 PEM 并设置 `JWT_PUBLIC_KEY_FILE`，两者只能选一个。生产无需保存签名私钥。

JWT 内的角色、租户、学生自报字段一律不用于授权。数据库匹配 `(issuer, subject)` 得到 active principal，再加载 `principal_roles` 与 active `student_grants`；普通管理员也必须有学生授权。`X-Demo-Actor` 不会认证用户。每次请求重新校验授权，所以撤回授权后即使令牌尚未过期也不能读取历史或重放写入。

首批可信授权数据由部署管理员经受控后台/数据库流程录入，需依次建立 `tenants → campuses / principals → students / principal_roles → student_grants`。主体 subject 必须来自已验证的身份提供方账户；授权必须对应有效的学生参与授权。本切片未提供用户自助注册、人员导入或授权管理页面，测试夹具不是部署种子数据。

学生/托管点/租户关系和授权同时有数据库复合外键保护。跨生、同租户未授权、跨租户读取和写入统一返回404；未映射或停用身份返回403；令牌缺失/无效返回401；身份配置或密钥服务不可用返回503。

## 写入与恢复

创建作业必须带8–128字符 `Idempotency-Key`。作用域是已认证主体、授权学生和操作；对 JSON 键排序后的载荷求哈希。同键同载荷返回原201结果，同键不同载荷409。幂等记录与作业在同一个数据库事务提交；唯一约束保护 PostgreSQL 并发争用，失败不保留“半完成”记录。当前没有幂等清理任务，记录持续保存，后续引入清理前必须先冻结客户端重试窗口。

日期使用带时区的 RFC3339，输出归一 UTC。历史游标携带学生与分页位置，不能切换学生范围；游标不充当授权凭据。所有业务读取仍重新校验数据库授权。

响应错误遵循 `Problem` Schema，隐藏 SQL、凭据和完整请求。`X-Request-Id` 由服务端产生。应用无上传/检查成功的假结果，数据库不可用不会冒充创建成功。

## 验证入口

本机无 PostgreSQL/Docker 时，仅运行 SQLite 替身：

```sh
services/backend/.venv/bin/python -m pytest services/backend/tests -q
```

SQLite 必须显式启用，仅用于测试，不证明 PostgreSQL 的锁与事务语义。测试默认在临时文件建库，走 Alembic 迁移。测试签名私钥只在运行时内存生成，公开 PEM 写临时目录，不入仓库。

GitHub CI PostgreSQL 17 service 的命令：

```sh
# DATABASE_URL 指向本次 CI 的专用服务库；不得使用生产库。
python -m pip install -r services/backend/requirements.txt
alembic -c services/backend/alembic.ini upgrade head
alembic -c services/backend/alembic.ini upgrade head

# BACKEND_TEST_DATABASE_URL 指向 CI 专用测试库。
python -m pytest services/backend/tests -q --require-postgres
```

必须设置 `BACKEND_TEST_DATABASE_URL=postgresql+psycopg://...`；未设置时 `--require-postgres` 失败，不静默跳过。PG 测试在指定测试库内为每个用例创建随机 `xueban_test_<随机ID>` schema、执行迁移，结束只删除自己的 schema；测试账户需要建 schema 权限。不会删除指定库内已有业务表。禁止把真实生产库提供给测试。

用例覆盖 JWT 签名/issuer/audience/expiry/iat/算法、伪造角色无效、撤权与越权、契约拒绝、幂等冲突、事务失败回滚、游标隔离、精确 CORS、迁移重复执行/降级后重建、关闭原服务后由新 Python 进程读回已提交数据。PG 专用用例同时测试同键同载荷和同键不同载荷并发。

本轮本机只有 SQLite 验证；PostgreSQL 实跑结果以集成 CI 为准。JWKS 路径使用运行时生成的公钥与受控网络替身检查，真实外部身份提供方、密钥轮转、生产 TLS、托管授权录入及真实设备联调仍待集成；本切片不能作为上线验收。

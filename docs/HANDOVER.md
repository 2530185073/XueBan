# 技术交接清单（P1-16）

按 [ANDROID_PHASE1 §交付](../ANDROID_PHASE1.md) 要求的交接包逐项列出。状态“已具备”表示仓库中有可运行产物并经 CI 验证；“待现场”表示需要现场人员、真实设备、签名密钥或真实样本。

| 交接件 | 状态 | 位置 |
|---|---|---|
| Android App 源码与构建 | 已具备（0.3.0：账号登录、多页拍摄与旋转裁切、WorkManager 续传；debug + R8 未签名 release，CI 构建）；真机待验收 | [apps/android](../apps/android/README.md)、`.github/workflows/android-checks.yml` |
| 签名安装包 | 待现场：签名密钥由项目方保管，通过 `XUEBAN_KEYSTORE_PATH`、`XUEBAN_KEYSTORE_PASSWORD`、`XUEBAN_KEY_ALIAS`、`XUEBAN_KEY_PASSWORD` 环境变量注入，密钥不入仓库 | `apps/android/app/build.gradle.kts` |
| 后端服务 | 已具备 | [services/backend](../services/backend/README.md) |
| 坐班工作台 / 学生网页 | 已具备（由后端同源托管） | [apps/staff-web](../apps/staff-web/README.md)、[apps/student-web](../apps/student-web/README.md) |
| 正式接口 | 已具备（`0.1.0-draft`，CI 校验） | [contracts/phase1](../contracts/phase1/README.md) |
| 数据库迁移 | 已具备（`0001 → 0009`） | `services/backend/migrations/versions/` |
| 部署产物与配置模板 | 已具备 | `infra/compose.yaml`、`infra/Dockerfile`、`infra/Caddyfile`、`infra/xueban.env.example`、`infra/model.env.example` |
| 部署、升级与回滚 | 已具备（CI Deployment smoke） | [DEPLOY](runbooks/DEPLOY.md) |
| 备份、恢复与删除重放 | 已具备脚本与手册；生产演练待现场 | [BACKUP_RESTORE](runbooks/BACKUP_RESTORE.md)、`infra/scripts/` |
| 日常运维 | 已具备 | [OPERATIONS](runbooks/OPERATIONS.md) |
| 坐班操作手册 | 已具备 | [STAFF_MANUAL](runbooks/STAFF_MANUAL.md) |
| 支持手册 | 已具备 | [SUPPORT](runbooks/SUPPORT.md) |
| 设备矩阵 | 模板已具备；设备与结果待现场 | [DEVICE_MATRIX](runbooks/DEVICE_MATRIX.md) |
| 评测运行器与回归门槛 | 已具备；阈值 `provisional`，保留集与冻结阈值待现场 | [evals/correction](../evals/correction/README.md) |
| 冻结记录 | 工程口径已冻结；设备/题型/样本/阈值待现场 | [PHASE1_FREEZE](PHASE1_FREEZE.md) |
| 试点流程与 C0/R1/R2 证据 | 清单已具备；证据待现场 | [PILOT](runbooks/PILOT.md) |
| 已知支持范围 | 待现场：按冻结题型与设备矩阵结果填写 | 本表与 [PHASE1_FREEZE](PHASE1_FREEZE.md) |
| 后续阶段学习层 | 已具备（知识图谱、长期 AI、家长报告、举一反三、预习小测） | [LEARNING_LAYER](LEARNING_LAYER.md) |

验证记录见 [VALIDATION](../VALIDATION.md)。

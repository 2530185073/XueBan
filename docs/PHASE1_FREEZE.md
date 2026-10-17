# 一期冻结记录（P1-01）

本记录区分**已由工程冻结并有自动化证据**的口径，与**必须由现场负责人确认**的口径。后者未确认前，相关验收项一律记为“待现场”，不得用网页、模拟或合成样本替代。

最后更新：2026-10-17

## 已冻结（工程）

| 项目 | 冻结内容 | 证据 |
|---|---|---|
| 学段与教材 | 初中、人教版；后续可扩展，但一期验收只对声明范围计分 | [PHASE1](../PHASE1.md)、[CURRICULUM_RESOURCES](../CURRICULUM_RESOURCES.md) |
| 平台 | Android 平板 App（Kotlin/Compose/CameraX/WorkManager），学生网页为同账号的补充入口，不计入 App 交付 | [ANDROID_PHASE1](../ANDROID_PHASE1.md)、[apps/android](../apps/android/README.md) |
| 账号与归属 | 一学员一账号（用户名+密码），账号绑定固定 `student_id`；首登强制改密；坐班老师由校区管理员开通 | [ACCOUNT_FOUNDATION](ACCOUNT_FOUNDATION.md)、`services/backend/tests/test_account_auth.py` |
| 接口与数据结构 | `contracts/phase1` 32 个操作、4 份 JSON Schema；`Problem` 列出后端实际返回的全部错误码；前端校验器由契约生成，CI 校验一致 | `contracts/phase1/validate.py`、两端 `contracts:check` |
| 数据库迁移 | Alembic 链 `0001 → 0010`，SQLite 与 PostgreSQL 双库测试 | `services/backend/migrations/versions/` |
| 纠错规则 | 模型输出只作候选；老师确认才成为正式结论；看过提示后的作答不能记为独立；订正永不算独立 | [blueprint-v3/10](../blueprint-v3/10-纠错核心流程与首发验收.md)、`test_c0_acceptance.py` |
| 学习规则版本 | `r1.2-custody`（变更即触发全量重算） | `services/backend/xueban_backend/learning_rules.py` |
| 模型服务 | BytePlus ModelArk AP，五角色统一模型 `deepseek-v4-1-flash-260910`，严格 JSON 解析与本地 Schema 校验 | [MODEL_SERVICE_PLAN](../MODEL_SERVICE_PLAN.md) |
| 评测口径 | 固定分母、模型与人工分列、按题型/图像条件切片；回归比较器零容忍“错题放行” | [evals/correction](../evals/correction/README.md)、`gates.json`（`provisional`） |

## 待现场确认（负责人与阻塞范围）

| 项目 | 需要确认的内容 | 建议负责人 | 未确认时阻塞 |
|---|---|---|---|
| 首发设备 | 平板型号（至少一款主力机）、最低 Android 版本、相机/内存条件；填入 [设备矩阵](runbooks/DEVICE_MATRIX.md) | 现场负责人 + 研发 | P1-03、P1-13、R1 设备与体验 |
| 安装与签名 | 安装渠道（MDM/直接分发）、签名密钥保管人、升级策略 | 项目方 | P1-16 签名安装包 |
| 首发题型 | 年级、册次、修订版；首批题型（建议计算、选择、填空、简单代数）逐项列出，未列入的题只保存和求助 | 学科负责人 | C0 计分范围、R1 纠错质量 |
| 独立保留集 | 授权的真实学生作业样本、标注规范、双人复核；冻结后不用于调参 | 学科负责人 + 评测 | R1 纠错质量 |
| 发布阈值 | 在保留集上把 `gates.json` 从 `provisional` 改为 `frozen`，写明置信要求与最小切片分母 | 产品 + 学科负责人 | R1、R2 |
| 坐班容量 | 每日峰值提交题数、实测平均复核秒数、排班人数与响应时限 | 现场负责人 | R1 试点准备 |
| 课程图谱 | `pilot_seed_unreviewed` 图谱的教研审核（后续阶段功能，不阻塞一期） | 教研 | 后续阶段上线 |

确认后在本表对应行写明结论、日期与确认人，并同步 [phase1-backlog](../blueprint-v3/contracts/phase1-backlog.json) 中 P1-01 的状态。

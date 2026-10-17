# 完整网页版技术交接清单（P1-16 / W1-07）

本轮以 [一期范围](../PHASE1.md) 与 [完整网页路线](WEB_ROADMAP.md) 为交付依据：学生、老师、管理员和家长在网页完成全部当期业务，App 移植单列后续。R2 必须覆盖 [phase1-backlog](../blueprint-v3/contracts/phase1-backlog.json) 核心 16 项和 `web_module_items` 的 W1-01～W1-07 全部验收。

“工程已具备”仅指仓库已有相应实现、产物或验证记录；不等于所有网页流程或真实现场验收通过。“待补齐/待验收”明确列出剩余工作。本轮调整交付口径，不新增已通过的测试或现场结论；既有运行结果见 [VALIDATION](../VALIDATION.md)。

## 四角色网页交付

| 交接件 | 现有基础与剩余工作 | 位置 / 任务 |
|---|---|---|
| 学生网页纠错 | 账号登录、上传、检查、提示、订正再检和历史已有工程；多页拍摄/上传、排序、旋转裁切、历史浏览及真实平板异常恢复按完整网页范围补齐并验收 | [apps/student-web](../apps/student-web/README.md)、[WEB_LOCAL_RUN](WEB_LOCAL_RUN.md)；P1-03～P1-13 |
| 老师网页 | 登录、复核队列、原图/识别核对、接单、改判与识别修订已有工程；实际复核效率、未确认写入和页面刷新恢复待验收 | [apps/staff-web](../apps/staff-web/README.md)；P1-09、P1-10、P1-13 |
| 管理员网页 | 复用已有账号与权限后端；学员/教材档案、老师开户/重置、校区授权与既有学情/运行指标的完整网页操作待补齐和验收 | [ACCOUNT_FOUNDATION](ACCOUNT_FOUNDATION.md)；W1-01 |
| 知识图谱与长期 AI | 知识点候选/确认、证据与图谱、长期记忆、偏好和目标已有实现；课程图谱仍需教研审核，事实引用、撤销传播及学生/老师网页操作待验收 | [LEARNING_LAYER](LEARNING_LAYER.md)；W1-02、W1-03 |
| 每日反馈、半月报告与家长网页 | 草稿、发布、更正、分享链接、有效期/撤销及家长页面已有实现；发布、复制消息、人工确认发送状态待收口，实际反馈工时和内容质量待验收 | [LEARNING_LAYER](LEARNING_LAYER.md)；W1-04 |
| 审核题库与举一反三 | 已有题库审核与自愿活动工程；内容审核、学校任务完成/老师确认有余力/孩子自愿三项门禁，以及退出体验待验收 | [LEARNING_LAYER](LEARNING_LAYER.md)；W1-05 |
| 预习与自愿小测 | 已有工程；题库内容、网页操作、跳过退出与帮助条件保存待验收，不以额外做题作为纠错完成条件 | [LEARNING_LAYER](LEARNING_LAYER.md)；W1-06 |
| 四角色联合验收 | 以同一条真实作业与订正证据贯通学生、老师、管理员和家长；全部模块正常/异常流程、权限、更正和历史连续性待验收 | [PILOT](runbooks/PILOT.md)、[DEVICE_MATRIX](runbooks/DEVICE_MATRIX.md)；W1-07 |

每日反馈的当前能力是发布、生成链接并复制消息供老师发送；**复制消息不代表真实发送或渠道送达**。现有 `view_count` 可作为链接访问记录，不能据此证明特定家长已阅读。人工确认发送的记录与页面状态需按 W1-04 完成；未接入真实发送渠道时，不宣称已有送达回执。

## 共用服务、运行与验收资料

| 交接件 | 状态与验收边界 | 位置 |
|---|---|---|
| 后端服务与 Worker | 工程已具备；完整网页并发、任务恢复、学习层刷新和现场运行待验收 | [services/backend](../services/backend/README.md) |
| 网页构建与托管 | 学生和老师网页已有构建与后端同源托管；完整角色入口、生产域名及浏览器矩阵待验收 | [WEB_LOCAL_RUN](WEB_LOCAL_RUN.md)、[DEPLOY](runbooks/DEPLOY.md) |
| 正式接口 | 已有 `0.1.0-draft` 契约与 CI 校验；完整网页扩展接口的文档、响应校验和版本兼容须一并交接 | [contracts/phase1](../contracts/phase1/README.md) |
| 数据库迁移 | 已有 `0001 → 0010` 与验证记录；升级、回滚范围和备份恢复结果须绑定交付版本 | `services/backend/migrations/versions/` |
| 部署产物与配置模板 | 工程已具备，实际生产配置由项目方落实 | `infra/compose.yaml`、`infra/Dockerfile`、`infra/Caddyfile`、`infra/xueban.env.example`、`infra/model.env.example` |
| 部署、升级与回滚 | 已有手册和 CI Deployment smoke；目标环境的升级与失败回滚演练待完成 | [DEPLOY](runbooks/DEPLOY.md) |
| 备份、恢复与删除重放 | 已具备脚本与手册；含图片、原作答、订正、改判、报告和删除记录的实际恢复演练待完成 | [BACKUP_RESTORE](runbooks/BACKUP_RESTORE.md)、`infra/scripts/` |
| 日常运维、告警与容量 | 已有手册及部分服务指标；告警接入、模型成本、峰值等待与人工容量待现场核实 | [OPERATIONS](runbooks/OPERATIONS.md) |
| 坐班操作与支持 | 已有手册；完整网页各角色流程、账号管理、家长人工发送与异常恢复须随功能补齐后交接 | [STAFF_MANUAL](runbooks/STAFF_MANUAL.md)、[SUPPORT](runbooks/SUPPORT.md) |
| 网页设备矩阵 | 模板已具备；声明设备、浏览器版本、拍照、多页、刷新/断网恢复和账号切换结果待现场 | [DEVICE_MATRIX](runbooks/DEVICE_MATRIX.md) |
| 评测集与发布门槛 | 已有判题回归运行器；原图到再检端到端覆盖、真实人工金标、保留集及绝对发布阈值待补齐。阈值须在查看保留集结果前冻结 | [evals/correction](../evals/correction/README.md) |
| 支持范围与负责人 | 网页交付范围已统一；具体设备/题型、质量门槛、授权、保留期限、恢复目标和责任人待冻结 | [PHASE1_FREEZE](PHASE1_FREEZE.md) |
| C0 / R1 / R2 证据 | 清单已具备；真实验收仍待完成。R1 可先小范围试点，R2 不能排除任何完整网页模块 | [PILOT](runbooks/PILOT.md) |

R2 交付包须包含：绑定版本的网页构建与部署入口、服务和迁移、配置模板、支持范围、核心 16 项及 W1 七项验收结果、真实试点与质量报告、设备记录、备份恢复/回滚证据、坐班与运维责任交接。建议试点为 10–20 名学生、至少 10 个实际出勤日；实际范围在启动前冻结，未发生的场景保持未验收。

## App 独立后续交付（保留现有成果）

以下内容不作为本轮完整网页版 R2 的前置条件，也不因平台顺序调整而标记为未开发。

| 交接件 | 现有状态与待办 | 位置 |
|---|---|---|
| Android App 源码与构建 | 已具备 0.3.0：账号登录、多页拍摄/排序、旋转裁切和 WorkManager 续跑；已有 debug 与 R8 未签名 release 的 CI 构建记录，原生真机待验收 | [apps/android](../apps/android/README.md)、`.github/workflows/android-checks.yml` |
| 签名安装包 | 待项目方落实签名密钥与分发。通过 `XUEBAN_KEYSTORE_PATH`、`XUEBAN_KEYSTORE_PASSWORD`、`XUEBAN_KEY_ALIAS`、`XUEBAN_KEY_PASSWORD` 注入，密钥不入仓库 | `apps/android/app/build.gradle.kts` |
| 安装升级与原生设备矩阵 | 真实相机、系统权限、弱网/系统终止恢复、覆盖升级和分发待后续 App 阶段验收 | [ANDROID_PHASE1](../ANDROID_PHASE1.md)、[DEVICE_MATRIX](runbooks/DEVICE_MATRIX.md) |
| 共用账号与数据连续性 | App 复用网页版验证后的后端、账号、档案和业务规则；界面复用程度由后续技术方案决定，需验收跨端历史与订正连续性 | [ACCOUNT_FOUNDATION](ACCOUNT_FOUNDATION.md)、[WEB_ROADMAP](WEB_ROADMAP.md) |

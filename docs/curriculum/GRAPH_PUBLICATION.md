# 具体教材的图谱发布与学生选择

现有学生未显式选书时，继续使用原年级/出版社范围；七、八年级数学原默认仍为北师大版。发布一份新人教书图不会改变这些学生。首个可选具体资源是人教七年级上册、ISBN `9787107382468`，固定到导入时的图谱版本；再次发布同书新版本也不会自动迁移已选学生。

审核状态 `source_verified_ai_reviewed` 表示官方书目/目录来源已核对，项目自编技能与关系已由另一 AI 审查。它不代表教师审核、官方逐节点图谱、教材全文核对或现场纸书印次确认。本轮图谱涵盖第 3、4 章及第 1、2 章部分相关技能，未建模范围不做掌握判断。完整范围、来源和边界保存在候选及审查文件中。

## 迁移与导入

以下命令从 `services/backend` 执行，使用项目虚拟环境。实际数据库连接由已有安全环境变量 `DATABASE_URL` 或项目配置传入，不放入命令文本、仓库或日志。

```bash
python -m alembic upgrade head
python -m scripts.curriculum_graph validate \
  --candidate xueban_backend/data/curriculum/candidates/pep-math-g7-up-9787107382468-candidate-20261021.json \
  --review xueban_backend/data/curriculum/candidates/pep-math-g7-up-9787107382468-candidate-20261021.audit.json \
  --version pep-math-g7-up-9787107382468-r2
```

`validate` 不连接数据库，也不发布。以下动作使用同一组选项：

```bash
python -m scripts.curriculum_graph import \
  --candidate xueban_backend/data/curriculum/candidates/pep-math-g7-up-9787107382468-candidate-20261021.json \
  --review xueban_backend/data/curriculum/candidates/pep-math-g7-up-9787107382468-candidate-20261021.audit.json \
  --version pep-math-g7-up-9787107382468-r2

python -m scripts.curriculum_graph publish \
  --candidate xueban_backend/data/curriculum/candidates/pep-math-g7-up-9787107382468-candidate-20261021.json \
  --review xueban_backend/data/curriculum/candidates/pep-math-g7-up-9787107382468-candidate-20261021.audit.json \
  --version pep-math-g7-up-9787107382468-r2
```

`import` 创建草稿，`publish` 将相同内容发布。可直接调用 `publish` 完成首次导入和发布。重复执行相同版本、候选及审查文件是幂等操作；同版本内容不同会拒绝，必须使用新版本。候选原文保持 `research_candidate_not_published`，数据库中的发布状态单独记录，避免把研究文档的属性伪装成已经上线。

本地 SQLite 测试库须显式附加 `--allow-sqlite`。生产数据库不使用这个选项。迁移 `0012_curriculum_binding` 只增加图谱元数据及关系类型，不修改学生档案或现有证据；若库中已有 `supports/specializes_to` 关系，降级会拒绝，避免默默改变关系含义。

## 来源与关系校验

导入要求独立审查 sidecar 的 `candidate_sha256` 与候选原字节完全一致，状态为 `source_verified_ai_reviewed`，有审查身份、时间、说明和 `review_kind=independent_ai_content_review`；`teacher_review`、`student_edition_confirmed` 必须为 false。校验还包括书目身份、源文件校验和、目录节引用、节点/边唯一性、关系端点与前置环路。

数据库保留完整候选快照、审查记录、两个文件摘要、来源列表和节点/关系原文。同一版本不会覆盖。此次共 29 个展示节点（25 个技能、4 个章节）和 37 条关系：35 条有条件的 `prerequisite_of`、1 条 `supports`、1 条 `specializes_to`。容器名 `prerequisite_edges` 不决定关系类型；支持与细化关系不会转成前置边，全部关系都不用于强制学习关卡，也不传播薄弱或掌握状态。

## 学生选择与兼容

- `GET /v1/students/{id}/curriculum-books` 返回 `profile_revision`、当前 `current_binding` 及当前年级可选 `books`。只有身份、来源与审查记录完整的已发布书图可选。
- `PUT` 同路径提交 `expected_revision` 与 `graph_version`；学生本人或获授权坐班老师可操作。`graph_version=null` 表示明确取消选择，恢复原默认范围；操作记录保留于档案版本历史中。切换年级前须先取消具体书的绑定。
- `GET /v1/curriculum/graph?student_id=...` 或 `?question_id=...` 返回该学生当前绑定图，校验学生权限。没有参数的旧接口仅返回旧默认图，不会意外返回新人教图。
- 知识地图返回真实 `graph_review_status`、`book_identity` 和 `source_note`。教材范围由具体 ISBN 和图谱版本确定，不写成 `publisher_latest`。无证据节点保持 `no_judgment`；重算时隐藏旧结论。
- 新作业保存所选书的出版社、册次和 `edition=isbn:9787107382468`。旧作业不会被后台自动改成新书。切图后旧关联、证据和审计记录保留；不匹配当前图版本的记录不参与当前地图。
- 选做题库创建、生成和列表接口支持 `graph_version` 或获授权的 `student_id`；两者同时提供时必须与学生当前绑定一致。旧客户端不提供两者时仍使用旧默认图。生成任务在排队时固定版本，执行时不会跟随之后的换书；题库、练习会话和回答记录都保留 `graph_version`，选题与数量统计不能只按同名 `knowledge_id` 跨图查找。
- 旧教材的错题仍可见，明确保留原始教材信息，但不能仅凭同名模型标签归入新教材或作为新书练习来源。历史预习详情使用会话开始时的图谱，之后换书不会改写原内容。首版只选择一本具体书，不宣称已经完成多科教材图谱。

迁移 `0013_practice_graph_binding` 为历史选做题库及会话补充版本字段。旧题目只在知识点 ID 对应唯一旧图时回填；无法证明所属图谱的记录保留 `null` 并退出新选题，不猜测版本。旧生成任务未记录版本时返回 `graph_version_missing`，需明确版本后重新排队。新任务始终携带固定版本。

## 验证

`tests/test_curriculum_binding.py` 覆盖独立审查摘要门禁、导入发布幂等、关系类型保真、旧学生不被切图、显式选书/清除、版本固定、过期写入拒绝、权限隔离、旧作业和旧证据留存，以及新作业拍照判断到正向 AI 知识证据的 HTTP/Worker 流程。测试使用临时数据库；不自动迁移本机运行库。

`tests/test_practice_graph_binding.py` 覆盖新书题目创建/生成、任务排队后换书、两图复用知识点 ID 的题库隔离、旧教材来源隔离、历史会话与回答版本保留、权限及保守迁移。

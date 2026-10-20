# 人教七上知识图谱候选包与出处

核验日期：2026-10-21；候选修订：第 2 版。核验方式：AI 代理读取官方公开元数据、逐页查看教材公开预览、查看课标 PDF 渲染页。第 1 版已由另一代理独立核对来源和数学内容，第 2 版按其 6 项修订意见更新，待定点复核。**这不是教师审核记录，也不是课程图谱发布记录。**

候选文件：[`pep-math-g7-up-9787107382468-candidate-20261021.json`](../../services/backend/xueban_backend/data/curriculum/candidates/pep-math-g7-up-9787107382468-candidate-20261021.json)。

本包可以供开发实现“教材目录预览 + 有出处的候选知识点”。没有修改运行中的教材配置、既有审核状态、API、题目知识点映射或学生状态。没有导入数据库，没有切换学生教材。原图纠错、同题订正、照片历史不应等待教材图谱完成。

## 1. 三种事实分开保存

| 层次 | 本次拿到的证据 | 可以表达 | 不可以表达 |
| --- | --- | --- | --- |
| 官方教材身份与目录 | 国家平台公开元数据、封面、扉页、目录预览 | 人教七上、2022 课标修订、封面审核年份、资源标识、主章节及页码 | 由出版社提供了我们的知识图谱、现场每个学生都用此版 |
| 项目整理的知识节点与关系 | 依据目录范围、课标要求和数学知识拆分的 AI 草案 | 候选技能、可观察目标、可能错误类型、有适用条件的前置关系 | 教师已审核、出版社认可、所有同年级学生都该掌握 |
| 现场教材与学生学习情况 | 本次没有取得现场教材确认或新增学生作答证据 | 等待确认；未观察到 | 自动认定教材版本、学过章节、熟练程度或学习习惯 |

数据中 `official_table_of_contents` 与 `proposed_pilot_scope` 独立。前者保留来源 URL 与对应目录页，后者明确 `content_origin=project_ai_authored`。所有关系 `official_relation=false`、`gates_learning=false`、`requires_whole_source_node_mastery=false`、`propagate_mastery_or_weakness=false`；人工审核人和审核时间为空。缺少来源技能的作答证据应记作“未观察到”，不能由目标题的结果反推前置技能已掌握或薄弱。

## 2. 已核实的教材资源身份

| 字段 | 核验结果 | 证据及限制 |
| --- | --- | --- |
| 出版社、年级、册次 | 人民教育出版社，数学，七年级上册 | 国家平台标签、封面及扉页一致 |
| 课标系列 | 根据 2022 年版课程标准修订 | 平台书名；出版社 2024 年新教材介绍 |
| 封面审核年份 | 2024 | 封面标示审核通过年份；不能改称“2024 年第 1 版” |
| ISBN | 9787107382468 | 平台 `ti_items` 的 source 原文件名；不是从现场纸书扫码得到 |
| 平台资源 ID | `540ac93d-67fc-4353-9e49-1ef20d02b5a4` | 公开元数据 |
| 本次资源版本 | `1787023107563` | 公开预览 URL 中的资源版本；平台更新时间为 `2026-08-18T12:09:37.305+0800` |
| 具体版次、印次 | 未核实 | 编著/版权预览未给出；保持 `null` |
| 学生实际使用版本 | 未核实 | 需现场封面、版权页或 ISBN 加目录匹配；不由平台“最新”自动决定 |

因此应使用明确的资源身份“人教七上，2024 审核、2022 课标修订，ISBN 9787107382468，2026-10-21 核对的资源快照”，不要笼统写成“2026 最新教材”。ISBN 和资源快照也不能单独证明每一次印刷的正文完全一致。

### 主要出处

- [国家中小学智慧教育平台教材目录版本入口](https://s-file-1.ykt.cbern.com.cn/zxx/ndrs/resources/tch_material/version/data_version.json)：本次返回 `module_version=1254188463`。仅表明此次公开目录快照。
- [该教材的国家平台公开详情](https://s-file-1.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/540ac93d-67fc-4353-9e49-1ef20d02b5a4.json)：书名、标签、更新时间、ISBN 所在 source 文件名及公开预览。
- [封面公开预览](https://r3-ndr.ykt.cbern.com.cn/edu_product/esp/assets/540ac93d-67fc-4353-9e49-1ef20d02b5a4.t/zh-CN/1787023107563/transcode/image/1.jpg)：确认 2024 审核标识。
- [编著/版权页公开预览](https://r3-ndr.ykt.cbern.com.cn/edu_product/esp/assets/540ac93d-67fc-4353-9e49-1ef20d02b5a4.t/zh-CN/1787023107563/transcode/image/3.jpg)：没有可据以填写具体版次、印次的文字。
- [人教版义务教育数学（七～九年级）新教材介绍](https://www.pep.com.cn/xw/zt/hd/12/xjcjs/cz/202408/t20240826_1994351.html)：确认系列的课标基础、结构修订背景，不能替代本册逐节目录证据。
- [义务教育数学课程标准（2022 年版）官方 PDF](https://www.pep.com.cn/xw/zt/rjwy/yjkb2022/202205/P020220512583134605579.pdf)：正文第 54–55 页，PDF 第 61–62 页，“初中部分／数与代数／第四学段（7～9 年级）／数与式”。已查看渲染页，核对有理数、代数表示、代入、整式及去括号和合并同类项的范围。课标覆盖整个学段，不把所有内容强行塞进七上。

每条来源的访问日期、支持的事实、不支持的结论和文件 SHA-256 均在候选 JSON 中。没有把教材图片、整本 PDF、受限下载链接或正文加入仓库。

## 3. 核验目录与首包范围

本包记录 6 章、15 个主节的名称和起始页；不声称包含所有栏目、数学活动、综合实践或练习。

| 章 | 目录章名 | 教材正文起始页 | 本包用途 |
| --- | --- | --- | --- |
| 1 | 有理数 | 1 | 必要前置知识 |
| 2 | 有理数的运算 | 24 | 必要前置知识 |
| 3 | 代数式 | 68 | 首包主范围 |
| 4 | 整式的加减 | 88 | 首包主范围 |
| 5 | 一元一次方程 | 110 | 只记录目录，后续扩展 |
| 6 | 几何图形初步 | 149 | 只记录目录，后续扩展 |

目录来源：[第 1–2 章](https://r3-ndr.ykt.cbern.com.cn/edu_product/esp/assets/540ac93d-67fc-4353-9e49-1ef20d02b5a4.t/zh-CN/1787023107563/transcode/image/5.jpg)、[第 3–5 章](https://r2-ndr.ykt.cbern.com.cn/edu_product/esp/assets/540ac93d-67fc-4353-9e49-1ef20d02b5a4.t/zh-CN/1787023107563/transcode/image/6.jpg)、[第 6 章](https://r3-ndr.ykt.cbern.com.cn/edu_product/esp/assets/540ac93d-67fc-4353-9e49-1ef20d02b5a4.t/zh-CN/1787023107563/transcode/image/7.jpg)。

首包主节为 `3.1 列代数式表示数量关系`（69 页）、`3.2 代数式的值`（79 页）、`4.1 整式`（89 页）、`4.2 整式的加法与减法`（95 页）。注意章标题“整式的加减”与 4.2 节标题不是同一字符串。

项目草案共 **25 个技能节点**：14 个主范围节点、10 个前置节点，以及 1 个同册常见题支点“科学记数法”。共有 **37 条项目关系**：35 条有条件的解题步骤依赖 `prerequisite_of`、1 条概念支持 `supports`、1 条范围细化 `specializes_to`。科学记数法只覆盖正的大数，不作为代数式学习的必经门槛。粒度围绕作业纠错展开，例如把“去括号”拆成正号括号、负号括号、带数值因数的分配；这些是项目为追踪错误位置设计的拆分，不是教材官方逐条节点。

`prerequisite_edges` 暂保留为候选文件的容器名称，**消费端必须按 `relation_type` 分流，不能把所有条目当作必需前置关系**。`relation_policy` 规定每种关系的含义；`applies_when`、`source_skill_slice`、`evidence_trigger` 限定实际观察的步骤。例如 `数的分配律 → 带数值因数的去括号` 可解释为什么漏乘某一项值得复看，但共同整体合并也是合法方法。学生在 `3(x-2)` 出错并不证明其整章、所有前置节点或学习习惯均薄弱，也不必为了点亮节点额外安排整套测验。

“识别同类项”的主归属改为 4.2，4.1 只存入 `related_official_section_ids` 表示单项式、项及指数的相关背景。主节名称和起始页已经核实；这项技能归属仍是项目建议，不声称已逐页核对正文中的教学位置。

## 4. 仓库现状与不能直接复用的地方

以下为 2026-10-21 本候选初稿准备时对工作树的只读核验，作为不能直接沿用旧种子的背景；同期其他代理正在实施的发布与绑定变更不由本候选文档验收：

1. `curriculum/official_catalog.json` 的七、八年级数学配置为 **北京师范大学出版社／北师大版**，`curriculum_catalog.py::_SPECS` 同样配置 BNUP。不能把本次人教候选直接挂到这些学生上。
2. `curriculum/edition_review.json` 现有两条地理旧版判定。它们不构成数学图谱审核，也不能复用为本候选的审核记录。
3. 现有种子文件实际位于 `services/backend/xueban_backend/data/curriculum/pep-math-junior-pilot-1.json`，根目录没有 `data/curriculum`。现种子有 111 个节点：1 个学科、32 个章、78 个概念；前置边为 76 条，仍为 `pilot_seed_unreviewed`。
4. 现种子第 4 章写为“整式的加法与减法”，与本次官方目录章名“整式的加减”不同；八、九年级范围也没有随本次一并核实。不能整包升级审核状态。
5. 现种子的 `stage=junior_middle_school`、`term=upper`、`textbook_revision=2024` 与当前目录匹配使用的 `junior_middle`、`up`、`publisher_latest` 存在语义/枚举差异。本候选不会用批量字符串替换假装完成迁移。
6. 当前 `graph_is_latest_reviewed` 仅认 `publisher_latest_reviewed`，`node_matches_scope` 要求当前学科/出版社和 `publisher_latest`；本候选既未导入，也不符合该发布门槛。未来需要明确的版次绑定与图谱版本选择，而不是把候选标志改成通过。
7. 迁移 `0006_learning_graph.py` 指向原种子的固定路径。本候选放在独立 `candidates/` 子目录，没有改迁移或导入流程。

候选节点中的 `legacy_node_candidates` 只供后续独立复核时查找可能对应项，`legacy_links_are_equivalent=false`。拆分、合并或改教材不能自动继承旧掌握状态。

## 5. 可直接实施且不阻塞学生体验的顺序

1. **先保留纠错证据。** 学生继续拍照、看结果、订正和查看历史。每条证据保留学生、照片、题目、题目版本、判定来源与时间；待核和服务故障不进入掌握/薄弱统计。
2. **展示有来源的目录候选。** 可以只读预览这本书的已核验目录与主范围，明确“候选知识结构”；不向学生显示伪造的教师审核标记。
3. **一次确认实际教材。** 校区或学员资料设置保存出版社、年级、册次、ISBN/封面及目录证据，记录确认人、时间和范围。学生上传作业不必每次确认，不因暂未确认教材而无法纠错；未确认时只展示题目层的 AI 知识点建议。
4. **由 AI 团队独立交叉核验并发布小范围图谱版本。** 将“来源目录核实”“AI 整理及交叉核验”“校区实际使用确认”分别记录，不要求先取得教师审核才能继续开发，也不把 AI 核验冒充教师审核。第 3–4 章核验通过后可以先发布该范围，未覆盖的其他章节继续积累题目证据，不能展示为 0 分或未掌握。此步骤需要配套独立发布状态、显式版次选择与发布实现，本包未改 API。
5. **逐条把作答映射到节点。** 映射需要题目版本和图谱版本；同一道题出现多个目标时区分主目标与前置依赖。主目标和学生作答都有证据才更新状态；待核不更新，老师主动核实与 AI 初判分别计数。
6. **用证据展示进步。** 首屏优先展示“本周观察到什么、哪次订正成功、还需确认什么”。节点详情可追到原图、错误位置、订正和再次作答；其他节点保持“尚未观察”。练习按需、可选，不为凑满图谱加压。

建议未来内部字段拆为 `catalog_evidence_status`、`knowledge_model_review_status`、`student_edition_binding_status`；这些名称是接口设计建议，本次没有增加 API 或更改现有字段。产品界面只显示学生能理解的状态，不把技术状态名堆给学生。

## 6. 独立核对结果与第 2 版修订

独立核对记录见 [`PEP_G7_UP_AUDIT_20261021.md`](PEP_G7_UP_AUDIT_20261021.md)。另一代理重开官方来源，核对教材身份、6 章 15 主节与页码；逐项复算第 1 版 **25 道项目自编核验题**，参考答案均相容，并提出 6 项必修和其他范围建议。该代理已看过原参考答案，因此记录为**独立内容复核，不是盲测**。节点 ID 的集合覆盖不代表每个技能已得到充分验证，也不能直接据此给学生点亮节点。

第 2 版保留原 25 道 `review_cases`，另新增 **22 道 `validation_cases` 边界核验样例**：前 14 道由独立审核反例整理，后 8 道补充方法、题干范围与正反对照。它们用于审查知识结构及后续回归，不是学生新增必做作业，不是出版社题库，也不作为人工金标。新样例及修订规则的状态为 `revision_2_pending_independent_recheck`。

| 审核项 | 第 2 版修订 | 对应边界样例 |
| --- | --- | --- |
| A1，负数代入 | 仅在遗漏必要括号改变含义时归因；省略冗余括号及正确心算不因此判错 | 05 正确的 `-2+3=1`；22 错误的 `a²→-2²` |
| A2，整式范围 | 明确非负整数指数、数值分母、π、系数 ±1、非零常数；零式次数不在本首包确定性判分范围 | 06、07、08、20 |
| A3，次数与题面项数 | 化简后按非零项判次数；题干明确问未化简题面项数时保留原结构 | 08、21 |
| A4，分类到数轴 | edge-01 改为 `supports`；缺少分类作答不使数轴定位失败 | 14 |
| A5，分配到整式加减 | edge-35 仅在实际采取逐项展开方法时适用；接受共同整体合并 | 10、11 |
| A6，化简后代入 | edge-37 仅在化简后仍含需赋值字母时适用；常数结果不判漏代入 | 12；若题干要求展示过程，可单独评价过程证据 |
| 其他建议 | 来源支持范围收窄；同类项主归属 4.2；edge-20 改 `specializes_to`；所有关系非关卡、不传播掌握/薄弱；明确具体来源子技能 | 01–04、09、13、15–19；全部关系约束 |

后续定点复核按候选文件的 SHA-256 锁定版本，逐项检查 A1–A6、其余规则、22 道边界样例和全部关系语义。记录代理身份、时间、检查范围、分歧和结论。可由当前 AI 团队继续完成，不要求教师审核或付费 API 才能推进；审查通过也不得自动确认任何学生的教材版本。

候选原始状态保持 `research_candidate_not_published`。可另建引用准确候选哈希的独立 AI 审查清单，使用 `source_verified_ai_reviewed` 表示来源和项目内容交叉核验通过，并由发布流程记录正式版本。此状态不等于“官方知识图谱”“教师审核”“学生教材已确认”或“真实识图准确率已验证”。

在仓库根目录执行以下离线检查，无网络、无数据库写入：

```bash
python3 - <<'PY'
import json
from collections import Counter, defaultdict, deque
from pathlib import Path
p = Path("services/backend/xueban_backend/data/curriculum/candidates/pep-math-g7-up-9787107382468-candidate-20261021.json")
d = json.loads(p.read_text())
assert d["candidate_revision"] == 2
graph = d["proposed_pilot_scope"]
nodes = {n["id"]: n for n in graph["knowledge_nodes"]}
assert len(nodes) == len(graph["knowledge_nodes"]) == 25
sections = {s["id"] for c in d["official_table_of_contents"]["chapters"] for s in c["sections"]}
sources = {s["id"] for s in d["sources"]}
assert len(sections) == 15
for n in nodes.values():
    assert set(n["official_section_ids"]) <= sections
    assert set(n.get("related_official_section_ids", [])) <= sections
    assert n["standard_source_id"] in sources and n["human_reviewer"] is None
like_terms = nodes["draft-pep7-identify-like-terms"]
assert like_terms["official_section_ids"] == ["pep-g7-up-4.2"]
assert like_terms["related_official_section_ids"] == ["pep-g7-up-4.1"]
edges = graph["prerequisite_edges"]
edge_ids = {e["id"] for e in edges}
assert len(edge_ids) == len(edges) == 37
assert Counter(e["relation_type"] for e in edges) == {
    "prerequisite_of": 35, "supports": 1, "specializes_to": 1,
}
for field in ("gates_learning", "requires_whole_source_node_mastery",
              "propagate_mastery_or_weakness"):
    assert graph["relation_policy"][field] is False
indegree = {n: 0 for n in nodes}
next_nodes = defaultdict(list)
pairs = set()
for e in edges:
    a, b = e["from_id"], e["to_id"]
    assert a in nodes and b in nodes and a != b and (a, b) not in pairs
    assert not e["official_relation"] and not e["propagate_mastery_or_weakness"]
    assert not e["gates_learning"] and not e["requires_whole_source_node_mastery"]
    assert e["applies_when"] and e["source_skill_slice"] and e["evidence_trigger"]
    pairs.add((a, b)); next_nodes[a].append(b); indegree[b] += 1
queue = deque(n for n, count in indegree.items() if count == 0)
seen = []
while queue:
    n = queue.popleft(); seen.append(n)
    for target in next_nodes[n]:
        indegree[target] -= 1
        if indegree[target] == 0:
            queue.append(target)
assert len(seen) == len(nodes) and len(pairs) == 37
covered = set().union(*(set(c["proposed_node_ids"]) for c in d["review_cases"]))
assert covered == set(nodes) and len(d["review_cases"]) == 25
cases = d["validation_cases"]
assert len({c["id"] for c in cases}) == len(cases) == 22
for c in cases:
    assert set(c["node_ids"]) <= set(nodes) and set(c["edge_ids"]) <= edge_ids
    assert c["reference_answer"] and c["expected_behavior"]
    assert not c["student_required_exercise"] and not c["is_human_gold"]
assert set().union(*(set(c["audit_request_ids"]) for c in cases)) == {
    "A1", "A2", "A3", "A4", "A5", "A6",
}
assert not d["auto_import"] and d["status"] == "research_candidate_not_published"
print("PASS: revision 2; 25 nodes, 37 non-gating acyclic relations "
      "(35 prerequisite / 1 supports / 1 specializes), 15 sections, "
      "25 reference cases + 22 boundary cases; no activation")
PY
```

这个脚本验证结构完整性，**不会证明知识内容正确或替代独立 AI 数学核对**。

## 7. 本次验证与保留事项

- 第 2 版候选 JSON 可解析；6 章、15 主节、25 节点、37 条关系；25 道原核验题覆盖所有节点 ID，新增 22 道边界样例覆盖 A1–A6。
- 节引用、来源引用、旧节点候选引用均存在；节点和关系 ID 唯一，无重复关系或自环；有向关系图无环。35 条有条件依赖、1 条支持、1 条范围细化均不作为学习关卡。
- 第 1 版官方来源和 25 道参考答案已完成独立 AI 内容复核；6 项结构修订和 22 道边界样例等待对第 2 版的独立定点复核。原参考答案审查不是盲测。
- 候选默认 `auto_import=false`，`status=research_candidate_not_published`；没有教师审核人、没有学生教材确认。
- 初稿生成前后核对过原 `official_catalog.json`、`edition_review.json`、`curriculum_catalog.py` 与种子文件的 SHA-256；本代理第 2 版仅修订候选 JSON 和本文档，没有编辑审核文档或生产代码。
- 没有运行新的付费模型请求。没有把官方可在线预览等同于教材全文入库、商用复制或向量化授权；本包只保存书目/目录事实、引用及项目原创的目标描述。
- 尚需完成：第 2 版独立定点复核、正式发布与学生教材匹配、真实题目映射验收。现场版次暂未确认不阻塞纠错或候选图谱开发；在身份确认前不将教材学习结论归给具体学生。**候选数据准备完成不等于学生知识图谱已上线。**

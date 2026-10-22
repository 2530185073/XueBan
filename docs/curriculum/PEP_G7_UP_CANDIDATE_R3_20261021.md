# 人教七上全主节知识结构候选 R3

日期：2026-10-21。作者：AI 代理 `/root/pr3_frontend_review`。本次新增一个独立候选文件，不改 R2、R2 审核记录、生产代码或数据库。

候选文件：[`pep-math-g7-up-9787107382468-candidate-r3-20261021.json`](../../services/backend/xueban_backend/data/curriculum/candidates/pep-math-g7-up-9787107382468-candidate-r3-20261021.json)。

SHA-256：`c7a7c8aacce97e74c95d2430bb8f33ce7ed37fcaf866ba6a04d8a8483a985f79`。

R3 为已核实的七上 6 章、15 主节提供了 **75 个可观察技能节点、71 条有条件关系、75 道参考核验题和 52 道边界核验样例**。这里的“全主节”表示每个官方主节均有实质技能，不表示全书每个例题、练习、栏目、题型或正文位置已经逐一核验。所有技能和关系都是项目 AI 整理，不能标为出版社官方知识图谱。

独立复核期间按主代理授权，仅将 `draft-pep7-angle-bisector` 与 `draft-pep7-complement-supplement` 的课标印刷页定位从 63 修正为 64；数学定义、关系及所有案例未改变。审核结论锁定上方修正后的哈希。

R3 候选保留生成时状态 `research_candidate_not_published`、`auto_import=false`、`publication_review.status=not_performed`。之后完成的[独立审核](PEP_G7_UP_AUDIT_R3_20261021.md)及候选旁的 `.audit.json` 对上方准确哈希记录 `source_verified_ai_reviewed`，发布 CLI 的只读校验已通过；本次没有导入或发布数据库。教师审核为 `false`、人工审核人为 `null`，学生实际教材未确认；这些状态不因目录或父版本通过审查而自动升级。没有付费模型调用，也没有向学生分配新增练习。

## 1. 版本继承与来源

R2 及其独立 AI 审核都保持原字节：

- R2 候选 SHA-256：`bb514e01492d067df83417f0cd410cf55ec6475ec5ce7b109ded9091c79efab4`。
- [R2 独立审核清单](../../services/backend/xueban_backend/data/curriculum/candidates/pep-math-g7-up-9787107382468-candidate-20261021.audit.json) 已给出 `source_verified_ai_reviewed`，范围为 25 节点、37 关系、25 参考题和 22 边界题。这不是教师审核；原参考答案可见，因此不是盲测。
- R3 保留 25 个节点的 ID、名称、数学目标和 R2 修订后的边界，不自动迁移任何学生状态、旧练习或掌握证据。新副本将这些节点的 `scope_role` 设为全册主范围，并标明继承版本；50 个新节点仍待独立审查。
- 新版本复用知识 ID 不代表练习、题目映射或个人证据可以只靠 ID 跨版本匹配。实现应同时核对版本和原作答证据；本次候选数据没有实施迁移。

教材身份沿用同一官方资源：人民教育出版社数学七年级上册、2022 课标修订系列、2024 审核标识、ISBN `9787107382468`、资源 ID `540ac93d-67fc-4353-9e49-1ef20d02b5a4`、资源快照 `1787023107563`。ISBN 来自官方源文件名，具体印次和现场学生纸书仍未确认。

以下来源支持不同层次的事实，不能互相替代：

| 来源 | 支持范围 | 保留限制 |
| --- | --- | --- |
| [国家平台教材详情](https://s-file-1.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/540ac93d-67fc-4353-9e49-1ef20d02b5a4.json)及已核封面/目录预览 | 教材资源身份、6 章 15 主节名称与起始页 | 不证明现场版本、完整正文或出版社节点关系 |
| [2022 数学课标官方 PDF](https://www.pep.com.cn/xw/zt/rjwy/yjkb2022/202205/P020220512583134605579.pdf)，印刷 54–55 页 / PDF 61–62 页 | R2 已核的有理数、代数表示、代入和整式范围 | 学段标准不等于七上逐节正文 |
| 同一 PDF，印刷 56 页 / PDF 63 页 | 本次查看渲染页，核对方程、解、等式性质、一元一次方程及实际合理性范围 | 不纳入同页后续的方程组、二次方程或不等式内容 |
| 同一 PDF，印刷 58–59 页 / PDF 65–66 页 | 本次查看渲染页，核对有理数运算、运算律、简单近似与代数表达的学业要求 | 近似数等细节的精确教材正文位置仍未核对 |
| 同一 PDF，印刷 63–64 页 / PDF 70–71 页 | 本次查看渲染页，核对点线面角、线段中点、距离、度分秒与角平分范围 | 不把后续相交平行线、三角形证明等内容加入七上 |
| [人教七上配套交互资源目录](https://www.pep.com.cn/xw/zt/xkzt/csazy/csa7s/) | 官方页面将“几何体及其展开图”归于 6.1，将圆柱侧面最短路线活动归于 6.2 | 页面未给本候选 ISBN；不独自证明相同印次或全部正文，未把资源活动变成学生必做任务 |

本次网页工具再次读取课标 PDF 返回 403，实际视觉核查使用此前成功获得、SHA-256 与 R2 核实值一致的官方 PDF 临时副本；该限制写入新增 source 的 `refetch_note`。没有把图片、PDF、出版社练习或原文段落加入仓库。知识技能及核验题均为项目原创描述。

## 2. 逐节覆盖

下表页码都是已经核实的**主节起始页**，不是每个细粒度技能在正文中的具体页码。节点归属是项目整理建议；同一技能可关联多个主节，因此各行数量之和大于 75 个唯一节点。

| 主节 | 正文起始页 | 技能数 | 技能范围 |
| --- | --- | --- | --- |
| 1.1 正数和负数 | 2 | 3 | 有理数符号与相反数；相反意义量的正负表示；基准量、零与实际量 |
| 1.2 有理数及其大小比较 | 7 | 5 | 有理数的分类；用数轴表示数；有理数符号与相反数；绝对值；有理数大小比较 |
| 2.1 有理数的加法与减法 | 25 | 4 | 有理数加减；有理数运算顺序；减去一个数转化为加其相反数；加法交换结合与合理组合 |
| 2.2 有理数的乘法与除法 | 38 | 7 | 有理数乘除；有理数运算顺序；数的分配律；倒数与零的边界；多因数乘积的符号与零因数；除法转乘倒数；分数与有限小数混合运算 |
| 2.3 有理数的乘方 | 51 | 4 | 乘方与负数底数；有理数运算顺序；科学记数法的数值表达；按指定数位取近似数 |
| 3.1 列代数式表示数量关系 | 69 | 5 | 字母表示数量；代数式书写；从数量关系列式；代数式所表示的数量关系；公式中的单位与数量含义 |
| 3.2 代数式的值 | 79 | 4 | 代数式代入求值；负数代入的括号；整式化简后求值；代入前检查式子是否有意义 |
| 4.1 整式 | 89 | 2 | 单项式的识别与系数次数；多项式的项与次数 |
| 4.2 整式的加法与减法 | 95 | 7 | 识别同类项；合并同类项；括号前为正号的去括号；括号前为负号的去括号；带数值因数的去括号；整式加减与化简；整式化简后求值 |
| 5.1 方程 | 111 | 5 | 从等式中识别方程；方程的解与代入检验；一元一次方程的识别范围；等式两边同加同减；等式两边同乘与非零同除 |
| 5.2 解一元一次方程 | 120 | 7 | 移项中的项与符号；解方程时去括号；方程中的同类项合并；非零系数化为1；数值分母方程去分母；含有限小数系数的方程；求解后检验原方程 |
| 5.3 实际问题与一元一次方程 | 133 | 7 | 应用题设未知数及范围；从数量关系建立一元一次方程；行程与工作速率的方程建模；折扣利润与百分比基准；分配与总量约束建模；几何数量的简单线性建模；方程解的实际合理性 |
| 6.1 几何图形 | 150 | 6 | 平面图形与立体图形的抽象；常见几何体的面与结构；点线面体的基本关系；从不同方向观察几何体；简单立体展开图与折叠对应；从题干和标记提取几何条件 |
| 6.2 直线、射线、线段 | 162 | 5 | 直线射线线段的区别与命名；两点确定一条直线；线段长短比较与和差；线段中点及长度关系；两点间距离与线段最短 |
| 6.3 角 | 170 | 8 | 角的顶点边与表示；用量角器读角度；角的大小类别与特殊角；角的度分秒换算；角的和差与位置关系；角平分线及半角关系；余角补角的和与适用范围；方位角的起始方向与偏向 |

第 5 章新增技能区分“理解方程及解”“合法等式变形”“具体求解步骤”“实际问题建模和验证”，不会把解题固定为唯一流程。第 6 章同时要求读取明确条件，避免模型把示意图看似相等、居中或垂直当作已知事实。

“从不同方向观察”“展开图”“方位”“余补角”的具体粒度仍为项目建议，部分有配套资源或学段范围支持，未声明本次完成了逐页正文审核。复杂三视图、投影定理、尺规证明、平行线判定、三角形证明均不在本候选目标中。

## 3. 关系和个人证据的使用边界

71 条关系中有 **54 条 `prerequisite_of`、11 条 `supports`、6 条 `specializes_to`**。容器仍名为 `prerequisite_edges`，消费端必须读取具体 `relation_type`，不能全当学习前置关卡。

每条关系都有 `applies_when`、`source_skill_slice` 和 `evidence_trigger`，并统一设置：

- `official_relation=false`：项目关系，不冒充官方关系。
- `gates_learning=false`：不限制学生进入作业、订正或查看知识点。
- `requires_whole_source_node_mastery=false`：只涉及实际使用的步骤，不能要求整节点全部先通过。
- `propagate_mastery_or_weakness=false`：目标题出错不自动使前置节点变薄弱。

例如：只有真正采取“乘倒数”的解法才从该步骤观察倒数能力；纯整数度的角计算不触发度分秒换算依赖；方程可先两边同除去括号外的系数，而不强制逐项展开。`specializes_to` 只组织具体情境，不要求先做一份通用建模试卷。

新增细分节点可能与 R2 宽节点描述同一作答。`evidence_policy.overlapping_skills` 明确：可以保留主目标、次目标或依赖角色，但同一学生、题目版本、判定事件不是多次独立练习，不能靠命中多个节点虚增次数或置信度。未观察的步骤继续保持“尚未观察”，待核和服务失败不计掌握或薄弱。

## 4. 参考题与边界样例

75 道 `review_cases` 对全部 75 个节点形成 ID 集合覆盖：其中 25 道沿用 R2 已审题，50 道为本次原创。52 道 `validation_cases` 包含 22 道 R2 已审边界与 30 道新边界。这些数量不代表充分验证了每个技能，更不能换算为真实识图准确率。

本次重点边界包括：

| 范围 | 必须接受或区分的情况 |
| --- | --- |
| 数与代数 | 零偏差不等于零总量；减法没有交换律；零没有倒数；精度表达与数值等价分开；含字母分母可为代数式但不是整式 |
| 等式和求解 | 同乘 0 保持等式成立但失去可逆约束；不能除以 0；`0x=0` 与 `0x=2` 需分别解释；非整数解不自动错误 |
| 解法多样性 | 先同除再求解、使用非最小公倍数去分母、间接设未知数都可能正确；不强制某种步骤顺序 |
| 应用建模 | 缺少相向/同时等条件不能自行补；比例基准会变化；负数解是否合理取决于变量含义；人数不能随意四舍五入凑解 |
| 立体与线段 | 一个视图不能唯一反推几何体；6 个方块不一定是正方体展开图；射线命名有方向；两点唯一性要求两点不同；线段和差需要点序 |
| 角与图像 | 边延长不改变角；0°、90°、180°不能塞入锐钝范围；度分秒是六十进制；角和需位置条件；互补不要求相邻；模糊图不提供精确角度 |
| 数据落地 | 同一作答的宽节点与细节点命中不得重复当独立证据；没有学生证据不能从完整图谱推断已学或掌握 |

第 1 版整理过程中自检修正了方位示例用词：北偏东 30°与东偏北 60°的两个偏转角**互余**，不称互补。当前文件中已修正；这不改变模型输出或学生数据。

## 5. 独立交叉核对清单

送审要求为：R3 须对准确 SHA-256 建立新的审核记录，不可复用 R2 已审状态来覆盖新增内容。现已完成，结果见[独立审核记录](PEP_G7_UP_AUDIT_R3_20261021.md)。原参考答案随候选可见，本轮记录为非严格盲审。

1. 核对书目与 15 主节是否完全延用已核事实，检查新增来源的支持范围没有夸大；不把资源更新日期当教材版次。
2. 逐项检查 50 个新增技能是否是可观察作答能力，是否存在过宽或超出七上范围的目标；确认同名 ID 的语义稳定。
3. 独立推导 50 道新参考题和 30 道新边界题；尤其检查等式乘除零、抵消后的方程类型、应用题隐含条件、图形外观与角的范围。
4. 检查所有新增关系的方向、类型和适用条件；确认没有任意的必过顺序、全节点依赖或自动传播。
5. 检查宽节点与细分节点同时命中时的证据角色，不能把新增粒度变成额外学习任务或重复掌握样本。
6. 输出节点/边/样例 ID、明确结论、理由及需修订项；通过后另建锁定本候选哈希的 AI 审核清单，保留教师审核与学生教材确认的真实状态。

## 6. 离线结构检查

在仓库根目录执行，不访问 API 或数据库：

```bash
python3 - <<'PY_CHECK'
import hashlib, json
from collections import Counter, defaultdict, deque
from pathlib import Path
base = Path('services/backend/xueban_backend/data/curriculum/candidates')
p = base / 'pep-math-g7-up-9787107382468-candidate-r3-20261021.json'
d = json.loads(p.read_text()); g = d['proposed_pilot_scope']
nodes = {n['id']: n for n in g['knowledge_nodes']}
sections = {s['id'] for c in d['official_table_of_contents']['chapters'] for s in c['sections']}
sources = {s['id'] for s in d['sources']}
assert d['candidate_revision'] == 3 and len(nodes) == len(g['knowledge_nodes']) == 75
assert len(d['official_table_of_contents']['chapters']) == 6 and len(sections) == 15
for n in nodes.values():
    assert set(n['official_section_ids']) <= sections
    assert set(n.get('related_official_section_ids', [])) <= sections
    assert n['standard_source_id'] in sources
    assert set(n.get('supporting_source_ids', [])) <= sources
    assert n['human_reviewer'] is None and n['observable_objective']
assert all(s['primary_skill_ids'] for s in d['coverage_manifest']['sections'])
edges = g['prerequisite_edges']; edge_ids = {e['id'] for e in edges}
assert len(edges) == len(edge_ids) == 71
assert Counter(e['relation_type'] for e in edges) == {'prerequisite_of':54, 'supports':11, 'specializes_to':6}
pairs = set(); next_nodes = defaultdict(list); indegree = dict.fromkeys(nodes, 0)
for e in edges:
    a,b = e['from_id'],e['to_id']
    assert a in nodes and b in nodes and a != b and (a,b) not in pairs
    assert all(e[k] is False for k in ('official_relation','gates_learning','requires_whole_source_node_mastery','propagate_mastery_or_weakness'))
    assert e['applies_when'] and e['source_skill_slice'] and e['evidence_trigger']
    pairs.add((a,b)); next_nodes[a].append(b); indegree[b] += 1
queue = deque(k for k,v in indegree.items() if v == 0); visited = []
while queue:
    a = queue.popleft(); visited.append(a)
    for b in next_nodes[a]:
        indegree[b] -= 1
        if indegree[b] == 0: queue.append(b)
assert len(visited) == len(nodes)
assert len(d['review_cases']) == 75 and len(d['validation_cases']) == 52
assert set().union(*(set(c['proposed_node_ids']) for c in d['review_cases'])) == set(nodes)
for group in ('review_cases','validation_cases'):
    assert len({c['id'] for c in d[group]}) == len(d[group])
for c in d['validation_cases']:
    assert set(c['node_ids']) <= set(nodes) and set(c['edge_ids']) <= edge_ids
    assert not c['student_required_exercise'] and not c['is_human_gold']
assert d['publication_review']['status'] == 'not_performed'
assert not d['teacher_review'] and not d['student_edition_confirmed'] and not d['auto_import']
old = base / 'pep-math-g7-up-9787107382468-candidate-20261021.json'
old_audit = old.with_suffix('.audit.json')
assert hashlib.sha256(old.read_bytes()).hexdigest() == d['extends_candidate']['candidate_sha256']
assert hashlib.sha256(old_audit.read_bytes()).hexdigest() == d['extends_candidate']['review_manifest_sha256']
print('PASS: 75 skills, 71 non-gating acyclic relations, 15 represented sections, 75 reference + 52 boundary cases; R2 and its audit unchanged; no activation')
PY_CHECK
```

此检查只证明数据结构和约束一致，不替代数学内容审查、真实照片识别验证或正式发布验收。学生仍应首先完成自己的作业与订正；图谱覆盖扩展不增加必做练习。

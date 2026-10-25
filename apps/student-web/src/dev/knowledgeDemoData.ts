import type { KnowledgeGraphEdge, KnowledgeGraphNode, LearningStatus } from '../core/learning'

/**
 * Independent, synthetic grade-seven data for a local frontend demonstration.
 * Publisher names are display labels, not verified textbook or curriculum mappings.
 * Statuses are illustrative, not assessments of the current student. Each recorded
 * topic has one fictional latest answer; no_judgment topics have no answer record.
 * Nothing here reads or writes student records, storage, a database, or the network.
 */
export interface DemoTopic {
  id: string
  name: string
  objective: string
  status: LearningStatus
  question: string
  answer: string
  correctAnswer: string
  steps: string[]
}

export const DEMO_SUBJECTS: { id: string; name: string; publisher: string; topics: DemoTopic[] }[] = [
  {
    id: 'demo_math', name: '数学', publisher: '北京师范大学出版社',
    topics: [
      {
        id: 'demo_math_signed_numbers', name: '正数与负数',
        objective: '能用正数、负数表示具有相反意义的量。', status: 'stable_for_now',
        question: '如果零上温度用正数表示，零下 3 ℃ 应记作多少摄氏度？',
        answer: '−3 ℃', correctAnswer: '−3 ℃',
        steps: ['零下温度低于 0 ℃，用负数表示。', '在 3 前加负号，得到 −3 ℃。'],
      },
      {
        id: 'demo_math_opposites', name: '相反数',
        objective: '能写出一个非零有理数的相反数，并知道 0 的相反数是 0。', status: 'consolidating',
        question: '−5 的相反数是多少？', answer: '5', correctAnswer: '5',
        steps: ['非零相反数只有符号不同。', '将 −5 的负号改为正号，得到 5。'],
      },
      {
        id: 'demo_math_absolute_value', name: '绝对值',
        objective: '能根据数轴上到原点的距离求一个数的绝对值。', status: 'needs_help_now',
        question: '|−4| 等于多少？', answer: '−4', correctAnswer: '4',
        steps: ['绝对值表示数轴上的点到原点的距离。', '−4 到原点的距离是 4，所以 |−4| = 4。'],
      },
      {
        id: 'demo_math_addition', name: '异号有理数相加',
        objective: '能比较绝对值，确定异号有理数相加所得的符号与数值。', status: 'no_judgment',
        question: '', answer: '', correctAnswer: '', steps: [],
      },
    ],
  },
  {
    id: 'demo_chinese', name: '语文', publisher: '人民教育出版社',
    topics: [
      {
        id: 'demo_chinese_narrative_elements', name: '记叙的要素',
        objective: '能从简短叙事中找出时间、地点和人物。', status: 'stable_for_now',
        question: '“周一下午，小林在教室里帮助同桌整理书本。”这句话的时间、地点和人物分别是什么？',
        answer: '时间是周一下午，地点是教室，人物是小林和同桌。',
        correctAnswer: '时间：周一下午；地点：教室；人物：小林和同桌。',
        steps: ['“周一下午”交代时间，“教室里”交代地点。', '参与这件事的人物是小林和同桌。'],
      },
      {
        id: 'demo_chinese_event_summary', name: '概括主要事件',
        objective: '能用“谁做了什么”的方式概括一件简单的事。', status: 'needs_help_now',
        question: '“放学后，小林发现同桌的书散落在地，便蹲下帮忙捡起并整理好。”请概括主要事件。',
        answer: '放学了。', correctAnswer: '小林帮助同桌捡起并整理散落的书。',
        steps: ['找出主要人物小林。', '提取“帮助同桌捡书、整理书”这一主要行为。', '将人物和行为连成一句话。'],
      },
      {
        id: 'demo_chinese_simile', name: '比喻',
        objective: '能在简单的比喻句中找出本体和喻体。', status: 'consolidating',
        question: '“弯弯的月亮像小船。”这句话把什么比作什么？',
        answer: '把月亮比作小船。', correctAnswer: '把弯弯的月亮比作小船。',
        steps: ['被描写的事物是月亮，即本体。', '用来作比的事物是小船，即喻体。'],
      },
      {
        id: 'demo_chinese_personification', name: '拟人',
        objective: '能识别把事物当作人来描写的简单句子。', status: 'no_judgment',
        question: '', answer: '', correctAnswer: '', steps: [],
      },
    ],
  },
  {
    id: 'demo_english', name: '英语', publisher: '人民教育出版社',
    topics: [
      {
        id: 'demo_english_be', name: 'be 动词的基本形式',
        objective: '能根据主语选择 am、is 或 are。', status: 'stable_for_now',
        question: '填入 am、is 或 are：I ___ a student.', answer: 'am', correctAnswer: 'am',
        steps: ['主语是 I。', '一般现在时中，I 与 am 搭配。'],
      },
      {
        id: 'demo_english_negative', name: 'be 动词的否定句',
        objective: '能在 be 动词后加 not 构成否定句。', status: 'needs_help_now',
        question: '把 She is a student. 改为否定句。',
        answer: 'She not is a student.', correctAnswer: 'She is not a student.',
        steps: ['找到 be 动词 is。', '在 is 后加 not，其余语序保持不变。'],
      },
      {
        id: 'demo_english_question', name: 'be 动词的一般疑问句',
        objective: '能把 be 动词移到主语前构成一般疑问句。', status: 'consolidating',
        question: '把 He is your brother. 改为一般疑问句。',
        answer: 'Is he your brother?', correctAnswer: 'Is he your brother?',
        steps: ['把 is 移到主语 he 前，句首字母大写。', '句末使用问号：Is he your brother?'],
      },
      {
        id: 'demo_english_short_answer', name: 'be 动词疑问句的简短回答',
        objective: '能用 Yes 或 No 配合代词和 be 动词回答简单的一般疑问句。', status: 'no_judgment',
        question: '', answer: '', correctAnswer: '', steps: [],
      },
    ],
  },
  {
    id: 'demo_history', name: '历史', publisher: '人民教育出版社',
    topics: [
      {
        id: 'demo_history_dynasties', name: '早期王朝的先后顺序',
        objective: '能按先后顺序排列夏、商、西周和东周。', status: 'stable_for_now',
        question: '请按建立时间的先后排列：东周、商、夏、西周。',
        answer: '夏、商、西周、东周。', correctAnswer: '夏 → 商 → 西周 → 东周。',
        steps: ['夏在商之前，商在西周之前。', '西周之后是东周。'],
      },
      {
        id: 'demo_history_enfeoffment', name: '西周分封制',
        objective: '能说出西周实行分封制是为了稳定统治、扩大统治范围。', status: 'consolidating',
        question: '西周实行分封制的主要目的是什么？',
        answer: '让周王更好地管理各地。', correctAnswer: '稳定周初的政治形势，巩固疆土、扩大统治范围。',
        steps: ['西周疆域较广，需要安排对各地的管理。', '通过分封诸侯管理地方，巩固周王朝的统治。'],
      },
      {
        id: 'demo_history_eastern_zhou', name: '东周的两个时期',
        objective: '能知道东周分为春秋和战国两个时期。', status: 'needs_help_now',
        question: '春秋和战国属于西周还是东周？', answer: '西周。', correctAnswer: '东周。',
        steps: ['周朝分为西周和东周。', '东周又分为春秋和战国两个时期。'],
      },
      {
        id: 'demo_history_oracle_bones', name: '商朝甲骨文',
        objective: '能知道甲骨文是刻写在龟甲和兽骨上的文字，商朝已有使用。', status: 'no_judgment',
        question: '', answer: '', correctAnswer: '', steps: [],
      },
    ],
  },
  {
    id: 'demo_civics', name: '道德与法治', publisher: '人民教育出版社',
    topics: [
      {
        id: 'demo_civics_emotions', name: '识别情绪',
        objective: '能根据生活情境识别喜、怒、哀、惧等基本情绪。', status: 'stable_for_now',
        question: '小明收到朋友的祝福，感到很开心。这主要属于喜、怒、哀、惧中的哪一种？',
        answer: '喜。', correctAnswer: '喜。',
        steps: ['抓住情境中的“开心”。', '开心、愉快属于喜的表现。'],
      },
      {
        id: 'demo_civics_regulation', name: '合理调节情绪',
        objective: '能在生气时选择有助于平静且不伤害他人的调节方式。', status: 'needs_help_now',
        question: '和同学发生争执后，哪种做法更合适：A. 先深呼吸，平静后沟通；B. 立即辱骂对方？',
        answer: 'B', correctAnswer: 'A',
        steps: ['先识别自己正在生气，暂停冲动的言行。', '深呼吸有助于平静，之后再理性沟通。'],
      },
      {
        id: 'demo_civics_respect', name: '尊重他人',
        objective: '能在同学交往中选择认真倾听、礼貌表达的行为。', status: 'consolidating',
        question: '同学发言时，你有不同意见，应怎样表达？',
        answer: '等他说完再说自己的想法。', correctAnswer: '认真听完，再礼貌地提出自己的不同意见和理由。',
        steps: ['先认真倾听，不随意打断。', '礼貌表达不同观点，并说明理由。'],
      },
      {
        id: 'demo_civics_online_safety', name: '网上交友的自我保护',
        objective: '能知道不随意向陌生网友透露住址、电话等个人信息。', status: 'no_judgment',
        question: '', answer: '', correctAnswer: '', steps: [],
      },
    ],
  },
  {
    id: 'demo_geography', name: '地理', publisher: '人民教育出版社',
    topics: [
      {
        id: 'demo_geography_coordinates', name: '经纬网定位',
        objective: '能根据经纬度判断一个地点位于赤道和本初子午线的哪一侧。', status: 'needs_help_now',
        question: '某点位于 30°N、120°E。它在赤道以北还是以南？在本初子午线以东还是以西？',
        answer: '赤道以南，本初子午线以西。', correctAnswer: '赤道以北，本初子午线以东。',
        steps: ['N 表示北纬，30°N 位于赤道以北。', 'E 表示东经，120°E 位于本初子午线以东。'],
      },
      {
        id: 'demo_geography_reference_lines', name: '经纬度的起点',
        objective: '能说出赤道是 0° 纬线，本初子午线是 0° 经线。', status: 'stable_for_now',
        question: '0° 纬线和 0° 经线分别叫什么？',
        answer: '赤道和本初子午线。', correctAnswer: '0° 纬线是赤道，0° 经线是本初子午线。',
        steps: ['纬度从赤道向南、向北计算。', '经度从本初子午线向东、向西计算。'],
      },
      {
        id: 'demo_geography_scale', name: '地图比例尺',
        objective: '能解释比例尺 1∶100000 的含义，并换算厘米与千米。', status: 'consolidating',
        question: '比例尺为 1∶100000 时，图上 1 厘米代表实地多少千米？',
        answer: '1 千米。', correctAnswer: '1 千米。',
        steps: ['图上 1 厘米代表实地 100000 厘米。', '100000 厘米 = 1000 米 = 1 千米。'],
      },
      {
        id: 'demo_geography_distance', name: '根据比例尺求实地距离',
        objective: '能根据图上距离和比例尺计算实地距离。', status: 'no_judgment',
        question: '', answer: '', correctAnswer: '', steps: [],
      },
    ],
  },
  {
    id: 'demo_biology', name: '生物', publisher: '江苏凤凰科学技术出版社',
    topics: [
      {
        id: 'demo_biology_cell_structure', name: '细胞的基本结构',
        objective: '能指出洋葱鳞片叶内表皮细胞与人口腔上皮细胞共有的细胞膜、细胞质和细胞核。', status: 'stable_for_now',
        question: '洋葱鳞片叶内表皮细胞和人口腔上皮细胞共有的三个基本结构是什么？',
        answer: '细胞膜、细胞质、细胞核。', correctAnswer: '细胞膜、细胞质、细胞核。',
        steps: ['这两类细胞都有细胞膜。', '细胞内部都有细胞质和细胞核。'],
      },
      {
        id: 'demo_biology_cell_wall', name: '动植物细胞的区别',
        objective: '能知道洋葱鳞片叶内表皮细胞有细胞壁，而人口腔上皮细胞没有细胞壁。', status: 'needs_help_now',
        question: '洋葱鳞片叶内表皮细胞和人口腔上皮细胞中，哪一种有细胞壁？',
        answer: '人口腔上皮细胞。', correctAnswer: '洋葱鳞片叶内表皮细胞有细胞壁，人口腔上皮细胞没有。',
        steps: ['洋葱鳞片叶内表皮细胞属于植物细胞，有细胞壁。', '人口腔上皮细胞属于动物细胞，没有细胞壁。'],
      },
      {
        id: 'demo_biology_magnification', name: '显微镜的放大倍数',
        objective: '能用目镜倍数乘物镜倍数求显微镜的总放大倍数。', status: 'consolidating',
        question: '目镜为 10 倍、物镜为 10 倍时，总放大倍数是多少？',
        answer: '100 倍。', correctAnswer: '100 倍。',
        steps: ['总放大倍数等于目镜倍数乘物镜倍数。', '10 × 10 = 100。'],
      },
      {
        id: 'demo_biology_slide', name: '制作植物细胞临时装片',
        objective: '能说出盖盖玻片时让一侧先接触水滴、再缓缓放平，以减少气泡。', status: 'no_judgment',
        question: '', answer: '', correctAnswer: '', steps: [],
      },
    ],
  },
]

export const DEMO_NODES: KnowledgeGraphNode[] = DEMO_SUBJECTS.flatMap((subject, subjectIndex) =>
  subject.topics.map((topic, topicIndex) => ({
    id: topic.id,
    node_type: 'skill',
    parent_id: null,
    name: topic.name,
    definition: '合成的七年级前端演示知识点，不代表真实学习记录或官方教材目录。',
    observable_objective: topic.objective,
    sort_order: subjectIndex * 4 + topicIndex,
    subject: subject.name,
    chapter_id: subject.id,
    chapter_name: subject.name,
    learning_status: topic.status,
    reason_codes: [],
    valid_evidence_count: topic.status === 'no_judgment' ? 0 : 1,
    model_evidence_count: 0,
    staff_verified_evidence_count: 0,
    assessment_source: 'none',
  })),
)

// Each selected pair illustrates a simple prerequisite within one subject.
// The graph deliberately makes no claim to reproduce an official curriculum.
const prerequisites: { from: string; to: string; rationale: string }[] = [
  { from: 'demo_math_signed_numbers', to: 'demo_math_opposites', rationale: '先区分正负号，再理解非零相反数只有符号不同。' },
  { from: 'demo_math_absolute_value', to: 'demo_math_addition', rationale: '异号有理数相加需要比较两个数的绝对值。' },
  { from: 'demo_chinese_narrative_elements', to: 'demo_chinese_event_summary', rationale: '先识别叙事中的人物等要素，再概括谁做了什么。' },
  { from: 'demo_english_be', to: 'demo_english_negative', rationale: '先正确选择 be 动词，再在其后添加 not。' },
  { from: 'demo_english_be', to: 'demo_english_question', rationale: '先识别陈述句中的 be 动词，再将其移到主语前。' },
  { from: 'demo_english_question', to: 'demo_english_short_answer', rationale: '先理解 be 动词一般疑问句，再练习相应的简短回答。' },
  { from: 'demo_history_dynasties', to: 'demo_history_eastern_zhou', rationale: '先区分西周与东周，再将春秋和战国归入东周。' },
  { from: 'demo_civics_emotions', to: 'demo_civics_regulation', rationale: '先识别当前情绪，再选择合适的调节方式。' },
  { from: 'demo_geography_reference_lines', to: 'demo_geography_coordinates', rationale: '先认识经纬度的起点，再根据南北纬、东西经判断相对位置。' },
  { from: 'demo_geography_scale', to: 'demo_geography_distance', rationale: '先理解比例尺及单位换算，再由图上距离求实地距离。' },
  { from: 'demo_biology_cell_structure', to: 'demo_biology_cell_wall', rationale: '先认识两类细胞共有的基本结构，再比较细胞壁的有无。' },
]

export const DEMO_EDGES: KnowledgeGraphEdge[] = prerequisites.map(({ from, to, rationale }) => ({
  id: `demo_edge_${from}_${to}`,
  from_id: from,
  to_id: to,
  relation_type: 'prerequisite_of',
  scope: '七年级同学科合成演示',
  rationale: `合成演示关系，非官方课程关系：${rationale}`,
}))

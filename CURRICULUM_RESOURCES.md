# 初中人教版资源核验与接入

核验日期：2026-10-16。用户已确定首发初中、人教版。以下按实际 GitHub 文件树、固定提交、元数据及许可文件核验，未批量下载教材 PDF，也未导入第三方教材、题目或图谱数据。

结论：找到了普通初中数学七至九年级上下六册 PDF 及多种索引，但尚不能把它们称作“当前学生在用、版次已核对、可直接用于生产的完整教材与答案库”。一期拍照纠错不等待教材收齐；真实照片中的题干、作答和过程是检查输入。

## 五个候选资源

| 资源与固定提交 | 实际内容 | 核验结果 | 本项目用途 |
|---|---|---|---|
| [TapXWorld/ChinaTextbook](https://github.com/TapXWorld/ChinaTextbook/tree/5a80345f2043ba6f8db8d7be9cf3db82725ff1f7/初中/数学/人教版-人民教育出版社) · `5a80345f2043ba6f8db8d7be9cf3db82725ff1f7` | 人教数学七八九上下六册 PDF；另有五四学制及部分考试资源 | 未发现 LICENSE；六册与下列标记为 2012 的目录逐一相同，不是已核验的现行新版及完整标准答案库 | 资源定位、旧版比对候选；暂不纳入产品分发 |
| [litongjava 数学教材库](https://github.com/litongjava/china_high_school_math_people_teach_a_version_pdf/tree/8707c238c8fad9f8e196cdde77d921a861ee24bc/初中人教教程-2012) · `8707c238c8fad9f8e196cdde77d921a861ee24bc` | 同样六册 PDF；[README](https://github.com/litongjava/china_high_school_math_people_teach_a_version_pdf/blob/8707c238c8fad9f8e196cdde77d921a861ee24bc/readme.md) 明确目录年份 2012 | [Apache-2.0](https://github.com/litongjava/china_high_school_math_people_teach_a_version_pdf/blob/8707c238c8fad9f8e196cdde77d921a861ee24bc/LICENSE) 是仓库许可声明，未见出版社授权再许可教材证据；未逐册对照版权页 | 用作旧版判别线索，不能据仓库许可推定教材可分发 |
| [tyuan529/NewCNTextbooks](https://github.com/tyuan529/NewCNTextbooks/blob/3403401df0574809d673898633287e073b29d147/data/textbooks.json) · `3403401df0574809d673898633287e073b29d147` | 六册元数据，无教材正文、答案或章节内容 | 六册 `edition_year` 全空，官方链接仅为总入口，下载为空，`copyright_status=do_not_archive`；未发现 LICENSE；有[内容权利说明](https://github.com/tyuan529/NewCNTextbooks/blob/3403401df0574809d673898633287e073b29d147/docs/copyright-and-takedown.md) | 借鉴资源登记字段；作者的 verified 标记不替代本项目核验 |
| [pengboyjak/textbook-library](https://github.com/pengboyjak/textbook-library/blob/46bcffbdd527a3edb2762bcaa242cd7871b91383/references/junior.md) · `46bcffbdd527a3edb2762bcaa242cd7871b91383` | JSON/CSV 索引、查询脚本，无教材正文；初中索引的数学区块为五四学制六至九年级八册 | 本次未在该初中索引中找到普通六三学制数学六册区块；部分标题标“根据2022年版课程标准修订”；[MIT 软件许可及内容声明](https://github.com/pengboyjak/textbook-library/blob/46bcffbdd527a3edb2762bcaa242cd7871b91383/LICENSE) 区分软件与教材权利 | 参考检索实现及官方详情页线索；导入前校验学制与册次 |
| [haolpku/K12-KGraph](https://github.com/haolpku/K12-KGraph/blob/865bc3556768224d929825d7c258a5e77ac1aab0/books.yaml) · `865bc3556768224d929825d7c258a5e77ac1aab0` | 配置列六册，但 source_pdf/source_md 全空；含七上 demo 图谱，完整集另链外站，本次未下载 | 数据 [CC BY-NC-SA 4.0](https://github.com/haolpku/K12-KGraph/blob/865bc3556768224d929825d7c258a5e77ac1aab0/LICENSE)，代码另 [MIT](https://github.com/haolpku/K12-KGraph/blob/865bc3556768224d929825d7c258a5e77ac1aab0/LICENSE-CODE)；无逐册版次证据 | 研究图谱结构与构建思路；当前不导入其非商业数据用于商业产品，图谱研究不阻塞一期 |

TapXWorld 与 litongjava 的六册 Git blob SHA 逐一相同；例如七上 `779270b934df6d196ea4f4624e8181dc467d518e`，七下 `ceed223bb0ed69ae2f8710be78b0759d7683a005`。相同文件与明确旧版目录的对应是版本风险证据，不等于已经读过每册版权页。仓库最近更新、星数或标题不能作为教材版次证据。

## 官方版本核对

[人教官方教材目录](https://www.pep.com.cn/rjyc/kcjc/gjkc/rjbjc/)可作为出版社目录入口，但本次页面核验未得到全部逐册版权页。[教育部教学用书目录](https://www.moe.gov.cn/srcsite/A26/s8001/202408/W020250418502592948423.pdf)的搜索可读内容区分人教普通初中数学七上至九下与五四学制六上至九下；直接获取 PDF 出现重定向问题，未声称逐页读过原文。两者均不能代替学生手中教材的版次核对。

必须分别保存课程标准版本、教材修订版、印次及学制。例如“根据2022年版课程标准修订”不等于“2022年出版”。同一知识点在不同教材的章节顺序可以不同，不能只用“人教版七上”作为唯一课程键。

## 六册资源登记起点

以下仅登记普通六三学制候选；若现场为五四学制，应另建六至九年级八册清单，不混入此表。

| 册次 | GitHub PDF 候选 | 当前学生版本已核验 | 生产内容使用条件已核验 | 当前可用于生产 |
|---|---|---|---|---|
| 七上 | 有，与标记为2012的文件相同 | 否 | 否 | 否 |
| 七下 | 有，与标记为2012的文件相同 | 否 | 否 | 否 |
| 八上 | 有，与标记为2012的文件相同 | 否 | 否 | 否 |
| 八下 | 有，与标记为2012的文件相同 | 否 | 否 | 否 |
| 九上 | 有，与标记为2012的文件相同 | 否 | 否 | 否 |
| 九下 | 有，与标记为2012的文件相同 | 否 | 否 | 否 |

版本证据、文件完整性、使用条件和教学内容正确性独立记录。找到了 PDF 不代表已获得答案；获得了教材答案也不能替代对学生等价解法、过程或拍摄缺失的核验。

## 接入任务与数据字段

资源 Agent 下一单：为实际首批学校建立资源登记，字段包括 `school_system`、`publisher`、`subject`、`grade`、`term`、`curriculum_standard`、`revision`、`print_edition`、`isbn`、`official_url`、`source_commit`、`file_fingerprint`、`version_evidence`、`rights_evidence`、`review_status`。未知值保留空值和原因，禁止猜测年份补齐。

确认版本后才创建“教材 → 章节 → 知识点”映射，并保留原始位置与审核版本。课程图谱结构可以复用，孩子的掌握与薄弱状态必须由其个人有效纠错证据生成。未经核验的第三方知识图谱仅作研究参考，不直接成为孩子的诊断结论。

首发数学之外的语文、英语、物理、化学等按实际年级与作业类型分别排期和验收；本次没有完成全科教材、题目及答案资源核验。接入前仍须核对来源、版次、内容范围和实际使用安排。

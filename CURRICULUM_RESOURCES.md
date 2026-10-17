# 教材资源：在用版本清单、GitHub 副本与官方现行版核对

核验日期：2026-10-17。范围按项目方提供的各年级出版社：只收主科，音乐、体育与健康、美术不收。初中“政治”即统编《道德与法治》，“历史”为《中国历史》。

结论：
- GitHub 上要的 46 册已全部下载，并逐个按 Git blob 哈希校验通过，合计约 1148 MB。PDF 只存在本机资料目录，不入库、不对外分发。
- 46 册里有 38 册**不是学生本学年在用的版本**。初一、初二各科和五、六年级上册，现行都是“根据2022年版课程标准修订”的新版（封面有“2024”标识）；而 GitHub 副本封面是“教育部审定 2011–2022”的旧版。
- 只有五、六年级下册的 8 册（语文、数学、英语、科学）与平台现行版同代，因为这几册官方也还没有出修订版。
- 因此章节和知识点映射以**国家中小学智慧教育平台现行版**为准，GitHub 旧版只作对照。现场仍以学生课本版权页上的 ISBN 和版次为最终依据。

## 在用版本清单（项目方提供）

| 年级 | 语文 | 数学 | 英语 | 道德与法治 | 历史 | 地理 | 生物学 | 物理 | 科学 |
|---|---|---|---|---|---|---|---|---|---|
| 初一 | 统编（人教社） | 北师大版 | 人教版 | 统编 | 统编 | 人教版 | 苏科版（江苏凤凰科技） | — | — |
| 初二 | 统编 | 北师大版 | 人教版 | 统编 | 统编 | 人教版 | 苏科版 | 苏科版 | — |
| 五年级 | 统编 | 人教版 | 陕旅版（三年级起点） | — | — | — | — | — | 教科版 |
| 六年级 | 统编 | 人教版 | 陕旅版（三年级起点） | — | — | — | — | — | 教科版 |

机器可读版本在 `curriculum/selection.json`，每册上、下两本，共 46 册。注意：现有课程图谱是人教数学，而初中数学实际用北师大版，只有五、六年级数学是人教版。

## 来源与权利

| 来源 | 取得内容 | 权利与使用 |
|---|---|---|
| [TapXWorld/ChinaTextbook](https://github.com/TapXWorld/ChinaTextbook/tree/5a80345f2043ba6f8db8d7be9cf3db82725ff1f7) · 固定提交 `5a80345f2043ba6f8db8d7be9cf3db82725ff1f7`，最后推送 2025-10-18 | 46 册 PDF。北师大数学每册拆成两段，下载后合并 | 仓库未声明许可，教材版权属出版社。只在本机做版本核对和目录整理 |
| 国家中小学智慧教育平台公开目录（`s-file-1.ykt.cbern.com.cn/zxx/ndrs/...`），即 [happycola233/tchMaterial-parser](https://github.com/happycola233/tchMaterial-parser) 读取的同一目录 | 现行版书名、平台 ID、源文件名里的 ISBN，以及每册前 10 页公开预览图（封面、版权页、目录） | 只读公开目录和公开预览。正式 PDF 需要登录平台下载，脚本不下载、不绕过登录 |

## 46 册核对结果

判定依据：本地副本封面上的审定年份（或版权页、前言落款年份），对比平台现行版是否为 2022 课标修订版。对封面读不出年份的两册地理下册，人工比对了目录，证据记在 `curriculum/edition_review.json`。

| 年级 | 册 | GitHub 副本 | 平台现行版 | 结论 |
|---|---|---|---|---|
| 初一 | 语文、数学、英语、道法、历史、地理、生物学 上下册（14 册） | 审定 2011–2016 旧版；英语为 2012 版 *Go for it!* | 2022 课标修订版（七上 2024 秋起，七下 2025 春起） | 全部过时 |
| 初二 | 语文、数学、英语、物理、道法、历史、地理、生物学 上下册（16 册） | 审定 2012–2017 旧版 | 2022 课标修订版（八上 2025 秋起，八下 2026 春起） | 全部过时 |
| 五、六年级 | 语文、数学、英语、科学 上册（8 册） | 审定 2013–2022 | 2022 课标修订版（2026 秋起） | 过时 |
| 五、六年级 | 语文、数学、英语、科学 下册（8 册） | 审定 2013–2022 | 平台现行仍为该版 | 同代可用 |

从官方预览版权页读到的部分现行版信息（OCR 结果，待人工复核）：

| 册 | ISBN | 版次 / 印次 |
|---|---|---|
| 北师大数学七上 | 978-7-303-29973-7 | 2024 年 7 月第 1 版，2026 年 7 月第 3 次印刷 |
| 北师大数学八上 | 978-7-303-30730-2 | 2025 年 7 月第 1 版，2026 年第 2 次印刷 |
| 苏科生物学七上 / 七下 | 978-7-5713-4495-5 / 978-7-5713-4911-0 | 2024 年 8 月 / 2024 年 12 月修订版 |
| 苏科生物学八上 / 八下 | 978-7-5713-5266-0 / 978-7-5713-5816-7 | 2025 年 7 月 / 2026 年 1 月修订版 |
| 苏科物理八上 / 八下 | 978-7-5713-4500-6 / 978-7-5713-4914-1 | 2024 年 8 月 / 2025 年 1 月修订版 |

其余各册的平台 ID、书名、ISBN（源文件名里有就记录）、版次和印次，都在 `curriculum/textbooks.json` 的 `current_official` 字段。统编和人教各册的平台预览版权页把版次、印次留空，现场需要拍学生课本版权页补齐。

## 目录数据

- `textbooks.json` 中每册 `current_official.edition.contents_ocr`：现行版目录页的 OCR 原文行，46 册都已取到。状态 `ocr_unreviewed`，用作“教材 → 章节 → 知识点”映射的起点，必须经人工校对后才能使用。
- `textbooks.json` 中每册 `toc`：从 GitHub 副本文字层解析出的目录。扫描本没有文字层，这一项为空。对 38 册旧版，它只反映旧目录，不能直接用于学生。

## 复现方法

```bash
python3 curriculum/fetch_textbooks.py --workers 4      # 下载 46 册到 ~/.local/share/xueban/textbooks（可用 XUEBAN_TEXTBOOK_DIR 改目录），逐段校验 blob SHA
python3 curriculum/official_catalog.py                 # 读平台公开目录 → curriculum/official_catalog.json
python3 -m venv ~/.local/share/xueban/toolchain/pdf-venv
~/.local/share/xueban/toolchain/pdf-venv/bin/pip install pymupdf pyobjc-framework-Vision pyobjc-framework-Quartz
~/.local/share/xueban/toolchain/pdf-venv/bin/python curriculum/extract_catalog.py   # 版权页、目录、封面 OCR、官方预览对比 → textbooks.json
```

下载脚本交替使用 `raw.githubusercontent.com`（支持断点续传）和 GitHub API 的 blob 接口，因为前者在本地网络下经常超时。OCR 使用 macOS Vision，只能在 macOS 上运行。

## 后续

1. 现场：每个年级、每个科目各拍一本学生课本的封面和版权页，核对 ISBN 与版次，结果填入 `edition_review.json`。
2. 现行版 PDF：由学校或项目方用平台账号登录下载，可使用 tchMaterial-parser 并填入 Access Token，放入本机资料目录后重跑 `extract_catalog.py`。
3. 以现行版目录建立“教材 → 章节 → 知识点”映射，优先做北师大初中数学和人教五、六年级数学，并保留原始页码与审核版本。

## 早先核验（2026-10-16，人教初中数学，仍有效）

ChinaTextbook 与 [litongjava](https://github.com/litongjava/china_high_school_math_people_teach_a_version_pdf/tree/8707c238c8fad9f8e196cdde77d921a861ee24bc/初中人教教程-2012) 的人教初中数学六册 blob SHA 逐一相同，后者明确标为 2012 目录。[NewCNTextbooks](https://github.com/tyuan529/NewCNTextbooks/blob/3403401df0574809d673898633287e073b29d147/data/textbooks.json) 和 [textbook-library](https://github.com/pengboyjak/textbook-library/blob/46bcffbdd527a3edb2762bcaa242cd7871b91383/references/junior.md) 只有索引，没有教材正文。[K12-KGraph](https://github.com/haolpku/K12-KGraph/blob/865bc3556768224d929825d7c258a5e77ac1aab0/books.yaml) 的数据许可是 CC BY-NC-SA 4.0，只用于研究图谱结构，不导入商业产品。

资源登记字段保持：`school_system`、`publisher`、`subject`、`grade`、`term`、`curriculum_standard`、`revision`、`print_edition`、`isbn`、`official_url`、`source_commit`、`file_fingerprint`、`version_evidence`、`rights_evidence`、`review_status`。未知值留空并写明原因，禁止猜测年份补齐。孩子的掌握与薄弱状态必须由本人有效纠错证据生成，第三方图谱不直接作为诊断结论。

"""Record which edition the national platform (basic.smartedu.cn) currently publishes for each selected volume.

Reads only the platform's public catalog JSON and keeps the URLs of its public preview pages (the first
pages of each book, shown on the site without login); it does not download PDFs (those need a platform login).
For every grade/subject/term in selection.json it picks the entry with the newest 年度 tag, skipping
五·四学制 and special-education editions, and writes curriculum/official_catalog.json with the title,
ISBN (from the source file name), size and update time. fetch_textbooks.py copies are then compared by ISBN.

    python3 curriculum/official_catalog.py
"""
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG = "https://s-file-1.ykt.cbern.com.cn/zxx/ndrs/resources/tch_material/version/data_version.json"
DETAILS = "https://s-file-1.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/{}.json"
GRADES = {5: "五年级", 6: "六年级", 7: "七年级", 8: "八年级"}
TERMS = {"上册": "up", "下册": "down"}
PREVIEW_PAGES = 10
UNIFIED = {"语文", "历史", "道德与法治"}
VERSIONS = {"北京师范大学出版社": "北师大版", "江苏凤凰科学技术出版社": "苏科版", "教育科学出版社": "教科版", "陕西旅游出版社": "陕旅版"}
SUBJECT_IDS = {"语文": "chinese", "数学": "math", "英语": "english", "道德与法治": "morality-law", "历史": "history",
               "地理": "geography", "生物学": "biology", "物理": "physics", "科学": "science"}


def get_json(url):
    """curl, because the local python.org build lacks CA certificates."""
    return json.loads(subprocess.run(["curl", "-fsS", "--retry", "3", "--connect-timeout", "30", url],
                                     capture_output=True, check=True).stdout)


def version_tag(subject, publisher):
    if publisher == "人民教育出版社":
        return "统编版" if subject in UNIFIED else "人教版"
    return VERSIONS[publisher]


def main():
    selection = json.loads((HERE / "selection.json").read_text())
    entries = [b for url in get_json(CATALOG)["urls"].split(",") for b in get_json(url)]
    books = []
    for klass in selection["classes"]:
        for subject in klass["subjects"]:
            version = version_tag(subject["subject"], subject["publisher"])
            for term_name, term in TERMS.items():
                candidates = []
                for entry in entries:
                    tags = {t["tag_dimension_id"]: t["tag_name"] for t in entry.get("tag_list") or []}
                    title = entry["global_title"]["zh-CN"]
                    names = {t["tag_name"] for t in entry.get("tag_list") or []}
                    if (tags.get("zxxnj"), tags.get("zxxxk"), tags.get("zxxbb"), tags.get("zxxcc")) != (GRADES[klass["grade"]], subject["subject"], version, term_name):
                        continue
                    if "五" in title and "四学制" in title or "盲校" in names or "聋校" in names or entry.get("status") != "ONLINE":
                        continue
                    year = max((int(m.group(1)) for n in names if (m := re.fullmatch(r"(\d{4})年度", n))), default=0)
                    candidates.append((year, entry.get("update_time", ""), entry))
                if not candidates:
                    raise SystemExit(f"No official entry for {klass['label']} {subject['subject']} {version} {term_name}")
                year, _, entry = max(candidates, key=lambda c: (c[0], c[1]))
                details = get_json(DETAILS.format(entry["id"]))
                source = next((i for i in details.get("ti_items", []) if i.get("ti_file_flag") == "source"), {})
                file_name = ((source.get("ti_storages") or [source.get("ti_storage") or ""])[0]).rsplit("/", 1)[-1]
                isbn = re.match(r"(97[89]\d{10})_", file_name)
                title = entry["global_title"]["zh-CN"]
                preview = (entry.get("custom_properties") or {}).get("preview") or {}
                preview_pages = [preview[k] for k in sorted(preview, key=lambda k: int(re.sub(r"\D", "", k) or 0))][:PREVIEW_PAGES]
                books.append({"id": f"g{klass['grade']}-{SUBJECT_IDS[subject['subject']]}-{term}", "platform_id": entry["id"],
                              "title": title, "version": version, "catalog_year": year or None,
                              "revised_2022_standard": "2022年版课程标准修订" in title,
                              "isbn": isbn.group(1) if isbn else None, "bytes": source.get("ti_size"),
                              "updated_at": entry.get("update_time"), "alternatives": len(candidates) - 1,
                              "preview_pages": preview_pages})
                print(f"{books[-1]['id']:<22} {'新' if books[-1]['revised_2022_standard'] else '旧'} {year or '-'} "
                      f"ISBN {books[-1]['isbn'] or '-'} {title}", flush=True)
    out = {"schema_version": 1, "source": "国家中小学智慧教育平台 公开目录 (basic.smartedu.cn)", "catalog_url": CATALOG,
           "note": "只读取公开目录与详情 JSON；PDF 需登录平台下载，本脚本不下载。", "books": books}
    (HERE / "official_catalog.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote curriculum/official_catalog.json ({len(books)} volumes)")


if __name__ == "__main__":
    main()

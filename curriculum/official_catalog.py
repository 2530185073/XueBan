"""Record the latest matching publisher editions in the public national catalog.

Reads public catalog metadata and preview URLs only. Local textbook copies are
not used to decide which edition is current. Unmatched publishers and missing
subjects are recorded explicitly rather than silently replaced by another
publisher's edition.

    python3 curriculum/official_catalog.py
"""
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG = "https://s-file-1.ykt.cbern.com.cn/zxx/ndrs/resources/tch_material/version/data_version.json"
DETAILS = "https://s-file-1.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/{}.json"
GRADES = {5: "五年级", 6: "六年级", 7: "七年级", 8: "八年级"}
TERMS = {"上册": "up", "下册": "down", "全一册": "full"}
PREVIEW_PAGES = 10
UNIFIED = {"语文", "历史", "道德与法治"}
VERSIONS = {"北京师范大学出版社": "北师大版", "江苏凤凰科学技术出版社": "苏科版",
            "教育科学出版社": "教科版", "陕西旅游出版社": "陕旅版",
            "人民音乐出版社": "人音版", "人民美术出版社": "人美版"}
SUBJECT_IDS = {"语文": "chinese", "数学": "math", "英语": "english", "道德与法治": "morality-law", "政治": "morality-law", "历史": "history",
               "地理": "geography", "生物学": "biology", "物理": "physics", "科学": "science",
               "体育与健康": "physical-education", "艺术音乐": "music", "音乐": "music", "美术": "art"}
CANONICAL_SUBJECT = {"政治": "道德与法治", "生物学": "生物"}
PLATFORM_SUBJECT = {"艺术音乐": "艺术·音乐", "音乐": "艺术·音乐", "美术": "艺术·美术"}


def get_json(url):
    """curl, because the local python.org build lacks CA certificates."""
    return json.loads(subprocess.run(["curl", "-fsS", "--retry", "5", "--retry-all-errors",
                                      "--retry-delay", "1", "--connect-timeout", "20", "--max-time", "45", url],
                                     capture_output=True, check=True).stdout)


def version_tag(subject, publisher):
    if publisher == "人民教育出版社":
        return "统编版" if subject in UNIFIED else "人教版"
    return VERSIONS.get(publisher)


def tags_for(entry):
    return {tag["tag_dimension_id"]: tag["tag_name"] for tag in entry.get("tag_list") or []}


def ordinary_edition(entry):
    names = {tag["tag_name"] for tag in entry.get("tag_list") or []}
    title = entry.get("global_title", {}).get("zh-CN", "")
    return (entry.get("status") == "ONLINE" and not ("五" in title and "四学制" in title)
            and not names.intersection({"盲校", "聋校"}) and "盲校" not in title and "聋校" not in title)


def annual_version(entry):
    years = [int(match.group(1)) for name in tags_for(entry).values()
             if (match := re.fullmatch(r"(\d{4})年度", name))]
    return max(years, default=0)


def version_matches(actual, expected):
    return actual == expected or bool(expected and actual and actual.startswith(expected + "（"))


def main():
    selection = json.loads((HERE / "selection.json").read_text())
    entries = [b for url in get_json(CATALOG)["urls"].split(",") for b in get_json(url)]
    books = []
    requested_scopes = []
    for klass in selection["classes"]:
        for subject in klass["subjects"]:
            version = version_tag(subject["subject"], subject["publisher"])
            subject_id = SUBJECT_IDS.get(subject["subject"])
            expected_terms = ("full",) if subject["subject"] == "体育与健康" else ("up", "down")
            candidates_by_term = {term: [] for term in TERMS.values()}
            matching_by_term = {term: [] for term in TERMS.values()}
            for entry in entries:
                tags = tags_for(entry)
                term = next((value for name, value in TERMS.items() if tags.get("zxxcc") == name), None)
                if (term is None or tags.get("zxxnj") != GRADES[klass["grade"]]
                        or tags.get("zxxxk") != PLATFORM_SUBJECT.get(subject["subject"], subject["subject"])
                        or not ordinary_edition(entry)):
                    continue
                candidates_by_term[term].append(entry)
                if subject_id and version_matches(tags.get("zxxbb"), version):
                    matching_by_term[term].append(entry)

            scope_books = []
            for term_name, term in TERMS.items():
                if term not in expected_terms:
                    continue
                candidates = matching_by_term[term]
                if not candidates:
                    continue
                entry = max(candidates, key=lambda item: (annual_version(item), item.get("update_time", "")))
                try:
                    details = get_json(DETAILS.format(entry["id"]))
                except (subprocess.CalledProcessError, json.JSONDecodeError):
                    details = {}
                source = next((i for i in details.get("ti_items", []) if i.get("ti_file_flag") == "source"), {})
                file_name = ((source.get("ti_storages") or [source.get("ti_storage") or ""])[0]).rsplit("/", 1)[-1]
                isbn = re.match(r"(97[89]\d{10})_", file_name)
                title = entry["global_title"]["zh-CN"]
                preview = (entry.get("custom_properties") or {}).get("preview") or {}
                preview_pages = [preview[k] for k in sorted(preview, key=lambda k: int(re.sub(r"\D", "", k) or 0))][:PREVIEW_PAGES]
                book = {"id": f"g{klass['grade']}-{subject_id}-{term}", "platform_id": entry["id"],
                              "grade": klass["grade"], "subject": CANONICAL_SUBJECT.get(subject["subject"], subject["subject"]),
                              "publisher": subject["publisher"], "term": term,
                              "title": title, "version": version, "catalog_year": annual_version(entry) or None,
                              "revised_2022_standard": "2022年版课程标准修订" in title,
                              "isbn": isbn.group(1) if isbn else None, "bytes": source.get("ti_size"),
                              "updated_at": entry.get("update_time"), "alternatives": len(candidates) - 1,
                              "preview_pages": preview_pages}
                books.append(book)
                scope_books.append(book)
                print(f"{book['id']:<22} {book['catalog_year'] or '-'} ISBN {book['isbn'] or '-'} {title}", flush=True)
            candidate_versions = sorted({tags_for(entry).get("zxxbb", "") for term in expected_terms
                                         for entry in candidates_by_term[term]
                                         if tags_for(entry).get("zxxbb")})
            status = ("available" if {book["term"] for book in scope_books} == set(expected_terms) else "incomplete" if scope_books else
                      "publisher_mismatch" if candidate_versions else "unavailable")
            requested_scopes.append({"grade": klass["grade"],
                                     "subject": CANONICAL_SUBJECT.get(subject["subject"], subject["subject"]),
                                     "publisher": subject["publisher"], "version_tag": version,
                                     "status": status, "candidate_versions": candidate_versions,
                                     "book_ids": [book["id"] for book in scope_books]})
            if status != "available":
                print(f"{klass['label']} {subject['subject']} / {subject['publisher']}: {status}; "
                      f"candidate versions={candidate_versions or '-'}", flush=True)
    out = {"schema_version": 2, "source": "国家中小学智慧教育平台 公开目录 (basic.smartedu.cn)", "catalog_url": CATALOG,
           "verified_at": datetime.now(timezone.utc).isoformat(),
           "note": "只读取公开目录与详情 JSON；PDF 需登录平台下载。目录中不存在同出版社版本时明确标为未匹配，不使用旧版或其他出版社替代。",
           "requested_scopes": requested_scopes, "books": books}
    (HERE / "official_catalog.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote curriculum/official_catalog.json ({len(books)} volumes)")


if __name__ == "__main__":
    main()

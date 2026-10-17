"""Read the copyright page and table of contents of every fetched textbook into curriculum/textbooks.json.

Needs PyMuPDF, plus pyobjc Vision for OCR of scanned pages and covers (kept out of the backend's dependencies):
    python3 -m venv ~/.local/share/xueban/toolchain/pdf-venv
    ~/.local/share/xueban/toolchain/pdf-venv/bin/pip install pymupdf pyobjc-framework-Vision pyobjc-framework-Quartz
    ~/.local/share/xueban/toolchain/pdf-venv/bin/python curriculum/extract_catalog.py

Only facts are copied: edition and print dates, ISBN, editors, and chapter/lesson titles with page
numbers. Body text stays in the local PDF. Each copy is also compared with the edition the national platform
publishes now (official_catalog.json): the cover year of the local copy against the official preview cover.
"""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
LIBRARY = Path(os.environ.get("XUEBAN_TEXTBOOK_DIR", Path.home() / ".local/share/xueban/textbooks"))
# The 2022 compulsory-education curriculum standards reached classrooms from autumn 2024 onwards.
NEW_CURRICULUM_YEAR = 2024
LOCAL_PAGES = 12
HEADING = re.compile(r"^(第[一二三四五六七八九十百\d]+[单元章节课部分]|Unit\s*\d+|Module\s*\d+|Starter|Lesson\s*\d+|附录|后记|综合|名著导读|课外古诗词)", re.I)
TRAILING_PAGE = re.compile(r"^(.*?)[\s.…·]*?(\d{1,3})$")


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def copyright_page(doc):
    candidates = list(range(min(10, doc.page_count))) + list(range(max(0, doc.page_count - 4), doc.page_count))
    for index in candidates:
        text = squash(doc[index].get_text())
        if ("版次" in text or "印次" in text or "Copyright©" in text) and ("ISBN" in text or "书号" in text or "出版" in text):
            return index, text
    return None, ""


def edition_facts(text: str) -> dict:
    editions = [{"year": int(y), "month": int(m), "number": int(n)} for y, m, n in re.findall(r"(\d{4})年(\d{1,2})月第(\d+)版", text)]
    prints = [{"year": int(y), "month": int(m), "number": int(n)} for y, m, n in re.findall(r"(\d{4})年(\d{1,2})月第(\d+)次印刷", text)]
    isbn = re.search(r"ISBN([0-9][0-9\-]{9,16}[0-9Xx])", text)
    chief = re.search(r"总主编[：:]?(.{2,24}?)(?:主编|编写|责任|副主编|执行|$)", text)
    editors = re.search(r"(?<!总)主编[：:]?(.{2,40}?)(?:副主编|编写|责任|核心|本册|$)", text)
    latest = max(editions, key=lambda e: (e["year"], e["month"]), default=None)
    copyright_year = re.search(r"Copyright©(\d{4})", text)
    return {"editions": editions, "latest_edition": latest,
            "copyright_year": int(copyright_year.group(1)) if copyright_year else None, "printing": prints[-1] if prints else None,
            "print_date_blank": "印次年月第次印刷" in text, "isbn": isbn.group(1) if isbn else None,
            "chief_editor": chief.group(1).strip("，,、 ") if chief else None,
            "editors": editors.group(1).strip("，,、 ") if editors else None,
            "curriculum_2022_revision": "2022年版" in text or "根据2022" in text}


def version_status(facts: dict) -> str:
    year = facts["latest_edition"]["year"] if facts.get("latest_edition") else facts.get("copyright_year")
    if year is None:
        return "edition_unreadable"
    return "post_2022_standard" if year >= NEW_CURRICULUM_YEAR else "pre_2022_standard"


def raw_lines(page):
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = re.sub(r"\s+", " ", "".join(span["text"] for span in line["spans"])).strip()
            if text:
                x0, y0, x1, y1 = line["bbox"]
                yield (y0 + y1) / 2, x0, x1, text


def page_rows(page):
    """Rows of a contents page: same-height fragments joined left to right, page numbers attached to the nearest row."""
    width = page.rect.width
    numbers, rows = [], []
    for y, x0, x1, text in raw_lines(page):
        if re.fullmatch(r"\d{1,3}", text) and x0 > width * 0.55:
            numbers.append((y, int(text)))
            continue
        row = next((r for r in rows if abs(r["y"] - y) < 4), None)
        if row is None:
            rows.append({"y": y, "parts": [(x0, text)], "page": None})
        else:
            row["parts"].append((x0, text))
    rows.sort(key=lambda r: r["y"])
    for y, number in numbers:
        nearest = min(rows, key=lambda r: abs(r["y"] - y), default=None)
        if nearest is not None and abs(nearest["y"] - y) < 10 and nearest["page"] is None:
            nearest["page"] = number
    return rows


def numbered_lines(page):
    return sum(1 for row in page_rows(page) if row["page"] is not None)


CIRCLED = {chr(c): str(i + 1) for i, c in enumerate(range(0x278A, 0x2794))} | {chr(c): str(i + 1) for i, c in enumerate(range(0x2776, 0x2780))}
SCOPE_ITEM = re.compile(r"([\u2776-\u277F\u278A-\u2793]|\b[1-9]\b|\b1[0-9]\b)\s*([A-Z][^➊-➓❶-❿]{1,80}?)\s+Page\s+(S?\d{1,3})")


def scope_entries(doc):
    """English books list units in a scope-and-sequence table ("➋ This is my sister. Page 7")."""
    pages, entries = [], []
    for index in range(min(14, doc.page_count)):
        text = " ".join(doc[index].get_text().split()).replace("ff ", "ff").replace("Th ", "Th").replace("ft ", "ft")
        found = SCOPE_ITEM.findall(text)
        if len(found) >= 2:
            pages.append(index)
            heading = "Starter Units" if "Starter Units" in text[:80] else "Units"
            entries.append({"title": heading, "page": None, "heading": True})
            for number, title, page in found:
                entries.append({"title": f"{CIRCLED.get(number, number)} {title.strip()}", "page": page if page.startswith("S") else int(page), "heading": False})
    return [index + 1 for index in pages], entries


def toc_entries(doc):
    """The contents run: the first pages (within 14) that say 目录 or are dense with page-numbered rows."""
    scope_pages, scope = scope_entries(doc)
    if len(scope) >= 4:
        return scope_pages, scope
    pages, entries = [], []
    for index in range(min(14, doc.page_count)):
        page = doc[index]
        if "目录" in squash(page.get_text()[:400]) or numbered_lines(page) >= 6:
            if pages and index != pages[-1] + 1:
                break
            pages.append(index)
        elif pages:
            break
    for index in pages:
        for row in page_rows(doc[index]):
            parts = [text for _, text in sorted(row["parts"])]
            joined = squash("".join(parts))
            if joined in ("统编版",) or ("目录" in joined and len(joined) <= 6) or re.fullmatch(r"[IVX]+|[○●·]+", joined):
                continue
            headings = [p for p in parts if HEADING.match(p.replace(" ", "")) and len(p) <= 12]
            for heading in headings:
                entries.append({"title": heading, "page": None, "heading": True})
            rest = [p for p in parts if p not in headings and p not in ("阅读",)]
            title = re.sub(r"\s+", " ", " ".join(rest)).replace("\t", " ").strip(" .…·○●")
            if not title:
                if headings and row["page"] is not None:
                    entries[-1]["page"] = row["page"]
                continue
            if title.startswith("——") and entries:
                entries[-1]["title"] += title
                entries[-1]["page"] = entries[-1]["page"] or row["page"]
            elif headings and len(headings) == len(parts) - len(rest) and entries and entries[-1]["heading"] and row["page"] is None and not re.match(r"^\d", title):
                entries[-1]["title"] += " " + title
            else:
                entries.append({"title": title, "page": row["page"], "heading": False})
    return [index + 1 for index in pages], entries


def ocr_image(path) -> str:
    """macOS Vision OCR (pyobjc-framework-Vision); returns "" where it is unavailable."""
    try:
        import Quartz
        import Vision
        from Foundation import NSURL
    except ImportError:
        return ""
    source = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(path)), None)
    image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["zh-Hans", "en-US"])
    request.setUsesLanguageCorrection_(False)
    Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None).performRequests_error_([request], None)
    return "\n".join(r.topCandidates_(1)[0].string() for r in request.results() or [])


def page_text(doc, index, scratch: Path) -> str:
    """Text layer, or OCR of the rendered page for scanned books."""
    text = doc[index].get_text()
    if len(squash(text)) >= 20:
        return text
    image = scratch / f"{index}.png"
    doc[index].get_pixmap(dpi=110).save(image)
    return ocr_image(image)


def official_pages(current) -> list[str]:
    """OCR text of the platform's public preview pages, cached next to the local library."""
    cache = LIBRARY / "official-preview" / current["platform_id"]
    cache.mkdir(parents=True, exist_ok=True)
    texts = []
    for number, url in enumerate(current.get("preview_pages", []), 1):
        image, text_file = cache / f"{number}.jpg", cache / f"{number}.txt"
        if not text_file.exists():
            if not image.exists():
                fetched = subprocess.run(["curl", "-fsS", "--retry", "5", "--retry-all-errors", "--connect-timeout", "30",
                                          "-o", str(image), url], capture_output=True)
                if fetched.returncode != 0:
                    image.unlink(missing_ok=True)
                    texts.append("")
                    continue
            text_file.write_text(ocr_image(image))
        texts.append(text_file.read_text())
    return texts


def cover_year(text: str) -> int | None:
    """The approval/edition year printed on a cover: "教育部审定2016" on older printings, a "2024" badge on the
    revised editions. OCR sometimes splits the stamp, so the first plausible year on the cover counts."""
    flat = squash(text)
    stamp = re.search(r"审定(20[012]\d)", flat) or re.search(r"(20[12]\d)", flat)
    return int(stamp.group(1)) if stamp else None


def front_matter_year(doc, facts, scratch: Path) -> int | None:
    """Fallback when the cover carries no year: the latest edition or copyright year, else a preface signed
    "编者 2012年9月" in the first pages."""
    if facts.get("latest_edition"):
        return facts["latest_edition"]["year"]
    if facts.get("copyright_year"):
        return facts["copyright_year"]
    for index in range(min(6, doc.page_count)):
        signed = re.search(r"编[者写](?:组)?(20[012]\d)年\d{1,2}月", squash(page_text(doc, index, scratch)))
        if signed:
            return int(signed.group(1))
    return None


def is_contents(text: str) -> bool:
    flat = squash(text)
    return bool(re.search(r"[目日][录泉錄]|CONTENTS|Contents", flat)) or len(re.findall(r"第.{1,3}[章单元]|Unit\d", flat)) >= 3


def continues_contents(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or sum(len(line) for line in lines) / len(lines) > 22:
        return False
    return sum(bool(re.search(r"\d{1,3}$", line)) for line in lines) >= 4


def official_facts(pages: list[str]) -> dict:
    """Copyright facts and the contents pages' OCR lines from the official preview (OCR, unreviewed)."""
    page = next((t for t in pages if "版次" in squash(t) or "ISBN" in t), "")
    facts = edition_facts(squash(page))
    contents = []
    for number, text in enumerate(pages, 1):
        if "版次" in squash(text):
            continue
        following = contents and contents[-1]["preview_page"] == number - 1 and continues_contents(text)
        if is_contents(text) or following:
            contents.append({"preview_page": number, "lines": [line.strip() for line in text.splitlines() if line.strip()]})
        elif contents:
            break
    return {"cover_year": cover_year(pages[0]) if pages else None, "editions": facts["editions"],
            "printing": facts["printing"], "isbn": facts["isbn"], "contents_ocr": contents,
            "contents_review_status": "ocr_unreviewed"}


def current_match(local_cover_year, current, official) -> dict:
    """Compare a local copy with the edition the national platform publishes now (official_catalog.json)."""
    if local_cover_year is None:
        verdict = "check_manually"
    elif current["revised_2022_standard"]:
        verdict = "same_generation" if local_cover_year >= NEW_CURRICULUM_YEAR else "outdated"
    else:
        verdict = "same_generation" if local_cover_year < NEW_CURRICULUM_YEAR else "check_manually"
    return {"platform_id": current["platform_id"], "title": current["title"],
            "isbn": current["isbn"] or (official["isbn"] or "").replace("-", "") or None,
            "revised_2022_standard": current["revised_2022_standard"], "edition": official,
            "local_cover_year": local_cover_year, "verdict": verdict}


def main():
    manifest_path = HERE / "textbooks.json"
    manifest = json.loads(manifest_path.read_text())
    official_path = HERE / "official_catalog.json"
    official = {b["id"]: b for b in json.loads(official_path.read_text())["books"]} if official_path.exists() else {}
    reviews = {r["id"]: r for r in json.loads((HERE / "edition_review.json").read_text())["reviews"]}
    for book in manifest["books"]:
        path = LIBRARY / (re.sub(r"\.pdf(_merge_folder/.*)?$", "", book["parts"][0]["path"]) + ".pdf")
        doc = pymupdf.open(path)
        index, text = copyright_page(doc)
        facts = edition_facts(text)
        toc_pages, entries = toc_entries(doc)
        book["pages"] = doc.page_count
        book["text_layer"] = any(len(squash(doc[i].get_text())) >= 20 for i in range(min(LOCAL_PAGES, doc.page_count)))
        book["edition"] = {"copyright_pdf_page": index + 1 if index is not None else None, **facts, "status": version_status(facts)}
        book["toc"] = {"pdf_pages": toc_pages, "outline": [{"level": level, "title": title, "page": page} for level, title, page in doc.get_toc()],
                       "entries": entries, "review_status": "auto_extracted_unreviewed"}
        current = official.get(book["id"])
        if current:
            with tempfile.TemporaryDirectory() as scratch:
                cover = Path(scratch) / "cover.png"
                doc[0].get_pixmap(dpi=100).save(cover)
                local_year = cover_year(ocr_image(cover)) or front_matter_year(doc, facts, Path(scratch))
            book["current_official"] = current_match(local_year, current, official_facts(official_pages(current)))
            if book["current_official"]["verdict"] == "check_manually" and book["id"] in reviews:
                book["current_official"]["verdict"] = reviews[book["id"]]["verdict"]
                book["current_official"]["review"] = reviews[book["id"]]
        verdict = book.get("current_official", {})
        print(f"{book['id']:<22} 封面 {verdict.get('local_cover_year') or '-'} 官方 {verdict.get('edition', {}).get('cover_year') or '-'} "
              f"{'新版' if current and current['revised_2022_standard'] else '旧版'} 目录 {len(entries)} 条 -> {verdict.get('verdict', '-')}", flush=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()

"""Download the selected textbooks from a pinned ChinaTextbook commit and verify every byte.

PDFs go to a local library directory (default ~/.local/share/xueban/textbooks), never into the
repository. Each downloaded part is checked against its Git blob SHA from the pinned tree, split
parts are merged, and curriculum/textbooks.json records what was fetched.

    python3 curriculum/fetch_textbooks.py [--dest DIR] [--workers 3]
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DEST = Path(os.environ.get("XUEBAN_TEXTBOOK_DIR", Path.home() / ".local/share/xueban/textbooks"))
TERMS = {"上册": "up", "下册": "down", "全一册": "full"}
GRADE_WORDS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
SUBJECT_IDS = {"语文": "chinese", "数学": "math", "英语": "english", "道德与法治": "morality-law", "历史": "history",
               "地理": "geography", "生物学": "biology", "物理": "physics", "科学": "science"}


def request(url, headers=None, timeout=120):
    token = github_token()
    headers = {"User-Agent": "xueban-textbook-fetch", **(headers or {})}
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def load_tree(repo, commit, cache):
    if cache.exists():
        return json.loads(cache.read_text())
    with request(f"https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1") as response:
        tree = json.load(response)
    if tree.get("truncated"):
        sys.exit("GitHub returned a truncated tree; cannot resolve every file.")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(tree, ensure_ascii=False))
    return tree


def blob_sha(path):
    digest = hashlib.sha1(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def books(selection, tree):
    """Group blobs (including *.pdf_merge_folder/*.pdf.N parts) into one entry per volume."""
    blobs = [t for t in tree["tree"] if t["type"] == "blob"]
    for klass in selection["classes"]:
        for subject in klass["subjects"]:
            grouped = {}
            for blob in blobs:
                if not blob["path"].startswith(subject["path"] + "/"):
                    continue
                name = blob["path"].rsplit("/", 1)[-1]
                if subject.get("match") and subject["match"] not in name:
                    continue
                title = re.sub(r"\.pdf(\.\d+)?$", "", name)
                grouped.setdefault(title, []).append(blob)
            if not grouped:
                sys.exit(f"No files for {klass['label']} {subject['subject']} under {subject['path']}")
            for title, parts in sorted(grouped.items()):
                term = next((v for k, v in TERMS.items() if k in title), None)
                grades = re.findall(r"([一二三四五六七八九])年级(?!起点)", title)
                grade = GRADE_WORDS[grades[-1]] if grades else None
                if term is None or grade != klass["grade"]:
                    sys.exit(f"Cannot read grade/term from {title}")
                parts.sort(key=lambda b: int(re.search(r"\.pdf\.(\d+)$", b["path"]).group(1)) if b["path"][-1].isdigit() else 0)
                yield {"id": f"g{grade}-{SUBJECT_IDS[subject['subject']]}-{term}", "grade": grade, "class_label": klass["label"],
                       "subject": subject["subject"], "term": term, "publisher": subject["publisher"], "title": title,
                       "parts": [{"path": b["path"], "blob_sha": b["sha"], "bytes": b["size"]} for b in parts]}


def github_token():
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        token = result.stdout.strip() if result.returncode == 0 else None
    return token


def verified(target, part):
    return target.exists() and target.stat().st_size == part["bytes"] and blob_sha(target) == part["blob_sha"]


def download(repo, commit, part, target, attempts=30):
    """Alternate raw.githubusercontent.com (resumable, curl -C -) with the API blob endpoint, which stays reachable
    when the raw host times out. curl uses the system trust store."""
    raw = f"https://raw.githubusercontent.com/{repo}/{commit}/{urllib.parse.quote(part['path'])}"
    blob = f"https://api.github.com/repos/{repo}/git/blobs/{part['blob_sha']}"
    token = github_token()
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(attempts):
        if verified(target, part):
            return
        if target.exists() and target.stat().st_size >= part["bytes"]:
            target.unlink()
        common = ["curl", "-fsSL", "--connect-timeout", "30", "--speed-limit", "1000", "--speed-time", "60"]
        if attempt % 2 == 0 or not token:
            result = subprocess.run([*common, "-C", "-", "-o", str(target), raw], capture_output=True, text=True)
        else:
            staging = target.with_name(target.name + ".blob")
            result = subprocess.run([*common, "-H", "Accept: application/vnd.github.raw+json", "-H", f"Authorization: Bearer {token}",
                                     "-o", str(staging), blob], capture_output=True, text=True)
            if result.returncode == 0 and verified(staging, part):
                staging.replace(target)
            staging.unlink(missing_ok=True)
        if result.returncode not in (0, 33):
            print(f"  retry {attempt + 1} {part['path']}: curl {result.returncode} {result.stderr.strip()[:100]}", flush=True)
            time.sleep(min(30, 5 * (attempt + 1)))
    if verified(target, part):
        return
    raise RuntimeError(f"Could not download {part['path']}")


def fetch(repo, commit, dest, book):
    final = dest / (re.sub(r"\.pdf(_merge_folder/.*)?$", "", book["parts"][0]["path"]) + ".pdf")
    for part in book["parts"]:
        download(repo, commit, part, dest / "parts" / part["path"] if len(book["parts"]) > 1 else final)
    if len(book["parts"]) > 1:
        final.parent.mkdir(parents=True, exist_ok=True)
        with final.open("wb") as out:
            for part in book["parts"]:
                out.write((dest / "parts" / part["path"]).read_bytes())
    return {**book, "local_path": str(final.relative_to(dest)), "bytes": final.stat().st_size, "sha256": sha256(final)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    selection = json.loads((HERE / "selection.json").read_text())
    repo, commit = selection["source"]["repository"], selection["source"]["commit"]
    tree = load_tree(repo, commit, args.dest / f"tree-{commit}.json")
    wanted = list(books(selection, tree))
    print(f"{len(wanted)} volumes, {sum(p['bytes'] for b in wanted for p in b['parts']) / 1e6:.0f} MB", flush=True)
    done = {}
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(fetch, repo, commit, args.dest, book): book["id"] for book in wanted}
        for future in concurrent.futures.as_completed(futures):
            try:
                book = future.result()
            except RuntimeError as error:
                print(f"failed {futures[future]}: {error}", flush=True)
                continue
            done[book["id"]] = book
            print(f"ok {book['id']} {book['bytes']} {book['sha256'][:12]}", flush=True)
    missing = [b["id"] for b in wanted if b["id"] not in done]
    if missing:
        sys.exit(f"{len(missing)} volumes still missing ({', '.join(missing)}); rerun to resume.")
    manifest_path = HERE / "textbooks.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"books": []}
    extra = {b["id"]: {k: v for k, v in b.items() if k in ("edition", "toc")} for b in previous["books"]}
    ordered = [{**done[b["id"]], **extra.get(b["id"], {})} for b in wanted]
    for book in ordered:
        book.pop("local_path")
    manifest = {"schema_version": 1, "source": selection["source"], "library_dir_env": "XUEBAN_TEXTBOOK_DIR",
                "books": ordered}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {manifest_path.relative_to(HERE.parent)}", flush=True)


if __name__ == "__main__":
    main()

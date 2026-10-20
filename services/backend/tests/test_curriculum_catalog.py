from xueban_backend import curriculum_catalog as catalog


def test_science_publishers_are_distinct_and_only_declared_scopes_are_enabled():
    assert catalog.supported_scope(5, "科学", "科学教育出版社").publisher_code == "CSP"
    assert catalog.supported_scope(5, "科学", "教育科学出版社") is None
    assert catalog.supported_scope(6, "科学", "教育科学出版社").publisher_code == "ECSP"
    assert catalog.supported_scope(6, "科学", "科学教育出版社") is None
    assert catalog.supported_scope(7, "道德与法治", "人民教育出版社").subject == "政治"
    assert catalog.supported_scope(9, "数学", "人民教育出版社") is None


def test_latest_catalog_does_not_substitute_a_different_science_publisher(monkeypatch):
    verified_at = "2026-10-19T00:00:00+00:00"
    official_books = [
        {"grade": 6, "subject": "科学", "publisher": "教育科学出版社", "term": term,
         "title": f"六年级科学{term}", "isbn": None, "catalog_year": None,
         "updated_at": "2026-10-01T00:00:00+0800", "revised_2022_standard": True}
        for term in ("up", "down")
    ]
    monkeypatch.setattr(catalog, "_official_catalog", lambda: {
        "verified_at": verified_at,
        "requested_scopes": [
            {"grade": 5, "subject": "科学", "publisher": "科学教育出版社", "status": "publisher_mismatch"},
            {"grade": 6, "subject": "科学", "publisher": "教育科学出版社", "status": "available"},
        ],
    })
    monkeypatch.setattr(catalog, "_official_books", lambda: tuple({
        "grade": book["grade"], "subject": book["subject"], "publisher": book["publisher"],
        "term": book["term"], "official": book, "contents_review_status": "unreviewed",
    } for book in official_books))

    grade5 = catalog.scope_payload(catalog.supported_scope(5, "科学", "科学教育出版社"))
    grade6 = catalog.scope_payload(catalog.supported_scope(6, "科学", "教育科学出版社"))

    assert grade5["catalog_status"] == "publisher_mismatch"
    assert grade5["catalog_verified_at"] == verified_at
    assert grade5["books"] == []
    assert grade6["catalog_status"] == "available"
    assert [book["term"] for book in grade6["books"]] == ["up", "down"]


def test_official_catalog_matching_supports_art_labels_and_full_volume():
    from curriculum.official_catalog import version_matches

    assert version_matches("人音版（五线谱）（主编：李心草）", "人音版")
    assert version_matches("人美版（主编：黄宗贤）", "人美版")
    assert catalog.supported_scope(5, "体育与健康", "人民教育出版社") is not None
    assert catalog.supported_scope(5, "艺术·音乐", "人民音乐出版社").subject == "艺术音乐"

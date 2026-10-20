"""Validate/import/publish an immutable, source-reviewed textbook candidate."""
import argparse
import json
from pathlib import Path

from sqlalchemy.orm import Session

from xueban_backend.config import database_url_from_environment
from xueban_backend.curriculum_publication import import_publication, prepare_publication
from xueban_backend.db import create_database_engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "import", "publish"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--allow-sqlite", action="store_true", help="Only for an explicit local test database")
    args = parser.parse_args()
    prepared = prepare_publication(args.candidate.read_bytes(), args.review.read_bytes(), args.version)
    if args.action == "validate":
        result = {"status": "validated", "graph_version": args.version, "nodes": len(prepared["nodes"]),
                  "edges": len(prepared["edges"]), "candidate_sha256": prepared["import_digest"]}
    else:
        url = database_url_from_environment()
        if not url:
            parser.error("DATABASE_URL is required for import/publish")
        engine = create_database_engine(url, allow_sqlite=args.allow_sqlite)
        try:
            with Session(engine) as session, session.begin():
                result = import_publication(session, prepared, publish=args.action == "publish")
        finally:
            engine.dispose()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

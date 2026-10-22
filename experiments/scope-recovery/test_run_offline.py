"""Standard-library-only packaging safety checks; no model or database calls."""
import importlib.util
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("run_offline", Path(__file__).with_name("run_offline.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RunnerSafety(unittest.TestCase):
    def test_environment_is_allowlist_and_sqlite_is_default(self):
        env = runner.clean_environment({"PATH": "/synthetic/bin", "HOME": "/private",
            "OPENAI_API_KEY": "synthetic-key", "ARK_API_KEY": "synthetic-key",
            "CUSTOM_UNKNOWN_MODEL_TOKEN": "synthetic-key", "PYTHONPATH": "/private",
            "HTTP_PROXY": "https://proxy.invalid", "BACKEND_TEST_DATABASE_URL": "postgresql://synthetic/db"})
        self.assertEqual(env["PATH"], "/synthetic/bin")
        self.assertNotIn("BACKEND_TEST_DATABASE_URL", env)
        self.assertNotIn("synthetic-key", str(env))
        for key in ("HOME", "PYTHONPATH", "HTTP_PROXY"):
            self.assertNotIn(key, env)

    def test_postgres_is_explicit(self):
        url = "postgresql+psycopg://synthetic:secret@example.invalid/db"
        self.assertEqual(runner.clean_environment({"BACKEND_TEST_DATABASE_URL": url}, require_postgres=True)["BACKEND_TEST_DATABASE_URL"], url)
        for value in ("", "sqlite:///anything"):
            with self.assertRaises(ValueError):
                runner.clean_environment({"BACKEND_TEST_DATABASE_URL": value}, require_postgres=True)

    def test_existing_paths_including_dangling_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            existing = root / "existing"; existing.mkdir()
            link = root / "link"; link.symlink_to(root / "missing")
            for path in (existing, link):
                with self.assertRaises(ValueError):
                    runner.new_destination(path)
            self.assertTrue(existing.is_dir())
            self.assertTrue(link.is_symlink())

    def test_destination_inside_worktree_or_symlink_to_it_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"; repo.mkdir(); (repo / ".git").write_text("gitdir: synthetic")
            child = repo / "child"; child.mkdir()
            link = root / "link"; link.symlink_to(child, target_is_directory=True)
            for parent in (repo, child, link):
                with self.assertRaises(ValueError):
                    runner.new_destination(parent / "new")
                self.assertFalse((parent / "new").exists())

    def test_archive_rejects_traversal_and_links_before_writing(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("/escape", tarfile.REGTYPE),
                           ("link", tarfile.SYMTYPE), ("hard", tarfile.LNKTYPE)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as tar:
                    member = tarfile.TarInfo(name); member.type = kind; member.linkname = "outside"
                    tar.addfile(member)
                stream.seek(0)
                with self.assertRaises((ValueError, tarfile.FilterError)):
                    runner.extract_regular_archive(stream, Path(temp))
                self.assertEqual(list(Path(temp).iterdir()), [])

    def test_database_output_redaction(self):
        url = "postgresql+psycopg://u:s%40secret@example.invalid/db"
        output = runner.redact(url + " s@secret s%40secret postgresql://u:other@example.invalid/db", url)
        for value in (url, "s@secret", "s%40secret", "other@example"):
            self.assertNotIn(value, output)

    def test_interpreter_keeps_virtualenv_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            link = Path(temp) / "python"
            link.symlink_to(os.sys.executable)
            self.assertEqual(runner.interpreter(str(link)), str(link))

    def test_frozen_package_verifies(self):
        manifest = runner.check_package(Path(__file__).parent)
        self.assertEqual(set(manifest["files"]), runner.EXPECTED_PATHS)


if __name__ == "__main__":
    unittest.main()

import unittest
import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("kstext_publisher.py")
SPEC = importlib.util.spec_from_file_location("kstext_publisher", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module from {MODULE_PATH}")
kp = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = kp
SPEC.loader.exec_module(kp)


class PublisherLogicTests(unittest.TestCase):
    def test_cache_dir_for_repo_is_scoped(self) -> None:
        repo_dir = kp.cache_dir_for_repo("bedkillerspacex-boop/KsText")
        self.assertEqual(repo_dir, kp.DEFAULT_REPO_CACHE_ROOT / "bedkillerspacex-boop__KsText")

    def test_target_display_contains_repo_branch_and_cache(self) -> None:
        repo_dir = kp.cache_dir_for_repo("bedkillerspacex-boop/KsText")
        text = kp.target_display("bedkillerspacex-boop/KsText", "master", repo_dir)
        self.assertIn("bedkillerspacex-boop/KsText@master", text)
        self.assertIn(str(repo_dir), text)

    def test_parse_push_progress_with_size(self) -> None:
        progress = kp.parse_push_progress("Writing objects: 50% (3/6), 3.00 MiB | 1.00 MiB/s")
        self.assertIsNotNone(progress)
        percent, text = progress
        self.assertEqual(percent, 50.0)
        self.assertIn("50%", text)
        self.assertIn("3.00 MB/6.00 MB", text)

    def test_build_index_from_documents_updates_changed_version(self) -> None:
        doc = kp.PackDocument(
            path=Path("packs/demo.json"),
            schema_version=1,
            pack_id="demo",
            name="Demo",
            author="Author",
            summary="Summary",
            language="zh-CN",
            tags=["killsay"],
            server_tags=["generic"],
            entries=["line1", "line2"],
            file_version=1,
            file_updated_at="2026-01-01T00:00:00Z",
        )
        result = kp.build_index_from_documents(
            "bedkillerspacex-boop/KsText",
            "master",
            [doc],
            {
                "demo": {
                    "version": 1,
                    "updatedAt": "2026-01-01T00:00:00Z",
                    "sha256": "old",
                }
            },
            [],
            True,
        )
        pack = result.index_data["packs"][0]
        self.assertEqual(pack["version"], 2)
        self.assertEqual(doc.file_version, 2)
        self.assertEqual(pack["sha256"], kp.sha256_bytes(kp.json_bytes(kp.pack_payload(doc))))


if __name__ == "__main__":
    unittest.main()

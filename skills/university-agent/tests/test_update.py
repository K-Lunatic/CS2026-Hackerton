"""Version checks never overwrite a development checkout."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import update_turtleneck


class UpdateTests(unittest.TestCase):
    def test_semver_check(self):
        self.assertTrue(update_turtleneck.is_newer("0.5.0", "0.4.2"))
        self.assertFalse(update_turtleneck.is_newer("0.4.2", "0.4.2"))

    def test_check_reads_remote_metadata_without_downloading_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "plugin.json").write_text(json.dumps({"version": "0.4.2"}), encoding="utf-8")
            cache = root / "update.json"
            with patch.object(update_turtleneck, "package_root", return_value=root), \
                 patch.object(update_turtleneck, "_fetch", return_value=b'{"version":"0.5.0"}'), \
                 patch.object(update_turtleneck, "_cache_path", return_value=cache):
                result = update_turtleneck.check(force=True)
            self.assertEqual(result["status"], "UPDATE_AVAILABLE")
            self.assertTrue(cache.is_file())

    def test_apply_skips_git_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "plugin.json").write_text(json.dumps({"version": "0.4.2"}), encoding="utf-8")
            (root / ".git").mkdir()
            with patch.object(update_turtleneck, "package_root", return_value=root):
                result = update_turtleneck.apply_update()
            self.assertEqual(result["status"], "SKIPPED")


if __name__ == "__main__":
    unittest.main()

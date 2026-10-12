"""Version checks never overwrite a development checkout."""
import json
import io
import zipfile
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

    def test_update_keeps_both_hosts_in_sync_and_uses_the_check_cache(self):
        archive = io.BytesIO()
        prefix = 'CS2026-Hackerton-dev/'
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr(prefix + 'skills/university-agent/SKILL.md', 'new skill')
            bundle.writestr(prefix + 'plugin.json', '{"version":"0.5.0"}')
            bundle.writestr(prefix + '.claude-plugin/plugin.json', '{"version":"0.5.0"}')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / 'skills/university-agent'
            target.mkdir(parents=True)
            (target / 'SKILL.md').write_text('old skill')
            (root / 'plugin.json').write_text('{"version":"0.4.2"}')
            (root / '.claude-plugin').mkdir()
            (root / '.claude-plugin/plugin.json').write_text('{"version":"0.4.2"}')
            with patch.object(update_turtleneck, 'package_root', return_value=root), patch.object(update_turtleneck, 'skill_root', return_value=target), patch.object(update_turtleneck, 'check', return_value={'status': 'UPDATE_AVAILABLE', 'remoteVersion': '0.5.0'}) as check, patch.object(update_turtleneck, '_fetch', return_value=archive.getvalue()):
                result = update_turtleneck.apply_update()
            self.assertEqual(result['status'], 'UPDATED')
            check.assert_called_once_with(force=False)
            self.assertEqual((target / 'SKILL.md').read_text(), 'new skill')
            for path in ('plugin.json', '.claude-plugin/plugin.json'):
                self.assertEqual(json.loads((root / path).read_text())['version'], '0.5.0')

    def test_claude_package_matches_shared_skill_and_codex_version(self):
        repo = ROOT.parents[1]
        codex = json.loads((repo / 'plugin.json').read_text())
        claude = json.loads((repo / '.claude-plugin/plugin.json').read_text())
        marketplace = json.loads((repo / '.claude-plugin/marketplace.json').read_text())
        self.assertEqual(claude['name'], codex['name'])
        self.assertEqual(claude['version'], codex['version'])
        plugin = marketplace['plugins'][0]
        self.assertEqual(plugin['name'], claude['name'])
        self.assertEqual((repo / plugin['source']).resolve(), repo.resolve())
        self.assertTrue((ROOT / 'references/claude-code.md').is_file())


if __name__ == "__main__":
    unittest.main()

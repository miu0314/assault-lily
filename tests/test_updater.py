# -*- coding: utf-8 -*-
"""更新器逻辑测试：版本比较 / 发布解析 / 更新决策 / 覆盖脚本 / 资源包解压。"""

import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

import updater


def _release_payload(version="1.2.0", assets_stamp="abc12345"):
    return {
        "tag_name": f"v{version}",
        "html_url": f"https://github.com/miu0314/assault-lily/releases/tag/v{version}",
        "assets": [
            {"name": f"AssaultLilyBot-v{version}-win-x64.zip",
             "browser_download_url": "https://example.com/full.zip",
             "size": 105000000},
            {"name": f"AssaultLilyBot-assets-{assets_stamp}.zip",
             "browser_download_url": "https://example.com/assets.zip",
             "size": 1500000},
        ],
    }


class VersionTest(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(updater.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(updater.parse_version("1.2"), (1, 2))
        self.assertEqual(updater.parse_version("v0.1.0"), (0, 1, 0))
        self.assertEqual(updater.parse_version(""), ())

    def test_is_newer(self):
        self.assertTrue(updater.is_newer((1, 2, 3), (1, 2, 2)))
        self.assertTrue(updater.is_newer((1, 3), (1, 2, 9)))
        self.assertFalse(updater.is_newer((1, 2, 3), (1, 2, 3)))
        self.assertFalse(updater.is_newer((1, 2), (1, 2, 1)))


class ParseReleaseTest(unittest.TestCase):
    def test_picks_both_assets(self):
        info = updater.parse_release(_release_payload())
        self.assertEqual(info.version, (1, 2, 0))
        self.assertEqual(info.full.name, "AssaultLilyBot-v1.2.0-win-x64.zip")
        self.assertEqual(info.assets_stamp, "abc12345")

    def test_missing_assets_pack_is_ok(self):
        payload = _release_payload()
        payload["assets"] = payload["assets"][:1]
        info = updater.parse_release(payload)
        self.assertIsNone(info.assets)
        self.assertIsNone(info.assets_stamp)


class DecideTest(unittest.TestCase):
    def test_program_update_wins(self):
        info = updater.parse_release(_release_payload("1.2.0"))
        kind, message = updater.decide(info, "1.1.0", "abc12345")
        self.assertEqual(kind, "program")
        self.assertIn("1.2.0", message)

    def test_assets_only_update(self):
        info = updater.parse_release(_release_payload("1.2.0", "newstamp"))
        kind, message = updater.decide(info, "1.2.0", "oldstamp")
        self.assertEqual(kind, "assets")
        self.assertIn("模板", message)

    def test_nothing_to_do(self):
        info = updater.parse_release(_release_payload("1.2.0", "same"))
        kind, _ = updater.decide(info, "1.2.0", "same")
        self.assertIsNone(kind)
        self.assertIsNone(updater.decide(None, "1.2.0", "same")[0])


class UpdateScriptTest(unittest.TestCase):
    def test_script_waits_and_keeps_user_config(self):
        script = updater.build_update_script(
            zip_path=r"C:\temp\new.zip",
            target_dir=r"C:\app\AssaultLilyBot",
            exe_path=r"C:\app\AssaultLilyBot\AssaultLilyBot.exe",
            log_path=r"C:\temp\update.log")
        self.assertIn("tasklist", script)
        self.assertIn("config.json", script)          # 用户配置被排除
        self.assertIn("config_selected.json", script)
        self.assertIn(r"C:\app\AssaultLilyBot", script)
        self.assertIn("AssaultLilyBot.exe", script)
        self.assertNotIn("{{", script)                 # 占位符都替换掉了


class ApplyAssetsTest(unittest.TestCase):
    def _make_pack(self, path, entries):
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in entries.items():
                zf.writestr(name, data)

    def test_extract_assets_and_write_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            pack = base / "pack.zip"
            self._make_pack(pack, {
                "assets/quests/a.png": "png",
                "assets_version.txt": "stamp123",
                "../evil.txt": "bad",
            })
            count = updater.apply_assets_pack(pack, base)
            self.assertTrue((base / "assets" / "quests" / "a.png").exists())
            self.assertEqual((base / "assets_version.txt").read_text(encoding="utf-8").strip(),
                             "stamp123")
            self.assertFalse((base.parent / "evil.txt").exists())
            self.assertEqual(count, 2)

    def test_stamp_file_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertEqual(updater.local_assets_stamp(base), "")
            (base / "assets_version.txt").write_text("xyz\n", encoding="utf-8")
            self.assertEqual(updater.local_assets_stamp(base), "xyz")


if __name__ == "__main__":
    unittest.main()

class CacheTest(unittest.TestCase):
    def _cache(self, base, payload, age_seconds=0):
        import json
        import time

        (base / "update_cache.json").write_text(
            json.dumps({"checked_at": time.time() - age_seconds,
                        "release": payload}),
            encoding="utf-8")

    def test_recent_cache_skips_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._cache(base, _release_payload("1.3.0", "stamp9"))
            with patch.object(updater, "fetch_latest",
                              side_effect=AssertionError("不该联网")):
                release, kind, _ = updater.check("1.2.0", base)
            self.assertEqual(kind, "program")
            self.assertEqual(release.version, (1, 3, 0))

    def test_stale_cache_goes_online(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._cache(base, _release_payload("1.3.0", "stamp9"), age_seconds=7 * 3600)
            with patch.object(updater, "fetch_latest",
                              side_effect=updater.UpdateError("离线")) as mocked:
                release, kind, message = updater.check("1.2.0", base)
            self.assertTrue(mocked.called)
            self.assertIsNone(release)
            self.assertIn("离线", message)
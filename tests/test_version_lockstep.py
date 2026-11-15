"""Every place the plugin version lives must agree with VERSION.

The Claude Code plugin manager reads the version from the manifests, while
`/dm:dnd update --check` reads VERSION. When they disagree, `/plugin update dm`
reports "already at the latest version" while the skill reports an update, and
existing installs never receive the release. This drift shipped three times
(v2.1.4, v2.2.1, v2.7.0), each time because only VERSION and CHANGELOG were
bumped by hand. `scripts/bump_version.py` updates all of them; this test fails
any release that skips it.
"""
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


class VersionLockstep(unittest.TestCase):
    def test_plugin_json_matches_version(self):
        data = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(data["version"], _version())

    def test_marketplace_dm_entry_matches_version(self):
        data = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        entries = [p for p in data["plugins"] if p.get("name") == "dm"]
        self.assertEqual(len(entries), 1, "expected exactly one `dm` plugin entry")
        self.assertEqual(entries[0]["version"], _version())

    def test_skill_picker_prefix_matches_version(self):
        text = (ROOT / "skills" / "dnd" / "SKILL.md").read_text(encoding="utf-8")
        m = re.search(r'description:\s*"?v(\d+\.\d+(?:\.\d+)?)\s*·', text)
        self.assertIsNotNone(m, "SKILL.md description has no 'vX.Y.Z ·' prefix")
        self.assertEqual(m.group(1), _version())

    def test_changelog_has_a_section_for_version(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertRegex(text, rf"(?m)^## \[{re.escape(_version())}\]")


if __name__ == "__main__":
    unittest.main()

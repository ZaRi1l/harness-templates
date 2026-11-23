import importlib.util
import json
import re
import unittest

from helpers import ROOT, TEMPLATES


def load_verify(template: str):
    spec = importlib.util.spec_from_file_location(f"verify_{template}", ROOT / template / ".claude/hooks/verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTemplates(unittest.TestCase):
    def test_verify_py_is_identical_everywhere(self):
        first = (ROOT / TEMPLATES[0] / ".claude/hooks/verify.py").read_bytes()
        for t in TEMPLATES[1:]:
            self.assertEqual((ROOT / t / ".claude/hooks/verify.py").read_bytes(), first,
                             f"{t}/.claude/hooks/verify.py가 다른 템플릿과 다르다")

    def test_configs_are_valid(self):
        defaults = load_verify(TEMPLATES[0]).DEFAULTS
        for t in TEMPLATES:
            with self.subTest(t):
                json.loads((ROOT / t / ".claude/settings.json").read_text(encoding="utf-8"))
                cfg = json.loads((ROOT / t / ".claude/hooks/verify.config.json").read_text(encoding="utf-8"))
                self.assertLessEqual(set(cfg) - {"_comment"}, set(defaults))

    def test_hook_scripts_exist(self):
        for t in TEMPLATES:
            text = (ROOT / t / ".claude/settings.json").read_text(encoding="utf-8")
            for script in re.findall(r"\$\{CLAUDE_PROJECT_DIR\}/\.claude/hooks/([\w.]+)", text):
                with self.subTest(t, script=script):
                    self.assertTrue((ROOT / t / ".claude/hooks" / script).is_file())

    def test_agents_referenced_by_managers_exist(self):
        for t in TEMPLATES:
            for agent in (ROOT / t / ".claude/agents").glob("*.md"):
                m = re.search(r"^tools:.*Agent\(([^)]*)\)", agent.read_text(encoding="utf-8"), re.M)
                for name in (m.group(1).split(",") if m else []):
                    with self.subTest(t, agent=name.strip()):
                        self.assertTrue((agent.parent / f"{name.strip()}.md").is_file())

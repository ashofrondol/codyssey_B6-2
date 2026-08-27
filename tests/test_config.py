"""컨벤션 설정 — 보너스 2(템플릿 커스터마이징)·3(안전 모드 정책)."""

import json
import os
import unittest

from helpers import BaseTest, TempRepo

from aigitgen import config
from aigitgen.errors import ConfigError


def write_config(repo_path: str, data: dict) -> None:
    with open(os.path.join(repo_path, config.CONFIG_FILENAME), "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False)


class TestDefaults(BaseTest):
    def test_설정_파일이_없으면_기본값(self):
        with TempRepo() as repo:
            settings = config.load(repo.path)
            self.assertEqual(settings.convention.name, "default")
            self.assertEqual(settings.convention.commit.title_max, 72)
            self.assertEqual(settings.convention.pr.title_max, 80)
            self.assertEqual(settings.safety.max_files, 10)
            self.assertEqual(settings.safety.max_diff_lines, 200)

    def test_설정_없이_컨벤션_이름을_주면_오류(self):
        with TempRepo() as repo:
            with self.assertRaises(ConfigError):
                config.load(repo.path, "team")


class TestConventionLoading(BaseTest):
    def test_팀_컨벤션을_읽는다(self):
        with TempRepo() as repo:
            write_config(repo.path, {
                "conventions": {"team": {
                    "commit": {"prefixes": ["feat", "fix"], "scope": "required", "title_max": 60},
                    "pr": {"title_max": 70, "extra_sections": ["Risk"], "min_bullets": 2,
                           "checklist": ["테스트 통과"]},
                }},
            })
            settings = config.load(repo.path, "team")
            self.assertEqual(settings.convention.commit.prefixes, ["feat", "fix"])
            self.assertEqual(settings.convention.commit.scope, "required")
            self.assertEqual(settings.convention.pr.sections, ["Why", "What", "How to Test", "Risk"])
            self.assertEqual(settings.convention.pr.checklist, ["테스트 통과"])
            self.assertIn("team", settings.available)

    def test_default_convention_키가_기본_선택을_바꾼다(self):
        with TempRepo() as repo:
            write_config(repo.path, {
                "default_convention": "team",
                "conventions": {"team": {"commit": {"title_max": 55}, "pr": {}}},
            })
            self.assertEqual(config.load(repo.path).convention.commit.title_max, 55)

    def test_없는_컨벤션_이름은_사용_가능_목록을_알려준다(self):
        with TempRepo() as repo:
            write_config(repo.path, {"conventions": {"team": {"commit": {}, "pr": {}}}})
            with self.assertRaises(ConfigError) as ctx:
                config.load(repo.path, "없는이름")
            self.assertIn("team", str(ctx.exception))

    def test_필수_섹션은_추가_섹션으로_다시_적을_수_없다(self):
        with TempRepo() as repo:
            write_config(repo.path, {
                "conventions": {"team": {"commit": {}, "pr": {"extra_sections": ["Why"]}}},
            })
            with self.assertRaises(ConfigError) as ctx:
                config.load(repo.path, "team")
            self.assertIn("Why", str(ctx.exception))

    def test_권장_길이가_최대_길이보다_크면_오류(self):
        with TempRepo() as repo:
            write_config(repo.path, {
                "conventions": {"team": {"commit": {"title_recommended": 90, "title_max": 72}, "pr": {}}},
            })
            with self.assertRaises(ConfigError):
                config.load(repo.path, "team")

    def test_깨진_JSON_은_친절한_오류(self):
        with TempRepo() as repo:
            with open(os.path.join(repo.path, config.CONFIG_FILENAME), "w", encoding="utf-8") as fp:
                fp.write("{ not json")
            with self.assertRaises(ConfigError) as ctx:
                config.load(repo.path)
            self.assertIn("JSON", str(ctx.exception))


class TestSafeModeConfig(BaseTest):
    def test_숫자_정책을_바꾼다(self):
        with TempRepo() as repo:
            write_config(repo.path, {"safe_mode": {"max_files": 3, "max_diff_lines": 50}})
            settings = config.load(repo.path)
            self.assertEqual((settings.safety.max_files, settings.safety.max_diff_lines), (3, 50))

    def test_사용자_정규식_규칙을_앞에_끼워넣는다(self):
        with TempRepo() as repo:
            write_config(repo.path, {
                "safe_mode": {"extra_patterns": [["사번", "EMP-\\d{6}", "«MASKED:EMP»"]]},
            })
            policy = config.load(repo.path).safety
            self.assertEqual(policy.patterns[0][0], "사번")
            self.assertGreater(len(policy.patterns), 1)  # 기본 규칙도 살아 있다

    def test_잘못된_정규식은_오류(self):
        with TempRepo() as repo:
            write_config(repo.path, {"safe_mode": {"extra_patterns": [["x", "([", "y"]]}})
            with self.assertRaises(ConfigError) as ctx:
                config.load(repo.path)
            self.assertIn("정규식", str(ctx.exception))

    def test_0_이하의_상한은_오류(self):
        with TempRepo() as repo:
            write_config(repo.path, {"safe_mode": {"max_files": 0}})
            with self.assertRaises(ConfigError):
                config.load(repo.path)


if __name__ == "__main__":
    unittest.main()

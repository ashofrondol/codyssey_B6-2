"""안전 모드 검증 — 제약 사항(민감정보)과 보너스 3(정책 조정)."""

import unittest

from helpers import BaseTest

from aigitgen import redact
from aigitgen.redact import SafetyPolicy


class TestMasking(BaseTest):
    def test_대표적인_키_형태를_전부_가린다(self):
        text = "\n".join(
            [
                'ANTHROPIC_API_KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz"',
                "aws = AKIAIOSFODNN7EXAMPLE",
                "gh = ghp_abcdefghijklmnopqrstuvwxyz012345",
                "google = AIzaSyA12345678901234567890123456789012345",
                "mail: someone@example.com",
                "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
                "주민번호 901231-1234567",
            ]
        )
        masked, counts = redact.mask(text, SafetyPolicy())
        for secret in (
            "sk-ant-api03",
            "AKIAIOSFODNN7EXAMPLE",
            "ghp_abcdefghij",
            "AIzaSyA1234567890",
            "someone@example.com",
            "901231-1234567",
        ):
            self.assertNotIn(secret, masked, f"{secret} 가 남아 있다")
        self.assertGreaterEqual(sum(counts.values()), 6)

    def test_private_key_블록_전체를_가린다(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\nabc\n-----END RSA PRIVATE KEY-----"
        masked, counts = redact.mask(text, SafetyPolicy())
        self.assertNotIn("MIIEow", masked)
        self.assertEqual(counts["private-key-block"], 1)

    def test_평범한_코드는_건드리지_않는다(self):
        text = "def add(a, b):\n    return a + b\n"
        masked, counts = redact.mask(text, SafetyPolicy())
        self.assertEqual(masked, text)
        self.assertEqual(counts, {})

    def test_사용자_정의_규칙을_추가할_수_있다(self):
        policy = SafetyPolicy(patterns=[("사번", r"EMP-\d{6}", "«MASKED:EMP»")])
        masked, counts = redact.mask("담당자 EMP-123456", policy)
        self.assertIn("«MASKED:EMP»", masked)
        self.assertEqual(counts["사번"], 1)


DIFF = "".join(
    f"diff --git a/f{i}.py b/f{i}.py\n--- a/f{i}.py\n+++ b/f{i}.py\n@@ -1 +1 @@\n-old{i}\n+new{i}\n"
    for i in range(5)
)


class TestTruncation(BaseTest):
    def test_파일을_블록으로_쪼갠다(self):
        self.assertEqual(len(redact.split_by_file(DIFF)), 5)

    def test_헤더가_없으면_통째로_한_덩어리(self):
        self.assertEqual(redact.split_by_file("just text"), ["just text"])

    def test_빈_입력은_빈_목록(self):
        self.assertEqual(redact.split_by_file(""), [])

    def test_파일_수_상한이_적용된다(self):
        text, total, kept, _ = redact.truncate(DIFF, SafetyPolicy(max_files=2, max_diff_lines=999))
        self.assertEqual((total, kept), (5, 2))
        self.assertIn("파일 3개 생략", text)
        self.assertNotIn("f4.py", text)

    def test_줄_수_상한이_적용된다(self):
        text, _, _, lines = redact.truncate(DIFF, SafetyPolicy(max_files=99, max_diff_lines=8))
        self.assertIn("줄 수 상한", text)
        self.assertLessEqual(lines, 10)  # 생략 안내 줄 포함


class TestApply(BaseTest):
    def test_마스킹이_잘라내기보다_먼저_일어난다(self):
        # 상한을 넘겨 잘려나갈 위치에 비밀을 둔다. 순서가 반대면 검사되지 않는다.
        secret_diff = (
            "diff --git a/a.py b/a.py\n" + "+line\n" * 30 + '+KEY = "sk-ant-abcdefghijklmnopqrst"\n'
        )
        out, report = redact.apply(secret_diff, SafetyPolicy(max_diff_lines=200))
        self.assertNotIn("sk-ant-abcdefghijklmnopqrst", out)
        self.assertEqual(report.masked_total, 1)

    def test_안전모드_off_면_원문_그대로_보낸다(self):
        out, report = redact.apply(DIFF, SafetyPolicy(enabled=False))
        self.assertEqual(out, DIFF)
        self.assertFalse(report.enabled)
        self.assertIn("안전 모드 OFF", report.summary())

    def test_보고서가_수치를_남긴다(self):
        _, report = redact.apply(DIFF, SafetyPolicy(max_files=2))
        self.assertEqual(report.files_dropped, 3)
        self.assertIn("파일 2/5개", report.summary())


if __name__ == "__main__":
    unittest.main()

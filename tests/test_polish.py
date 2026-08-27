"""출력 형식 검증·다듬기 — 요구사항 3·4·5(길이/템플릿/불릿 규칙)."""

import unittest

from helpers import BaseTest

from aigitgen.config import CommitConvention, Convention, PrConvention
from aigitgen.gitctx import ChangedFile, GitContext
from aigitgen import polish


def make_ctx(n_files=2, diff_lines=10):
    files = [ChangedFile(path=f"src/mod{i}.py", index_status="M", work_status=" ") for i in range(n_files)]
    return GitContext(
        root="/tmp/x",
        branch="feature/x",
        status_text="M src/mod0.py",
        files=files,
        diff_text="\n".join(f"+line{i}" for i in range(diff_lines)),
        diff_command="git diff HEAD",
    )


class TestCommitValidation(BaseTest):
    def setUp(self):
        self.conv = Convention()
        self.ctx = make_ctx()

    def draft(self, **kw):
        base = dict(
            type="feat", scope="cli", subject="커밋 메시지 자동 생성 추가",
            body_bullets=["git diff 를 프롬프트로 전달"], files_mentioned=["aigitgen/cli.py"],
        )
        base.update(kw)
        return polish.CommitDraft(**base)

    def test_정상_초안은_위반이_없다(self):
        self.assertEqual(polish.validate_commit(self.draft(), self.conv), [])

    def test_제목_72자_초과를_잡는다(self):
        problems = polish.validate_commit(self.draft(subject="가" * 80), self.conv)
        self.assertTrue(any("72자" in p for p in problems))

    def test_제목_72자_초과를_잘라낸다(self):
        drafted, fixes = polish.polish_commit(self.draft(subject="가" * 80), self.conv, self.ctx)
        self.assertLessEqual(len(drafted.title), self.conv.commit.title_max)
        self.assertTrue(any("줄임" in f for f in fixes))
        self.assertEqual(polish.validate_commit(drafted, self.conv), [])

    def test_스코프가_길어도_제목_상한을_지킨다(self):
        """subject 만 줄여서는 못 맞추는 경우 — 예전에는 못 맞추고도 줄였다고 보고했다."""
        drafted, fixes = polish.polish_commit(self.draft(scope="가" * 80), self.conv, self.ctx)
        self.assertLessEqual(len(drafted.title), self.conv.commit.title_max)
        self.assertEqual(polish.validate_commit(drafted, self.conv), [])
        self.assertTrue(any("스코프" in f for f in fixes), fixes)
        self.assertTrue(any("이내로 줄임" in f for f in fixes), fixes)

    def test_줄이지_못하면_줄였다고_보고하지_않는다(self):
        conv = Convention(commit=CommitConvention(title_recommended=1, title_max=4))
        drafted, fixes = polish.polish_commit(self.draft(scope="", subject="가" * 40), conv, self.ctx)
        # 타입 이름만으로도 상한을 넘는 극단 — 정직하게 실패를 알린다
        if len(drafted.title) > conv.commit.title_max:
            self.assertTrue(any("줄이지 못했습니다" in f for f in fixes), fixes)
            self.assertFalse(any(f.endswith("이내로 줄임") for f in fixes), fixes)

    def test_제목이_상한_안이면_아무것도_손대지_않는다(self):
        before = self.draft()
        drafted, fixes = polish.polish_commit(before, self.conv, self.ctx)
        self.assertEqual(fixes, [])
        self.assertEqual(drafted.title, "feat(cli): 커밋 메시지 자동 생성 추가")

    def test_컨벤션이_본문을_금지하면_본문을_지운다(self):
        conv = Convention(commit=CommitConvention(body="none"))
        self.assertTrue(polish.validate_commit(self.draft(), conv))
        drafted, fixes = polish.polish_commit(self.draft(), conv, self.ctx)
        self.assertEqual(drafted.body_bullets, [])
        self.assertEqual(drafted.files_mentioned, [])
        self.assertEqual(polish.validate_commit(drafted, conv), [])
        self.assertTrue(any("본문 제거" in f for f in fixes), fixes)

    def test_컨벤션이_본문을_요구하면_Git_사실로_채운다(self):
        conv = Convention(commit=CommitConvention(body="required"))
        empty = self.draft(body_bullets=[], files_mentioned=[])
        self.assertTrue(polish.validate_commit(empty, conv))
        drafted, fixes = polish.polish_commit(empty, conv, self.ctx)
        self.assertTrue(drafted.body_bullets and drafted.files_mentioned)
        self.assertEqual(polish.validate_commit(drafted, conv), [])
        self.assertIn("src/mod0.py", drafted.render())

    def test_허용되지_않은_타입을_교체한다(self):
        drafted, fixes = polish.polish_commit(self.draft(type="wip"), self.conv, self.ctx)
        self.assertEqual(drafted.type, "chore")
        self.assertTrue(fixes)

    def test_마침표로_끝나면_잡고_지운다(self):
        self.assertTrue(polish.validate_commit(self.draft(subject="추가함."), self.conv))
        drafted, _ = polish.polish_commit(self.draft(subject="추가함."), self.conv, self.ctx)
        self.assertFalse(drafted.title.endswith("."))

    def test_본문_불릿_개수_규칙(self):
        problems = polish.validate_commit(self.draft(body_bullets=["a", "b", "c"]), self.conv)
        self.assertTrue(any("1~2개" in p for p in problems))
        drafted, _ = polish.polish_commit(self.draft(body_bullets=["a", "b", "c"]), self.conv, self.ctx)
        self.assertEqual(len(drafted.body_bullets), 2)

    def test_언급_파일_개수_규칙(self):
        problems = polish.validate_commit(self.draft(files_mentioned=["a", "b", "c", "d"]), self.conv)
        self.assertTrue(any("1~3개" in p for p in problems))

    def test_스코프_필수_컨벤션(self):
        conv = Convention()
        conv.commit.scope = "required"
        self.assertTrue(polish.validate_commit(self.draft(scope=""), conv))

    def test_렌더_결과는_제목_한_줄로_시작한다(self):
        text = self.draft().render()
        self.assertEqual(text.splitlines()[0], "feat(cli): 커밋 메시지 자동 생성 추가")
        self.assertEqual(text.splitlines()[1], "")

    def test_payload_의_불릿_기호를_떼어낸다(self):
        drafted = polish.commit_from_payload(
            {"type": "fix", "scope": "", "subject": "x", "body_bullets": ["- 이미 기호가 붙음"],
             "files_mentioned": ["a.py"]},
            self.conv,
        )
        self.assertEqual(drafted.body_bullets, ["이미 기호가 붙음"])


class TestPrValidation(BaseTest):
    def setUp(self):
        self.conv = Convention()
        self.ctx = make_ctx()

    def payload(self, **kw):
        base = {
            "title": "feat: 커밋/PR 자동 생성 기능 추가",
            "why": ["작성 시간이 오래 걸렸다"],
            "what": ["CLI 를 추가했다"],
            "how_to_test": ["python main.py commit 실행"],
        }
        base.update(kw)
        return base

    def test_세_섹션이_모두_있으면_통과(self):
        draft = polish.pr_from_payload(self.payload(), self.conv)
        self.assertEqual(polish.validate_pr(draft, self.conv), [])
        body = draft.render_body()
        for header in ("## Why", "## What", "## How to Test"):
            self.assertIn(header, body)

    def test_섹션이_비면_잡고_사실로_채운다(self):
        draft = polish.pr_from_payload(self.payload(what=[]), self.conv)
        problems = polish.validate_pr(draft, self.conv)
        self.assertTrue(any("What" in p for p in problems))
        fixed, fixes = polish.polish_pr(draft, self.conv, self.ctx)
        self.assertEqual(polish.validate_pr(fixed, self.conv), [])
        self.assertTrue(any("보충" in f for f in fixes))
        self.assertIn("src/mod0.py", fixed.render_body())

    def test_제목_80자_상한(self):
        draft = polish.pr_from_payload(self.payload(title="가" * 100), self.conv)
        self.assertTrue(any("80자" in p for p in polish.validate_pr(draft, self.conv)))
        fixed, _ = polish.polish_pr(draft, self.conv, self.ctx)
        self.assertLessEqual(len(fixed.title), 80)

    def test_추가_섹션과_체크리스트가_붙는다(self):
        conv = Convention(pr=PrConvention(extra_sections=["Risk"], checklist=["테스트 통과"], min_bullets=1))
        draft = polish.pr_from_payload(self.payload(risk=["롤백 계획 있음"]), conv)
        body = draft.render_body()
        self.assertIn("## Risk", body)
        self.assertIn("- [ ] 테스트 통과", body)
        # 필수 3개가 여전히 앞에 온다
        self.assertLess(body.index("## Why"), body.index("## Risk"))

    def test_min_bullets_2_규칙(self):
        conv = Convention(pr=PrConvention(min_bullets=2))
        draft = polish.pr_from_payload(self.payload(), conv)
        self.assertEqual(len(polish.validate_pr(draft, conv)), 3)
        fixed, _ = polish.polish_pr(draft, conv, self.ctx)
        self.assertEqual(polish.validate_pr(fixed, conv), [])

    def test_보충_문장이_기존_불릿과_중복되지_않는다(self):
        conv = Convention(pr=PrConvention(min_bullets=3))
        # 이미 들어 있는 문장과 같은 후보는 건너뛰어야 한다.
        draft = polish.pr_from_payload(
            self.payload(what=["변경 파일 2개: src/mod0.py, src/mod1.py"]), conv
        )
        fixed, _ = polish.polish_pr(draft, conv, self.ctx)
        what = fixed.section("What")
        self.assertEqual(len(what), 3)
        self.assertEqual(len(set(what)), 3, f"중복된 불릿이 있다: {what}")

    def test_피드백_메시지에_위반이_모두_담긴다(self):
        text = polish.feedback_message(["A 위반", "B 위반"])
        self.assertIn("- A 위반", text)
        self.assertIn("- B 위반", text)


if __name__ == "__main__":
    unittest.main()

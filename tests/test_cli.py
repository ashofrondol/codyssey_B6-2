"""CLI 종단 검증 — 실행 한 번으로 자동화 흐름이 끝까지 도는지 확인한다.

AI API 는 :class:`helpers.FakeGenerator` 로 대체한다.
네트워크도 API Key 도 필요 없이 요구사항 1~5 전체를 실행으로 검증한다.
"""

import json
import os
import unittest

from helpers import BaseTest, TempRepo, run_cli, run_cli_streams

from aigitgen import config

COMMIT_OK = {
    "type": "feat",
    "scope": "cli",
    "subject": "커밋 메시지 자동 생성 기능 추가",
    "body_bullets": ["git diff 결과를 AI 입력 컨텍스트로 전달"],
    "files_mentioned": ["src/app.py"],
}
COMMIT_BAD = {
    "type": "wip",
    "scope": "",
    "subject": "가" * 100,
    "body_bullets": ["a", "b", "c"],
    "files_mentioned": [],
}
PR_OK = {
    "title": "feat: 커밋/PR 자동 생성 기능 추가",
    "why": ["작성에 시간이 오래 걸린다"],
    "what": ["CLI 명령 두 개를 추가했다"],
    "how_to_test": ["python main.py commit 실행"],
}
PR_BAD = {"title": "가" * 120, "why": [], "what": [], "how_to_test": []}


def dirty(repo: TempRepo) -> None:
    """추적 중인 파일을 고쳐 `git diff` 에 실제로 잡히게 만든다."""
    repo.commit({"src/app.py": "def run():\n    return 0\n"}, "base")
    repo.write("src/app.py", "def run():\n    return 1\n")


class TestCommitCommand(BaseTest):
    def test_한_번_실행으로_커밋_메시지가_출력된다(self):
        with TempRepo() as repo:
            dirty(repo)
            code, out, gen = run_cli(["commit", "--repo", repo.path], [COMMIT_OK])
            self.assertEqual(code, 0)
            self.assertEqual(gen.calls, 1)  # API 호출 1회
            self.assertIn("Commit Message", out)
            self.assertIn("feat(cli): 커밋 메시지 자동 생성 기능 추가", out)
            self.assertIn("[DONE]", out)
            self.assertIn("API 호출 1회", out)

    def test_제목이_한_줄로_출력된다(self):
        with TempRepo() as repo:
            dirty(repo)
            _, out, _ = run_cli(["commit", "--repo", repo.path], [COMMIT_OK])
            body = out.split("Commit Message")[1]
            title = [ln for ln in body.splitlines() if ln.startswith("feat")]
            self.assertEqual(len(title), 1)

    def test_형식_위반이면_1회_재생성하고_2회를_넘지_않는다(self):
        with TempRepo() as repo:
            dirty(repo)
            code, out, gen = run_cli(["commit", "--repo", repo.path], [COMMIT_BAD, COMMIT_BAD])
            self.assertEqual(code, 0)
            self.assertEqual(gen.calls, 2)  # 재생성 1회 포함, 상한 2회
            self.assertIn("재생성", out)
            self.assertIn("[WARN]", out)
            # 재생성해도 안 고쳐지면 후처리가 규칙을 맞춘다
            self.assertIn("후처리", out)

    def test_재생성_요청에_위반_목록이_실린다(self):
        with TempRepo() as repo:
            dirty(repo)
            _, _, gen = run_cli(["commit", "--repo", repo.path], [COMMIT_BAD, COMMIT_OK])
            self.assertEqual(len(gen.prompts), 2)
            self.assertIn("형식 규칙을 어겼다", gen.prompts[1])
            self.assertIn("72자", gen.prompts[1])

    def test_no_retry_는_호출을_1회로_고정한다(self):
        with TempRepo() as repo:
            dirty(repo)
            _, out, gen = run_cli(["commit", "--repo", repo.path, "--no-retry"], [COMMIT_BAD])
            self.assertEqual(gen.calls, 1)
            self.assertNotIn("재생성", out)

    def test_후처리가_제목_길이를_규칙_안으로_넣는다(self):
        with TempRepo() as repo:
            dirty(repo)
            _, out, _ = run_cli(["commit", "--repo", repo.path, "--no-retry"], [COMMIT_BAD])
            block = out.split("Commit Message")[1]
            title = next(ln for ln in block.splitlines() if ln and not ln.startswith("-"))
            self.assertLessEqual(len(title), 72)


class TestPrCommand(BaseTest):
    def test_PR_제목과_본문이_함께_출력된다(self):
        with TempRepo() as repo:
            dirty(repo)
            code, out, gen = run_cli(["pr", "--repo", repo.path], [PR_OK])
            self.assertEqual(code, 0)
            self.assertEqual(gen.calls, 1)
            self.assertIn("PR Title", out)
            self.assertIn("PR Body", out)
            for header in ("## Why", "## What", "## How to Test"):
                self.assertIn(header, out)

    def test_각_섹션에_불릿이_최소_1개_있다(self):
        with TempRepo() as repo:
            dirty(repo)
            _, out, _ = run_cli(["pr", "--repo", repo.path, "--no-retry"], [PR_BAD])
            body = out.split("PR Body")[1]
            for header in ("## Why", "## What", "## How to Test"):
                after = body.split(header, 1)[1].lstrip().splitlines()
                self.assertTrue(after and after[0].startswith("- "), f"{header} 아래 불릿이 없다")


class TestNoChanges(BaseTest):
    def test_변경이_없으면_API_를_부르지_않고_종료한다(self):
        with TempRepo() as repo:
            code, out, gen = run_cli(["commit", "--repo", repo.path], [COMMIT_OK])
            self.assertEqual(code, 0)
            self.assertIn("변경 사항이 없습니다", out)
            self.assertIsNone(gen)  # 생성기를 만들지도 않았다


class TestScope(BaseTest):
    """수집 범위(`--staged`)와 조기 종료 판정이 어긋나지 않는지."""

    def test_스테이지된_변경이_없으면_API_를_부르지_않는다(self):
        with TempRepo() as repo:
            repo.commit({"src/app.py": "x = 0\n"}, "base")
            repo.write("src/app.py", "x = 1\n")  # 작업 트리에만 변경. add 하지 않음
            code, out, gen = run_cli(["commit", "--repo", repo.path, "--staged"], [COMMIT_OK])
            self.assertEqual(code, 0)
            self.assertIn("스테이지된 변경이 없습니다", out)
            self.assertIsNone(gen, "빈 diff 로 AI 를 호출했다")

    def test_같은_상태라도_staged_없이는_진행한다(self):
        with TempRepo() as repo:
            repo.commit({"src/app.py": "x = 0\n"}, "base")
            repo.write("src/app.py", "x = 1\n")
            code, out, gen = run_cli(["commit", "--repo", repo.path], [COMMIT_OK])
            self.assertEqual(code, 0)
            self.assertIsNotNone(gen)
            self.assertNotIn("없습니다", out)

    def test_staged_일_때_프롬프트에_미스테이지_파일이_안_실린다(self):
        with TempRepo() as repo:
            repo.commit({"a.py": "0\n", "b.py": "0\n"}, "base")
            repo.write("a.py", "1\n")
            repo.add_all()
            repo.write("b.py", "2\n")  # 스테이지하지 않음
            _, out, gen = run_cli(["commit", "--repo", repo.path, "--staged"], [COMMIT_OK])
            self.assertIn("a.py", gen.prompts[0])
            self.assertNotIn("b.py", gen.prompts[0])
            self.assertIn("1개 파일 변경 감지", out)


class TestOutputLayout(BaseTest):
    """출력 구획 — 한글 제목에서도 구분선 폭이 맞는가."""

    @staticmethod
    def width(text):
        import unicodedata

        return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)

    def test_구분선이_한글_제목에서도_같은_폭이다(self):
        with TempRepo() as repo:
            dirty(repo)
            _, out, _ = run_cli(["commit", "--repo", repo.path, "--show-prompt"], [COMMIT_OK])
            rules = [ln for ln in out.splitlines() if ln.startswith("---") and ln.endswith("--")]
            self.assertTrue(rules)
            widths = {self.width(ln) for ln in rules}
            self.assertEqual(widths, {62}, f"구분선 폭이 제각각이다: {sorted(widths)}")


class TestSafeMode(BaseTest):
    SECRET = 'KEY = "sk-ant-abcdefghijklmnopqrstuvwx"\nmail = "me@example.com"\n'

    def test_기본값은_안전모드_ON_이고_프롬프트에_비밀이_없다(self):
        with TempRepo() as repo:
            repo.commit({"src/app.py": "x = 0\n"}, "base")
            repo.write("src/app.py", self.SECRET)
            _, out, gen = run_cli(["commit", "--repo", repo.path], [COMMIT_OK])
            self.assertIn("안전 모드 ON", out)
            self.assertNotIn("sk-ant-abcdefghijklmnopqrstuvwx", gen.prompts[0])
            self.assertNotIn("me@example.com", gen.prompts[0])
            self.assertIn("«MASKED:", gen.prompts[0])

    def test_no_safe_mode_는_원문을_그대로_보낸다(self):
        with TempRepo() as repo:
            repo.commit({"src/app.py": "x = 0\n"}, "base")
            repo.write("src/app.py", self.SECRET)
            _, out, gen = run_cli(["commit", "--repo", repo.path, "--no-safe-mode"], [COMMIT_OK])
            self.assertIn("안전 모드 OFF", out)
            self.assertIn("sk-ant-abcdefghijklmnopqrstuvwx", gen.prompts[0])

    def test_줄_수_상한_옵션이_프롬프트를_줄인다(self):
        with TempRepo() as repo:
            repo.commit({"src/big.py": "base\n"}, "base")
            repo.write("src/big.py", "\n".join(f"line{i}" for i in range(300)))
            _, out, gen = run_cli(
                ["commit", "--repo", repo.path, "--max-diff-lines", "20"], [COMMIT_OK]
            )
            self.assertIn("줄 수 상한", gen.prompts[0])
            self.assertNotIn("line299", gen.prompts[0])

    def test_파일_수_상한_옵션(self):
        with TempRepo() as repo:
            repo.commit({f"f{i}.py": "base\n" for i in range(6)}, "base")
            for i in range(6):
                repo.write(f"f{i}.py", f"x = {i}\n")
            _, out, gen = run_cli(["commit", "--repo", repo.path, "--max-files", "2"], [COMMIT_OK])
            self.assertIn("파일 2/6개", out)
            self.assertIn("파일 4개 생략", gen.prompts[0])


class TestOptions(BaseTest):
    def test_모델_temperature_max_tokens_가_전달된다(self):
        with TempRepo() as repo:
            dirty(repo)
            _, out, gen = run_cli(
                ["commit", "--repo", repo.path, "--model", "claude-haiku-4-5",
                 "--temperature", "0.7", "--max-tokens", "1234"],
                [COMMIT_OK],
            )
            self.assertEqual(gen.params.model, "claude-haiku-4-5")
            self.assertEqual(gen.params.temperature, 0.7)
            self.assertEqual(gen.params.max_tokens, 1234)
            self.assertIn("temperature=0.7", out)
            self.assertIn("max_tokens=1234", out)

    def test_PDF_예시의_홑대시_옵션도_받는다(self):
        with TempRepo() as repo:
            dirty(repo)
            _, _, gen = run_cli(
                ["commit", "--repo", repo.path, "-model", "claude-haiku-4-5",
                 "-temperature", "0.9", "-max-tokens", "999"],
                [COMMIT_OK],
            )
            self.assertEqual(gen.params.model, "claude-haiku-4-5")
            self.assertEqual(gen.params.temperature, 0.9)
            self.assertEqual(gen.params.max_tokens, 999)

    def test_temperature_미지원_모델은_경고하고_빼고_보낸다(self):
        with TempRepo() as repo:
            dirty(repo)
            from aigitgen.client import ModelParams

            params = ModelParams(model="claude-opus-5", temperature=0.7, temperature_explicit=True)
            self.assertFalse(params.sends_temperature)
            self.assertIn("미전송", params.describe())

    def test_범위를_벗어난_temperature_는_거부된다(self):
        with TempRepo() as repo:
            dirty(repo)
            code, out, _ = run_cli(["commit", "--repo", repo.path, "--temperature", "3"], [COMMIT_OK])
            self.assertEqual(code, 2)

    def test_show_prompt_는_실제_페이로드를_보여준다(self):
        with TempRepo() as repo:
            dirty(repo)
            _, out, gen = run_cli(["commit", "--repo", repo.path, "--show-prompt"], [COMMIT_OK])
            self.assertIn("===== system =====", out)
            self.assertIn("output_config.format", out)
            self.assertIn(gen.prompts[0][:40], out)

    def test_staged_옵션이_수집_범위를_바꾼다(self):
        with TempRepo() as repo:
            repo.commit({"staged.py": "a = 0\n", "later.py": "b = 0\n"}, "base")
            repo.write("staged.py", "a = 1\n")
            repo.add_all()
            repo.write("later.py", "b = 2\n")
            _, out, gen = run_cli(["commit", "--repo", repo.path, "--staged"], [COMMIT_OK])
            self.assertIn("git diff --cached", out)
            self.assertIn("staged.py", gen.prompts[0])


class TestConventionOption(BaseTest):
    def test_컨벤션_적용_전후로_결과_형식이_달라진다(self):
        with TempRepo() as repo:
            dirty(repo)
            with open(os.path.join(repo.path, config.CONFIG_FILENAME), "w", encoding="utf-8") as fp:
                json.dump(
                    {"conventions": {"team": {
                        "commit": {"prefixes": ["feat", "fix"], "scope": "required"},
                        "pr": {"extra_sections": ["Risk"], "checklist": ["로컬 테스트 통과"]},
                    }}},
                    fp, ensure_ascii=False,
                )
            _, before, _ = run_cli(["pr", "--repo", repo.path, "--no-retry"], [PR_OK])
            _, after, _ = run_cli(
                ["pr", "--repo", repo.path, "--convention", "team", "--no-retry"], [PR_OK]
            )
            self.assertNotIn("## Risk", before)
            self.assertIn("## Risk", after)
            self.assertIn("- [ ] 로컬 테스트 통과", after)
            self.assertIn("컨벤션 'team' 적용", after)

    def test_없는_컨벤션은_설정_오류로_끝난다(self):
        with TempRepo() as repo:
            dirty(repo)
            code, out, _ = run_cli(["commit", "--repo", repo.path, "--convention", "없음"], [COMMIT_OK])
            self.assertEqual(code, 2)


class TestDryRun(BaseTest):
    def test_dry_run_은_API_없이_동작한다(self):
        with TempRepo() as repo:
            dirty(repo)
            from aigitgen import cli

            import io
            from contextlib import redirect_stderr, redirect_stdout

            out_buf, log_buf = io.StringIO(), io.StringIO()
            with redirect_stdout(out_buf), redirect_stderr(log_buf):
                code = cli.main(["commit", "--repo", repo.path, "--dry-run"])
            self.assertEqual(code, 0)
            self.assertIn("AI API 를 호출하지 않았습니다", log_buf.getvalue())
            self.assertIn("API 호출 0회", log_buf.getvalue())
            self.assertIn("[DRY-RUN]", out_buf.getvalue())  # 초안 제목에 표시가 남는다


class TestConstraints(BaseTest):
    """과제 제약을 코드 자체로 검사한다 — 서술이 아니라 AST 로 증명한다."""

    PACKAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aigitgen")
    #: 이 도구가 실행해도 되는 git 하위 명령. 수집 전용이며 저장소를 바꾸지 않는다.
    ALLOWED_GIT = {"rev-parse", "status", "diff", "symbolic-ref"}

    def _modules(self):
        import ast

        for name in sorted(os.listdir(self.PACKAGE)):
            if name.endswith(".py"):
                with open(os.path.join(self.PACKAGE, name), encoding="utf-8") as fp:
                    yield name, ast.parse(fp.read(), name)

    def test_실행하는_git_하위_명령이_수집용으로_제한된다(self):
        """git push 등 원격 반영 명령은 호출조차 하지 않는다."""
        import ast

        seen = set()
        for name, tree in self._modules():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                target = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if target != "run_git":
                    continue
                if not node.args or not isinstance(node.args[0], (ast.List, ast.Tuple)):
                    continue
                first = node.args[0].elts[0]
                self.assertIsInstance(first, ast.Constant, f"{name}: git 하위 명령이 리터럴이 아니다")
                seen.add(first.value)
        self.assertTrue(seen, "run_git 호출을 하나도 찾지 못했다 — 테스트가 무의미해졌다")
        self.assertTrue(
            seen <= self.ALLOWED_GIT,
            f"허용되지 않은 git 하위 명령: {sorted(seen - self.ALLOWED_GIT)}",
        )

    def test_외부_프로세스_실행은_gitctx_한_곳에만_있다(self):
        """subprocess 를 여기저기서 쓰면 위 화이트리스트 검사를 우회할 수 있다."""
        import ast

        for name, tree in self._modules():
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                if any(n.split(".")[0] in ("subprocess", "os") and n == "subprocess" for n in names):
                    self.assertEqual(name, "gitctx.py", f"{name} 이 subprocess 를 직접 쓴다")

    def test_직접_HTTP_를_열지_않는다(self):
        """네트워크는 공식 SDK 를 통해서만 나간다. GitHub API 연동은 구현하지 않는다."""
        import ast

        banned = {"requests", "urllib", "urllib.request", "http.client", "socket", "httpx", "httpx2"}
        for name, tree in self._modules():
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name, banned, f"{name} 이 {alias.name} 을 직접 임포트한다")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module, banned, f"{name} 이 {node.module} 을 직접 임포트한다")

    def test_API_Key_가_코드에_하드코딩돼_있지_않다(self):
        import ast
        import re

        looks_like_key = re.compile(r"sk-ant-[A-Za-z0-9]{16,}|\bsk-[A-Za-z0-9]{20,}")
        for name, tree in self._modules():
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertIsNone(
                        looks_like_key.search(node.value), f"{name} 에 API Key 형태 문자열이 있다"
                    )

    def test_API_Key_는_환경변수에서만_읽는다(self):
        from aigitgen import client
        from aigitgen.errors import ApiKeyMissingError

        self.assertEqual(client.resolve_api_key({"AI_API_KEY": "value"}), "value")
        self.assertEqual(
            client.resolve_api_key({"ANTHROPIC_API_KEY": "a", "AI_API_KEY": "b"}), "a"
        )
        with self.assertRaises(ApiKeyMissingError) as ctx:
            client.resolve_api_key({})
        self.assertIn("환경변수", str(ctx.exception))


class TestTitleRecommendation(BaseTest):
    """R5-2 는 50자(권장)와 72자(상한)를 구분한다 — 실행 단계에서 둘 다 드러나야 한다."""

    #: `feat(cli): ` 11자 + 55자 = 66자. 권장 50자는 넘고 상한 72자는 안 넘는 구간.
    BETWEEN = dict(COMMIT_OK, subject="가" * 55)

    def test_권장_초과_상한_이내면_경고하되_자르지_않는다(self):
        with TempRepo() as repo:
            dirty(repo)
            code, out, _ = run_cli(["commit", "--repo", repo.path, "--no-retry"], [self.BETWEEN])
            self.assertEqual(code, 0)
            self.assertIn("권장 50자를 넘었습니다", out)
            title = next(ln for ln in out.split("Commit Message")[1].splitlines() if ln.startswith("feat"))
            self.assertEqual(len(title), 66, "권장 초과는 경고일 뿐 — 제목을 잘라서는 안 된다")

    def test_권장_이내면_아무_말도_하지_않는다(self):
        with TempRepo() as repo:
            dirty(repo)
            _, out, _ = run_cli(["commit", "--repo", repo.path], [COMMIT_OK])
            self.assertNotIn("권장", out)

    def test_권장_초과는_재생성을_부르지_않는다(self):
        """경고는 돈을 쓰지 않는다 — 50자 때문에 호출 횟수(C1-2)를 소모하면 안 된다."""
        with TempRepo() as repo:
            dirty(repo)
            _, out, gen = run_cli(["commit", "--repo", repo.path], [self.BETWEEN])
            self.assertEqual(gen.calls, 1)
            self.assertNotIn("재생성", out)


class TestStreamSeparation(BaseTest):
    """로그는 stderr, 산출물은 stdout — `python main.py commit > msg.txt` 가 초안만 담는다."""

    def test_stdout_에는_로그_접두어가_없다(self):
        with TempRepo() as repo:
            dirty(repo)
            _, out, log, _ = run_cli_streams(["commit", "--repo", repo.path], [COMMIT_OK])
            self.assertIn("Commit Message", out)
            self.assertIn("feat(cli): 커밋 메시지 자동 생성 기능 추가", out)
            for tag in ("[INFO]", "[WARN]", "[DONE]", "[ERROR]"):
                self.assertNotIn(tag, out, f"{tag} 가 산출물 스트림으로 샜다")
            self.assertIn("[INFO]", log)
            self.assertIn("[DONE]", log)

    def test_오류도_stderr_로_가고_stdout_은_비어_있다(self):
        with TempRepo() as repo:
            dirty(repo)
            code, out, log, _ = run_cli_streams(
                ["commit", "--repo", repo.path, "--convention", "없음"], [COMMIT_OK]
            )
            self.assertEqual(code, 2)
            self.assertIn("[ERROR]", log)
            self.assertEqual(out, "", "실패했는데 stdout 에 무언가 남으면 파이프가 쓰레기를 받는다")


if __name__ == "__main__":
    unittest.main()

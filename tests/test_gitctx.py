"""Git 수집 계층 검증 — 요구사항 1(Git 변경 사항 수집)."""

import os
import unittest

from helpers import BaseTest, TempRepo, git

from aigitgen import gitctx
from aigitgen.errors import GitError


class TestRepoDetection(BaseTest):
    def test_저장소가_아니면_안내와_함께_실패한다(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(GitError) as ctx:
                gitctx.collect(tmp)
            self.assertIn("Git 저장소가 아닙니다", str(ctx.exception))

    def test_없는_경로는_경로_오류로_구분된다(self):
        with self.assertRaises(GitError) as ctx:
            gitctx.collect("/definitely/not/here")
        self.assertIn("경로를 찾을 수 없습니다", str(ctx.exception))

    def test_하위_디렉터리에서_실행해도_루트를_찾는다(self):
        with TempRepo() as repo:
            sub = os.path.join(repo.path, "src", "deep")
            os.makedirs(sub)
            self.assertEqual(gitctx.collect(sub).root, repo.path)


class TestCollect(BaseTest):
    def test_변경이_없으면_is_empty(self):
        with TempRepo() as repo:
            ctx = gitctx.collect(repo.path)
            self.assertTrue(ctx.is_empty)
            self.assertEqual(ctx.files, [])
            self.assertEqual(ctx.diff_lines, 0)

    def test_수정된_파일이_status와_diff에_모두_잡힌다(self):
        with TempRepo() as repo:
            repo.write("README.md", "# temp\nchanged\n")
            ctx = gitctx.collect(repo.path)
            self.assertFalse(ctx.is_empty)
            self.assertEqual([f.path for f in ctx.files], ["README.md"])
            self.assertIn("changed", ctx.diff_text)
            self.assertEqual(ctx.diff_command, "git diff HEAD")

    def test_추적되지_않은_파일은_이름만_잡힌다(self):
        with TempRepo() as repo:
            repo.write("new.txt", "secret content\n")
            ctx = gitctx.collect(repo.path)
            self.assertEqual([f.path for f in ctx.untracked_files], ["new.txt"])
            # 내용은 diff 에 실리지 않는다 — 의도된 한계
            self.assertNotIn("secret content", ctx.diff_text)

    def test_staged_only_는_스테이지된_변경만_본다(self):
        with TempRepo() as repo:
            repo.write("staged.txt", "A\n")
            repo.add_all()
            repo.write("unstaged.txt", "B\n")
            ctx = gitctx.collect(repo.path, staged_only=True)
            self.assertEqual(ctx.diff_command, "git diff --cached")
            self.assertIn("staged.txt", ctx.diff_text)
            self.assertNotIn("+B", ctx.diff_text)

    def test_최초_커밋_전에도_동작한다(self):
        with TempRepo(initial_commit=False) as repo:
            repo.write("first.py", "x = 1\n")
            repo.add_all()
            ctx = gitctx.collect(repo.path)
            # HEAD 가 없으므로 --cached 로 내려간다
            self.assertEqual(ctx.diff_command, "git diff --cached")
            self.assertIn("first.py", ctx.diff_text)
            self.assertEqual(ctx.branch, "main")

    def test_브랜치_이름을_읽는다(self):
        with TempRepo() as repo:
            git(repo.path, "checkout", "-q", "-b", "feature/x")
            repo.write("a.txt", "a\n")
            self.assertEqual(gitctx.collect(repo.path).branch, "feature/x")


class TestParseStatus(BaseTest):
    def test_상태_코드를_해석한다(self):
        files = gitctx.parse_status("M  a.py\n?? b.py\n D c.py\nR  old.py -> new.py\n")
        self.assertEqual([f.path for f in files], ["a.py", "b.py", "c.py", "new.py"])
        self.assertTrue(files[0].staged)
        self.assertTrue(files[1].untracked)
        self.assertFalse(files[2].staged)
        self.assertIn("추가", gitctx.parse_status("A  x.py\n")[0].label)


if __name__ == "__main__":
    unittest.main()

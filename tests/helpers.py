"""테스트 공용 도구 — 일회용 Git 저장소와 가짜 생성기."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aigitgen import cli  # noqa: E402
from aigitgen.client import Generator, ModelParams, Usage  # noqa: E402


def git(repo: str, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    proc = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} 실패: {proc.stderr}")
    return proc.stdout


class TempRepo:
    """`with TempRepo() as repo:` 로 쓰는 일회용 저장소."""

    def __init__(self, initial_commit: bool = True) -> None:
        self.initial_commit = initial_commit
        self._tmp: Optional[tempfile.TemporaryDirectory] = None
        self.path = ""

    def __enter__(self) -> "TempRepo":
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.realpath(self._tmp.name)
        git(self.path, "init", "-q", "-b", "main", ".")
        if self.initial_commit:
            self.write("README.md", "# temp\n")
            git(self.path, "add", "-A")
            git(self.path, "commit", "-qm", "init")
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._tmp:
            self._tmp.cleanup()

    def write(self, relpath: str, content: str) -> str:
        full = os.path.join(self.path, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fp:
            fp.write(content)
        return full

    def add_all(self) -> None:
        git(self.path, "add", "-A")

    def commit(self, files: Dict[str, str], message: str = "add") -> None:
        """파일을 만들고 곧바로 커밋한다. 이후 수정분이 diff 에 잡히게 하는 준비 단계."""
        for relpath, content in files.items():
            self.write(relpath, content)
        self.add_all()
        git(self.path, "commit", "-qm", message)


class FakeGenerator(Generator):
    """API 를 부르지 않고 미리 준비한 응답을 순서대로 돌려준다."""

    name = "fake"

    def __init__(self, params: ModelParams, payloads: Sequence[Dict[str, Any]]) -> None:
        super().__init__(params)
        self.payloads: List[Dict[str, Any]] = [dict(p) for p in payloads]
        self.prompts: List[str] = []

    def generate(self, system: str, user: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        self.prompts.append(user)
        self.usage.add(Usage(input_tokens=100, output_tokens=20))
        index = min(self.calls - 1, len(self.payloads) - 1)
        return self.payloads[index]


def run_cli(argv: Sequence[str], payloads: Optional[Sequence[Dict[str, Any]]] = None):
    """CLI 를 실행하고 (종료코드, 표준출력, 생성기) 를 돌려준다."""
    holder: Dict[str, Any] = {}

    def factory(params: ModelParams, ctx: Any, args: Any) -> Generator:
        gen = FakeGenerator(params, payloads or [{}])
        holder["generator"] = gen
        return gen

    buffer = io.StringIO()
    # stderr 로 나가는 [ERROR] 줄도 같은 버퍼에 모아 테스트가 함께 검사할 수 있게 한다.
    with redirect_stdout(buffer), redirect_stderr(buffer):
        code = cli.main(list(argv), factory=factory)
    return code, buffer.getvalue(), holder.get("generator")


class BaseTest(unittest.TestCase):
    maxDiff = None

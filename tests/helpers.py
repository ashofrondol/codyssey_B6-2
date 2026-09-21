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


def _fake_factory(payloads: Optional[Sequence[Dict[str, Any]]]):
    """(factory, 생성기를 꺼내 볼 상자) — 두 `run_cli*` 가 같은 조립을 쓰게 한다."""
    holder: Dict[str, Any] = {}

    def factory(params: ModelParams, ctx: Any, args: Any) -> Generator:
        gen = FakeGenerator(params, payloads or [{}])
        holder["generator"] = gen
        return gen

    return factory, holder


def run_cli(argv: Sequence[str], payloads: Optional[Sequence[Dict[str, Any]]] = None):
    """CLI 를 실행하고 (종료코드, 표준출력, 생성기) 를 돌려준다.

    로그(stderr)와 산출물(stdout)을 **한 버퍼에 섞어** 돌려준다. 터미널에서 사람이 보는
    모습이 이쪽이고, 대부분의 테스트는 "어딘가에 이 문구가 나왔는가"만 보면 된다.
    스트림이 실제로 나뉘었는지 검사하려면 :func:`run_cli_streams` 를 쓴다.
    """
    factory, holder = _fake_factory(payloads)
    buffer = io.StringIO()
    with redirect_stdout(buffer), redirect_stderr(buffer):
        code = cli.main(list(argv), factory=factory)
    return code, buffer.getvalue(), holder.get("generator")


def run_cli_streams(argv: Sequence[str], payloads: Optional[Sequence[Dict[str, Any]]] = None):
    """CLI 를 실행하고 (종료코드, stdout, stderr, 생성기) 를 **나눠서** 돌려준다."""
    factory, holder = _fake_factory(payloads)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv), factory=factory)
    return code, out.getvalue(), err.getvalue(), holder.get("generator")


class BaseTest(unittest.TestCase):
    maxDiff = None

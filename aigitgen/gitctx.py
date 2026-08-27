"""Git 변경 사항 수집.

과제 제약에 따라 수집 범위는 `git status` / `git diff` 로 한정한다.
`git push`, GitHub PR 생성 같은 원격 반영 기능은 이 도구에 없다.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import List, Sequence

from .errors import GitError

#: git 명령 1건당 최대 대기 시간(초). 거대 저장소에서도 멈추지 않도록 상한을 둔다.
GIT_TIMEOUT = 30

#: `git status --porcelain` 의 XY 코드 → 사람이 읽는 라벨
_STATUS_LABEL = {
    "M": "수정",
    "A": "추가",
    "D": "삭제",
    "R": "이름변경",
    "C": "복사",
    "U": "충돌",
    "?": "미추적",
    "!": "무시됨",
}


@dataclass(frozen=True)
class ChangedFile:
    """`git status --porcelain=v1` 한 줄을 해석한 결과."""

    path: str
    index_status: str  # 스테이지 영역 상태 (X)
    work_status: str  # 작업 트리 상태 (Y)

    @property
    def staged(self) -> bool:
        return self.index_status not in (" ", "?")

    @property
    def untracked(self) -> bool:
        return self.index_status == "?"

    @property
    def label(self) -> str:
        """`추가(스테이지됨)` 같은 한글 라벨."""
        code = self.index_status if self.staged else self.work_status
        name = _STATUS_LABEL.get(code, code)
        return f"{name}(스테이지됨)" if self.staged else name

    def __str__(self) -> str:  # pragma: no cover - 표시용
        return f"{self.path} [{self.label}]"


@dataclass
class GitContext:
    """AI 에게 넘길 Git 변경 사항 묶음."""

    root: str
    branch: str
    status_text: str
    files: List[ChangedFile] = field(default_factory=list)
    diff_text: str = ""
    #: diff 를 수집할 때 실제로 실행한 명령(README/로그에 그대로 노출한다)
    diff_command: str = ""
    #: `--staged` 로 스테이지된 변경만 수집했는가
    staged_only: bool = False

    @property
    def diff_lines(self) -> int:
        return 0 if not self.diff_text else len(self.diff_text.splitlines())

    @property
    def tracked_files(self) -> List[ChangedFile]:
        return [f for f in self.files if not f.untracked]

    @property
    def untracked_files(self) -> List[ChangedFile]:
        return [f for f in self.files if f.untracked]

    @property
    def relevant_files(self) -> List[ChangedFile]:
        """이번 수집 범위에 해당하는 변경 파일.

        `--staged` 일 때 작업 트리에만 있는 변경까지 세면, diff 가 0줄인데도
        "변경이 있다"고 판단해 근거 없이 AI 를 호출하게 된다.
        """
        return [f for f in self.files if f.staged] if self.staged_only else self.files

    @property
    def is_empty(self) -> bool:
        """커밋 메시지를 만들 근거가 하나도 없는 상태인가."""
        return not self.relevant_files and not self.diff_text.strip()

    @property
    def scope_label(self) -> str:
        """사용자에게 보여줄 수집 범위 이름."""
        return "스테이지된 변경" if self.staged_only else "변경 사항"


def run_git(args: Sequence[str], cwd: str) -> str:
    """git 하위 명령을 실행하고 stdout 을 돌려준다.

    실패하면 stderr 를 담은 :class:`GitError` 를 던진다.
    """
    if not os.path.isdir(cwd):
        raise GitError(f"경로를 찾을 수 없습니다: {cwd}")
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
        )
    except FileNotFoundError as exc:  # git 자체가 없음
        raise GitError("git 명령을 찾을 수 없습니다. Git 이 설치되어 있는지 확인하세요.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} 가 {GIT_TIMEOUT}초 안에 끝나지 않았습니다.") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        first = detail[0] if detail else f"종료 코드 {proc.returncode}"
        raise GitError(f"git {' '.join(args)} 실패: {first}")
    return proc.stdout


def repo_root(start: str) -> str:
    """Git 저장소 루트를 찾는다. 저장소가 아니면 :class:`GitError`."""
    try:
        out = run_git(["rev-parse", "--show-toplevel"], cwd=start)
    except GitError as exc:
        raise GitError(
            "여기는 Git 저장소가 아닙니다. Git 이 초기화된 프로젝트 루트에서 실행하세요. "
            f"(원래 오류: {exc})"
        ) from exc
    return out.strip()


def current_branch(root: str) -> str:
    """현재 브랜치 이름. 커밋이 하나도 없으면 예정 브랜치 이름을 돌려준다."""
    try:
        name = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root).strip()
    except GitError:
        name = ""  # 최초 커밋 전에는 HEAD 를 풀 수 없다
    if name and name != "HEAD":
        return name
    # 최초 커밋 전이라 HEAD 가 아직 없는 경우
    ref = run_git(["symbolic-ref", "--quiet", "HEAD"], cwd=root).strip()
    return ref.rsplit("/", 1)[-1] if ref else "HEAD"


def _has_commit(root: str) -> bool:
    try:
        run_git(["rev-parse", "--verify", "HEAD"], cwd=root)
    except GitError:
        return False
    return True


def parse_status(porcelain: str) -> List[ChangedFile]:
    """`git status --porcelain=v1` 출력을 :class:`ChangedFile` 목록으로."""
    files: List[ChangedFile] = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        index_status, work_status, path = line[0], line[1], line[3:]
        # `R  old -> new` 형태는 새 경로만 남긴다
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(ChangedFile(path=path.strip('"'), index_status=index_status, work_status=work_status))
    return files


def collect(cwd: str = ".", staged_only: bool = False) -> GitContext:
    """저장소의 변경 사항을 모아 :class:`GitContext` 로 돌려준다.

    - ``staged_only=True``  → ``git diff --cached`` (스테이지된 것만)
    - ``staged_only=False`` → ``git diff HEAD`` (스테이지 + 미스테이지 전부).
      최초 커밋 전이라 ``HEAD`` 가 없으면 ``git diff --cached`` 로 내려간다.

    추적되지 않는 파일(untracked)의 *내용* 은 diff 에 잡히지 않는다.
    파일 이름만 status 로 전달하며, 이는 README 에 명시한 의도된 한계다.
    """
    root = repo_root(cwd)
    status_porcelain = run_git(["status", "--porcelain=v1"], cwd=root)
    files = parse_status(status_porcelain)
    status_text = run_git(["status", "--short", "--branch"], cwd=root).strip()
    if staged_only:
        # 범위를 좁혀 실행했는데 스테이지되지 않은 파일 이름까지 프롬프트로 나가면
        # 모델이 이번 커밋과 무관한 파일을 언급한다. 브랜치 줄과 스테이지된 줄만 남긴다.
        status_text = "\n".join(
            line for line in status_text.splitlines()
            if line.startswith("##") or (len(line) > 1 and line[0] not in " ?")
        )

    if staged_only:
        diff_args = ["diff", "--cached"]
    elif _has_commit(root):
        diff_args = ["diff", "HEAD"]
    else:
        diff_args = ["diff", "--cached"]

    diff_text = run_git(diff_args, cwd=root)

    return GitContext(
        root=root,
        branch=current_branch(root),
        status_text=status_text,
        files=files,
        diff_text=diff_text,
        diff_command="git " + " ".join(diff_args),
        staged_only=staged_only,
    )

"""터미널 출력 — 사용자가 결과를 검토할 수 있도록 구획을 나눠 보여준다.

**스트림을 두 개로 나눈다.**

    진행 상황(`[INFO]/[WARN]/[DONE]/[ERROR]`, 위반 목록)  →  stderr
    산출물(구분선으로 감싼 커밋 메시지·PR 초안)            →  stdout

터미널에서는 둘 다 그대로 보이므로 화면은 달라지지 않는다.
달라지는 것은 리다이렉트할 때다 — `python main.py commit > msg.txt` 가
로그를 섞지 않고 초안만 담는다. 명세 0.8 의 학습 질문("그대로 `git commit -F -`
에 파이프할 수 있나")에 대한 답이 이 분리다.

경계를 이 한 파일 안에 가둔 이유도 같다. `cli.py` 는 어느 스트림인지 모르고
`render.info()` / `render.block()` 만 부른다.
"""

from __future__ import annotations

import sys
import unicodedata
from typing import Iterable, Optional, TextIO

WIDTH = 62


def display_width(text: str) -> int:
    """터미널 표시폭. 한글·한자 등 동아시아 문자는 두 칸을 차지한다.

    `len()` 으로 여백을 계산하면 한글이 든 제목에서 구분선이 어긋난다.
    """
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _write(stream: TextIO, text: str) -> None:
    stream.write(text + "\n")
    stream.flush()  # 두 스트림이 한 파일로 합쳐져도(`2>&1`) 순서가 유지되도록


def _log(stream: Optional[TextIO], text: str) -> None:
    """진행 상황 → stderr.

    기본값을 인자 기본값으로 굳히면 `redirect_stderr` 같은 교체가 먹지 않는다.
    그래서 호출 시점에 `sys.stderr` 를 다시 읽는다.
    """
    _write(stream if stream is not None else sys.stderr, text)


def _emit(stream: Optional[TextIO], text: str) -> None:
    """산출물 → stdout. 기본값을 늦게 읽는 이유는 `_log` 와 같다."""
    _write(stream if stream is not None else sys.stdout, text)


def info(message: str, stream: Optional[TextIO] = None) -> None:
    _log(stream, f"[INFO] {message}")


def warn(message: str, stream: Optional[TextIO] = None) -> None:
    _log(stream, f"[WARN] {message}")


def done(message: str, stream: Optional[TextIO] = None) -> None:
    _log(stream, f"[DONE] {message}")


def error(message: str, stream: Optional[TextIO] = None) -> None:
    _log(stream, f"[ERROR] {message}")


def bullets(lines: Iterable[str], prefix: str = "       - ", stream: Optional[TextIO] = None) -> None:
    """로그 줄에 딸린 세부 목록. 로그이므로 로그와 같은 스트림으로 나간다."""
    for line in lines:
        _log(stream, f"{prefix}{line}")


def rule(title: str = "", stream: Optional[TextIO] = None) -> None:
    """`------- 제목 -------` 형태의 구분선."""
    if not title:
        _emit(stream, "-" * WIDTH)
        return
    label = f" {title} "
    pad = max(0, WIDTH - display_width(label))
    left = pad // 2
    _emit(stream, "-" * left + label + "-" * (pad - left))


def block(title: str, content: str, stream: Optional[TextIO] = None) -> None:
    """구분선으로 감싼 결과 블록 — 사용자가 복사해 갈 산출물."""
    _emit(stream, "")
    rule(title, stream)
    _emit(stream, content)
    rule("", stream)

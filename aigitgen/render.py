"""터미널 출력 — 사용자가 결과를 검토할 수 있도록 구획을 나눠 보여준다."""

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


def _write(stream: Optional[TextIO], text: str) -> None:
    # 기본값을 인자로 굳히면 redirect_stdout 같은 교체가 먹지 않는다.
    # 그래서 호출 시점에 sys.stdout / sys.stderr 를 다시 읽는다.
    out = stream if stream is not None else sys.stdout
    out.write(text + "\n")
    out.flush()


def info(message: str, stream: Optional[TextIO] = None) -> None:
    _write(stream, f"[INFO] {message}")


def warn(message: str, stream: Optional[TextIO] = None) -> None:
    _write(stream, f"[WARN] {message}")


def done(message: str, stream: Optional[TextIO] = None) -> None:
    _write(stream, f"[DONE] {message}")


def error(message: str, stream: Optional[TextIO] = None) -> None:
    _write(stream if stream is not None else sys.stderr, f"[ERROR] {message}")


def rule(title: str = "", stream: Optional[TextIO] = None) -> None:
    """`------- 제목 -------` 형태의 구분선."""
    if not title:
        _write(stream, "-" * WIDTH)
        return
    label = f" {title} "
    pad = max(0, WIDTH - display_width(label))
    left = pad // 2
    _write(stream, "-" * left + label + "-" * (pad - left))


def block(title: str, content: str, stream: Optional[TextIO] = None) -> None:
    """구분선으로 감싼 결과 블록."""
    _write(stream, "")
    rule(title, stream)
    _write(stream, content)
    rule("", stream)


def bullets(lines: Iterable[str], prefix: str = "       - ", stream: Optional[TextIO] = None) -> None:
    for line in lines:
        _write(stream, f"{prefix}{line}")

"""안전 모드(safe-mode) — 프롬프트로 나가기 전에 diff 를 손본다.

과제가 요구한 두 가지 대응을 **둘 다** 구현한다.

(A) 마스킹  : API Key/토큰/이메일 같은 패턴을 정규표현식으로 찾아 가린다.
(B) 전송 제한: 파일 수와 줄 수에 상한을 두고 초과분을 잘라낸다(기본 10개 파일 / 200줄).

두 정책 모두 CLI 옵션과 `.ai-gitgen.json` 으로 숫자를 바꿀 수 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Pattern, Tuple

#: 기본 마스킹 규칙 — (이름, 정규식, 치환 문구)
#: 순서가 중요하다. 더 구체적인 규칙(공급자별 키)을 먼저 둔다.
DEFAULT_PATTERNS: List[Tuple[str, str, str]] = [
    ("private-key-block",
     r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
     "«MASKED:PRIVATE_KEY»"),
    ("anthropic-key", r"sk-ant-[A-Za-z0-9_\-]{16,}", "«MASKED:ANTHROPIC_KEY»"),
    ("openai-key", r"\bsk-[A-Za-z0-9]{20,}\b", "«MASKED:API_KEY»"),
    ("github-token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", "«MASKED:GITHUB_TOKEN»"),
    ("aws-access-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "«MASKED:AWS_ACCESS_KEY»"),
    ("google-api-key", r"\bAIza[0-9A-Za-z_\-]{30,}", "«MASKED:GOOGLE_API_KEY»"),
    ("slack-token", r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b", "«MASKED:SLACK_TOKEN»"),
    ("jwt", r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b", "«MASKED:JWT»"),
    ("bearer", r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}", "Bearer «MASKED:TOKEN»"),
    # KEY = "값" / password: '값' 같은 대입문의 오른쪽만 가린다.
    ("assignment",
     r"(?i)\b([A-Za-z0-9_\-]*(?:pass(?:word|wd)?|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)"
     r"[A-Za-z0-9_\-]*)(\s*[:=]{1,2}>?\s*)(['\"])((?!«)[^'\"\n]{4,})\3",
     r"\1\2\3«MASKED:SECRET»\3"),
    ("email", r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", "«MASKED:EMAIL»"),
    # 한국 주민등록번호 형태
    ("kr-rrn", r"\b\d{6}-[1-4]\d{6}\b", "«MASKED:RRN»"),
]

#: diff 안에서 한 파일 블록이 시작되는 지점
_FILE_HEADER = re.compile(r"^diff --git ", re.MULTILINE)


@dataclass
class SafetyPolicy:
    """안전 모드 정책. 숫자는 전부 조정 가능하다."""

    enabled: bool = True
    max_files: int = 10
    max_diff_lines: int = 200
    patterns: List[Tuple[str, str, str]] = field(default_factory=lambda: list(DEFAULT_PATTERNS))

    def compiled(self) -> List[Tuple[str, Pattern[str], str]]:
        return [(name, re.compile(rx), repl) for name, rx, repl in self.patterns]


@dataclass
class RedactionReport:
    """무엇을 얼마나 가리고 잘랐는지에 대한 기록. 로그로 그대로 출력한다."""

    enabled: bool = True
    masked: Dict[str, int] = field(default_factory=dict)
    files_total: int = 0
    files_kept: int = 0
    lines_before: int = 0
    lines_after: int = 0

    @property
    def masked_total(self) -> int:
        return sum(self.masked.values())

    @property
    def files_dropped(self) -> int:
        return max(0, self.files_total - self.files_kept)

    @property
    def lines_dropped(self) -> int:
        return max(0, self.lines_before - self.lines_after)

    def summary(self) -> str:
        if not self.enabled:
            return "안전 모드 OFF — diff 를 가공하지 않고 그대로 전송합니다."
        parts = [
            f"마스킹 {self.masked_total}건",
            f"파일 {self.files_kept}/{self.files_total}개",
            f"줄 {self.lines_after}/{self.lines_before}줄",
        ]
        if self.masked:
            detail = ", ".join(f"{k}×{v}" for k, v in sorted(self.masked.items()))
            parts.append(f"규칙별({detail})")
        return "안전 모드 ON — " + " · ".join(parts)


def split_by_file(diff_text: str) -> List[str]:
    """diff 텍스트를 파일 단위 블록으로 나눈다.

    `diff --git` 헤더가 없는 출력(예: `git diff --stat`)은 통째로 한 덩어리로 본다.
    """
    if not diff_text:
        return []
    positions = [m.start() for m in _FILE_HEADER.finditer(diff_text)]
    if not positions:
        return [diff_text]
    blocks = []
    if positions[0] > 0:  # 헤더 앞에 붙은 잔여 텍스트 보존
        blocks.append(diff_text[: positions[0]])
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(diff_text)
        blocks.append(diff_text[start:end])
    return blocks


def mask(text: str, policy: SafetyPolicy) -> Tuple[str, Dict[str, int]]:
    """정규식 규칙으로 민감정보를 가린다. (가린 텍스트, 규칙별 건수)."""
    counts: Dict[str, int] = {}
    for name, rx, repl in policy.compiled():
        text, n = rx.subn(repl, text)
        if n:
            counts[name] = counts.get(name, 0) + n
    return text, counts


def truncate(diff_text: str, policy: SafetyPolicy) -> Tuple[str, int, int, int]:
    """파일 수·줄 수 상한을 적용한다.

    반환: (잘라낸 diff, 전체 파일 수, 남긴 파일 수, 남긴 줄 수)
    """
    blocks = split_by_file(diff_text)
    files_total = len(blocks)
    kept_blocks: List[str] = []
    kept_lines = 0
    files_kept = 0

    for block in blocks[: policy.max_files]:
        lines = block.splitlines()
        room = policy.max_diff_lines - kept_lines
        if room <= 0:
            break
        if len(lines) > room:
            lines = lines[:room]
            lines.append(f"... (줄 수 상한 {policy.max_diff_lines}줄 초과분 생략)")
        kept_blocks.append("\n".join(lines))
        kept_lines += len(lines)
        files_kept += 1

    if files_total > files_kept:
        kept_blocks.append(f"... (파일 {files_total - files_kept}개 생략 — 상한 {policy.max_files}개)")

    text = "\n".join(kept_blocks)
    return text, files_total, files_kept, len(text.splitlines()) if text else 0


def apply(diff_text: str, policy: SafetyPolicy) -> Tuple[str, RedactionReport]:
    """정책을 적용해 (전송할 diff, 보고서) 를 만든다.

    안전 모드가 꺼져 있으면 원문을 그대로 돌려주되 보고서에는 그 사실을 남긴다.
    """
    lines_before = len(diff_text.splitlines()) if diff_text else 0
    if not policy.enabled:
        return diff_text, RedactionReport(
            enabled=False,
            files_total=len(split_by_file(diff_text)),
            files_kept=len(split_by_file(diff_text)),
            lines_before=lines_before,
            lines_after=lines_before,
        )

    # 순서가 중요하다: 먼저 가리고(A), 그 다음 자른다(B).
    # 반대로 하면 잘려나간 구간의 비밀은 검사조차 되지 않는다.
    masked_text, counts = mask(diff_text, policy)
    truncated, files_total, files_kept, lines_after = truncate(masked_text, policy)

    return truncated, RedactionReport(
        enabled=True,
        masked=counts,
        files_total=files_total,
        files_kept=files_kept,
        lines_before=lines_before,
        lines_after=lines_after,
    )

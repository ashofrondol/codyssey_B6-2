"""출력 형식 검증과 다듬기.

과제는 "검증 후 재생성 또는 후처리 중 택1" 을 요구하지만, 이 도구는 둘 다 한다.

    생성 → 검증 → (위반 있으면) 위반 목록을 붙여 1회 재생성 → 검증 → 후처리 → 출력

재생성은 최대 1회이므로 한 번 실행에 API 호출은 많아야 2회다(과제 제약).
후처리 단계는 API 를 부르지 않으며, **없는 사실을 지어내지 않는다** —
비어 있는 칸은 Git 에서 직접 읽은 사실(파일 이름, diff 줄 수)로만 채운다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

from .config import REQUIRED_PR_SECTIONS, Convention
from .gitctx import GitContext
from .prompts import _section_key

#: 불릿 앞에 모델이 붙였을 수 있는 기호들
_BULLET_MARKERS = ("- ", "* ", "• ", "· ", "– ")

#: 제목을 줄일 때 subject 에 최소한 남겨 두려는 글자 수.
#: 이보다 좁아지면 subject 대신 scope 를 먼저 줄인다.
MIN_SUBJECT = 12


def _one_line(text: str) -> str:
    """줄바꿈을 없애고 공백을 정리해 한 줄로 만든다."""
    return " ".join(str(text).split())


def _clean_bullet(text: str) -> str:
    line = _one_line(text)
    for marker in _BULLET_MARKERS:
        if line.startswith(marker):
            line = line[len(marker) :]
            break
    return line.strip()


def _truncate(text: str, limit: int) -> str:
    """`limit` 자 이내로 줄인다. 가능하면 단어 경계에서 자른다."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    # 너무 많이 잘려나가면(절반 미만) 그냥 글자 단위로 자른다 — 한국어는 공백이 드물다.
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,·-–—")


@dataclass
class CommitDraft:
    type: str = ""
    scope: str = ""
    subject: str = ""
    body_bullets: List[str] = field(default_factory=list)
    files_mentioned: List[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        head = f"{self.type}({self.scope})" if self.scope else self.type
        return f"{head}: {self.subject}" if head else self.subject

    def render(self) -> str:
        """실제로 복사해 쓸 커밋 메시지 전문."""
        lines = [self.title]
        if self.body_bullets or self.files_mentioned:
            lines.append("")
            lines.extend(f"- {b}" for b in self.body_bullets)
            if self.files_mentioned:
                lines.append(f"- 변경 파일: {', '.join(self.files_mentioned)}")
        return "\n".join(lines)


@dataclass
class PrDraft:
    title: str = ""
    #: (헤더, 불릿 목록) 순서 보존
    sections: List[Tuple[str, List[str]]] = field(default_factory=list)
    checklist: List[str] = field(default_factory=list)

    def section(self, heading: str) -> List[str]:
        for name, bullets in self.sections:
            if name == heading:
                return bullets
        return []

    def render_body(self) -> str:
        parts: List[str] = []
        for heading, bullets in self.sections:
            block = [f"## {heading}"]
            block.extend(f"- {b}" for b in bullets)
            parts.append("\n".join(block))
        if self.checklist:
            block = ["## Checklist"]
            block.extend(f"- [ ] {item}" for item in self.checklist)
            parts.append("\n".join(block))
        return "\n\n".join(parts)


def commit_from_payload(data: Dict[str, Any], conv: Convention) -> CommitDraft:
    """모델 응답(dict)을 :class:`CommitDraft` 로. 타입이 어긋나도 죽지 않게 방어한다."""
    return CommitDraft(
        type=_one_line(data.get("type", "")),
        scope=_one_line(data.get("scope", "")),
        subject=_one_line(data.get("subject", "")),
        body_bullets=[_clean_bullet(b) for b in _as_list(data.get("body_bullets")) if _clean_bullet(b)],
        files_mentioned=[_one_line(f) for f in _as_list(data.get("files_mentioned")) if _one_line(f)],
    )


def pr_from_payload(data: Dict[str, Any], conv: Convention) -> PrDraft:
    sections: List[Tuple[str, List[str]]] = []
    for heading in conv.pr.sections:
        key = _section_key(heading)
        bullets = [_clean_bullet(b) for b in _as_list(data.get(key)) if _clean_bullet(b)]
        sections.append((heading, bullets))
    return PrDraft(title=_one_line(data.get("title", "")), sections=sections, checklist=list(conv.pr.checklist))


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def validate_commit(draft: CommitDraft, conv: Convention) -> List[str]:
    """규칙 위반 목록. 빈 리스트면 통과."""
    c = conv.commit
    problems: List[str] = []

    if not draft.subject:
        problems.append("커밋 제목 본문이 비어 있다.")
    if draft.type and draft.type not in c.prefixes:
        problems.append(f"커밋 타입 '{draft.type}' 이 허용 목록({', '.join(c.prefixes)})에 없다.")
    if not draft.type:
        problems.append("커밋 타입 접두사가 없다.")
    if c.scope == "required" and not draft.scope:
        problems.append("컨벤션이 스코프를 요구하는데 비어 있다.")
    if c.scope == "none" and draft.scope:
        problems.append("컨벤션이 스코프를 금지하는데 값이 있다.")

    title = draft.title
    if len(title) > c.title_max:
        problems.append(f"커밋 제목이 {len(title)}자로 최대 {c.title_max}자를 넘는다.")
    if title.endswith("."):
        problems.append("커밋 제목이 마침표로 끝난다.")
    if "\n" in title:
        problems.append("커밋 제목이 한 줄이 아니다.")

    has_body = bool(draft.body_bullets or draft.files_mentioned)
    if c.body == "required" and not has_body:
        problems.append("컨벤션이 본문을 요구하는데 비어 있다.")
    if c.body == "none" and has_body:
        problems.append("컨벤션이 본문을 금지하는데 내용이 있다.")

    # 본문은 기본적으로 선택이지만, 포함한다면 최소 품질 기준을 만족해야 한다.
    if has_body:
        if not (1 <= len(draft.body_bullets) <= 2):
            problems.append(f"본문 불릿이 {len(draft.body_bullets)}개다. 1~2개여야 한다.")
        if not (1 <= len(draft.files_mentioned) <= 3):
            problems.append(f"언급 파일이 {len(draft.files_mentioned)}개다. 1~3개여야 한다.")
    return problems


def validate_pr(draft: PrDraft, conv: Convention) -> List[str]:
    p = conv.pr
    problems: List[str] = []

    if not draft.title:
        problems.append("PR 제목이 비어 있다.")
    if len(draft.title) > p.title_max:
        problems.append(f"PR 제목이 {len(draft.title)}자로 최대 {p.title_max}자를 넘는다.")
    if "\n" in draft.title:
        problems.append("PR 제목이 한 줄이 아니다.")

    present = [name for name, _ in draft.sections]
    for required in REQUIRED_PR_SECTIONS:
        if required not in present:
            problems.append(f"PR 본문에 '## {required}' 섹션이 없다.")
    for heading, bullets in draft.sections:
        if len(bullets) < p.min_bullets:
            problems.append(f"'{heading}' 섹션의 불릿이 {len(bullets)}개다. 최소 {p.min_bullets}개여야 한다.")
    return problems


def _fit_title(draft: CommitDraft, limit: int) -> List[str]:
    """제목 **전체** 가 `limit` 이내가 되도록 줄인다.

    제목은 `type(scope): subject` 이므로 `subject` 만 줄여서는 상한을 못 맞출 수 있다.
    스코프가 길면 스코프를 먼저 줄이고, 그래도 모자라면 스코프를 버린다.

    마지막에 **실제로 상한 안에 들어왔는지 다시 재서** 보고 문구를 정한다.
    줄이지 못했는데 "줄였다"고 적으면 사용자가 그대로 복사해 쓴다.
    """
    if len(draft.title) <= limit:
        return []

    fixes: List[str] = []

    def head_len(scope: str) -> int:
        """`type(scope): ` 또는 `type: ` 의 길이."""
        return len(draft.type) + (len(scope) + 2 if scope else 0) + 2

    room = limit - head_len(draft.scope)
    if room < MIN_SUBJECT and draft.scope:
        allowed = limit - MIN_SUBJECT - len(draft.type) - 4  # 괄호 2 + ": " 2
        if allowed >= 1:
            draft.scope = _truncate(draft.scope, allowed) or draft.scope[:allowed]
            fixes.append(f"제목 길이를 맞추려고 스코프를 {len(draft.scope)}자로 줄임")
        else:
            draft.scope = ""
            fixes.append("제목 길이를 맞추려고 스코프를 제거")
        room = limit - head_len(draft.scope)

    if room < 1:
        draft.subject = ""
    elif len(draft.subject) > room:
        draft.subject = _truncate(draft.subject, room) or draft.subject[:room]

    if len(draft.title) <= limit:
        fixes.append(f"제목을 {limit}자 이내로 줄임")
    else:
        fixes.append(
            f"제목을 {limit}자 이내로 줄이지 못했습니다 (현재 {len(draft.title)}자) — 직접 줄이세요"
        )
    return fixes


def polish_commit(draft: CommitDraft, conv: Convention, ctx: GitContext) -> Tuple[CommitDraft, List[str]]:
    """규칙에 맞게 손본다. 무엇을 고쳤는지 함께 돌려준다."""
    c = conv.commit
    fixes: List[str] = []

    if not draft.type or draft.type not in c.prefixes:
        fallback = "chore" if "chore" in c.prefixes else c.prefixes[0]
        fixes.append(f"커밋 타입을 '{draft.type or '(없음)'}' → '{fallback}' 으로 교체")
        draft.type = fallback
    if c.scope == "none" and draft.scope:
        fixes.append("컨벤션에 따라 스코프 제거")
        draft.scope = ""

    if draft.subject.endswith("."):
        draft.subject = draft.subject.rstrip(".")
        fixes.append("제목 끝 마침표 제거")
    if not draft.subject:
        draft.subject = f"{len(ctx.relevant_files)}개 파일 변경"
        fixes.append("빈 제목을 변경 파일 수로 채움")

    fixes.extend(_fit_title(draft, c.title_max))

    if c.body == "none" and (draft.body_bullets or draft.files_mentioned):
        draft.body_bullets, draft.files_mentioned = [], []
        fixes.append("컨벤션에 따라 본문 제거")
    elif c.body == "required" and not (draft.body_bullets or draft.files_mentioned):
        draft.body_bullets = [f"diff {ctx.diff_lines}줄 반영"]
        draft.files_mentioned = [f.path for f in ctx.relevant_files[:3]] or ["(변경 파일 없음)"]
        fixes.append("컨벤션이 본문을 요구해 Git 사실로 채움")

    if draft.body_bullets or draft.files_mentioned:
        if len(draft.body_bullets) > 2:
            draft.body_bullets = draft.body_bullets[:2]
            fixes.append("본문 불릿을 2개로 줄임")
        if not draft.body_bullets:
            draft.body_bullets = [f"diff {ctx.diff_lines}줄 반영"]
            fixes.append("본문 불릿이 없어 diff 규모로 채움")
        if len(draft.files_mentioned) > 3:
            draft.files_mentioned = draft.files_mentioned[:3]
            fixes.append("언급 파일을 3개로 줄임")
        if not draft.files_mentioned:
            draft.files_mentioned = [f.path for f in ctx.relevant_files[:3]] or ["(변경 파일 없음)"]
            fixes.append("언급 파일이 없어 변경 파일 목록에서 채움")
    return draft, fixes


def polish_pr(draft: PrDraft, conv: Convention, ctx: GitContext) -> Tuple[PrDraft, List[str]]:
    p = conv.pr
    fixes: List[str] = []

    if not draft.title:
        draft.title = f"{ctx.branch}: 변경 파일 {len(ctx.relevant_files)}개"
        fixes.append("빈 PR 제목을 브랜치/변경 규모로 채움")
    if len(draft.title) > p.title_max:
        draft.title = _truncate(draft.title, p.title_max)
        fixes.append(f"PR 제목을 {p.title_max}자 이내로 줄임")

    present = {name for name, _ in draft.sections}
    for required in REQUIRED_PR_SECTIONS:
        if required not in present:
            draft.sections.append((required, []))
            fixes.append(f"누락된 '## {required}' 섹션 추가")
    # 헤더 순서를 컨벤션 순서로 정렬한다.
    order = {name: i for i, name in enumerate(p.sections)}
    draft.sections.sort(key=lambda item: order.get(item[0], len(order)))

    for index, (heading, bullets) in enumerate(draft.sections):
        if len(bullets) >= p.min_bullets:
            continue
        need = p.min_bullets - len(bullets)
        seen = {b.strip() for b in bullets}
        for candidate in _filler_pool(ctx, heading):
            if len(bullets) >= p.min_bullets:
                break
            if candidate.strip() in seen:  # 같은 문장을 두 번 넣지 않는다
                continue
            bullets = [*bullets, candidate]
            seen.add(candidate.strip())
        draft.sections[index] = (heading, bullets)
        fixes.append(f"'{heading}' 섹션 불릿을 Git 사실로 {need}개 보충")
    return draft, fixes


def _filler_pool(ctx: GitContext, heading: str) -> List[str]:
    """후처리로 채워 넣을 문장 후보. 전부 Git 에서 직접 읽은 사실이다.

    섹션에 맞는 문장을 먼저 두고, 모자라면 공통 사실을 순서대로 덧붙인다.
    없는 사실을 지어내지 않기 위해 후보는 전부 수집 결과에서만 만든다.
    """
    names = ", ".join(f.path for f in ctx.relevant_files[:3]) or "(변경 파일 없음)"
    specific = {
        "Why": [f"브랜치 {ctx.branch} 의 변경 사항을 정리하기 위한 PR (배경은 작성자가 보완 필요)"],
        "What": [f"변경 파일 {len(ctx.relevant_files)}개: {names}"],
        "How to Test": [f"`{ctx.diff_command}` 로 변경 내용을 확인"],
    }
    common = [
        f"변경 파일 {len(ctx.relevant_files)}개: {names}",
        f"diff {ctx.diff_lines}줄",
        f"대상 브랜치: {ctx.branch}",
        f"수집 명령: {ctx.diff_command}",
        "세부 내용은 작성자가 보완 필요",
    ]
    return [*specific.get(heading, []), *common]


def feedback_message(problems: Sequence[str]) -> str:
    """재생성 요청에 붙일 위반 목록."""
    listed = "\n".join(f"- {p}" for p in problems)
    return (
        "\n\n---\n방금 만든 초안이 아래 형식 규칙을 어겼다. "
        "내용은 유지하되 규칙을 지켜 다시 만들어라.\n" + listed
    )

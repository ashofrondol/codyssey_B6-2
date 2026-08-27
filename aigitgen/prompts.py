"""프롬프트 설계 — 이 도구의 결과 품질을 실제로 결정하는 부분.

설계 원칙 4가지.

1. **역할과 입력 계약을 먼저 못박는다.** 모델이 무엇을 받고 무엇을 돌려줘야 하는지
   추측하지 않게 한다.
2. **출력 형식은 문장이 아니라 JSON 스키마로 강제한다.** 파싱 실패와 형식 위반을
   후처리로 막는 대신, 애초에 구조가 어긋날 수 없게 만든다(structured outputs).
3. **규칙은 숫자로 준다.** "짧게" 대신 "50자 이내, 최대 72자". 검증기가 같은 숫자로
   재검사하므로 프롬프트와 검증기가 어긋나지 않는다.
4. **없는 사실을 만들지 못하게 한다.** diff 에서 확인되지 않는 내용은 쓰지 말라고
   명시하고, 근거로 쓸 파일 목록을 함께 넘긴다.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from .config import Convention
from .gitctx import GitContext
from .redact import RedactionReport

_COMMON_RULES = """\
너는 시니어 개발자의 커밋/PR 작성 습관을 그대로 따르는 보조 도구다.
아래 Git 변경 사항만을 근거로 작성한다.

지켜야 할 것
- diff 에서 확인되지 않는 사실을 지어내지 않는다. 확실하지 않으면 파일 이름 수준으로만 쓴다.
- 「«MASKED:...»」 로 가려진 값은 민감정보다. 그 값 자체를 추측하거나 언급하지 않는다.
- diff 가 "생략" 표시로 잘려 있으면, 잘린 부분을 상상해 채우지 않는다.
- 무엇을 했는지(What)와 왜 했는지(Why)를 구분해 쓴다.
- 과장 표현("대폭 개선", "완벽하게")과 상투어("여러 가지 개선")를 쓰지 않는다.
"""


def _fmt_files(ctx: GitContext, limit: int = 40) -> str:
    files = ctx.relevant_files
    lines = [f"- {f.path} [{f.label}]" for f in files[:limit]]
    if len(files) > limit:
        lines.append(f"- ... 외 {len(files) - limit}개")
    return "\n".join(lines) if lines else "- (없음)"


def build_context_block(ctx: GitContext, diff_for_prompt: str, report: RedactionReport) -> str:
    """모델에게 넘길 '변경 사항' 본문. commit/pr 이 공유한다."""
    notes: List[str] = []
    if report.enabled:
        if report.masked_total:
            notes.append(f"민감정보 {report.masked_total}건이 «MASKED:...» 로 치환됨")
        if report.files_dropped:
            notes.append(f"파일 {report.files_dropped}개가 상한으로 생략됨")
        if report.lines_dropped:
            notes.append(f"{report.lines_dropped}줄이 상한으로 생략됨")
    note_text = ("\n주의: " + " / ".join(notes)) if notes else ""

    untracked = [] if ctx.staged_only else ctx.untracked_files
    untracked_text = ""
    if untracked:
        names = ", ".join(f.path for f in untracked[:10])
        untracked_text = (
            f"\n\n## 추적되지 않은 새 파일 (내용은 diff 에 없음, 이름만 참고)\n{names}"
        )

    return f"""## 브랜치
{ctx.branch}

## 변경 파일 ({len(ctx.relevant_files)}개)
{_fmt_files(ctx)}

## git status --short
```
{ctx.status_text or "(출력 없음)"}
```{untracked_text}

## {ctx.diff_command} ({report.lines_after}줄 전송){note_text}
```diff
{diff_for_prompt or "(diff 없음 — 파일 목록만으로 판단할 것)"}
```"""


def commit_schema(conv: Convention) -> Dict[str, Any]:
    """커밋 메시지 구조 — structured outputs 로 강제한다."""
    return {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": list(conv.commit.prefixes),
                    "description": "커밋 종류 접두사",
                },
                "scope": {
                    "type": "string",
                    "description": "변경 범위(모듈/디렉터리). 없으면 빈 문자열.",
                },
                "subject": {
                    "type": "string",
                    "description": f"제목 본문. 접두사·스코프를 뺀 부분. {conv.commit.title_recommended}자 이내 권장.",
                },
                "body_bullets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 2,
                    "description": "핵심 변경 사항 1~2개. 각 항목은 한 줄.",
                },
                "files_mentioned": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 3,
                    "description": "본문에서 언급할 대표 변경 파일 또는 모듈 1~3개.",
                },
            },
            "required": ["type", "scope", "subject", "body_bullets", "files_mentioned"],
            "additionalProperties": False,
        },
    }


def pr_schema(conv: Convention) -> Dict[str, Any]:
    """PR 초안 구조 — Why/What/How to Test 는 스키마 차원에서 필수다."""
    bullets = {
        "type": "array",
        "items": {"type": "string"},
        "minItems": conv.pr.min_bullets,
    }
    props: Dict[str, Any] = {
        "title": {"type": "string", "description": f"PR 제목 한 줄. {conv.pr.title_max}자 이내."},
        "why": {**bullets, "description": "이 변경이 필요한 배경/문제."},
        "what": {**bullets, "description": "실제로 무엇을 바꿨는지."},
        "how_to_test": {**bullets, "description": "리뷰어가 따라할 수 있는 검증 절차."},
    }
    required = ["title", "why", "what", "how_to_test"]
    for extra in conv.pr.extra_sections:
        key = _section_key(extra)
        props[key] = {**bullets, "description": f"'{extra}' 섹션 내용."}
        required.append(key)
    return {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": props,
            "required": required,
            "additionalProperties": False,
        },
    }


def _section_key(heading: str) -> str:
    """추가 섹션 헤더를 JSON 키로 바꾼다. 'How to Test' → 'how_to_test'."""
    return "".join(c if c.isalnum() else "_" for c in heading.strip().lower()).strip("_")


def build_commit_prompt(
    ctx: GitContext, diff_for_prompt: str, report: RedactionReport, conv: Convention
) -> Tuple[str, str]:
    """(system, user) 를 만든다."""
    c = conv.commit
    scope_rule = {
        "required": "스코프를 반드시 넣는다. 예: feat(cli): ...",
        "optional": "스코프는 도움이 될 때만 넣는다. 없으면 scope 를 빈 문자열로 둔다.",
        "none": "스코프를 쓰지 않는다. scope 는 항상 빈 문자열이다.",
    }[c.scope]

    system = f"""{_COMMON_RULES}
이번 작업: **커밋 메시지 초안** 을 만든다.

형식 규칙
- 제목 = `<type>(<scope>): <subject>` 한 줄. type 은 {", ".join(c.prefixes)} 중 하나.
- {scope_rule}
- 제목 전체 길이는 {c.title_recommended}자 이내를 목표로 하고, {c.title_max}자를 절대 넘지 않는다.
- 제목은 마침표로 끝내지 않는다. 명령형/개조식으로 쓴다.
- body_bullets 는 핵심 변경 사항 1~2개. 각 항목은 한 문장, 앞에 기호를 붙이지 않는다.
- files_mentioned 는 대표 변경 파일 또는 모듈 1~3개의 경로.
- 작성 언어: {"한국어" if c.language == "ko" else c.language}."""

    user = f"""아래 Git 변경 사항을 읽고 커밋 메시지를 만들어라.

{build_context_block(ctx, diff_for_prompt, report)}"""
    return system, user


def build_pr_prompt(
    ctx: GitContext, diff_for_prompt: str, report: RedactionReport, conv: Convention
) -> Tuple[str, str]:
    p = conv.pr
    extra_rule = ""
    if p.extra_sections:
        extra_rule = "\n- 추가 섹션: " + ", ".join(p.extra_sections) + " (각각 불릿 필수)"
    checklist_rule = ""
    if p.checklist:
        checklist_rule = (
            "\n- 본문 끝에 붙을 체크리스트는 도구가 붙이므로 네가 쓰지 않는다: "
            + ", ".join(p.checklist)
        )

    system = f"""{_COMMON_RULES}
이번 작업: **Pull Request 제목과 본문 초안** 을 만든다.

형식 규칙
- 제목은 한 줄, {p.title_max}자 이내.
- 본문은 Why / What / How to Test 세 섹션을 반드시 갖는다.
- 각 섹션에 최소 {p.min_bullets}개 불릿을 쓴다. 불릿 앞 기호는 붙이지 않는다(도구가 붙인다).{extra_rule}{checklist_rule}
- Why 는 "왜 필요했는가", What 은 "무엇을 바꿨는가", How to Test 는 "리뷰어가 어떻게 확인하는가".
- How to Test 는 실제로 따라 할 수 있는 명령이나 절차로 쓴다. 추상적인 문장 금지.
- 문체: {p.tone}
- 작성 언어: {"한국어" if p.language == "ko" else p.language}."""

    user = f"""아래 Git 변경 사항을 읽고 PR 제목과 본문 초안을 만들어라.

{build_context_block(ctx, diff_for_prompt, report)}"""
    return system, user


def render_for_debug(system: str, user: str, schema: Dict[str, Any]) -> str:
    """`--show-prompt` 가 출력하는 내용. 실제 전송 페이로드와 같다."""
    return (
        "===== system =====\n"
        f"{system}\n\n"
        "===== user =====\n"
        f"{user}\n\n"
        "===== output_config.format =====\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )

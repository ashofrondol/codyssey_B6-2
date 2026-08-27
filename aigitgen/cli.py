"""CLI 진입점 — 명령 정의, 실행 흐름, 오류 처리.

흐름은 한 줄로 요약된다.

    git 수집 → 안전 모드 적용 → 프롬프트 조립 → AI 호출 → 검증 → (필요 시) 재생성 1회
    → 후처리 → 구획을 나눠 출력

원격 반영(`git push`, GitHub PR 생성)은 과제 제약에 따라 **구현하지 않는다.**
이 도구는 초안 텍스트를 터미널에 출력하는 데서 끝난다.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import __version__, config, gitctx, polish, prompts, redact, render
from .client import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    ClaudeGenerator,
    Generator,
    ModelParams,
    OfflineGenerator,
)
from .errors import AiGitGenError, ConfigError

#: 한 번 실행에서 허용하는 최대 API 호출 횟수 (과제 제약: 1~2회 권장)
MAX_API_CALLS = 2

GeneratorFactory = Callable[[ModelParams, gitctx.GitContext, argparse.Namespace], Generator]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="Git 변경 사항으로 커밋 메시지와 PR 초안을 만드는 AI CLI 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  python main.py commit\n"
            "  python main.py pr --temperature 0.5\n"
            "  python main.py commit --model claude-haiku-4-5 --max-tokens 1200\n"
            "  python main.py pr --no-safe-mode --show-prompt\n"
            "  python main.py commit --dry-run        # API Key 없이 흐름만 확인\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"ai-gitgen {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    api = common.add_argument_group("AI API 호출 파라미터")
    api.add_argument("--model", "-model", default=DEFAULT_MODEL, help=f"사용할 모델 (기본: {DEFAULT_MODEL})")
    api.add_argument(
        "--temperature",
        "-temperature",
        type=float,
        default=None,
        help=f"생성 다양성 0.0~1.0 (기본: {DEFAULT_TEMPERATURE}). 낮을수록 결정적이다.",
    )
    api.add_argument(
        "--max-tokens",
        "-max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help=f"응답 최대 토큰 (기본: {DEFAULT_MAX_TOKENS})",
    )
    api.add_argument("--no-retry", action="store_true", help="형식 위반 시 재생성하지 않는다 (호출 1회로 고정)")

    git = common.add_argument_group("Git 수집 범위")
    git.add_argument("--repo", default=".", help="대상 저장소 경로 (기본: 현재 디렉터리)")
    git.add_argument("--staged", action="store_true", help="스테이지된 변경만 대상으로 한다 (git diff --cached)")

    safe = common.add_argument_group("안전 모드 (민감정보 대응)")
    safe.add_argument(
        "--safe-mode",
        "-safe-mode",
        dest="safe_mode",
        action="store_true",
        default=True,
        help="민감정보 마스킹 + diff 전송량 제한 (기본값: 켜짐)",
    )
    safe.add_argument("--no-safe-mode", dest="safe_mode", action="store_false", help="안전 모드를 끈다")
    safe.add_argument("--max-files", type=int, default=None, help="전송할 최대 파일 수 (기본: 10)")
    safe.add_argument("--max-diff-lines", type=int, default=None, help="전송할 최대 diff 줄 수 (기본: 200)")

    misc = common.add_argument_group("기타")
    misc.add_argument("--convention", default="", help=".ai-gitgen.json 에 정의한 팀 컨벤션 이름")
    misc.add_argument("--show-prompt", action="store_true", help="실제로 전송하는 프롬프트를 출력한다")
    misc.add_argument("--dry-run", action="store_true", help="AI API 를 호출하지 않고 흐름만 확인한다")
    misc.add_argument("--debug", action="store_true", help="오류 시 스택트레이스를 출력한다")

    subs = parser.add_subparsers(dest="command", metavar="{commit,pr}")
    subs.add_parser("commit", parents=[common], help="커밋 메시지 초안 생성")
    subs.add_parser("pr", parents=[common], help="PR 제목/본문 초안 생성")
    return parser


def _make_settings(ctx: gitctx.GitContext, args: argparse.Namespace) -> config.Settings:
    settings = config.load(ctx.root, args.convention)
    policy = settings.safety
    policy.enabled = bool(args.safe_mode)
    if args.max_files is not None:
        if args.max_files < 1:
            raise ConfigError("--max-files 는 1 이상이어야 합니다.")
        policy.max_files = args.max_files
    if args.max_diff_lines is not None:
        if args.max_diff_lines < 1:
            raise ConfigError("--max-diff-lines 는 1 이상이어야 합니다.")
        policy.max_diff_lines = args.max_diff_lines
    return settings


def _make_params(args: argparse.Namespace) -> ModelParams:
    temperature = DEFAULT_TEMPERATURE if args.temperature is None else args.temperature
    if not 0.0 <= temperature <= 1.0:
        raise ConfigError(f"--temperature 는 0.0~1.0 이어야 합니다 (받은 값: {temperature}).")
    if args.max_tokens < 64:
        raise ConfigError(f"--max-tokens 는 64 이상이어야 합니다 (받은 값: {args.max_tokens}).")
    return ModelParams(
        model=args.model,
        temperature=temperature,
        max_tokens=args.max_tokens,
        temperature_explicit=args.temperature is not None,
    )


def _default_factory(params: ModelParams, ctx: gitctx.GitContext, args: argparse.Namespace) -> Generator:
    if args.dry_run:
        return OfflineGenerator(params, ctx)
    return ClaudeGenerator(params)


def _generate_with_validation(
    generator: Generator,
    system: str,
    user: str,
    schema: Dict[str, Any],
    to_draft: Callable[[Dict[str, Any]], Any],
    validate: Callable[[Any], List[str]],
    allow_retry: bool,
) -> Tuple[Any, List[str], bool]:
    """생성 → 검증 → (필요하면) 재생성 1회.

    반환: (초안, 남은 위반 목록, 재생성했는지)
    """
    payload = generator.generate(system, user, schema)
    draft = to_draft(payload)
    problems = validate(draft)
    if not problems or not allow_retry or generator.calls >= MAX_API_CALLS:
        return draft, problems, False

    render.warn(f"형식 위반 {len(problems)}건 — 위반 내용을 붙여 1회 재생성합니다.")
    render.bullets(problems)
    retry_payload = generator.generate(system, user + polish.feedback_message(problems), schema)
    retry_draft = to_draft(retry_payload)
    retry_problems = validate(retry_draft)
    # 재생성이 더 나빠졌으면 첫 결과를 쓴다.
    if len(retry_problems) > len(problems):
        return draft, problems, True
    return retry_draft, retry_problems, True


def _run(args: argparse.Namespace, factory: GeneratorFactory) -> int:
    ctx = gitctx.collect(args.repo, staged_only=args.staged)
    render.info(f"저장소: {ctx.root} (브랜치 {ctx.branch})")
    render.info(f"Git status 수집 완료: {len(ctx.relevant_files)}개 파일 변경 감지")
    render.info(f"{ctx.diff_command} 수집 완료: {ctx.diff_lines}줄")

    if ctx.is_empty:
        render.info(f"{ctx.scope_label}이 없습니다. 초안을 생성하지 않고 종료합니다.")
        return 0

    settings = _make_settings(ctx, args)
    if settings.source != "(기본값)":
        render.info(f"컨벤션 '{settings.convention.name}' 적용 ({settings.source})")

    diff_for_prompt, report = redact.apply(ctx.diff_text, settings.safety)
    render.info(report.summary())

    conv = settings.convention
    if args.command == "commit":
        system, user = prompts.build_commit_prompt(ctx, diff_for_prompt, report, conv)
        schema = prompts.commit_schema(conv)
        to_draft = lambda data: polish.commit_from_payload(data, conv)  # noqa: E731
        validate = lambda draft: polish.validate_commit(draft, conv)  # noqa: E731
    else:
        system, user = prompts.build_pr_prompt(ctx, diff_for_prompt, report, conv)
        schema = prompts.pr_schema(conv)
        to_draft = lambda data: polish.pr_from_payload(data, conv)  # noqa: E731
        validate = lambda draft: polish.validate_pr(draft, conv)  # noqa: E731

    if args.show_prompt:
        render.block("Prompt (전송 페이로드)", prompts.render_for_debug(system, user, schema))

    params = _make_params(args)
    generator = factory(params, ctx, args)
    for message in generator.warnings:
        render.warn(message)

    if not args.dry_run:
        render.info(f"AI API 요청 중... ({params.describe()})")

    draft, problems, retried = _generate_with_validation(
        generator, system, user, schema, to_draft, validate, allow_retry=not args.no_retry
    )

    if args.command == "commit":
        draft, fixes = polish.polish_commit(draft, conv, ctx)
    else:
        draft, fixes = polish.polish_pr(draft, conv, ctx)

    label = "커밋 메시지" if args.command == "commit" else "PR 초안"
    usage = generator.usage
    detail = f"API 호출 {generator.calls}회"
    if retried:
        detail += " (재생성 1회 포함)"
    if usage.input_tokens or usage.output_tokens:
        detail += f", 토큰 입력 {usage.input_tokens:,} / 출력 {usage.output_tokens:,}"
    render.done(f"{label} 생성 완료 — {detail}")

    if fixes:
        render.warn(f"후처리 {len(fixes)}건을 적용했습니다.")
        render.bullets(fixes)
    if problems:
        render.warn(f"규칙 위반 {len(problems)}건이 남아 후처리로 보정했습니다. 적용 전 검토하세요.")
        render.bullets(problems)

    if args.command == "commit":
        render.block("Commit Message", draft.render())
    else:
        render.block("PR Title", draft.title)
        render.block("PR Body", draft.render_body())

    render.info("생성 결과는 초안입니다. 검토 후 직접 커밋/PR 에 적용하세요.")
    return 0


def main(argv: Optional[Sequence[str]] = None, factory: Optional[GeneratorFactory] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.command:
        parser.print_help()
        return 0

    try:
        return _run(args, factory or _default_factory)
    except AiGitGenError as exc:
        render.error(str(exc))
        if args.debug:
            traceback.print_exc()
        return exc.exit_code
    except KeyboardInterrupt:  # pragma: no cover - 사용자 중단
        render.error("사용자가 중단했습니다.")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))

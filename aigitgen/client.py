"""AI API 연동 — Claude(Anthropic) Messages API 호출부.

책임을 셋으로 나눠 둔다.

- :class:`ModelParams` — CLI 로 조절되는 호출 파라미터(모델/temperature/max_tokens)
- :class:`ClaudeGenerator` — 실제 API 호출, 예외 변환, 호출 횟수 집계
- :class:`OfflineGenerator` — `--dry-run` 용. API 를 부르지 않고 Git 사실만으로 초안을 만든다.

API Key 는 **환경변수로만** 읽는다. 코드에 키를 적지 않으며, 키 값을 로그에 남기지도 않는다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from .errors import ApiCallError, ApiKeyMissingError
from .gitctx import GitContext

#: 우선순위대로 확인할 API Key 환경변수 이름.
#: `ANTHROPIC_API_KEY` 는 공식 SDK 가 기본으로 읽는 이름이고,
#: `AI_API_KEY` 는 과제 문서 예시가 쓰는 이름이다. 둘 다 지원한다.
API_KEY_ENV_NAMES: Tuple[str, ...] = ("ANTHROPIC_API_KEY", "AI_API_KEY")

#: 기본 모델.
#: 과제는 `temperature` 를 CLI 로 조절할 수 있어야 한다고 요구한다.
#: 최신 세대 모델(Opus 5 / Sonnet 5 등)은 sampling 파라미터를 아예 거부하므로,
#: temperature 가 실제로 먹히는 모델을 기본값으로 둔다.
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 2000

#: temperature / top_p / top_k 를 받지 않는 모델(보내면 400).
#: 이런 모델을 고르면 temperature 를 빼고 호출하고 경고를 남긴다.
SAMPLING_UNSUPPORTED = frozenset(
    {
        "claude-fable-5",
        "claude-mythos-5",
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-sonnet-5",
    }
)


@dataclass
class ModelParams:
    """API 호출 파라미터. 전부 CLI 옵션으로 바꿀 수 있다."""

    model: str = DEFAULT_MODEL
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    #: 사용자가 --temperature 를 직접 줬는가 (경고를 낼지 판단하는 데 쓴다)
    temperature_explicit: bool = False

    @property
    def sends_temperature(self) -> bool:
        return self.model not in SAMPLING_UNSUPPORTED

    def describe(self) -> str:
        temp = f"{self.temperature}" if self.sends_temperature else "미전송(모델이 미지원)"
        return f"model={self.model} temperature={temp} max_tokens={self.max_tokens}"


@dataclass
class Usage:
    """토큰 사용량. 비용 감각을 위해 항상 출력한다."""

    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


class Generator:
    """생성기 공통 인터페이스."""

    name = "generator"

    def __init__(self, params: ModelParams) -> None:
        self.params = params
        self.calls = 0
        self.usage = Usage()
        self.warnings: List[str] = []

    def generate(self, system: str, user: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


def resolve_api_key(env: Dict[str, str] | None = None) -> str:
    """환경변수에서 API Key 를 읽는다. 없으면 :class:`ApiKeyMissingError`."""
    source = os.environ if env is None else env
    for name in API_KEY_ENV_NAMES:
        value = (source.get(name) or "").strip()
        if value:
            return value
    names = " 또는 ".join(API_KEY_ENV_NAMES)
    raise ApiKeyMissingError(
        f"{names} 환경변수가 설정되지 않았습니다.\n"
        '        예) export ANTHROPIC_API_KEY="YOUR_KEY"'
    )


class ClaudeGenerator(Generator):
    """Anthropic 공식 SDK 로 Messages API 를 호출한다."""

    name = "claude"

    def __init__(self, params: ModelParams, api_key: str | None = None) -> None:
        super().__init__(params)
        self._api_key = api_key or resolve_api_key()
        self._client = self._make_client()
        if params.temperature_explicit and not params.sends_temperature:
            self.warnings.append(
                f"{params.model} 은 temperature 를 받지 않는 모델입니다. "
                "temperature 를 빼고 호출합니다. (조절하려면 --model claude-sonnet-4-6 등을 쓰세요)"
            )

    def _make_client(self):
        try:
            import anthropic  # 지연 임포트: --dry-run 이나 테스트에서는 SDK 없이도 동작해야 한다
        except ImportError as exc:  # pragma: no cover - 설치 안내
            raise ApiCallError(
                "anthropic 패키지가 설치돼 있지 않습니다.",
                cause="pip install -r requirements.txt 를 먼저 실행하세요",
            ) from exc
        self._anthropic = anthropic
        return anthropic.Anthropic(api_key=self._api_key)

    def generate(self, system: str, user: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        anthropic = self._anthropic
        kwargs: Dict[str, Any] = {
            "model": self.params.model,
            "max_tokens": self.params.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            # 출력 형식을 스키마로 강제한다. 파싱 실패와 형식 위반의 대부분이 여기서 사라진다.
            "output_config": {"format": schema},
        }
        if self.params.sends_temperature:
            # `temperature` 는 최신 SDK 의 타입 시그니처에서 빠졌다.
            # 최신 세대 모델이 sampling 파라미터를 아예 거부하도록 바뀌었기 때문이다.
            # sampling 을 지원하는 모델에서는 REST 본문에 그대로 실으면 동작하므로
            # `extra_body` 로 넘긴다. (`extra_body` 는 요청 JSON 에 병합된다)
            kwargs["extra_body"] = {"temperature": self.params.temperature}

        self.calls += 1
        try:
            response = self._client.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise ApiCallError("AI API 인증에 실패했습니다.", cause=f"API Key 를 확인하세요 / {exc}") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ApiCallError("이 API Key 에 권한이 없습니다.", cause=str(exc)) from exc
        except anthropic.NotFoundError as exc:
            raise ApiCallError(
                f"모델 '{self.params.model}' 을 찾을 수 없습니다.",
                cause=f"--model 값을 확인하세요 / {exc}",
            ) from exc
        except anthropic.RateLimitError as exc:
            retry_after = "잠시 후"
            response_obj = getattr(exc, "response", None)
            if response_obj is not None:
                retry_after = response_obj.headers.get("retry-after", retry_after)
            raise ApiCallError("요청 한도(rate limit)에 걸렸습니다.", cause=f"{retry_after} 뒤 재시도하세요") from exc
        except anthropic.BadRequestError as exc:
            raise ApiCallError("요청이 거부됐습니다(400).", cause=str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise ApiCallError("AI API 에 접속하지 못했습니다.", cause=f"네트워크 상태를 확인하세요 / {exc}") from exc
        except anthropic.APIStatusError as exc:
            kind = "서버 오류" if exc.status_code >= 500 else "API 오류"
            raise ApiCallError(f"{kind}({exc.status_code}) 가 발생했습니다.", cause=str(exc)) from exc

        usage = getattr(response, "usage", None)
        if usage is not None:
            self.usage.add(
                Usage(
                    input_tokens=getattr(usage, "input_tokens", 0) or 0,
                    output_tokens=getattr(usage, "output_tokens", 0) or 0,
                )
            )

        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            detail = getattr(response, "stop_details", None)
            raise ApiCallError(
                "모델이 요청을 거절했습니다.",
                cause=getattr(detail, "explanation", None) or "안전 정책",
            )
        if stop == "max_tokens":
            raise ApiCallError(
                "응답이 max_tokens 에서 잘렸습니다.",
                cause=f"--max-tokens 를 {self.params.max_tokens} 보다 크게 주고 다시 실행하세요",
            )

        text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text = block.text
                break
        if not text.strip():
            raise ApiCallError("모델이 빈 응답을 돌려줬습니다.", cause=f"stop_reason={stop}")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:  # 스키마 강제가 있으니 거의 오지 않는 경로
            raise ApiCallError("모델 응답을 JSON 으로 읽지 못했습니다.", cause=str(exc)) from exc
        if not isinstance(data, dict):
            raise ApiCallError("모델 응답이 객체가 아닙니다.", cause=f"type={type(data).__name__}")
        return data


class OfflineGenerator(Generator):
    """`--dry-run` 전용. API 를 호출하지 않고 Git 사실만으로 초안을 만든다.

    프롬프트 설계와 출력 형식 검증을 키 없이 연습하기 위한 경로다.
    결과에는 항상 `[DRY-RUN]` 이 붙어 실제 생성물과 헷갈리지 않게 한다.
    """

    name = "offline"

    def __init__(self, params: ModelParams, ctx: GitContext) -> None:
        super().__init__(params)
        self.ctx = ctx
        self.warnings.append("--dry-run: AI API 를 호출하지 않았습니다. 결과는 Git 사실만으로 조립한 초안입니다.")

    def _top_files(self, n: int) -> List[str]:
        return [f.path for f in self.ctx.relevant_files[:n]] or ["(변경 파일 없음)"]

    def generate(self, system: str, user: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        # API 호출이 아니므로 self.calls 를 늘리지 않는다.
        props = schema.get("schema", {}).get("properties", {})
        files = self._top_files(3)
        joined = ", ".join(files)

        if "subject" in props:
            enum = props.get("type", {}).get("enum", ["chore"])
            return {
                "type": "chore" if "chore" in enum else enum[0],
                "scope": "",
                "subject": f"[DRY-RUN] {len(self.ctx.relevant_files)}개 파일 변경",
                "body_bullets": [f"diff {self.ctx.diff_lines}줄 반영"],
                "files_mentioned": files,
            }

        data: Dict[str, Any] = {
            "title": f"[DRY-RUN] {self.ctx.branch} 브랜치 변경 사항",
            "why": ["AI 호출 없이 만든 초안입니다. 실제 배경은 직접 채워야 합니다."],
            "what": [f"변경 파일 {len(self.ctx.relevant_files)}개: {joined}"],
            # 후처리 보충 문장과 문자열이 같아야 중복 제거가 걸린다.
            "how_to_test": [f"`{self.ctx.diff_command}` 로 변경 내용을 확인"],
        }
        for key in props:
            if key not in data:
                data[key] = [f"(dry-run) {key} 섹션은 실제 AI 호출에서 채워집니다"]
        return data

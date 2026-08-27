"""도구 전역에서 쓰는 예외 정의.

CLI 는 이 예외들만 잡아 `[ERROR] ...` 한 줄로 바꿔 출력한다.
그 밖의 예외는 버그로 보고 그대로 터뜨린다(--debug 로 스택트레이스 확인).
"""


class AiGitGenError(Exception):
    """이 도구가 사용자에게 그대로 보여줄 수 있는 오류."""

    #: 프로세스 종료 코드
    exit_code = 1


class GitError(AiGitGenError):
    """git 명령 실행/해석 실패."""


class ConfigError(AiGitGenError):
    """설정 파일(.ai-gitgen.json) 또는 CLI 옵션 값이 잘못됨."""

    exit_code = 2


class ApiKeyMissingError(AiGitGenError):
    """API Key 환경변수가 없음."""

    exit_code = 3


class ApiCallError(AiGitGenError):
    """AI API 호출 실패(네트워크/인증/요청 오류 등).

    `cause` 에는 사용자에게 보여줄 원인 요약을 담는다.
    """

    exit_code = 4

    def __init__(self, message: str, cause: str = "") -> None:
        super().__init__(message)
        self.cause = cause

    def __str__(self) -> str:  # pragma: no cover - 문자열 조립뿐
        base = super().__str__()
        return f"{base} (원인: {self.cause})" if self.cause else base

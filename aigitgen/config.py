"""팀 컨벤션 설정 — `.ai-gitgen.json` 과 `--convention` 옵션.

보너스 과제 2(커밋/PR 템플릿 커스터마이징)와 3(안전 모드 정책 조정)의 구현부다.

설정 파일이 없으면 :data:`DEFAULT_CONVENTION` 하나만 있는 상태로 동작한다.
설정 파일 형식은 JSON 으로 고정했다 — 표준 라이브러리만으로 읽을 수 있어
`pip install pyyaml` 같은 추가 의존성을 만들지 않기 위해서다.

    {
      "default_convention": "team",
      "safe_mode": { "max_files": 6, "max_diff_lines": 120,
                     "extra_patterns": [["사번", "EMP-\\d{6}", "«MASKED:EMP»"]] },
      "conventions": {
        "team": {
          "commit": { "prefixes": ["feat", "fix", "docs"], "scope": "required",
                      "title_recommended": 50, "title_max": 72, "language": "ko" },
          "pr": { "title_max": 80, "extra_sections": ["Risk"], "min_bullets": 2,
                  "checklist": ["로컬 테스트 통과", "문서 갱신"], "tone": "간결한 평서문" }
        }
      }
    }
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from .errors import ConfigError
from .redact import DEFAULT_PATTERNS, SafetyPolicy

CONFIG_FILENAME = ".ai-gitgen.json"

#: PR 본문에 반드시 있어야 하는 섹션 헤더. 과제 요구사항이므로 컨벤션으로도 못 바꾼다.
REQUIRED_PR_SECTIONS: Tuple[str, ...] = ("Why", "What", "How to Test")


@dataclass
class CommitConvention:
    prefixes: List[str] = field(
        default_factory=lambda: ["feat", "fix", "docs", "style", "refactor", "test", "chore"]
    )
    scope: str = "optional"  # required | optional | none
    title_recommended: int = 50
    title_max: int = 72
    body: str = "optional"  # required | optional | none
    language: str = "ko"


@dataclass
class PrConvention:
    title_max: int = 80
    #: 필수 3개 뒤에 덧붙일 추가 섹션 (보너스 2)
    extra_sections: List[str] = field(default_factory=list)
    min_bullets: int = 1
    checklist: List[str] = field(default_factory=list)
    tone: str = "간결한 평서문"
    language: str = "ko"

    @property
    def sections(self) -> List[str]:
        return [*REQUIRED_PR_SECTIONS, *self.extra_sections]


@dataclass
class Convention:
    name: str = "default"
    commit: CommitConvention = field(default_factory=CommitConvention)
    pr: PrConvention = field(default_factory=PrConvention)


DEFAULT_CONVENTION = Convention()


@dataclass
class Settings:
    """설정 파일 + CLI 옵션을 합친 최종 결과."""

    convention: Convention = field(default_factory=Convention)
    safety: SafetyPolicy = field(default_factory=SafetyPolicy)
    source: str = "(기본값)"
    available: List[str] = field(default_factory=lambda: ["default"])


def _require(mapping: Any, where: str) -> Dict[str, Any]:
    if not isinstance(mapping, dict):
        raise ConfigError(f"{CONFIG_FILENAME}: {where} 는 객체({{...}})여야 합니다.")
    return mapping


def _int_field(raw: Dict[str, Any], key: str, current: int, where: str) -> int:
    if key not in raw:
        return current
    value = raw[key]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{CONFIG_FILENAME}: {where}.{key} 는 1 이상의 정수여야 합니다 (받은 값: {value!r}).")
    return value


def _str_list(raw: Dict[str, Any], key: str, current: List[str], where: str) -> List[str]:
    if key not in raw:
        return current
    value = raw[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{CONFIG_FILENAME}: {where}.{key} 는 문자열 배열이어야 합니다.")
    return list(value)


def _parse_convention(name: str, raw: Dict[str, Any]) -> Convention:
    conv = Convention(name=name)
    raw = _require(raw, f"conventions.{name}")

    commit_raw = _require(raw.get("commit", {}), f"conventions.{name}.commit")
    conv.commit.prefixes = _str_list(commit_raw, "prefixes", conv.commit.prefixes, f"{name}.commit")
    conv.commit.scope = str(commit_raw.get("scope", conv.commit.scope))
    conv.commit.body = str(commit_raw.get("body", conv.commit.body))
    conv.commit.language = str(commit_raw.get("language", conv.commit.language))
    conv.commit.title_recommended = _int_field(
        commit_raw, "title_recommended", conv.commit.title_recommended, f"{name}.commit"
    )
    conv.commit.title_max = _int_field(commit_raw, "title_max", conv.commit.title_max, f"{name}.commit")
    if conv.commit.scope not in ("required", "optional", "none"):
        raise ConfigError(f"{CONFIG_FILENAME}: {name}.commit.scope 는 required/optional/none 중 하나여야 합니다.")
    if conv.commit.title_recommended > conv.commit.title_max:
        raise ConfigError(
            f"{CONFIG_FILENAME}: {name}.commit.title_recommended({conv.commit.title_recommended}) 가 "
            f"title_max({conv.commit.title_max}) 보다 큽니다."
        )

    pr_raw = _require(raw.get("pr", {}), f"conventions.{name}.pr")
    conv.pr.title_max = _int_field(pr_raw, "title_max", conv.pr.title_max, f"{name}.pr")
    conv.pr.min_bullets = _int_field(pr_raw, "min_bullets", conv.pr.min_bullets, f"{name}.pr")
    conv.pr.extra_sections = _str_list(pr_raw, "extra_sections", conv.pr.extra_sections, f"{name}.pr")
    conv.pr.checklist = _str_list(pr_raw, "checklist", conv.pr.checklist, f"{name}.pr")
    conv.pr.tone = str(pr_raw.get("tone", conv.pr.tone))
    conv.pr.language = str(pr_raw.get("language", conv.pr.language))

    clash = [s for s in conv.pr.extra_sections if s in REQUIRED_PR_SECTIONS]
    if clash:
        raise ConfigError(
            f"{CONFIG_FILENAME}: {name}.pr.extra_sections 에 필수 섹션 {clash} 가 들어 있습니다. "
            "Why/What/How to Test 는 자동으로 포함되므로 따로 적지 않습니다."
        )
    return conv


def _parse_safety(raw: Dict[str, Any]) -> SafetyPolicy:
    raw = _require(raw, "safe_mode")
    policy = SafetyPolicy()
    policy.max_files = _int_field(raw, "max_files", policy.max_files, "safe_mode")
    policy.max_diff_lines = _int_field(raw, "max_diff_lines", policy.max_diff_lines, "safe_mode")

    extra = raw.get("extra_patterns", [])
    if extra:
        if not isinstance(extra, list):
            raise ConfigError(f"{CONFIG_FILENAME}: safe_mode.extra_patterns 는 배열이어야 합니다.")
        patterns = list(DEFAULT_PATTERNS)
        for item in extra:
            if not (isinstance(item, list) and len(item) == 3 and all(isinstance(v, str) for v in item)):
                raise ConfigError(
                    f"{CONFIG_FILENAME}: safe_mode.extra_patterns 의 각 원소는 "
                    '["규칙이름", "정규식", "치환문구"] 형태여야 합니다.'
                )
            name, regex, repl = item
            try:
                import re

                re.compile(regex)
            except re.error as exc:
                raise ConfigError(f"{CONFIG_FILENAME}: safe_mode 규칙 '{name}' 의 정규식이 잘못됐습니다: {exc}") from exc
            # 사용자 규칙을 앞에 둬 기본 규칙보다 먼저 적용한다.
            patterns.insert(0, (name, regex, repl))
        policy.patterns = patterns
    return policy


def load(repo_root: str, convention_name: str = "") -> Settings:
    """저장소 루트의 `.ai-gitgen.json` 을 읽어 :class:`Settings` 로 만든다.

    파일이 없으면 기본값으로 돌아간다(오류가 아니다).
    `convention_name` 이 주어졌는데 설정에 없으면 :class:`ConfigError`.
    """
    path = os.path.join(repo_root, CONFIG_FILENAME)
    settings = Settings()

    if not os.path.exists(path):
        if convention_name and convention_name != "default":
            raise ConfigError(
                f"--convention {convention_name} 를 쓰려면 저장소 루트에 {CONFIG_FILENAME} 이 있어야 합니다."
            )
        return settings

    try:
        with open(path, encoding="utf-8") as fp:
            raw = json.load(fp)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{CONFIG_FILENAME} 을 JSON 으로 읽을 수 없습니다: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{CONFIG_FILENAME} 을 열 수 없습니다: {exc}") from exc

    raw = _require(raw, "최상위")
    settings.source = path

    if "safe_mode" in raw:
        settings.safety = _parse_safety(raw["safe_mode"])

    conventions = _require(raw.get("conventions", {}), "conventions")
    settings.available = ["default", *sorted(conventions)]

    chosen = convention_name or str(raw.get("default_convention", "default"))
    if chosen == "default":
        settings.convention = copy.deepcopy(DEFAULT_CONVENTION)
        return settings
    if chosen not in conventions:
        raise ConfigError(
            f"컨벤션 '{chosen}' 이 {CONFIG_FILENAME} 에 없습니다. "
            f"사용 가능: {', '.join(settings.available)}"
        )
    settings.convention = _parse_convention(chosen, conventions[chosen])
    return settings

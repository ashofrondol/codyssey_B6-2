# AI 커밋/PR 자동 생성기

`git status` / `git diff` 결과를 읽어 **커밋 메시지**와 **Pull Request 초안**을
Claude API 로 만들어 터미널에 출력하는 CLI 도구. 코디세이 B6-2 미션 산출물이다.

핵심은 API 를 "부르는 것"이 아니라 **무엇을 입력으로 주고, 결과를 어떻게 검증하느냐**다.
이 저장소는 그 두 가지에 대부분의 코드를 쓴다 — [프롬프트 설계](aigitgen/prompts.py),
[안전 모드](aigitgen/redact.py), [형식 검증과 다듬기](aigitgen/polish.py).

- **개발 환경**: Python 3.10 이상 / 터미널 전용 (웹 화면 없음)
- **외부 의존성**: 공식 `anthropic` SDK 하나 (그 외는 전부 표준 라이브러리)
- **기본 모델**: `claude-sonnet-4-6` — `--temperature` 가 **실제로 동작하는** 모델을 기본값으로 뒀다
  (Opus 5 / Sonnet 5 등 최신 세대는 sampling 파라미터를 거부한다. 자세한 이유는 [설계 노트 2절](docs/설계_노트.md))
- **1회 실행 API 호출**: 기본 1회, 형식 위반 시 재생성 포함 **최대 2회**

---

## 1. 설치

```bash
git clone https://github.com/ashofrondol/codyssey_B6-2.git
cd codyssey_B6-2

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`python3 --version` 이 3.10 이상인지 먼저 확인한다.

## 2. 환경변수(API Key) 설정

키는 **환경변수로만** 읽는다. 코드 어디에도 키를 적지 않는다.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."      # macOS / Linux
```

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."      # Windows PowerShell
```

| 환경변수 | 우선순위 | 비고 |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | 1 | 공식 SDK 가 기본으로 읽는 이름 |
| `AI_API_KEY` | 2 | 과제 문서 예시가 쓰는 이름. 둘 중 아무거나 설정하면 된다 |

키가 없으면 트레이스백 대신 안내가 나온다 → [`docs/samples/07_no_api_key.txt`](docs/samples/07_no_api_key.txt)

```text
[ERROR] ANTHROPIC_API_KEY 또는 AI_API_KEY 환경변수가 설정되지 않았습니다.
        예) export ANTHROPIC_API_KEY="YOUR_KEY"
```

> API Key 를 셸 히스토리에 남기고 싶지 않다면 `read -s -p "key: " ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY` 를 쓴다.

## 3. 실행 방법

**Git 이 초기화된 프로젝트 루트**에서 실행한다.

```bash
python main.py commit          # 커밋 메시지 초안
python main.py pr              # PR 제목/본문 초안
```

키 없이 흐름만 보고 싶으면 `--dry-run` 을 붙인다. AI 를 부르지 않고
Git 사실만으로 초안을 조립하므로, 프롬프트·형식 검증 경로를 그대로 따라갈 수 있다.

```bash
python main.py commit --dry-run
```

### 명령 사용 예시

```bash
# 다른 저장소를 대상으로
python main.py commit --repo ../codyssey_B5-1

# 스테이지한 변경만
git add src/ && python main.py commit --staged

# 파라미터 조절
python main.py pr --temperature 0.5 --max-tokens 3000
python main.py commit --model claude-haiku-4-5

# 실제로 전송하는 프롬프트를 눈으로 확인
python main.py commit --show-prompt

# 팀 컨벤션 적용 (아래 7절)
python main.py pr --convention team

# 안전 모드 정책 조정
python main.py commit --max-files 3 --max-diff-lines 80
python main.py commit --no-safe-mode        # 권장하지 않음
```

### 전체 옵션

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| `--model` (`-model`) | `claude-sonnet-4-6` | 사용할 모델 |
| `--temperature` (`-temperature`) | `0.2` | 0.0~1.0. 낮을수록 결정적. sampling 미지원 모델에서는 경고 후 생략 |
| `--max-tokens` (`-max-tokens`) | `2000` | 응답 최대 토큰 |
| `--no-retry` | 꺼짐 | 형식 위반이어도 재생성하지 않음 (호출 1회 고정) |
| `--repo` | `.` | 대상 저장소 경로 |
| `--staged` | 꺼짐 | `git diff --cached` 만 수집 |
| `--safe-mode` / `--no-safe-mode` | **켜짐** | 민감정보 마스킹 + 전송량 제한 |
| `--max-files` | `10` | 전송할 최대 파일 수 |
| `--max-diff-lines` | `200` | 전송할 최대 diff 줄 수 |
| `--convention` | (없음) | `.ai-gitgen.json` 의 컨벤션 이름 |
| `--show-prompt` | 꺼짐 | 전송 페이로드 출력 |
| `--dry-run` | 꺼짐 | AI 호출 없이 흐름만 실행 |
| `--debug` | 꺼짐 | 오류 시 스택트레이스 출력 |

> `-model` 처럼 대시 하나짜리 표기도 받는다. 과제 문서 예시와 표기를 맞추기 위한 별칭이다.

---

## 4. 출력 예시

### 커밋 메시지

```text
$ python main.py commit

[INFO] 저장소: /work/demo-service (브랜치 feature/retry-on-timeout)
[INFO] Git status 수집 완료: 2개 파일 변경 감지
[INFO] git diff HEAD 수집 완료: 33줄
[INFO] 안전 모드 ON — 마스킹 2건 · 파일 2/2개 · 줄 33/33줄 · 규칙별(anthropic-key×1, email×1)
[INFO] AI API 요청 중... (model=claude-sonnet-4-6 temperature=0.2 max_tokens=2000)
[DONE] 커밋 메시지 생성 완료 — API 호출 1회, 토큰 입력 1,204 / 출력 168

----------------------- Commit Message -----------------------
feat(payment): 결제 요청에 지수 백오프 재시도 추가

- 타임아웃 발생 시 최대 3회까지 2^n 초 간격으로 재시도
- 변경 파일: src/payment.py, src/config.py
--------------------------------------------------------------
[INFO] 생성 결과는 초안입니다. 검토 후 직접 커밋/PR 에 적용하세요.
```

### PR 제목 / 본문

```text
-------------------------- PR Title --------------------------
feat: 결제 요청 타임아웃 재시도 처리 추가
--------------------------------------------------------------

-------------------------- PR Body ---------------------------
## Why
- 결제 게이트웨이 타임아웃 시 요청이 그대로 실패해 사용자가 재시도해야 했다

## What
- src/payment.py 의 charge() 에 지수 백오프 재시도 루프 추가
- src/config.py 에 TIMEOUT_SECONDS / MAX_RETRIES 상수 분리

## How to Test
- python -m pytest tests/test_payment.py 실행
- TimeoutError 를 주입해 3회 재시도 후 RuntimeError 가 나는지 확인
--------------------------------------------------------------
```

> 위 두 블록은 **형식을 보여주기 위한 예시**다. 모델 응답 문장은 실행할 때마다 달라진다.
> 아래 `docs/samples/` 는 이 저장소에서 **실제로 실행해 캡처한 출력**이다.

### 실제 캡처 (`bash demo.sh` 로 언제든 재생성)

| 파일 | 무엇을 보여주나 |
| --- | --- |
| [01_commit.txt](docs/samples/01_commit.txt) | `commit` 실행 흐름 전체 |
| [02_pr.txt](docs/samples/02_pr.txt) | `pr` 실행 — Why/What/How to Test 3섹션 |
| [03_pr_convention.txt](docs/samples/03_pr_convention.txt) | 팀 컨벤션 적용 후 (Risk 섹션 + 체크리스트) |
| [04_safemode_on.txt](docs/samples/04_safemode_on.txt) | 안전 모드 ON — 전송 프롬프트에 키가 없다 |
| [05_safemode_off.txt](docs/samples/05_safemode_off.txt) | 안전 모드 OFF — 같은 자리에 원문이 그대로 |
| [06_no_changes.txt](docs/samples/06_no_changes.txt) | 변경 사항이 없을 때 |
| [07_no_api_key.txt](docs/samples/07_no_api_key.txt) | API Key 미설정일 때 |

`bash demo.sh` 는 `--dry-run` 으로, `bash demo.sh --live` 는 실제 API 호출로 다시 캡처한다.

---

## 5. 민감정보 대응 — 안전 모드

`git diff` 에는 API Key, 토큰, 이메일이 섞여 들어갈 수 있다.
그대로 프롬프트에 넣으면 **외부 서비스로 비밀이 나간다.** 그래서 안전 모드가 **기본값으로 켜져** 있고,
두 가지 대응을 **모두** 적용한다.

**(A) 마스킹** — 정규표현식으로 찾아 `«MASKED:...»` 로 치환한다.

| 규칙 | 대상 |
| --- | --- |
| `private-key-block` | `-----BEGIN ... PRIVATE KEY-----` 블록 전체 |
| `anthropic-key` / `openai-key` | `sk-ant-...` / `sk-...` |
| `github-token` | `ghp_` `gho_` `ghu_` `ghs_` `ghr_` |
| `aws-access-key` | `AKIA...` / `ASIA...` |
| `google-api-key`, `slack-token`, `jwt`, `bearer` | 각 공급자 토큰 형태 |
| `assignment` | `password = "..."`, `SECRET: '...'` 같은 대입문의 **값** |
| `email`, `kr-rrn` | 이메일 주소, 주민등록번호 형태 |

**(B) 전송량 제한** — 기본 **최대 10개 파일 / 200줄**. 초과분은 잘라내고 생략 사실을 프롬프트에 적는다.
비용을 줄이는 동시에, 관련 없는 파일이 통째로 나가는 것을 막는다.

순서는 **마스킹 → 잘라내기** 다. 반대로 하면 잘려나간 구간의 비밀은 검사조차 되지 않는다.
([`redact.apply()`](aigitgen/redact.py) 의 주석과 `test_마스킹이_잘라내기보다_먼저_일어난다` 가 이 순서를 고정한다.)

### ON/OFF 결과 차이 (보너스 3 증빙)

같은 변경에 대해 `--show-prompt` 로 전송 페이로드를 찍어 비교한 것이다.

```diff
  [INFO] 안전 모드 ON — 마스킹 2건 · 파일 3/3개 · 줄 69/69줄 · 규칙별(anthropic-key×1, email×1)
  [INFO] 안전 모드 OFF — diff 를 가공하지 않고 그대로 전송합니다.

- +PG_API_KEY = "«MASKED:ANTHROPIC_KEY»"      ← 안전 모드 ON
- +SUPPORT_EMAIL = "«MASKED:EMAIL»"
+ +PG_API_KEY = "sk-ant-api03-EXAMPLE-NOT-A-REAL-KEY-000000"   ← 안전 모드 OFF
+ +SUPPORT_EMAIL = "billing@example.com"
```

전문: [04_safemode_on.txt](docs/samples/04_safemode_on.txt) ↔ [05_safemode_off.txt](docs/samples/05_safemode_off.txt)

**한계.** 정규표현식은 아는 형태만 잡는다. 사내 전용 토큰 형식은 `.ai-gitgen.json` 의
`safe_mode.extra_patterns` 에 직접 등록해야 한다(7절). 안전 모드는 **마지막 방어선이 아니라 안전망**이고,
애초에 비밀을 커밋하지 않는 것이 먼저다.

---

## 6. 비용과 요청 횟수

| 항목 | 값 |
| --- | --- |
| 한 번 실행의 API 호출 | **1회** (형식 위반 시 재생성 포함 최대 **2회**) |
| 호출 횟수 표시 | `[DONE] ... — API 호출 N회, 토큰 입력 x / 출력 y` 로 매번 출력 |
| 기본 `max_tokens` | 2000 |
| 전송 diff 상한 | 10개 파일 / 200줄 (안전 모드 ON 기준) |

호출 상한은 [`cli.MAX_API_CALLS`](aigitgen/cli.py) 에 상수로 박혀 있다. 재생성은 최대 1번이므로
`--no-retry` 를 쓰지 않아도 2회를 넘지 않는다.

**권장 사용법**

- 커밋 직전 한 번만 돌린다. 결과가 마음에 안 들면 `--temperature` 를 올려 **한 번 더** 돌리는 정도로 끝낸다.
- 거대한 변경은 먼저 커밋을 쪼갠다. diff 가 크면 비용도 늘고 요약 품질도 떨어진다.
- 형식만 확인하고 싶을 때는 `--dry-run` 을 쓴다. **비용이 0원**이다.
- `--max-diff-lines` 를 낮추면 입력 토큰이 곧바로 줄어든다.

---

## 7. 팀 컨벤션 커스터마이징 (보너스 2)

**대상 저장소 루트**에 `.ai-gitgen.json` 을 두면 커밋/PR 규칙을 바꿀 수 있다.
(이 저장소의 [`.ai-gitgen.json`](.ai-gitgen.json) 이 그대로 예시다.)

```json
{
  "safe_mode": {
    "max_files": 10,
    "max_diff_lines": 200,
    "extra_patterns": [["사내-사번", "EMP-\\d{6}", "«MASKED:EMP»"]]
  },
  "conventions": {
    "team": {
      "commit": { "prefixes": ["feat", "fix", "docs"], "scope": "required", "title_max": 72 },
      "pr": {
        "extra_sections": ["Risk"],
        "min_bullets": 2,
        "checklist": ["로컬 테스트 통과", "문서 갱신 확인", "민감정보 미포함 확인"]
      }
    }
  }
}
```

| 키 | 효과 |
| --- | --- |
| `commit.prefixes` | 허용 커밋 타입. 목록 밖 타입은 검증에서 걸리고 후처리로 교체된다 |
| `commit.scope` | `required` / `optional` / `none` |
| `commit.body` | `required` / `optional` / `none`. `none` 이면 본문을 지우고, `required` 면 비었을 때 Git 사실로 채운다 |
| `commit.title_recommended`, `title_max` | 제목 길이 규칙. 프롬프트와 검증기가 같은 숫자를 쓴다 |
| `pr.extra_sections` | Why/What/How to Test **뒤에** 붙는 섹션 |
| `pr.min_bullets` | 섹션당 최소 불릿 수 |
| `pr.checklist` | 본문 끝에 `- [ ]` 체크리스트로 붙는다 |
| `pr.tone` | 프롬프트에 그대로 전달되는 문체 지시 |
| `safe_mode.*` | 안전 모드 숫자 정책과 추가 마스킹 규칙 (보너스 3) |

> **Why/What/How to Test 는 컨벤션으로도 못 없앤다.** 과제가 요구한 필수 섹션이라
> `extra_sections` 에 이 셋을 적으면 설정 오류로 거부한다.
> `default_convention` 키로 `--convention` 없이도 기본 적용되게 할 수 있다.

### 적용 전 / 후 비교 (보너스 2 증빙)

| | 기본 (`python main.py pr`) | 팀 컨벤션 (`--convention team`) |
| --- | --- | --- |
| 섹션 | Why / What / How to Test | Why / What / How to Test / **Risk** |
| 섹션당 불릿 | 최소 1개 | 최소 **2개** |
| 체크리스트 | 없음 | **3개 항목** 자동 부착 |
| 커밋 스코프 | 선택 | **필수** (`feat(payment): ...`) |
| 마스킹 규칙 | 기본 12종 | 기본 12종 + **사번 / 슬랙 웹훅** |

전문: [02_pr.txt](docs/samples/02_pr.txt) ↔ [03_pr_convention.txt](docs/samples/03_pr_convention.txt)

---

## 8. 동작 흐름

```text
git status / git diff 수집        gitctx.py
        │  (변경 없으면 여기서 종료)
        ▼
안전 모드: 마스킹 → 잘라내기       redact.py
        ▼
프롬프트 조립 + JSON 스키마        prompts.py
        ▼
Claude API 호출 (1회)             client.py
        ▼
형식 검증 ── 위반? ─▶ 재생성 1회 ─┐  polish.py + cli.py
        │                        │
        ◀────────────────────────┘
        ▼
후처리로 남은 위반 보정            polish.py
        ▼
구분선으로 구획 나눠 출력          render.py
```

| 파일 | 역할 |
| --- | --- |
| [main.py](main.py) | 진입점 |
| [aigitgen/cli.py](aigitgen/cli.py) | 옵션 정의, 실행 흐름, 오류 → `[ERROR]` 한 줄 변환 |
| [aigitgen/gitctx.py](aigitgen/gitctx.py) | `git status` / `git diff` 수집 |
| [aigitgen/redact.py](aigitgen/redact.py) | 안전 모드 (마스킹 + 전송량 제한) |
| [aigitgen/prompts.py](aigitgen/prompts.py) | 시스템/사용자 프롬프트, 출력 JSON 스키마 |
| [aigitgen/client.py](aigitgen/client.py) | Claude API 호출, 예외 변환, 호출 횟수·토큰 집계 |
| [aigitgen/polish.py](aigitgen/polish.py) | 길이·템플릿 검증과 후처리 |
| [aigitgen/config.py](aigitgen/config.py) | `.ai-gitgen.json` 파싱 |
| [aigitgen/render.py](aigitgen/render.py) | 로그와 구획 출력 |
| [docs/설계_노트.md](docs/설계_노트.md) | 프롬프트·파라미터 설계 근거, 구조를 나눈 이유, 실무 적용 우선순위 |

`temperature` / `max_tokens` 를 바꿔 결과 차이를 직접 재현하는 절차는
[설계 노트 8절](docs/설계_노트.md#8-temperature--max_tokens-를-직접-실험해-보기)에 있다.

## 9. 테스트

```bash
python -m unittest discover -s tests -t tests -v
```

95개 테스트가 **네트워크도 API Key 도 없이** 2초 안에 끝난다.
AI 호출은 [`tests/helpers.py`](tests/helpers.py) 의 `FakeGenerator` 로 갈아끼우고,
Git 은 매번 임시 저장소를 새로 만들어 쓴다.

`anthropic` 이 설치돼 있으면 [`tests/test_client_wire.py`](tests/test_client_wire.py) 8건이 더 돈다.
로컬 HTTP 서버를 띄우고 `base_url` 을 그쪽으로 돌려 **실제로 전송되는 JSON 본문**을 받아 검사한다
(모델·`max_tokens`·스키마가 실렸는지, `temperature` 가 들어갔는지/빠졌는지, 401·500·연결 실패가
어떤 안내로 번역되는지). SDK 가 없으면 이 8건만 건너뛴다.

과제 제약은 서술이 아니라 **AST 검사**로 고정했다 (`tests/test_cli.py` 의 `TestConstraints`).

- 실행하는 git 하위 명령이 `rev-parse` / `status` / `diff` / `symbolic-ref` 로 제한되는가 → `git push` 는 호출 자체가 불가능
- `requests` / `urllib` / `socket` 을 직접 임포트하지 않는가 → 네트워크는 공식 SDK 로만
- 소스에 API Key 형태 문자열이 없는가
- `subprocess` 를 `gitctx.py` 밖에서 쓰지 않는가

## 10. 하지 않는 것

과제 제약에 따라 **원격 저장소를 건드리지 않는다.**

- `git push` 하지 않는다
- GitHub PR 을 API 로 만들지 않는다
- 커밋을 대신 실행하지 않는다 — 출력된 텍스트를 사용자가 **직접 검토하고** 적용한다

Git 수집 범위도 `git status` / `git diff` 로 한정한다.
추적되지 않은(untracked) 파일은 **이름만** 프롬프트에 넣고 내용은 넣지 않는다.

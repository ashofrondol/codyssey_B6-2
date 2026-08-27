#!/usr/bin/env bash
# README 에 실리는 출력 예시를 "실제 실행"으로 다시 만든다.
#
#   bash demo.sh            # AI API 호출 없이(--dry-run) 흐름·형식만 캡처
#   bash demo.sh --live     # ANTHROPIC_API_KEY 를 써서 진짜 생성 결과까지 캡처
#
# 결과는 docs/samples/*.txt 에 저장된다.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$ROOT/docs/samples"
SANDBOX="$(mktemp -d)"
LIVE=0
[[ "${1:-}" == "--live" ]] && LIVE=1

cleanup() { rm -rf "$SANDBOX"; }
trap cleanup EXIT

mkdir -p "$OUT"

# ── 1. 데모용 저장소를 만든다 ────────────────────────────────────────────────
git -C "$SANDBOX" init -q -b main .
git -C "$SANDBOX" config user.name "demo"
git -C "$SANDBOX" config user.email "demo@example.com"

mkdir -p "$SANDBOX/src"
cat > "$SANDBOX/src/payment.py" <<'PY'
def charge(amount):
    return {"ok": True, "amount": amount}
PY
cat > "$SANDBOX/README.md" <<'MD'
# demo service
MD
git -C "$SANDBOX" add -A
git -C "$SANDBOX" -c commit.gpgsign=false commit -qm "init: demo service"
git -C "$SANDBOX" checkout -q -b feature/retry-on-timeout

# 팀 컨벤션 설정은 "작업 대상 저장소" 루트에 둔다(보너스 2 데모).
cp "$ROOT/.ai-gitgen.json" "$SANDBOX/.ai-gitgen.json"

# ── 2. 의미 있는 변경 + 실수로 섞여 든 가짜 비밀 ─────────────────────────────
cat > "$SANDBOX/src/payment.py" <<'PY'
import time

# 아래 값은 데모용 가짜 키다. 실제 키가 아니다.
PG_API_KEY = "sk-ant-api03-EXAMPLE-NOT-A-REAL-KEY-000000"
SUPPORT_EMAIL = "billing@example.com"


def charge(amount, retries=3):
    """결제 요청. 타임아웃이면 지수 백오프로 재시도한다."""
    for attempt in range(retries):
        try:
            return _request(amount)
        except TimeoutError:
            time.sleep(2 ** attempt)
    raise RuntimeError("결제 게이트웨이 응답 없음")


def _request(amount):
    return {"ok": True, "amount": amount}
PY
cat > "$SANDBOX/src/config.py" <<'PY'
TIMEOUT_SECONDS = 5
MAX_RETRIES = 3
PY
git -C "$SANDBOX" add -A

run() { # run <출력파일> <설명> <인자...>
  local file="$1"; shift
  local title="$1"; shift
  {
    echo "\$ python main.py $*"
    echo
    (cd "$ROOT" && python3 main.py "$@" 2>&1) || true
  } > "$OUT/$file"
  echo "  → docs/samples/$file  ($title)"
}

MODE=(--dry-run)
if [[ $LIVE -eq 1 ]]; then
  MODE=()
  echo "[live] 실제 AI API 를 호출합니다."
fi

echo "샘플을 만드는 중..."
run 01_commit.txt          "커밋 메시지 생성"        commit --repo "$SANDBOX" "${MODE[@]}"
run 02_pr.txt              "PR 초안 생성"            pr     --repo "$SANDBOX" "${MODE[@]}"
run 03_pr_convention.txt   "팀 컨벤션 적용 (보너스 2)" pr    --repo "$SANDBOX" --convention team "${MODE[@]}"
run 04_safemode_on.txt     "안전 모드 ON 프롬프트"    commit --repo "$SANDBOX" --show-prompt --dry-run
run 05_safemode_off.txt    "안전 모드 OFF 프롬프트"   commit --repo "$SANDBOX" --show-prompt --no-safe-mode --dry-run

# 변경 사항 없음 / API Key 미설정 — 오류 경로도 실제로 찍는다
git -C "$SANDBOX" -c commit.gpgsign=false commit -qm "wip: 데모용 커밋"
run 06_no_changes.txt      "변경 사항 없음"          commit --repo "$SANDBOX"

git -C "$SANDBOX" checkout -q -- . 2>/dev/null || true
echo "x = 1" >> "$SANDBOX/src/config.py"
{
  echo "\$ unset ANTHROPIC_API_KEY AI_API_KEY"
  echo "\$ python main.py commit --repo \$SANDBOX"
  echo
  (cd "$ROOT" && env -u ANTHROPIC_API_KEY -u AI_API_KEY python3 main.py commit --repo "$SANDBOX" 2>&1) || true
} > "$OUT/07_no_api_key.txt"
echo "  → docs/samples/07_no_api_key.txt  (API Key 미설정)"

echo "완료. docs/samples/ 를 확인하세요."

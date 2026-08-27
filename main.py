#!/usr/bin/env python3
"""AI 커밋/PR 자동 생성기 진입점.

사용법:
    python main.py commit
    python main.py pr

자세한 옵션은 `python main.py --help` 참고.
"""

import sys

from aigitgen.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

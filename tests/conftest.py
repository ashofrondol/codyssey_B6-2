"""pytest 로 돌릴 때도 `import helpers` 가 되게 한다.

채점 실행 경로는 README 가 안내하는 `python -m unittest discover -s tests -t tests` 다.
`-t tests` 가 **최상위 디렉터리를 `tests/` 로** 지정하므로, 각 테스트 모듈은
`helpers` 를 최상위 모듈로 읽는다.

pytest 는 최상위를 저장소 루트로 잡기 때문에 같은 `import helpers` 가
`ModuleNotFoundError` 로 죽는다 — 테스트가 하나도 수집되지 않는다. 실행기를 바꿨을 뿐인데
전부 빨간 불이면, 사람은 테스트가 아니라 실행기를 의심하게 된다.

그래서 여기서 pytest 쪽 조건을 unittest 쪽에 맞춘다. 고칠 곳은 두 군데였다.

    (A) 테스트 파일의 import 문을 `from tests.helpers import ...` 로 바꾼다
    (B) pytest 에게 `tests/` 도 import 경로라고 알려준다  ← 이쪽

(A) 를 고르면 `-t tests` 로 도는 unittest 쪽이 깨진다(그때는 `tests` 패키지가
최상위가 아니다). 즉 **채점 실행 경로를 건드리게 된다.** 그래서 (B) 를 골랐다.
conftest.py 는 pytest 만 읽으므로 unittest 실행에는 아무 영향이 없다.

`tests/helpers.py` 가 저장소 루트를 `sys.path` 에 넣어 `aigitgen` 을 찾게 하는 것과
같은 이유·같은 방식이다.
"""

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

"""AI 커밋/PR 자동 생성기.

Git 변경 사항(`git status` / `git diff`)을 수집해 Claude API 로 넘기고,
커밋 메시지와 Pull Request 초안을 만들어 터미널에 출력한다.
"""

__version__ = "1.0.0"

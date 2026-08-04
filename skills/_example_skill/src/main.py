"""_example_skill 진입점 예시.

실제 스킬 작성 시 이 파일을 참고해 src/main.py를 구성하세요.
"""
from __future__ import annotations

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def run(name: str = "world") -> str:
    logger.info("run() 호출: name=%s", name)
    return f"hello, {name}"


if __name__ == "__main__":
    print(run())

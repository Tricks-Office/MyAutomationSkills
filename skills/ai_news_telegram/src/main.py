"""ai_news_telegram 진입점.

Hacker News에서 AI 관련 스토리를 수집해 규칙 기반으로 기술/비즈니스 카테고리로 분류하고,
Claude API 1회 호출로 카테고리별 Top5 랭킹/한국어 요약을 생성해 텔레그램으로 발송한다.
요구사항은 docs/PRD.md, docs/SRS.md, 구현 순서는 docs/IMPLEMENTATION_PLAN.md 참고.

Phase 0(뼈대): 실제 HN 검색/분류/Claude 랭킹 로직은 아직 없다. `.env`/`--mode` 로딩과
텔레그램 발송 함수의 end-to-end 배선만 하드코딩된 예시 메시지로 검증한다.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
REQUEST_TIMEOUT_SECONDS = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class RequiredEnvMissingError(RuntimeError):
    """필수 환경변수가 없을 때 발생 (SRS 7절: 완전 실패, 실행 시작 전 종료)."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """SRS FR-2: --mode는 daily/weekly만 허용, 그 외 값/누락 시 실행 시작 전 종료."""
    parser = argparse.ArgumentParser(description="AI 관련 Hot 뉴스를 텔레그램으로 발송")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["daily", "weekly"],
        help="daily: 최근 24시간, weekly: 최근 7일 이내 게시된 스토리를 검색 대상으로 함",
    )
    return parser.parse_args(argv)


def load_config() -> dict[str, str]:
    """SRS FR-1: 필수 환경변수를 로드하고, 하나라도 없으면 실행 시작 전에 실패 처리한다."""
    load_dotenv(REPO_ROOT / ".env")
    required = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY"]
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise RequiredEnvMissingError(f"필수 환경변수 누락: {', '.join(missing)}")
    return {key: os.environ[key] for key in required}


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    """Telegram Bot API로 메시지를 발송한다. 4096자 제한에 맞춰 분할 발송한다 (SRS FR-9)."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    chunk_size = 4000
    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)] or [text]

    for chunk in chunks:
        response = requests.post(
            url, json={"chat_id": chat_id, "text": chunk}, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"텔레그램 발송 실패: {payload}")


def build_placeholder_message(mode: str) -> str:
    """Phase 0 전용: 실제 HN/Claude 연동 전, 배선 확인용 하드코딩 예시 메시지."""
    return (
        f"[ai_news_telegram Phase 0 테스트 발송 - mode={mode}]\n\n"
        "📌 AI 기술 Hot 5 (예시)\n"
        "1. (예시) 신규 오픈소스 LLM 벤치마크 결과 공개\n"
        "   요약: 예시용 한줄 요약입니다.\n"
        "   🔗 https://example.com/tech-1 | 👍 120 | 💬 45\n\n"
        "💼 AI 비즈니스 Hot 5 (예시)\n"
        "1. (예시) AI 스타트업 대규모 투자 유치\n"
        "   요약: 예시용 한줄 요약입니다.\n"
        "   🔗 https://example.com/biz-1 | 👍 98 | 💬 30\n\n"
        "(실제 HN 검색/분류/Claude 랭킹 로직은 Phase 1~2에서 구현 예정)"
    )


def run(mode: str) -> int:
    """Phase 0 처리 흐름: 설정 로드 → 하드코딩 메시지 발송. 성공 0, 완전 실패 시 0이 아닌 값."""
    try:
        config = load_config()
    except RequiredEnvMissingError:
        logger.exception("환경변수 검증 실패, 실행을 시작하지 않음")
        return 1

    message = build_placeholder_message(mode)
    try:
        send_telegram_message(config["TELEGRAM_BOT_TOKEN"], config["TELEGRAM_CHAT_ID"], message)
    except Exception:
        logger.exception("텔레그램 발송 실패")
        return 1

    logger.info("Phase 0 end-to-end 발송 완료 (mode=%s)", mode)
    return 0


if __name__ == "__main__":
    args = parse_args()
    sys.exit(run(args.mode))

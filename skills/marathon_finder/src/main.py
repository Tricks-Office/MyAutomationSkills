"""marathon_finder 진입점.

한국(marathonmate.store)/중국 남경(Claude web_search)의 접수중인 5km 이상 도로 레이스를
수집해 data/marathon.db에 upsert하고, 발송 대상 대회를 텔레그램으로 요약 발송한다.
요구사항은 docs/PRD.md, docs/SRS.md 참고.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "marathon.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class Race:
    country: str  # "KR" | "CN"
    race_name: str
    race_date: str  # ISO date (YYYY-MM-DD)
    location: str
    distance_km: str  # 예: "5,10,21.0975,42.195"
    registration_start: str | None
    registration_end: str | None
    registration_url: str
    source_site: str


class RequiredEnvMissingError(RuntimeError):
    """필수 환경변수가 없을 때 발생 (SRS 7절: 완전 실패, 실행 시작 전 종료)."""


def load_config() -> dict[str, str]:
    load_dotenv(REPO_ROOT / ".env")
    required = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY"]
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise RequiredEnvMissingError(f"필수 환경변수 누락: {', '.join(missing)}")
    return {key: os.environ[key] for key in required}


def init_db(db_path: Path) -> None:
    """PRD 4절 스키마로 race 테이블을 생성한다 (이미 있으면 유지)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS race (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                country TEXT NOT NULL,
                race_name TEXT NOT NULL,
                race_date DATE NOT NULL,
                location TEXT,
                distance_km TEXT,
                registration_start DATE,
                registration_end DATE,
                registration_url TEXT,
                source_site TEXT,
                notify_telegram BOOLEAN NOT NULL DEFAULT 1,
                first_found_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                UNIQUE (country, race_name, race_date)
            )
            """
        )
        conn.commit()


def fetch_korea_races() -> list[Race]:
    """marathonmate.store 크롤링 (SRS FR-2). 핵심 로직 구현 단계에서 채운다."""
    raise NotImplementedError


def fetch_china_races(api_key: str) -> list[Race]:
    """Claude API web_search로 남경 대회 검색 (SRS FR-3). 핵심 로직 구현 단계에서 채운다."""
    raise NotImplementedError


def upsert_races(db_path: Path, races: list[Race]) -> tuple[int, int]:
    """races를 upsert하고 (신규 건수, 갱신 건수)를 반환한다 (SRS FR-5). 핵심 로직 구현 단계에서 채운다."""
    raise NotImplementedError


def get_notifiable_races(db_path: Path) -> list[Race]:
    """notify_telegram=TRUE이며 현재 접수중인 레코드를 조회한다 (SRS FR-6). 핵심 로직 구현 단계에서 채운다."""
    raise NotImplementedError


def format_telegram_message(races: list[Race]) -> str:
    """SRS FR-6/FR-7: 대회 목록 또는 0건 안내 메시지를 구성한다."""
    raise NotImplementedError


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    """Telegram Bot API로 메시지를 발송한다 (SRS FR-6)."""
    raise NotImplementedError


def run(db_path: Path = DEFAULT_DB_PATH) -> int:
    """SRS 6절 처리 흐름을 오케스트레이션한다. 성공 시 0, 완전 실패 시 0이 아닌 값을 반환한다."""
    try:
        config = load_config()
    except RequiredEnvMissingError:
        logger.exception("환경변수 검증 실패, 실행을 시작하지 않음")
        return 1

    init_db(db_path)

    korea_races: list[Race] = []
    china_races: list[Race] = []
    try:
        korea_races = fetch_korea_races()
    except Exception:
        logger.exception("한국(marathonmate.store) 수집 실패, 중국 결과로 계속 진행")

    try:
        china_races = fetch_china_races(config["ANTHROPIC_API_KEY"])
    except Exception:
        logger.exception("중국(Claude web_search) 수집 실패, 한국 결과로 계속 진행")

    if not korea_races and not china_races:
        logger.error("한국/중국 소스 모두 실패, 완전 실패로 종료")
        return 1

    new_count, updated_count = upsert_races(db_path, korea_races + china_races)
    logger.info("DB 반영 완료: 신규 %d건, 갱신 %d건", new_count, updated_count)

    notifiable = get_notifiable_races(db_path)
    message = format_telegram_message(notifiable)
    try:
        send_telegram_message(config["TELEGRAM_BOT_TOKEN"], config["TELEGRAM_CHAT_ID"], message)
    except Exception:
        logger.exception("텔레그램 발송 실패")
        return 1

    logger.info("실행 완료: 발송 대상 %d건", len(notifiable))
    return 0


if __name__ == "__main__":
    sys.exit(run())

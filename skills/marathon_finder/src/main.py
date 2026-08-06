"""marathon_finder 진입점.

한국(marathonmate.store)/중국 남경(Claude web_search)의 접수중인 5km 이상 도로 레이스를
수집해 data/marathon.db에 upsert하고, 발송 대상 대회를 텔레그램으로 요약 발송한다.
요구사항은 docs/PRD.md, docs/SRS.md 참고.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import anthropic
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "marathon.db"

KOREA_SCHEDULE_URL = "https://marathonmate.store/marathon-schedule"
MIN_DISTANCE_KM = 5.0
REQUEST_TIMEOUT_SECONDS = 30
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# SRS FR-10: marathonmate.store 목록 페이지 요청이 응답 지연으로 실패하면 그 실행의 한국
# 대회 전체가 누락된다(ai_news_telegram의 HN API 타임아웃 장애와 같은 유형). 연결 오류/
# 타임아웃/5xx만 재시도 대상(4xx는 재시도해도 결과가 같아 제외). 개별 대회 상세 페이지
# 요청은 이미 실패 시 폴백이 있어 대상이 아니다.
KOREA_SCHEDULE_REQUEST_TIMEOUT_SECONDS = 45
KOREA_SCHEDULE_MAX_ATTEMPTS = 3
KOREA_SCHEDULE_RETRY_BACKOFF_SECONDS = [1, 2]

CHINA_RACE_SCHEMA = {
    "type": "object",
    "properties": {
        "races": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "race_name": {"type": "string"},
                    "race_date": {
                        "type": "string",
                        "description": "YYYY-MM-DD 형식. 정확한 날짜를 모르면 빈 문자열",
                    },
                    "location": {"type": "string"},
                    "distance_km": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "예: [\"5\", \"10\", \"21.0975\", \"42.195\"]",
                    },
                    "registration_url": {"type": "string"},
                },
                "required": ["race_name", "race_date", "location", "distance_km", "registration_url"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["races"],
    "additionalProperties": False,
}

CHINA_SEARCH_PROMPT = """현재 시점 기준으로 중국 남경시(江苏省 南京市) 행정구역 내에서 접수 중인
5km 이상 도로/트레일 레이스를 web_search로 조사해줘. 상하이/쑤저우 등 인접 도시는 제외하고
남경시 행정구역 내 대회만 포함해. 각 대회의 대회명, 개최일(YYYY-MM-DD, 모르면 빈 문자열),
장소, 종목별 거리(km 숫자 문자열 배열), 공식 신청 링크를 정리해줘.
찾은 대회가 없으면 races를 빈 배열로 응답해."""

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


def parse_distances_km(text: str) -> list[float]:
    """대회 거리 표기 텍스트에서 km 단위 숫자를 추출한다 (SRS: 거리 기준만으로 필터)."""
    distances: list[float] = []
    if "하프" in text:
        distances.append(21.0975)
    if re.search(r"풀(?!코스)", text) or "풀코스" in text:
        distances.append(42.195)
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*km?\b", text, re.IGNORECASE):
        distances.append(float(match.group(1)))
    return distances


def format_distance_km(distances: list[float]) -> str:
    def fmt(value: float) -> str:
        return str(int(value)) if value == int(value) else str(value)

    return ",".join(fmt(d) for d in distances)


def _extract_registration_url(detail_html: str, fallback_url: str) -> str:
    """대회 상세 페이지의 JSON-LD(SportsEvent)에서 신청 링크를 추출한다."""
    soup = BeautifulSoup(detail_html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if data.get("@type") != "SportsEvent":
            continue
        offers = data.get("offers") or {}
        if isinstance(offers, dict) and offers.get("url"):
            return offers["url"]
        organizer = data.get("organizer") or {}
        if isinstance(organizer, dict) and organizer.get("url"):
            return organizer["url"]
    return fallback_url


def _is_retryable_request_error(exc: requests.exceptions.RequestException) -> bool:
    """연결 오류/타임아웃은 항상 재시도, HTTPError는 5xx만 재시도(4xx는 반복해도 결과가 같음)."""
    if isinstance(exc, requests.exceptions.HTTPError):
        return exc.response is not None and exc.response.status_code >= 500
    return True


def _fetch_korea_schedule_html(headers: dict[str, str]) -> str:
    """SRS FR-10: 목록 페이지 요청은 연결 오류/타임아웃/5xx에 한해 최대 3회까지 재시도한다."""
    for attempt in range(1, KOREA_SCHEDULE_MAX_ATTEMPTS + 1):
        try:
            response = requests.get(
                KOREA_SCHEDULE_URL, headers=headers, timeout=KOREA_SCHEDULE_REQUEST_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as exc:
            is_last_attempt = attempt == KOREA_SCHEDULE_MAX_ATTEMPTS
            if is_last_attempt or not _is_retryable_request_error(exc):
                logger.error("marathonmate.store 목록 페이지 요청 실패(재시도 중단), 원인=%s", exc)
                raise
            delay = KOREA_SCHEDULE_RETRY_BACKOFF_SECONDS[attempt - 1]
            logger.warning(
                "marathonmate.store 목록 페이지 요청 실패(%d/%d회 시도), %.0f초 뒤 재시도: 원인=%s",
                attempt,
                KOREA_SCHEDULE_MAX_ATTEMPTS,
                delay,
                exc,
            )
            time.sleep(delay)


def fetch_korea_races() -> list[Race]:
    """marathonmate.store 크롤링 (SRS FR-2)."""
    headers = {"User-Agent": USER_AGENT}
    html = _fetch_korea_schedule_html(headers)

    soup = BeautifulSoup(html, "html.parser")
    races: list[Race] = []

    for row in soup.select("table tbody tr"):
        cells = row.find_all("td")
        if len(cells) < 5:
            continue
        race_date = cells[0].get_text(strip=True)
        name_link = cells[1].find("a")
        if not name_link:
            continue
        race_name = name_link.get_text(strip=True)
        location = cells[2].get_text(strip=True)
        distance_text = cells[3].get_text(strip=True)
        status = cells[4].get_text(strip=True)

        if status != "접수중":
            continue

        distances = parse_distances_km(distance_text)
        if not distances or max(distances) < MIN_DISTANCE_KM:
            continue

        detail_url = urljoin(KOREA_SCHEDULE_URL, name_link["href"])
        registration_url = detail_url
        try:
            detail_response = requests.get(detail_url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
            detail_response.raise_for_status()
            registration_url = _extract_registration_url(detail_response.text, detail_url)
        except requests.RequestException:
            logger.warning("한국 대회 상세 페이지 조회 실패, 목록 페이지 링크로 대체: %s", detail_url)

        races.append(
            Race(
                country="KR",
                race_name=race_name,
                race_date=race_date,
                location=location,
                distance_km=format_distance_km(distances),
                registration_start=None,
                registration_end=None,
                registration_url=registration_url,
                source_site="marathonmate.store",
            )
        )

    logger.info("한국(marathonmate.store) 수집 완료: %d건", len(races))
    return races


def fetch_china_races(api_key: str) -> list[Race]:
    """Claude API web_search로 남경 대회 검색 (SRS FR-3)."""
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=4096,
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}],
        output_config={"format": {"type": "json_schema", "schema": CHINA_RACE_SCHEMA}},
        messages=[{"role": "user", "content": CHINA_SEARCH_PROMPT}],
    )

    if response.stop_reason == "refusal":
        logger.error("중국 검색 요청이 거부됨(stop_reason=refusal), 0건으로 처리")
        return []

    text_blocks = [block.text for block in response.content if block.type == "text"]
    if not text_blocks:
        logger.error("중국 검색 응답에 텍스트 블록 없음, 원본 응답: %s", response.content)
        return []

    raw_text = text_blocks[-1]
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.error("중국 검색 응답 JSON 파싱 실패, 원본 응답: %s", raw_text)
        return []

    races: list[Race] = []
    for item in payload.get("races", []):
        race_date = item.get("race_date", "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", race_date):
            logger.warning("중국 대회 날짜 형식 불명확, 건너뜀: %s (%s)", item.get("race_name"), race_date)
            continue

        distances = []
        for raw_distance in item.get("distance_km", []):
            try:
                distances.append(float(raw_distance))
            except (TypeError, ValueError):
                continue
        if not distances or max(distances) < MIN_DISTANCE_KM:
            continue

        races.append(
            Race(
                country="CN",
                race_name=item.get("race_name", ""),
                race_date=race_date,
                location=item.get("location", ""),
                distance_km=format_distance_km(distances),
                registration_start=None,
                registration_end=None,
                registration_url=item.get("registration_url", ""),
                source_site="claude_web_search",
            )
        )

    logger.info("중국(Claude web_search) 수집 완료: %d건", len(races))
    return races


def upsert_races(db_path: Path, races: list[Race]) -> tuple[int, int]:
    """races를 upsert하고 (신규 건수, 갱신 건수)를 반환한다 (SRS FR-5)."""
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    updated_count = 0

    with sqlite3.connect(db_path) as conn:
        for race in races:
            existing = conn.execute(
                "SELECT id FROM race WHERE country = ? AND race_name = ? AND race_date = ?",
                (race.country, race.race_name, race.race_date),
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE race
                    SET location = ?, distance_km = ?, registration_start = ?,
                        registration_end = ?, registration_url = ?, source_site = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        race.location,
                        race.distance_km,
                        race.registration_start,
                        race.registration_end,
                        race.registration_url,
                        race.source_site,
                        now,
                        existing[0],
                    ),
                )
                updated_count += 1
            else:
                conn.execute(
                    """
                    INSERT INTO race (
                        country, race_name, race_date, location, distance_km,
                        registration_start, registration_end, registration_url,
                        source_site, notify_telegram, first_found_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        race.country,
                        race.race_name,
                        race.race_date,
                        race.location,
                        race.distance_km,
                        race.registration_start,
                        race.registration_end,
                        race.registration_url,
                        race.source_site,
                        now,
                        now,
                    ),
                )
                new_count += 1
        conn.commit()

    return new_count, updated_count


def get_notifiable_races(db_path: Path, races: list[Race]) -> list[Race]:
    """이번 실행에서 수집된 대회 중 notify_telegram=TRUE인 레코드를 조회한다 (SRS FR-6).

    registration_end를 신뢰할 수 있는 소스가 없어(SRS 6절), "현재 접수중"은 이번 실행에서
    실제로 수집된 대회로 판단하고, notify_telegram 값만 DB에서 다시 확인한다
    (다른 스킬이 FALSE로 바꿨을 수 있으므로).
    """
    if not races:
        return []

    notifiable: list[Race] = []
    with sqlite3.connect(db_path) as conn:
        for race in races:
            row = conn.execute(
                """
                SELECT notify_telegram FROM race
                WHERE country = ? AND race_name = ? AND race_date = ?
                """,
                (race.country, race.race_name, race.race_date),
            ).fetchone()
            if row and row[0]:
                notifiable.append(race)

    return notifiable


def format_telegram_message(races: list[Race]) -> str:
    """SRS FR-6/FR-7: 대회 목록 또는 0건 안내 메시지를 구성한다."""
    if not races:
        return "이번 실행에서 접수중인 신규 대회가 없습니다."

    lines = [f"접수중인 대회 {len(races)}건"]
    for race in races:
        lines.append(
            "\n".join(
                [
                    "",
                    f"• {race.race_name}",
                    f"  날짜: {race.race_date} | 장소: {race.location}",
                    f"  거리: {race.distance_km}km",
                    f"  신청: {race.registration_url}",
                ]
            )
        )
    return "\n".join(lines)


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    """Telegram Bot API로 메시지를 발송한다 (SRS FR-6). 4096자 제한에 맞춰 분할 발송."""
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

    collected = korea_races + china_races
    new_count, updated_count = upsert_races(db_path, collected)
    logger.info("DB 반영 완료: 신규 %d건, 갱신 %d건", new_count, updated_count)

    notifiable = get_notifiable_races(db_path, collected)
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

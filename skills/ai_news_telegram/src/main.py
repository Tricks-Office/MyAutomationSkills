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
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
REQUEST_TIMEOUT_SECONDS = 30

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
HN_HITS_PER_PAGE = 1000
MODE_WINDOW_SECONDS = {"daily": 86400, "weekly": 86400 * 7}

# SRS FR-4: AI 관련 스토리 판별 키워드 (title/story_text 매칭, 대소문자 무시)
AI_KEYWORDS = [
    "ai",
    "artificial intelligence",
    "llm",
    "gpt",
    "machine learning",
    "generative",
    "openai",
    "anthropic",
    "claude",
    "gemini",
    "deep learning",
    "neural network",
]

# SRS FR-5: 기술/비즈니스 카테고리 분류 키워드 (두 세트 모두 매칭되면 개수 비교, 동률이면 기술)
TECH_KEYWORDS = [
    "model",
    "benchmark",
    "open source",
    "open-source",
    "research",
    "paper",
    "algorithm",
    "training",
    "inference",
    "dataset",
    "architecture",
]
BIZ_KEYWORDS = [
    "funding",
    "raise",
    "valuation",
    "acquisition",
    "acquire",
    "ipo",
    "revenue",
    "enterprise",
    "partnership",
    "regulation",
    "lawsuit",
    "layoff",
    "hiring",
    "investment",
    "investor",
    "startup",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class HNStory:
    item_id: str
    title: str
    url: str
    text: str
    points: int
    num_comments: int
    created_at_i: int
    category: str | None = field(default=None)
    hot_score: float | None = field(default=None)


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


def _hn_story_from_hit(hit: dict) -> HNStory | None:
    object_id = hit.get("objectID")
    if not object_id:
        return None
    return HNStory(
        item_id=object_id,
        title=hit.get("title") or "",
        url=hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}",
        text=hit.get("story_text") or "",
        points=hit.get("points") or 0,
        num_comments=hit.get("num_comments") or 0,
        created_at_i=hit.get("created_at_i") or 0,
    )


def _search_hn(query: str, since: int) -> list[dict]:
    params = {
        "query": query,
        "tags": "story",
        "numericFilters": f"created_at_i>{since}",
        "hitsPerPage": HN_HITS_PER_PAGE,
    }
    response = requests.get(HN_SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json().get("hits", [])


def fetch_hn_stories(mode: str, now: int | None = None) -> list[HNStory]:
    """HN Algolia API에서 기간 필터를 적용해 AI 관련 스토리를 검색한다 (SRS FR-3).

    HN Algolia의 `query`는 여러 단어를 한 번에 넘기면 AND(모두 포함) 조건으로 동작해
    ("ai openai"처럼 결합하면 두 단어를 모두 포함한 글만 반환) 키워드 하나를 통으로
    묶어 보낼 수 없다. 그래서 AI_KEYWORDS 각각으로 개별 검색해 objectID 기준으로
    합치는(OR) 방식을 쓴다. AI 관련 여부의 최종 판단은 filter_ai_related()가 한 번 더
    맡는다 (SRS FR-4).
    """
    now = now if now is not None else int(time.time())
    since = now - MODE_WINDOW_SECONDS[mode]

    stories_by_id: dict[str, HNStory] = {}
    for keyword in AI_KEYWORDS:
        for hit in _search_hn(keyword, since):
            story = _hn_story_from_hit(hit)
            if story is not None and story.item_id not in stories_by_id:
                stories_by_id[story.item_id] = story

    stories = list(stories_by_id.values())
    logger.info("HN 검색 완료 (mode=%s): %d건(중복 제거 후)", mode, len(stories))
    return stories


def _count_keyword_matches(text: str, keywords: list[str]) -> int:
    """단어 경계 기준으로 매칭하되, 복수형/소유격(-s, -'s)은 같은 키워드로 취급한다.

    엄격한 \\b{kw}\\b만 쓰면 "LLM"은 매칭되고 "LLMs"는 안 되는 등 복수형을 놓친다
    (실제 HN 데이터로 검증 중 발견).
    """
    return sum(
        1
        for kw in keywords
        if re.search(rf"\b{re.escape(kw)}(?:'s|s)?\b", text, re.IGNORECASE)
    )


def filter_ai_related(stories: list[HNStory]) -> list[HNStory]:
    """SRS FR-4: title/story_text에 AI_KEYWORDS가 하나라도 있는 스토리만 남긴다.

    fetch_hn_stories()의 키워드별 검색은 Algolia의 typo-tolerance/관련도 매칭으로
    관련 없는 글이 섞여 들어올 수 있어(예: 오타 유사도로 매칭), 여기서 우리가 정한
    키워드 목록으로 단어 경계 기준 재검증한다.
    """
    filtered = [
        story
        for story in stories
        if _count_keyword_matches(f"{story.title} {story.text}", AI_KEYWORDS) > 0
    ]
    logger.info("AI 키워드 필터 통과: %d/%d건", len(filtered), len(stories))
    return filtered


def classify_category(story: HNStory) -> str:
    """SRS FR-5: 기술/비즈니스 키워드 매칭 개수를 비교해 분류, 동률(0:0 포함)이면 기술."""
    text = f"{story.title} {story.text}"
    tech_count = _count_keyword_matches(text, TECH_KEYWORDS)
    biz_count = _count_keyword_matches(text, BIZ_KEYWORDS)
    return "tech" if tech_count >= biz_count else "biz"


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

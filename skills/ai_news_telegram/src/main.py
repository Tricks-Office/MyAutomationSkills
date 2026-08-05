"""ai_news_telegram 진입점.

Hacker News에서 AI 관련 스토리를 수집해 규칙 기반으로 기술/비즈니스 카테고리로 분류하고,
Claude API 1회 호출로 카테고리별 Top5 랭킹/한국어 요약을 생성해 텔레그램으로 발송한다.
요구사항은 docs/PRD.md, docs/SRS.md, 구현 순서는 docs/IMPLEMENTATION_PLAN.md 참고.

발송 이력 기반 중복 방지(SRS FR-6/FR-11)와 세부 예외 처리는 아직 없다 (Implementation
Plan Phase 3에서 구현 예정).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
REQUEST_TIMEOUT_SECONDS = 30

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
HN_HITS_PER_PAGE = 1000
MODE_WINDOW_SECONDS = {"daily": 86400, "weekly": 86400 * 7}

# SRS FR-7: Hot 점수 = points + comments * 가중치. 댓글은 단순 동의 표시인 points보다
# 적극적인 관여(논쟁/토론)를 나타낸다고 보고 더 높은 가중치를 준다.
HOT_SCORE_COMMENT_WEIGHT = 2.0
CANDIDATE_POOL_SIZE = 15

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

CATEGORY_LABELS = {"tech": "📌 AI 기술 Hot 5", "biz": "💼 AI 비즈니스 Hot 5"}
MIN_EXPECTED_ITEMS = 5

# SRS FR-8: Claude 응답 구조. item_id는 후보 목록의 값을 그대로 돌려받아야
# 랭킹 결과를 원본 HNStory(url/points/comments)와 다시 연결할 수 있다.
RANKING_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "item_id": {"type": "string"},
        "title_ko": {"type": "string"},
        "summary_ko": {"type": "string"},
    },
    "required": ["item_id", "title_ko", "summary_ko"],
    "additionalProperties": False,
}
RANKING_SCHEMA = {
    "type": "object",
    "properties": {
        "tech": {"type": "array", "items": RANKING_ITEM_SCHEMA},
        "biz": {"type": "array", "items": RANKING_ITEM_SCHEMA},
    },
    "required": ["tech", "biz"],
    "additionalProperties": False,
}

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


def compute_hot_score(story: HNStory) -> float:
    """SRS FR-7: 규칙 기반 인기 점수 = points + comments * HOT_SCORE_COMMENT_WEIGHT."""
    return story.points + story.num_comments * HOT_SCORE_COMMENT_WEIGHT


def build_candidate_pools(
    stories: list[HNStory], pool_size: int = CANDIDATE_POOL_SIZE
) -> dict[str, list[HNStory]]:
    """SRS FR-5/FR-7: 카테고리를 매기고 Hot 점수 상위 pool_size건씩 후보 풀을 만든다."""
    pools: dict[str, list[HNStory]] = {"tech": [], "biz": []}
    for story in stories:
        story.category = classify_category(story)
        story.hot_score = compute_hot_score(story)
        pools[story.category].append(story)

    for category, items in pools.items():
        items.sort(key=lambda s: s.hot_score, reverse=True)
        pools[category] = items[:pool_size]

    logger.info(
        "카테고리별 후보 풀 구성 완료: 기술 %d건, 비즈니스 %d건",
        len(pools["tech"]),
        len(pools["biz"]),
    )
    return pools


def collect_candidate_pools(mode: str) -> dict[str, list[HNStory]]:
    """SRS 6절 [검색]→[필터링]→[분류]→[점수화] 단계를 순서대로 실행한다."""
    stories = fetch_hn_stories(mode)
    ai_stories = filter_ai_related(stories)
    return build_candidate_pools(ai_stories)


class RankingError(RuntimeError):
    """Claude 랭킹/요약 호출 또는 응답 파싱이 실패했을 때 발생 (SRS 7절: 완전 실패)."""


def _build_ranking_prompt(pools: dict[str, list[HNStory]]) -> str:
    lines = [
        "아래 Hacker News 후보 중에서 카테고리별로 실제로 가장 화제성 있는(Hot) 소식을 골라줘.",
        "규칙:",
        "- tech, biz 각 카테고리에서 최대 5개까지 선정한다. 후보가 5개보다 적으면 있는 만큼만 선정한다.",
        "- item_id는 아래 후보 목록에 있는 값을 그대로 사용해야 하며, 목록에 없는 item_id를 만들어내면 안 된다.",
        "- title_ko는 원문 제목을 자연스러운 한국어로 번역/의역한다.",
        "- summary_ko는 핵심 내용을 한국어 한 문장으로 요약한다.",
        "",
        "[기술(tech) 후보]",
    ]
    for story in pools.get("tech", []):
        lines.append(f"- item_id={story.item_id} | points={story.points} comments={story.num_comments} | {story.title}")
    lines.append("")
    lines.append("[비즈니스(biz) 후보]")
    for story in pools.get("biz", []):
        lines.append(f"- item_id={story.item_id} | points={story.points} comments={story.num_comments} | {story.title}")
    return "\n".join(lines)


def _merge_ranked_items(pool: list[HNStory], ranked_items: list[dict]) -> list[dict]:
    """Claude가 반환한 item_id를 원본 HNStory(url/points/comments)와 다시 연결한다.

    후보 목록에 없는 item_id를 반환하면(모델의 실수) 조용히 건너뛴다 — 없는 링크로
    발송하는 것보다 안전하다.
    """
    pool_by_id = {story.item_id: story for story in pool}
    merged = []
    for item in ranked_items:
        story = pool_by_id.get(item.get("item_id"))
        if story is None:
            logger.warning("Claude가 후보 목록에 없는 item_id를 반환해 건너뜀: %s", item.get("item_id"))
            continue
        merged.append(
            {
                "item_id": story.item_id,
                "title_ko": item.get("title_ko", ""),
                "summary_ko": item.get("summary_ko", ""),
                "url": story.url,
                "points": story.points,
                "num_comments": story.num_comments,
            }
        )
    return merged


def rank_and_summarize(pools: dict[str, list[HNStory]], api_key: str) -> dict[str, list[dict]]:
    """SRS FR-8: Claude API를 1회 호출해 카테고리별 최종 Top5 + 한국어 요약을 만든다."""
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=4096,
        output_config={"format": {"type": "json_schema", "schema": RANKING_SCHEMA}},
        messages=[{"role": "user", "content": _build_ranking_prompt(pools)}],
    )

    if response.stop_reason == "refusal":
        raise RankingError("Claude 랭킹 요청이 거부됨(stop_reason=refusal)")

    text_blocks = [block.text for block in response.content if block.type == "text"]
    if not text_blocks:
        raise RankingError(f"Claude 응답에 텍스트 블록 없음, 원본 응답: {response.content}")

    raw_text = text_blocks[-1]
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RankingError(f"Claude 응답 JSON 파싱 실패, 원본 응답: {raw_text}") from exc

    # Claude structured output는 array에 maxItems를 지원하지 않아(400 에러) 스키마로
    # 5개 제한을 강제할 수 없다. 프롬프트로 "최대 5개"를 지시했지만 실제로 6개를 반환한
    # 사례를 확인해, 여기서 코드로 다시 한번 자른다 (SRS FR-8: 카테고리별 최종 Top5).
    ranked = {
        "tech": _merge_ranked_items(pools.get("tech", []), payload.get("tech", []))[:MIN_EXPECTED_ITEMS],
        "biz": _merge_ranked_items(pools.get("biz", []), payload.get("biz", []))[:MIN_EXPECTED_ITEMS],
    }
    logger.info(
        "Claude 랭킹/요약 완료: 기술 %d건, 비즈니스 %d건",
        len(ranked["tech"]),
        len(ranked["biz"]),
    )
    return ranked


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


def _format_category_section(category: str, items: list[dict]) -> str:
    """SRS FR-9/FR-10: 카테고리 섹션을 구성하고, 후보 부족/0건 안내 문구를 붙인다."""
    lines = [CATEGORY_LABELS[category]]

    if not items:
        lines.append("이번 기간 동안 발견된 소식이 없습니다.")
        return "\n".join(lines)

    if len(items) < MIN_EXPECTED_ITEMS:
        lines.append(f"(이번 실행에서는 후보가 {len(items)}건뿐입니다)")

    for idx, item in enumerate(items, start=1):
        lines.append(
            "\n".join(
                [
                    "",
                    f"{idx}. {item['title_ko']}",
                    f"   {item['summary_ko']}",
                    f"   🔗 {item['url']} | 👍 {item['points']} | 💬 {item['num_comments']}",
                ]
            )
        )
    return "\n".join(lines)


def format_final_message(mode: str, ranked: dict[str, list[dict]]) -> str:
    """SRS FR-9: 기술/비즈니스 섹션을 포함한 최종 텔레그램 메시지를 구성한다."""
    mode_label = "일간" if mode == "daily" else "주간"
    sections = [
        f"🤖 AI 뉴스 브리핑 ({mode_label})",
        "",
        _format_category_section("tech", ranked.get("tech", [])),
        "",
        _format_category_section("biz", ranked.get("biz", [])),
    ]
    return "\n".join(sections)


def run(mode: str) -> int:
    """SRS 6절 처리 흐름(발송 이력 DB 제외): 설정 로드 → 후보 수집 → Claude 랭킹/요약
    → 포맷팅 → 발송. 성공 0, 완전 실패 시 0이 아닌 값."""
    try:
        config = load_config()
    except RequiredEnvMissingError:
        logger.exception("환경변수 검증 실패, 실행을 시작하지 않음")
        return 1

    try:
        pools = collect_candidate_pools(mode)
        ranked = rank_and_summarize(pools, config["ANTHROPIC_API_KEY"])
    except Exception:
        logger.exception("후보 수집 또는 Claude 랭킹/요약 실패")
        return 1

    message = format_final_message(mode, ranked)
    try:
        send_telegram_message(config["TELEGRAM_BOT_TOKEN"], config["TELEGRAM_CHAT_ID"], message)
    except Exception:
        logger.exception("텔레그램 발송 실패")
        return 1

    logger.info(
        "실행 완료 (mode=%s): 기술 %d건, 비즈니스 %d건 발송",
        mode,
        len(ranked.get("tech", [])),
        len(ranked.get("biz", [])),
    )
    return 0


if __name__ == "__main__":
    args = parse_args()
    sys.exit(run(args.mode))

"""marathon_search 진입점.

data/marathon.db의 race 테이블을 읽기 전용으로 검색해, 알림 여부/기간/장소/대회명 조건을
하나 이상 조합(AND 또는 OR)해 필터링한 결과를 반환한다. 요구사항은 docs/PRD.md 참고.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "marathon.db"

EXIT_SUCCESS = 0
EXIT_ARG_ERROR = 1
EXIT_SYSTEM_ERROR = 2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class RaceRecord:
    id: int
    country: str
    race_name: str
    race_date: str
    location: str | None
    distance_km: str | None
    registration_end: str | None
    registration_url: str | None
    notify_telegram: bool


class ArgumentError(ValueError):
    """커맨드라인 인자 조합이 잘못됐을 때 발생 (PRD 6절: 인자 오류, DB 접근 안 함)."""


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="data/marathon.db의 race 테이블을 조건으로 필터링해 검색한다."
    )
    parser.add_argument("--notify", choices=["on", "off"], help="notify_telegram 필터")
    parser.add_argument("--date-from", help="대회 개최일(race_date) 하한 (YYYY-MM-DD)")
    parser.add_argument("--date-to", help="대회 개최일(race_date) 상한 (YYYY-MM-DD)")
    parser.add_argument("--location", help="장소 부분 일치 검색어")
    parser.add_argument("--name", help="대회명 부분 일치 검색어")
    parser.add_argument(
        "--match",
        choices=["and", "or"],
        default="and",
        help="필터 카테고리가 2개 이상일 때 결합 방식 (기본값 and)",
    )
    return parser


def _validate_date_str(value: str, field: str) -> None:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ArgumentError(f"{field}는 YYYY-MM-DD 형식이어야 합니다: {value}") from exc


def validate_args(args: argparse.Namespace) -> None:
    """PRD 3절 인자 규칙을 검증한다."""
    has_notify = args.notify is not None
    has_period = bool(args.date_from or args.date_to)
    has_location = bool(args.location)
    has_name = bool(args.name)

    if not (has_notify or has_period or has_location or has_name):
        raise ArgumentError(
            "--notify/--date-from/--date-to/--location/--name 중 최소 1개는 지정해야 합니다."
        )

    if args.date_from:
        _validate_date_str(args.date_from, "--date-from")
    if args.date_to:
        _validate_date_str(args.date_to, "--date-to")
    if args.date_from and args.date_to and args.date_from > args.date_to:
        raise ArgumentError("--date-from은 --date-to보다 미래일 수 없습니다.")


def _row_to_record(row: sqlite3.Row) -> RaceRecord:
    return RaceRecord(
        id=row["id"],
        country=row["country"],
        race_name=row["race_name"],
        race_date=row["race_date"],
        location=row["location"],
        distance_km=row["distance_km"],
        registration_end=row["registration_end"],
        registration_url=row["registration_url"],
        notify_telegram=bool(row["notify_telegram"]),
    )


def _build_categories(
    *,
    notify: str | None,
    date_from: str | None,
    date_to: str | None,
    location: str | None,
    name: str | None,
) -> list[tuple[str, list[object]]]:
    """PRD 3/4절: 알림/기간/장소/대회명 4개 카테고리별 SQL 조건을 만든다."""
    categories: list[tuple[str, list[object]]] = []

    if notify is not None:
        categories.append(("notify_telegram = ?", [1 if notify == "on" else 0]))

    if date_from or date_to:
        subconditions: list[str] = []
        subparams: list[object] = []
        if date_from:
            subconditions.append("race_date >= ?")
            subparams.append(date_from)
        if date_to:
            subconditions.append("race_date <= ?")
            subparams.append(date_to)
        categories.append((" AND ".join(subconditions), subparams))

    if location:
        categories.append(("location LIKE ?", [f"%{location}%"]))

    if name:
        categories.append(("race_name LIKE ?", [f"%{name}%"]))

    return categories


def search_races(
    db_path: Path,
    *,
    notify: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    location: str | None = None,
    name: str | None = None,
    match: str = "and",
) -> list[RaceRecord]:
    """PRD 5절: 카테고리별 조건을 만들고 --match(and/or)로 결합해 race 테이블을 조회한다."""
    categories = _build_categories(
        notify=notify, date_from=date_from, date_to=date_to, location=location, name=name
    )

    operator = " OR " if match == "or" else " AND "
    where_clause = operator.join(f"({cond})" for cond, _ in categories)
    params = [param for _, cond_params in categories for param in cond_params]

    query = (
        "SELECT id, country, race_name, race_date, location, distance_km, "
        "registration_end, registration_url, notify_telegram FROM race "
        f"WHERE {where_clause} ORDER BY race_date, country, race_name"
    )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    return [_row_to_record(row) for row in rows]


def describe_filters(
    *,
    notify: str | None,
    date_from: str | None,
    date_to: str | None,
    location: str | None,
    name: str | None,
    match: str,
) -> str:
    parts: list[str] = []
    if notify is not None:
        parts.append(f"알림={notify}")
    if date_from or date_to:
        parts.append(f"기간={date_from or '처음'}~{date_to or '이후'}")
    if location:
        parts.append(f"장소포함='{location}'")
    if name:
        parts.append(f"이름포함='{name}'")

    if len(parts) <= 1:
        return parts[0] if parts else "(조건 없음)"
    return f" {match.upper()} ".join(parts)


def format_results(records: list[RaceRecord], *, filters_desc: str) -> str:
    if not records:
        return f"조건에 맞는 대회를 찾지 못했습니다. (검색 조건: {filters_desc})"

    lines = [f"검색 조건: {filters_desc}", f"총 {len(records)}건"]
    for r in records:
        state = "ON" if r.notify_telegram else "OFF"
        lines.append("")
        lines.append(f"• [{r.country}] {r.race_name}")
        lines.append(f"  날짜: {r.race_date} | 장소: {r.location or '-'}")
        lines.append(f"  거리: {r.distance_km or '-'}km | 접수마감: {r.registration_end or '-'}")
        lines.append(f"  신청: {r.registration_url or '-'}")
        lines.append(f"  알림: {state}")
    return "\n".join(lines)


def run(argv: list[str] | None = None, db_path: Path = DEFAULT_DB_PATH) -> int:
    """PRD 5절 처리 흐름을 오케스트레이션한다."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        validate_args(args)
    except ArgumentError as exc:
        logger.error("인자 오류: %s", exc)
        print(f"인자 오류: {exc}")
        return EXIT_ARG_ERROR

    if not db_path.exists():
        logger.error("DB 파일을 찾을 수 없습니다: %s", db_path)
        print(f"DB 파일을 찾을 수 없습니다: {db_path}")
        return EXIT_SYSTEM_ERROR

    filters_desc = describe_filters(
        notify=args.notify,
        date_from=args.date_from,
        date_to=args.date_to,
        location=args.location,
        name=args.name,
        match=args.match,
    )

    try:
        records = search_races(
            db_path,
            notify=args.notify,
            date_from=args.date_from,
            date_to=args.date_to,
            location=args.location,
            name=args.name,
            match=args.match,
        )
    except sqlite3.Error:
        logger.exception("DB 조회 실패")
        print("DB 조회 중 오류가 발생했습니다.")
        return EXIT_SYSTEM_ERROR

    logger.info("검색 조건: %s, 매칭 %d건", filters_desc, len(records))
    print(format_results(records, filters_desc=filters_desc))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(run())

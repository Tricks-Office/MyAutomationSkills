import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import main


def _init_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE race (
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


def _insert_race(
    db_path: Path,
    *,
    country: str = "KR",
    race_name: str = "테스트 마라톤",
    race_date: str = "2026-09-01",
    location: str = "서울",
    registration_end: str = "2026-08-25",
    notify_telegram: int = 1,
) -> int:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO race (
                country, race_name, race_date, location, distance_km,
                registration_start, registration_end, registration_url,
                source_site, notify_telegram, first_found_at, updated_at
            ) VALUES (?, ?, ?, ?, '10', NULL, ?, 'https://example.com', 'test', ?, '2026-01-01', '2026-01-01')
            """,
            (country, race_name, race_date, location, registration_end, notify_telegram),
        )
        conn.commit()
        return cursor.lastrowid


def _seed(db_path: Path) -> None:
    _init_db(db_path)
    _insert_race(db_path, race_name="춘천마라톤", race_date="2026-09-06", location="춘천", notify_telegram=1)
    _insert_race(db_path, race_name="서울하프마라톤", race_date="2026-08-30", location="서울", notify_telegram=1)
    _insert_race(db_path, race_name="남경마라톤", race_date="2026-10-10", location="남경", country="CN", notify_telegram=0)


def test_validate_args_requires_one_category():
    args = main.build_arg_parser().parse_args([])
    try:
        main.validate_args(args)
        assert False, "ArgumentError가 발생해야 함"
    except main.ArgumentError:
        pass


def test_validate_args_accepts_single_category():
    args = main.build_arg_parser().parse_args(["--location", "서울"])
    main.validate_args(args)  # 예외 없이 통과해야 함


def test_validate_args_rejects_bad_date_format():
    args = main.build_arg_parser().parse_args(["--date-from", "2026/09/01"])
    try:
        main.validate_args(args)
        assert False, "ArgumentError가 발생해야 함"
    except main.ArgumentError:
        pass


def test_validate_args_rejects_inverted_date_range():
    args = main.build_arg_parser().parse_args(["--date-from", "2026-09-30", "--date-to", "2026-09-01"])
    try:
        main.validate_args(args)
        assert False, "ArgumentError가 발생해야 함"
    except main.ArgumentError:
        pass


def test_search_races_by_location_only(tmp_path):
    db_path = tmp_path / "marathon.db"
    _seed(db_path)

    records = main.search_races(db_path, location="서울")

    assert [r.race_name for r in records] == ["서울하프마라톤"]


def test_search_races_by_notify_off(tmp_path):
    db_path = tmp_path / "marathon.db"
    _seed(db_path)

    records = main.search_races(db_path, notify="off")

    assert [r.race_name for r in records] == ["남경마라톤"]


def test_search_races_by_period_open_range(tmp_path):
    db_path = tmp_path / "marathon.db"
    _seed(db_path)

    records = main.search_races(db_path, date_from="2026-09-01")

    assert {r.race_name for r in records} == {"춘천마라톤", "남경마라톤"}


def test_search_races_and_combination(tmp_path):
    db_path = tmp_path / "marathon.db"
    _seed(db_path)

    records = main.search_races(db_path, notify="on", location="춘천", match="and")

    assert [r.race_name for r in records] == ["춘천마라톤"]


def test_search_races_or_combination(tmp_path):
    db_path = tmp_path / "marathon.db"
    _seed(db_path)

    records = main.search_races(db_path, notify="off", location="춘천", match="or")

    assert {r.race_name for r in records} == {"춘천마라톤", "남경마라톤"}


def test_search_races_no_match_returns_empty_list(tmp_path):
    db_path = tmp_path / "marathon.db"
    _seed(db_path)

    records = main.search_races(db_path, name="존재하지않는대회")

    assert records == []


def test_run_success_with_results(tmp_path, capsys):
    db_path = tmp_path / "marathon.db"
    _seed(db_path)

    exit_code = main.run(["--location", "춘천"], db_path=db_path)

    assert exit_code == main.EXIT_SUCCESS
    assert "춘천마라톤" in capsys.readouterr().out


def test_run_success_with_zero_results(tmp_path, capsys):
    db_path = tmp_path / "marathon.db"
    _seed(db_path)

    exit_code = main.run(["--name", "존재하지않는대회"], db_path=db_path)

    assert exit_code == main.EXIT_SUCCESS
    assert "찾지 못했습니다" in capsys.readouterr().out


def test_run_arg_error_when_no_filter(tmp_path):
    db_path = tmp_path / "marathon.db"
    _seed(db_path)

    exit_code = main.run([], db_path=db_path)

    assert exit_code == main.EXIT_ARG_ERROR


def test_run_missing_db_file(tmp_path):
    db_path = tmp_path / "not_exists.db"

    exit_code = main.run(["--location", "서울"], db_path=db_path)

    assert exit_code == main.EXIT_SYSTEM_ERROR

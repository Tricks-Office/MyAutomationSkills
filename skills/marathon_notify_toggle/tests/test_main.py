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
    notify_telegram: int = 1,
) -> int:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO race (
                country, race_name, race_date, location, distance_km,
                registration_start, registration_end, registration_url,
                source_site, notify_telegram, first_found_at, updated_at
            ) VALUES (?, ?, ?, '서울', '10', NULL, NULL, 'https://example.com', 'test', ?, '2026-01-01', '2026-01-01')
            """,
            (country, race_name, race_date, notify_telegram),
        )
        conn.commit()
        return cursor.lastrowid


def test_validate_args_requires_action():
    args = main.build_arg_parser().parse_args(["--name", "춘천"])
    try:
        main.validate_args(args)
        assert False, "ArgumentError가 발생해야 함"
    except main.ArgumentError:
        pass


def test_validate_args_rejects_both_actions():
    args = main.build_arg_parser().parse_args(["--disable", "--enable", "--name", "춘천"])
    try:
        main.validate_args(args)
        assert False, "ArgumentError가 발생해야 함"
    except main.ArgumentError:
        pass


def test_validate_args_requires_name_or_id():
    args = main.build_arg_parser().parse_args(["--disable"])
    try:
        main.validate_args(args)
        assert False, "ArgumentError가 발생해야 함"
    except main.ArgumentError:
        pass


def test_validate_args_list_skips_other_checks():
    args = main.build_arg_parser().parse_args(["--list"])
    main.validate_args(args)  # 예외 없이 통과해야 함


def test_find_races_partial_name_match(tmp_path):
    db_path = tmp_path / "marathon.db"
    _init_db(db_path)
    _insert_race(db_path, race_name="춘천마라톤", race_date="2026-10-01")
    _insert_race(db_path, race_name="서울마라톤", race_date="2026-11-01")

    records = main.find_races(db_path, name="춘천")

    assert len(records) == 1
    assert records[0].race_name == "춘천마라톤"


def test_find_races_by_id_ignores_other_filters(tmp_path):
    db_path = tmp_path / "marathon.db"
    _init_db(db_path)
    race_id = _insert_race(db_path, race_name="춘천마라톤", race_date="2026-10-01", country="KR")

    records = main.find_races(db_path, race_id=race_id, country="CN")

    assert len(records) == 1
    assert records[0].id == race_id


def test_set_notify_toggles_and_is_idempotent(tmp_path):
    db_path = tmp_path / "marathon.db"
    _init_db(db_path)
    race_id = _insert_race(db_path, notify_telegram=1)

    assert main.set_notify(db_path, race_id, notify=False) is True
    assert main.find_races(db_path, race_id=race_id)[0].notify_telegram is False
    assert main.set_notify(db_path, race_id, notify=False) is False


def test_run_disable_success(tmp_path):
    db_path = tmp_path / "marathon.db"
    _init_db(db_path)
    _insert_race(db_path, race_name="춘천마라톤", notify_telegram=1)

    exit_code = main.run(["--disable", "--name", "춘천"], db_path=db_path)

    assert exit_code == main.EXIT_SUCCESS
    assert main.find_races(db_path, name="춘천")[0].notify_telegram is False


def test_run_enable_success(tmp_path):
    db_path = tmp_path / "marathon.db"
    _init_db(db_path)
    _insert_race(db_path, race_name="춘천마라톤", notify_telegram=0)

    exit_code = main.run(["--enable", "--name", "춘천"], db_path=db_path)

    assert exit_code == main.EXIT_SUCCESS
    assert main.find_races(db_path, name="춘천")[0].notify_telegram is True


def test_run_not_found(tmp_path):
    db_path = tmp_path / "marathon.db"
    _init_db(db_path)

    exit_code = main.run(["--disable", "--name", "존재하지않음"], db_path=db_path)

    assert exit_code == main.EXIT_NOT_FOUND


def test_run_ambiguous_does_not_change_db(tmp_path):
    db_path = tmp_path / "marathon.db"
    _init_db(db_path)
    _insert_race(db_path, race_name="춘천마라톤", race_date="2026-10-01", notify_telegram=1)
    _insert_race(db_path, race_name="춘천하프마라톤", race_date="2026-10-02", notify_telegram=1)

    exit_code = main.run(["--disable", "--name", "춘천"], db_path=db_path)

    assert exit_code == main.EXIT_AMBIGUOUS
    assert all(r.notify_telegram for r in main.find_races(db_path, name="춘천"))


def test_run_list(tmp_path, capsys):
    db_path = tmp_path / "marathon.db"
    _init_db(db_path)
    _insert_race(db_path, race_name="춘천마라톤")

    exit_code = main.run(["--list"], db_path=db_path)

    assert exit_code == main.EXIT_SUCCESS
    assert "춘천마라톤" in capsys.readouterr().out


def test_run_missing_db_file(tmp_path):
    db_path = tmp_path / "not_exists.db"

    exit_code = main.run(["--disable", "--name", "춘천"], db_path=db_path)

    assert exit_code == main.EXIT_SYSTEM_ERROR


def test_run_invalid_arg_combo(tmp_path):
    db_path = tmp_path / "marathon.db"
    _init_db(db_path)

    exit_code = main.run(["--disable", "--enable", "--name", "춘천"], db_path=db_path)

    assert exit_code == main.EXIT_SYSTEM_ERROR

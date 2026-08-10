import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import main


def test_escape_applescript_string_handles_quotes_and_backslashes() -> None:
    assert main._escape_applescript_string('a"b\\c') == 'a\\"b\\\\c'


def test_build_arg_parser_requires_room() -> None:
    parser = main.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--message", "hi"])


def test_build_arg_parser_rejects_message_and_message_file_together() -> None:
    parser = main.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--room", "방A", "--message", "hi", "--message-file", "f.txt"])


def test_build_arg_parser_accepts_multiple_rooms() -> None:
    parser = main.build_arg_parser()
    args = parser.parse_args(["--room", "방A", "--room", "방B", "--message", "hi"])
    assert args.room == ["방A", "방B"]


def test_resolve_message_returns_message_argument_directly() -> None:
    parser = main.build_arg_parser()
    args = parser.parse_args(["--room", "방A", "--message", "안녕"])
    assert main.resolve_message(args) == "안녕"


def test_resolve_message_reads_message_file(tmp_path: Path) -> None:
    message_file = tmp_path / "report.txt"
    message_file.write_text("1줄\n2줄", encoding="utf-8")
    parser = main.build_arg_parser()
    args = parser.parse_args(["--room", "방A", "--message-file", str(message_file)])
    assert main.resolve_message(args) == "1줄\n2줄"


def test_resolve_message_raises_argument_error_for_missing_file(tmp_path: Path) -> None:
    parser = main.build_arg_parser()
    args = parser.parse_args(["--room", "방A", "--message-file", str(tmp_path / "없음.txt")])
    with pytest.raises(main.ArgumentError):
        main.resolve_message(args)


def test_ensure_platform_macos_raises_on_non_darwin() -> None:
    with patch("main.platform.system", return_value="Linux"):
        with pytest.raises(main.ArgumentError):
            main.ensure_platform_macos()


def test_ensure_cliclick_installed_raises_when_missing() -> None:
    with patch("main.shutil.which", return_value=None):
        with pytest.raises(main.CliclickNotFoundError):
            main.ensure_cliclick_installed()


def test_run_returns_success_when_all_rooms_succeed() -> None:
    with patch("main.ensure_platform_macos"), patch("main.ensure_cliclick_installed"), patch(
        "main.send_message_to_room",
        side_effect=[
            main.RoomSendResult("방A", True),
            main.RoomSendResult("방B", True),
        ],
    ):
        exit_code = main.run(["--room", "방A", "--room", "방B", "--message", "hi"])
    assert exit_code == 0


def test_run_returns_failure_when_any_room_fails() -> None:
    with patch("main.ensure_platform_macos"), patch("main.ensure_cliclick_installed"), patch(
        "main.send_message_to_room",
        side_effect=[
            main.RoomSendResult("방A", True),
            main.RoomSendResult("방B", False, "검색 결과 없음"),
        ],
    ):
        exit_code = main.run(["--room", "방A", "--room", "방B", "--message", "hi"])
    assert exit_code == 1


def test_send_message_to_room_returns_failure_when_room_not_found() -> None:
    with patch("main.activate_kakaotalk_and_open_search", return_value=(0, 0, 10, 10)), patch(
        "main.focus_and_type_search_query"
    ), patch("main.find_exact_room_match", return_value=None):
        result = main.send_message_to_room("없는방", "hi")
    assert result.success is False
    assert "검색 결과" in result.reason


def test_send_message_to_room_returns_failure_on_frontmost_mismatch() -> None:
    with patch(
        "main.activate_kakaotalk_and_open_search",
        side_effect=main.FrontmostMismatchError("Code"),
    ):
        result = main.send_message_to_room("방A", "hi")
    assert result.success is False
    assert "Code" in result.reason

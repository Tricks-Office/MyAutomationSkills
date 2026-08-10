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


def test_send_message_to_room_closes_windows_even_on_early_failure() -> None:
    """여러 방을 연속 처리할 때 이전 방 창이 남아 있으면 다음 방 검색이 엉뚱한 곳에
    입력되는 사고가 실제로 재현됐다(ai_news_telegram 연동, 화공97/고대97 검색 실패).
    성공/실패 어느 경로든 close_all_room_windows가 항상 호출돼야 한다."""
    with patch("main.activate_kakaotalk_and_open_search", return_value=(0, 0, 10, 10)), patch(
        "main.focus_and_type_search_query"
    ), patch("main.find_exact_room_match", return_value=None), patch(
        "main.close_all_room_windows"
    ) as mock_close:
        main.send_message_to_room("없는방", "hi")
    mock_close.assert_called_once()


def test_send_message_to_room_closes_windows_on_success() -> None:
    match = main.RoomMatch(center_x=1, center_y=1)
    with patch("main.activate_kakaotalk_and_open_search", return_value=(0, 0, 10, 10)), patch(
        "main.focus_and_type_search_query"
    ), patch("main.find_exact_room_match", return_value=match), patch(
        "main.open_room_and_verify", return_value=True
    ), patch("main.get_message_input_center", return_value=(1, 1)), patch(
        "main.paste_message"
    ), patch("main.verify_pasted_text", return_value=True), patch(
        "main.get_send_button_center", return_value=(1, 1)
    ), patch("main.click_send_button"), patch(
        "main.verify_message_sent", return_value=True
    ), patch("main.close_all_room_windows") as mock_close:
        result = main.send_message_to_room("방A", "hi")
    assert result.success is True
    mock_close.assert_called_once()


def test_is_accessibility_denied_matches_known_markers() -> None:
    assert main._is_accessibility_denied("osascript에 보조 접근이 허용되지 않습니다.")
    assert main._is_accessibility_denied("Not allowed to send Apple events")
    assert not main._is_accessibility_denied("일반적인 다른 오류입니다")


def test_ensure_kakaotalk_installed_raises_when_app_missing() -> None:
    with patch("main.KAKAOTALK_APP_PATH") as mock_path:
        mock_path.exists.return_value = False
        with pytest.raises(main.KakaoTalkNotInstalledError):
            main.ensure_kakaotalk_installed()


def test_ensure_kakaotalk_ready_raises_permission_error_on_frontmost_check() -> None:
    with patch("main.ensure_kakaotalk_installed"), patch(
        "main.get_frontmost_process_name",
        side_effect=RuntimeError("AppleScript 실행 실패: 보조 접근이 허용되지 않습니다."),
    ):
        with pytest.raises(main.AccessibilityPermissionError):
            main.ensure_kakaotalk_ready()


def test_ensure_kakaotalk_ready_raises_not_logged_in_after_timeout() -> None:
    with patch("main.ensure_kakaotalk_installed"), patch(
        "main.get_frontmost_process_name", return_value="KakaoTalk"
    ), patch("main.run_applescript", return_value="NOT_READY"), patch(
        "main.APP_READY_TIMEOUT_SECONDS", 0.05
    ), patch("main.APP_READY_POLL_INTERVAL_SECONDS", 0.01):
        with pytest.raises(main.KakaoTalkNotLoggedInError):
            main.ensure_kakaotalk_ready()


def test_ensure_kakaotalk_ready_succeeds_when_window_ready() -> None:
    with patch("main.ensure_kakaotalk_installed"), patch(
        "main.get_frontmost_process_name", return_value="KakaoTalk"
    ), patch("main.run_applescript", return_value="READY"):
        main.ensure_kakaotalk_ready()  # 예외 없이 반환되면 성공

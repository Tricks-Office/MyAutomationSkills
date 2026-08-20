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
    with patch("main.shutil.which", return_value=None), patch("main.CLICLICK_COMMON_PATHS", []):
        with pytest.raises(main.CliclickNotFoundError):
            main.ensure_cliclick_installed()


class _FakeIoregResult:
    def __init__(self, stdout: str):
        self.stdout = stdout


def test_is_clamshell_closed_returns_true_when_yes(monkeypatch) -> None:
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *a, **kw: _FakeIoregResult('  |   "AppleClamshellState" = Yes\n'),
    )
    assert main.is_clamshell_closed() is True


def test_is_clamshell_closed_returns_false_when_no(monkeypatch) -> None:
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *a, **kw: _FakeIoregResult('  |   "AppleClamshellState" = No\n'),
    )
    assert main.is_clamshell_closed() is False


def test_is_clamshell_closed_returns_false_when_clamshell_mode_prevents_sleep(monkeypatch) -> None:
    """외부 모니터+전원 연결로 macOS가 뚜껑을 닫아도 잠들지 않는다고 판단한 경우
    (`AppleClamshellCausesSleep` = No) — 실제로 2026-08-16 아침 이 상태에서 뚜껑이
    닫혀 있다는 이유만으로 조기 실패해 카카오톡 발송이 누락됐다. 디스플레이가 실제로
    켜져 있으므로 자동화를 막지 않아야 한다."""
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *a, **kw: _FakeIoregResult(
            '  |   "AppleClamshellCausesSleep" = No\n'
            '  |   "AppleClamshellState" = Yes\n'
        ),
    )
    assert main.is_clamshell_closed() is False


def test_is_clamshell_closed_returns_true_when_clamshell_would_sleep(monkeypatch) -> None:
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *a, **kw: _FakeIoregResult(
            '  |   "AppleClamshellCausesSleep" = Yes\n'
            '  |   "AppleClamshellState" = Yes\n'
        ),
    )
    assert main.is_clamshell_closed() is True


def test_is_clamshell_closed_returns_none_when_property_absent(monkeypatch) -> None:
    """데스크톱 Mac 등 이 속성이 없는 환경에서는 판단 불가로 처리하고 자동화를
    막지 않는다."""
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **kw: _FakeIoregResult(""))
    assert main.is_clamshell_closed() is None


def test_is_clamshell_closed_returns_none_on_subprocess_error(monkeypatch) -> None:
    def _raise(*args, **kwargs):
        raise FileNotFoundError("ioreg not found")

    monkeypatch.setattr(main.subprocess, "run", _raise)
    assert main.is_clamshell_closed() is None


def test_ensure_clamshell_open_raises_when_closed() -> None:
    """Hermes 크론으로 새벽에 자동 실행됐을 때, 노트북 뚜껑이 닫혀 있어 GUI 자동화가
    근본적으로 불가능한 상태에서 텔레그램은 성공하고 카카오톡만 조용히 실패하는
    사고가 실제로 재현됐다(system log의 clamshell 상태 변화로 확인). 이제는 이 상태를
    조기에 감지해 명확한 이유로 즉시 실패한다."""
    with patch("main.is_clamshell_closed", return_value=True):
        with pytest.raises(main.ClamshellClosedError):
            main.ensure_clamshell_open()


def test_ensure_clamshell_open_does_not_raise_when_open_or_unknown() -> None:
    with patch("main.is_clamshell_closed", return_value=False):
        main.ensure_clamshell_open()
    with patch("main.is_clamshell_closed", return_value=None):
        main.ensure_clamshell_open()


def test_resolve_cliclick_path_prefers_shutil_which() -> None:
    with patch("main.shutil.which", return_value="/usr/bin/cliclick"):
        assert main.resolve_cliclick_path() == "/usr/bin/cliclick"


def test_resolve_cliclick_path_falls_back_to_common_paths_when_path_missing() -> None:
    """cron/launchd 등 비대화형 환경에서는 PATH에 Homebrew 경로가 없어 shutil.which가
    cliclick을 못 찾는 사고가 실제로 있었다(운영 중 텔레그램은 성공했지만 카카오톡
    발송만 조용히 실패). PATH 조회가 실패해도 Homebrew의 일반 설치 위치는 찾아야 한다."""
    with patch("main.shutil.which", return_value=None), patch(
        "main.CLICLICK_COMMON_PATHS", ["/opt/homebrew/bin/cliclick", "/usr/local/bin/cliclick"]
    ), patch("main.Path.is_file", lambda self: str(self) == "/opt/homebrew/bin/cliclick"):
        assert main.resolve_cliclick_path() == "/opt/homebrew/bin/cliclick"


def test_resolve_cliclick_path_returns_none_when_nowhere_found() -> None:
    with patch("main.shutil.which", return_value=None), patch("main.CLICLICK_COMMON_PATHS", []):
        assert main.resolve_cliclick_path() is None


def test_run_cliclick_raises_cliclick_not_found_error_when_unresolved() -> None:
    with patch("main.resolve_cliclick_path", return_value=None):
        with pytest.raises(main.CliclickNotFoundError):
            main.run_cliclick("c:1,1")


def test_run_returns_success_when_all_rooms_succeed() -> None:
    with patch("main.ensure_platform_macos"), patch("main.ensure_cliclick_installed"), patch(
        "main.ensure_clamshell_open"
    ), patch("main.ensure_kakaotalk_ready"), patch(
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
        "main.ensure_clamshell_open"
    ), patch("main.ensure_kakaotalk_ready"), patch(
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


def test_message_appears_in_history_truncates_long_message_for_comparison() -> None:
    """2026-08-19/20 사고: 원문 전체(수천 자)를 그대로 비교하면 접근성 트리 탐색이
    느려 OSASCRIPT_TIMEOUT_SECONDS를 반복적으로 넘겼다. AppleScript에는 앞부분
    (VERIFY_PREFIX_LENGTH)만 들어가야 한다."""
    long_message = "가" * 1000
    captured: dict = {}

    def fake_run_applescript(script: str) -> str:
        captured["script"] = script
        return "1"

    with patch("main.run_applescript", side_effect=fake_run_applescript):
        assert main.message_appears_in_history("방A", long_message) is True

    script = captured["script"]
    assert ("가" * main.VERIFY_PREFIX_LENGTH) in script
    assert long_message not in script


def test_message_appears_in_history_uses_full_text_when_shorter_than_prefix() -> None:
    captured: dict = {}

    def fake_run_applescript(script: str) -> str:
        captured["script"] = script
        return "0"

    with patch("main.run_applescript", side_effect=fake_run_applescript):
        assert main.message_appears_in_history("방A", "안녕") is False

    assert "안녕" in captured["script"]


def test_message_appears_in_history_limits_to_recent_rows() -> None:
    captured: dict = {}

    def fake_run_applescript(script: str) -> str:
        captured["script"] = script
        return "0"

    with patch("main.run_applescript", side_effect=fake_run_applescript):
        main.message_appears_in_history("방A", "안녕")

    assert f"rowCount - ({main.VERIFY_RECENT_ROWS} - 1)" in captured["script"]


def test_open_room_and_verify_succeeds_on_first_poll() -> None:
    match = main.RoomMatch(center_x=10, center_y=20)
    with patch("main.ensure_kakaotalk_frontmost"), patch(
        "main.run_cliclick"
    ) as mock_cliclick, patch(
        "main.run_applescript", return_value="카카오톡, 방A"
    ), patch("main.ROOM_OPEN_VERIFY_TIMEOUT_SECONDS", 0.2), patch(
        "main.ROOM_OPEN_VERIFY_POLL_INTERVAL_SECONDS", 0.02
    ):
        assert main.open_room_and_verify("방A", match) is True
    mock_cliclick.assert_called_once_with("dc:10,20")


def test_open_room_and_verify_retries_until_room_appears() -> None:
    """2026-08-20 사고 재현: 더블클릭 직후 곧바로 확인하면 아직 안 열려 있을 수
    있다 — 짧게 폴링해 일시적 지연을 흡수해야 한다."""
    match = main.RoomMatch(center_x=10, center_y=20)
    responses = iter(["카카오톡", "카카오톡", "카카오톡, 방A"])
    with patch("main.ensure_kakaotalk_frontmost"), patch("main.run_cliclick"), patch(
        "main.run_applescript", side_effect=lambda *a, **kw: next(responses)
    ), patch("main.ROOM_OPEN_VERIFY_TIMEOUT_SECONDS", 1.0), patch(
        "main.ROOM_OPEN_VERIFY_POLL_INTERVAL_SECONDS", 0.01
    ):
        assert main.open_room_and_verify("방A", match) is True


def test_open_room_and_verify_fails_after_timeout() -> None:
    match = main.RoomMatch(center_x=10, center_y=20)
    with patch("main.ensure_kakaotalk_frontmost"), patch("main.run_cliclick"), patch(
        "main.run_applescript", return_value="카카오톡"
    ), patch("main.ROOM_OPEN_VERIFY_TIMEOUT_SECONDS", 0.1), patch(
        "main.ROOM_OPEN_VERIFY_POLL_INTERVAL_SECONDS", 0.03
    ):
        assert main.open_room_and_verify("방A", match) is False


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


def test_log_file_is_configured_and_persists_across_exit_codes() -> None:
    """2026-08-16/17 사고: 방 일부만 실패해도 상위 프로세스(ai_news_telegram)는
    exit 0으로 끝나 Hermes 크론이 stderr를 버리는 바람에 시스템 로그로 정황만
    재구성해야 했다. exit 코드와 무관하게 항상 남는 파일 로그가 있어야 한다."""
    assert main._LOG_FILE_PATH.parent.is_dir()
    handler_classes = [type(h) for h in main._root_logger.handlers]
    assert main.logging.handlers.RotatingFileHandler in handler_classes


def test_list_stray_room_windows_parses_comma_separated_names() -> None:
    with patch("main.run_applescript", return_value="방A, 방B"):
        assert main._list_stray_room_windows() == ["방A", "방B"]


def test_list_stray_room_windows_returns_empty_list_when_none_remain() -> None:
    with patch("main.run_applescript", return_value=""):
        assert main._list_stray_room_windows() == []


def test_list_stray_room_windows_returns_empty_list_on_applescript_failure() -> None:
    """조회 자체가 실패해도 '창이 없다'고 확정하지 않되, 호출부(재시도 루프)가
    무한정 재시도하지 않도록 빈 목록으로 취급한다."""
    with patch("main.run_applescript", side_effect=RuntimeError("boom")):
        assert main._list_stray_room_windows() == []


def test_close_all_room_windows_succeeds_without_retry() -> None:
    with patch("main.run_applescript", return_value="") as mock_run, patch(
        "main._list_stray_room_windows", return_value=[]
    ) as mock_list, patch("main.logger") as mock_logger:
        main.close_all_room_windows()
    assert mock_run.call_count == 1
    assert mock_list.call_count == 1
    mock_logger.error.assert_not_called()


def test_close_all_room_windows_retries_then_succeeds(monkeypatch) -> None:
    """2026-08-17 사고 재현: 창 닫기 AppleScript가 타임아웃으로 죽어 일부 창이
    남아도, 조용히 다음 방으로 넘어가지 않고 재시도해서 실제로 다 닫혔는지
    확인한다."""
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    with patch("main.run_applescript", return_value="") as mock_run, patch(
        "main._list_stray_room_windows", side_effect=[["방A"], []]
    ) as mock_list, patch("main.logger") as mock_logger:
        main.close_all_room_windows()
    assert mock_run.call_count == 2
    assert mock_list.call_count == 2
    mock_logger.error.assert_not_called()
    mock_logger.warning.assert_called()


def test_close_all_room_windows_logs_error_when_stray_windows_persist(monkeypatch) -> None:
    """재시도를 다 써도 창이 남아 있으면, 다음 방 검색이 엉뚱한 창을 맞힐 수
    있다는 사실을 조용히 넘기지 않고 명확한 에러 로그로 남긴다."""
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    with patch("main.run_applescript", return_value="") as mock_run, patch(
        "main._list_stray_room_windows", return_value=["방A"]
    ) as mock_list, patch("main.logger") as mock_logger:
        main.close_all_room_windows()
    assert mock_run.call_count == main.CLOSE_WINDOWS_MAX_ATTEMPTS
    assert mock_list.call_count == main.CLOSE_WINDOWS_MAX_ATTEMPTS + 1
    mock_logger.error.assert_called_once()

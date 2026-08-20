import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import main


def test_parse_distances_km_basic():
    assert main.parse_distances_km("5km · 10km · 21.0975km") == [5.0, 10.0, 21.0975]


def test_parse_distances_km_half_and_full():
    assert main.parse_distances_km("풀 · 하프 · 10km") == [21.0975, 42.195, 10.0]


def test_parse_distances_km_trail_suffixes():
    assert main.parse_distances_km("38K-P · 14K") == [38.0, 14.0]
    assert main.parse_distances_km("9봉 85k · 5봉 43k") == [85.0, 43.0]


def test_parse_distances_km_excludes_non_distance_numbers():
    assert main.parse_distances_km("10km · 35세이상일반인 · 3000명") == [10.0]


def test_format_distance_km_trims_trailing_zero():
    assert main.format_distance_km([5.0, 10.0, 21.0975]) == "5,10,21.0975"


def test_format_telegram_message_empty():
    assert main.format_telegram_message([]) == "이번 실행에서 접수중인 신규 대회가 없습니다."


def test_format_telegram_message_lists_races():
    race = main.Race(
        country="KR",
        race_name="테스트 마라톤",
        race_date="2026-09-01",
        location="서울",
        distance_km="5,10",
        registration_start=None,
        registration_end=None,
        registration_url="https://example.com/race",
        source_site="marathonmate.store",
    )
    message = main.format_telegram_message([race])
    assert "테스트 마라톤" in message
    assert "https://example.com/race" in message


def _make_race(name: str, race_date: str = "2026-09-01") -> main.Race:
    return main.Race(
        country="KR",
        race_name=name,
        race_date=race_date,
        location="서울",
        distance_km="10",
        registration_start=None,
        registration_end=None,
        registration_url="https://example.com/race",
        source_site="marathonmate.store",
    )


def test_upsert_races_inserts_then_updates(tmp_path):
    db_path = tmp_path / "marathon.db"
    main.init_db(db_path)
    race = _make_race("업서트 테스트 마라톤")

    new_count, updated_count = main.upsert_races(db_path, [race])
    assert (new_count, updated_count) == (1, 0)

    new_count, updated_count = main.upsert_races(db_path, [race])
    assert (new_count, updated_count) == (0, 1)


def test_get_notifiable_races_respects_notify_flag(tmp_path):
    import sqlite3

    db_path = tmp_path / "marathon.db"
    main.init_db(db_path)
    race = _make_race("알림 테스트 마라톤")
    main.upsert_races(db_path, [race])

    assert len(main.get_notifiable_races(db_path, [race])) == 1

    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE race SET notify_telegram = 0 WHERE race_name = ?", (race.race_name,))
        conn.commit()

    assert main.get_notifiable_races(db_path, [race]) == []


def test_load_config_raises_when_env_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(main, "REPO_ROOT", tmp_path)

    try:
        main.load_config()
        assert False, "RequiredEnvMissingError가 발생해야 함"
    except main.RequiredEnvMissingError:
        pass


def test_run_returns_success_with_mocked_sources(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    race = _make_race("모킹 테스트 마라톤")
    sent = {}
    kakao_sent = {}

    monkeypatch.setattr(main, "fetch_korea_races", lambda: [race])
    monkeypatch.setattr(main, "fetch_china_races", lambda api_key: [])
    monkeypatch.setattr(
        main,
        "send_telegram_message",
        lambda token, chat_id, text: sent.update(token=token, chat_id=chat_id, text=text),
    )
    # 카카오톡 발송은 실제 subprocess/macOS 자동화를 부르므로 run() 테스트에서는 항상
    # stub 처리한다(ai_news_telegram의 기존 테스트 패턴과 동일). 카카오톡 발송 자체를
    # 검증하는 테스트는 send_kakaotalk_notification을 직접 호출해 별도로 확인한다.
    monkeypatch.setattr(
        main,
        "send_kakaotalk_notification",
        lambda message, rooms=main.KAKAOTALK_ROOMS: kakao_sent.update(message=message) or True,
    )

    db_path = tmp_path / "marathon.db"
    exit_code = main.run(db_path=db_path)

    assert exit_code == 0
    assert sent["text"] and race.race_name in sent["text"]
    assert kakao_sent["message"] == sent["text"]


def test_run_returns_failure_when_both_sources_fail(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "fetch_korea_races", _raise)
    monkeypatch.setattr(main, "fetch_china_races", lambda api_key: (_ for _ in ()).throw(RuntimeError("boom")))

    db_path = tmp_path / "marathon.db"
    exit_code = main.run(db_path=db_path)

    assert exit_code == 1


def _http_error(status_code: int) -> Exception:
    class _FakeHttpResponse:
        pass

    fake_response = _FakeHttpResponse()
    fake_response.status_code = status_code
    return main.requests.exceptions.HTTPError(response=fake_response)


def test_is_retryable_request_error_true_for_connection_and_timeout():
    assert main._is_retryable_request_error(main.requests.exceptions.Timeout("timed out"))
    assert main._is_retryable_request_error(main.requests.exceptions.ConnectionError("refused"))


def test_is_retryable_request_error_true_for_5xx_false_for_4xx():
    assert main._is_retryable_request_error(_http_error(503)) is True
    assert main._is_retryable_request_error(_http_error(404)) is False


def test_fetch_korea_schedule_html_retries_on_timeout_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(main.time, "sleep", lambda seconds: sleeps.append(seconds))

    class _FakeResponse:
        text = "<html>ok</html>"

        def raise_for_status(self):
            pass

    calls = {"count": 0}

    def _fake_get(url, headers, timeout):
        calls["count"] += 1
        if calls["count"] < 3:
            raise main.requests.exceptions.Timeout("timed out")
        assert timeout == main.KOREA_SCHEDULE_REQUEST_TIMEOUT_SECONDS
        return _FakeResponse()

    monkeypatch.setattr(main.requests, "get", _fake_get)

    html = main._fetch_korea_schedule_html({"User-Agent": "test"})

    assert html == "<html>ok</html>"
    assert calls["count"] == 3
    assert sleeps == main.KOREA_SCHEDULE_RETRY_BACKOFF_SECONDS


def test_fetch_korea_schedule_html_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(main.time, "sleep", lambda seconds: None)
    calls = {"count": 0}

    def _fake_get(url, headers, timeout):
        calls["count"] += 1
        raise main.requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(main.requests, "get", _fake_get)

    try:
        main._fetch_korea_schedule_html({"User-Agent": "test"})
        assert False, "ConnectionError가 발생해야 함"
    except main.requests.exceptions.ConnectionError:
        pass

    assert calls["count"] == main.KOREA_SCHEDULE_MAX_ATTEMPTS


def test_fetch_korea_schedule_html_does_not_retry_on_client_error(monkeypatch):
    monkeypatch.setattr(
        main.time, "sleep", lambda seconds: (_ for _ in ()).throw(AssertionError("재시도하면 안 됨"))
    )
    calls = {"count": 0}

    def _fake_get(url, headers, timeout):
        calls["count"] += 1
        raise _http_error(404)

    monkeypatch.setattr(main.requests, "get", _fake_get)

    try:
        main._fetch_korea_schedule_html({"User-Agent": "test"})
        assert False, "HTTPError가 발생해야 함"
    except main.requests.exceptions.HTTPError:
        pass

    assert calls["count"] == 1


class _FakeCompletedProcess:
    def __init__(self, returncode: int, stderr: str = ""):
        self.returncode = returncode
        self.stderr = stderr


def test_send_kakaotalk_notification_returns_true_on_success(monkeypatch):
    captured = {}

    def _fake_run(args, capture_output, text, timeout):
        captured["args"] = args
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(main.subprocess, "run", _fake_run)

    assert main.send_kakaotalk_notification("접수중인 대회 1건") is True
    assert str(main.KAKAOTALK_SENDER_SCRIPT) in captured["args"]
    for room in main.KAKAOTALK_ROOMS:
        assert room in captured["args"]
    assert "--message-file" in captured["args"]


def test_send_kakaotalk_notification_returns_false_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        main.subprocess, "run", lambda *a, **kw: _FakeCompletedProcess(returncode=1, stderr="방 없음")
    )
    assert main.send_kakaotalk_notification("접수중인 대회 1건") is False


def test_send_kakaotalk_notification_returns_false_and_does_not_raise_on_exception(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("subprocess 실행 실패")

    monkeypatch.setattr(main.subprocess, "run", _raise)
    assert main.send_kakaotalk_notification("접수중인 대회 1건") is False


def test_send_kakaotalk_notification_writes_message_to_temp_file(monkeypatch):
    written = {}

    def _fake_run(args, capture_output, text, timeout):
        idx = args.index("--message-file")
        written["content"] = Path(args[idx + 1]).read_text(encoding="utf-8")
        written["path"] = args[idx + 1]
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(main.subprocess, "run", _fake_run)

    main.send_kakaotalk_notification("멀티라인\n메시지 테스트")

    assert written["content"] == "멀티라인\n메시지 테스트"
    assert not Path(written["path"]).exists()  # finally 블록에서 삭제됨

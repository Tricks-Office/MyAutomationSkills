import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import main


def test_parse_args_accepts_daily():
    args = main.parse_args(["--mode", "daily"])
    assert args.mode == "daily"


def test_parse_args_accepts_weekly():
    args = main.parse_args(["--mode", "weekly"])
    assert args.mode == "weekly"


def test_parse_args_rejects_invalid_mode():
    try:
        main.parse_args(["--mode", "monthly"])
        assert False, "SystemExit이 발생해야 함"
    except SystemExit as exc:
        assert exc.code != 0


def test_parse_args_requires_mode():
    try:
        main.parse_args([])
        assert False, "SystemExit이 발생해야 함"
    except SystemExit as exc:
        assert exc.code != 0


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


def test_load_config_returns_values_when_present(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(main, "REPO_ROOT", tmp_path)

    config = main.load_config()
    assert config == {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_CHAT_ID": "12345",
        "ANTHROPIC_API_KEY": "test-key",
    }


def test_send_telegram_message_splits_long_text(monkeypatch):
    calls = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    def _fake_post(url, json, timeout):
        calls.append(json)
        return _FakeResponse()

    monkeypatch.setattr(main.requests, "post", _fake_post)

    long_text = "가" * 9000
    main.send_telegram_message("token", "chat-id", long_text)

    assert len(calls) == 3
    assert all(call["chat_id"] == "chat-id" for call in calls)


def test_send_telegram_message_raises_when_api_reports_failure(monkeypatch):
    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": False, "description": "Bad Request"}

    monkeypatch.setattr(main.requests, "post", lambda url, json, timeout: _FakeResponse())

    try:
        main.send_telegram_message("token", "chat-id", "hello")
        assert False, "RuntimeError가 발생해야 함"
    except RuntimeError:
        pass


def test_run_returns_success_when_send_succeeds(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(main, "REPO_ROOT", tmp_path)

    sent = {}
    monkeypatch.setattr(
        main,
        "send_telegram_message",
        lambda token, chat_id, text: sent.update(token=token, chat_id=chat_id, text=text),
    )

    assert main.run("daily") == 0
    assert sent["token"] == "test-token"
    assert "daily" in sent["text"]


def test_run_returns_failure_when_send_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(main, "REPO_ROOT", tmp_path)

    def _raise(token, chat_id, text):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "send_telegram_message", _raise)

    assert main.run("daily") == 1


def test_run_returns_failure_when_env_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(main, "REPO_ROOT", tmp_path)

    assert main.run("daily") == 1


def test_fetch_hn_stories_merges_and_dedupes_across_keywords(monkeypatch):
    calls = []

    class _FakeResponse:
        def __init__(self, hits):
            self._hits = hits

        def raise_for_status(self):
            pass

        def json(self):
            return {"hits": self._hits}

    def _fake_get(url, params, timeout):
        calls.append(params)
        idx = len(calls)
        return _FakeResponse(
            [
                {"objectID": "shared", "title": "shared story", "points": 1, "num_comments": 0, "created_at_i": 100},
                {"objectID": f"unique-{idx}", "title": f"story {idx}", "points": idx, "num_comments": 0, "created_at_i": 100},
            ]
        )

    monkeypatch.setattr(main.requests, "get", _fake_get)

    stories = main.fetch_hn_stories("daily", now=1_000_000)

    assert len(calls) == len(main.AI_KEYWORDS)
    assert len(stories) == 1 + len(main.AI_KEYWORDS)
    ids = {s.item_id for s in stories}
    assert "shared" in ids
    expected_since = 1_000_000 - main.MODE_WINDOW_SECONDS["daily"]
    assert all(p["numericFilters"] == f"created_at_i>{expected_since}" for p in calls)


def test_fetch_hn_stories_uses_wider_window_for_weekly(monkeypatch):
    calls = []

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"hits": []}

    def _fake_get(url, params, timeout):
        calls.append(params)
        return _FakeResponse()

    monkeypatch.setattr(main.requests, "get", _fake_get)
    main.fetch_hn_stories("weekly", now=1_000_000)

    expected_since = 1_000_000 - main.MODE_WINDOW_SECONDS["weekly"]
    assert all(p["numericFilters"] == f"created_at_i>{expected_since}" for p in calls)


def test_hn_story_from_hit_falls_back_to_discussion_url():
    story = main._hn_story_from_hit(
        {"objectID": "123", "title": "Ask HN: something", "points": 5, "num_comments": 2, "created_at_i": 1}
    )
    assert story is not None
    assert story.url == "https://news.ycombinator.com/item?id=123"


def test_hn_story_from_hit_returns_none_without_object_id():
    assert main._hn_story_from_hit({"title": "no id"}) is None

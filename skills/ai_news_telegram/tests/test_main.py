import json
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


def _stub_run_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(main, "REPO_ROOT", tmp_path)


def test_run_returns_success_when_send_succeeds(monkeypatch, tmp_path):
    _stub_run_env(monkeypatch, tmp_path)
    story = _make_story(item_id="abc", title="new AI model research paper")
    ranked_result = {"tech": [_make_ranked_item(1)], "biz": []}
    monkeypatch.setattr(main, "collect_candidate_pools", lambda mode, db_path: {"tech": [story], "biz": []})
    monkeypatch.setattr(main, "rank_and_summarize", lambda pools, api_key: ranked_result)

    sent = {}
    recorded = {}
    monkeypatch.setattr(
        main,
        "send_telegram_message",
        lambda token, chat_id, text: sent.update(token=token, chat_id=chat_id, text=text),
    )
    monkeypatch.setattr(
        main, "record_sent_items", lambda db_path, ranked: recorded.update(ranked=ranked)
    )

    exit_code = main.run("daily", db_path=tmp_path / "sent_items.db")

    assert exit_code == 0
    assert sent["token"] == "test-token"
    assert ranked_result["tech"][0]["title_ko"] in sent["text"]
    assert recorded["ranked"] == ranked_result


def test_run_skips_claude_call_when_no_candidates(monkeypatch, tmp_path):
    _stub_run_env(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "collect_candidate_pools", lambda mode, db_path: {"tech": [], "biz": []})

    def _fail_if_called(pools, api_key):
        raise AssertionError("후보가 없으면 Claude를 호출하면 안 됨")

    monkeypatch.setattr(main, "rank_and_summarize", _fail_if_called)
    monkeypatch.setattr(main, "send_telegram_message", lambda token, chat_id, text: None)

    assert main.run("daily", db_path=tmp_path / "sent_items.db") == 0


def test_run_returns_failure_when_candidate_collection_fails(monkeypatch, tmp_path):
    _stub_run_env(monkeypatch, tmp_path)

    def _raise(mode, db_path):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "collect_candidate_pools", _raise)

    assert main.run("daily", db_path=tmp_path / "sent_items.db") == 1


def test_run_returns_failure_when_ranking_fails(monkeypatch, tmp_path):
    _stub_run_env(monkeypatch, tmp_path)
    story = _make_story(item_id="abc", title="new AI model research paper")
    monkeypatch.setattr(main, "collect_candidate_pools", lambda mode, db_path: {"tech": [story], "biz": []})

    def _raise(pools, api_key):
        raise main.RankingError("boom")

    monkeypatch.setattr(main, "rank_and_summarize", _raise)

    assert main.run("daily", db_path=tmp_path / "sent_items.db") == 1


def test_run_returns_failure_when_send_fails(monkeypatch, tmp_path):
    _stub_run_env(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "collect_candidate_pools", lambda mode, db_path: {"tech": [], "biz": []})
    monkeypatch.setattr(main, "rank_and_summarize", lambda pools, api_key: {"tech": [], "biz": []})

    def _raise(token, chat_id, text):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "send_telegram_message", _raise)

    assert main.run("daily", db_path=tmp_path / "sent_items.db") == 1


def test_run_returns_failure_when_record_sent_items_fails(monkeypatch, tmp_path):
    _stub_run_env(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "collect_candidate_pools", lambda mode, db_path: {"tech": [], "biz": []})
    monkeypatch.setattr(main, "rank_and_summarize", lambda pools, api_key: {"tech": [], "biz": []})
    monkeypatch.setattr(main, "send_telegram_message", lambda token, chat_id, text: None)

    def _raise(db_path, ranked):
        raise RuntimeError("disk full")

    monkeypatch.setattr(main, "record_sent_items", _raise)

    assert main.run("daily", db_path=tmp_path / "sent_items.db") == 1


def test_run_returns_failure_when_env_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(main, "REPO_ROOT", tmp_path)

    assert main.run("daily", db_path=tmp_path / "sent_items.db") == 1


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


def _make_story(
    title: str = "",
    text: str = "",
    points: int = 0,
    num_comments: int = 0,
    item_id: str = "1",
    url: str = "https://example.com",
) -> main.HNStory:
    return main.HNStory(
        item_id=item_id,
        title=title,
        url=url,
        text=text,
        points=points,
        num_comments=num_comments,
        created_at_i=0,
    )


def test_filter_ai_related_keeps_matching_and_drops_unrelated():
    ai_story = _make_story(title="New LLM benchmark released")
    unrelated_story = _make_story(title="Thanks FedEx, this is why we keep getting phished")

    filtered = main.filter_ai_related([ai_story, unrelated_story])

    assert filtered == [ai_story]


def test_filter_ai_related_matches_plural_and_possessive_forms():
    plural_story = _make_story(title="Dev proves LLMs will run on anything")
    possessive_story = _make_story(title="OpenAI's new release")

    filtered = main.filter_ai_related([plural_story, possessive_story])

    assert plural_story in filtered
    assert possessive_story in filtered


def test_filter_ai_related_checks_story_text_too():
    story = _make_story(title="Show HN: my project", text="Built with a custom neural network")
    assert main.filter_ai_related([story]) == [story]


def test_classify_category_defaults_to_tech_on_tie():
    assert main.classify_category(_make_story(title="Just an AI story with no other signal")) == "tech"


def test_classify_category_detects_business_signal():
    story = _make_story(title="AI startup raises new funding round")
    assert main.classify_category(story) == "biz"


def test_classify_category_detects_tech_signal():
    story = _make_story(title="New open source model benchmark and research paper")
    assert main.classify_category(story) == "tech"


def test_classify_category_breaks_tie_toward_tech_when_equal_counts():
    # "model"(기술) 1개, "funding"(비즈니스) 1개로 동률 → 기술
    story = _make_story(title="New model funding announced")
    assert main.classify_category(story) == "tech"


def test_compute_hot_score_weights_comments_more_than_points():
    high_comments = _make_story(points=10, num_comments=10)
    high_points = _make_story(points=29, num_comments=0)
    assert main.compute_hot_score(high_comments) > main.compute_hot_score(high_points)


def test_build_candidate_pools_splits_and_ranks_by_category(monkeypatch):
    tech_low = _make_story(title="new model paper", points=1, num_comments=0)
    tech_high = _make_story(title="new model research benchmark", points=100, num_comments=50)
    biz_story = _make_story(title="AI startup raises funding", points=10, num_comments=5)

    pools = main.build_candidate_pools([tech_low, tech_high, biz_story])

    assert [s.title for s in pools["tech"]] == [tech_high.title, tech_low.title]
    assert [s.title for s in pools["biz"]] == [biz_story.title]


def test_build_candidate_pools_caps_pool_size():
    stories = [
        _make_story(title=f"model research paper {i}", points=i, num_comments=0) for i in range(20)
    ]
    pools = main.build_candidate_pools(stories, pool_size=5)
    assert len(pools["tech"]) == 5
    assert pools["tech"][0].points == 19


def test_get_sent_item_ids_empty_when_db_not_created(tmp_path):
    assert main.get_sent_item_ids(tmp_path / "sent_items.db") == set()


def test_record_sent_items_then_get_sent_item_ids_roundtrip(tmp_path):
    db_path = tmp_path / "sent_items.db"
    ranked = {
        "tech": [{"item_id": "t1"}, {"item_id": "t2"}],
        "biz": [{"item_id": "b1"}],
    }

    main.record_sent_items(db_path, ranked)

    assert main.get_sent_item_ids(db_path) == {"t1", "t2", "b1"}


def test_record_sent_items_is_idempotent_for_same_item(tmp_path):
    db_path = tmp_path / "sent_items.db"
    ranked = {"tech": [{"item_id": "t1"}], "biz": []}

    main.record_sent_items(db_path, ranked)
    main.record_sent_items(db_path, ranked)

    assert main.get_sent_item_ids(db_path) == {"t1"}


def test_exclude_sent_items_filters_out_known_ids():
    sent_story = _make_story(item_id="sent-1")
    fresh_story = _make_story(item_id="fresh-1")

    result = main.exclude_sent_items([sent_story, fresh_story], {"sent-1"})

    assert result == [fresh_story]


def test_collect_candidate_pools_orchestrates_pipeline(monkeypatch, tmp_path):
    ai_story = _make_story(item_id="ai-1", title="new AI model research paper", points=5, num_comments=1)
    unrelated_story = _make_story(item_id="unrelated-1", title="totally unrelated post", points=99, num_comments=99)

    monkeypatch.setattr(main, "fetch_hn_stories", lambda mode: [ai_story, unrelated_story])

    pools = main.collect_candidate_pools("daily", db_path=tmp_path / "sent_items.db")

    all_ids = {s.item_id for items in pools.values() for s in items}
    assert ai_story.item_id in all_ids
    assert len(pools["tech"]) + len(pools["biz"]) == 1


def test_collect_candidate_pools_excludes_previously_sent_items(monkeypatch, tmp_path):
    db_path = tmp_path / "sent_items.db"
    already_sent = _make_story(item_id="sent-1", title="new AI model research paper")
    fresh = _make_story(item_id="fresh-1", title="new AI model research benchmark")

    monkeypatch.setattr(main, "fetch_hn_stories", lambda mode: [already_sent, fresh])
    main.record_sent_items(db_path, {"tech": [{"item_id": "sent-1"}], "biz": []})

    pools = main.collect_candidate_pools("daily", db_path=db_path)

    all_ids = {s.item_id for items in pools.values() for s in items}
    assert "sent-1" not in all_ids
    assert "fresh-1" in all_ids


def test_merge_ranked_items_filters_unknown_ids():
    story = _make_story(item_id="abc", title="t", points=5, num_comments=2, url="https://example.com/abc")

    merged = main._merge_ranked_items(
        [story],
        [
            {"item_id": "abc", "title_ko": "제목", "summary_ko": "요약"},
            {"item_id": "unknown", "title_ko": "x", "summary_ko": "y"},
        ],
    )

    assert len(merged) == 1
    assert merged[0] == {
        "item_id": "abc",
        "title_ko": "제목",
        "summary_ko": "요약",
        "url": "https://example.com/abc",
        "points": 5,
        "num_comments": 2,
    }


class _FakeAnthropicBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeAnthropicResponse:
    def __init__(self, stop_reason: str, content: list):
        self.stop_reason = stop_reason
        self.content = content


class _FakeAnthropicMessages:
    def __init__(self, response):
        self._response = response

    def create(self, **kwargs):
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response):
        self._response = response

    def __call__(self, api_key):
        self.messages = _FakeAnthropicMessages(self._response)
        return self


def test_rank_and_summarize_parses_structured_response(monkeypatch):
    story = _make_story(item_id="abc", title="t")
    pools = {"tech": [story], "biz": []}
    payload = json.dumps({"tech": [{"item_id": "abc", "title_ko": "제목", "summary_ko": "요약"}], "biz": []})
    response = _FakeAnthropicResponse("end_turn", [_FakeAnthropicBlock(payload)])
    monkeypatch.setattr(main.anthropic, "Anthropic", _FakeAnthropicClient(response))

    ranked = main.rank_and_summarize(pools, "fake-key")

    assert ranked["tech"][0]["title_ko"] == "제목"
    assert ranked["biz"] == []


def test_rank_and_summarize_raises_on_refusal(monkeypatch):
    response = _FakeAnthropicResponse("refusal", [])
    monkeypatch.setattr(main.anthropic, "Anthropic", _FakeAnthropicClient(response))

    try:
        main.rank_and_summarize({"tech": [], "biz": []}, "fake-key")
        assert False, "RankingError가 발생해야 함"
    except main.RankingError:
        pass


def test_rank_and_summarize_raises_on_invalid_json(monkeypatch):
    response = _FakeAnthropicResponse("end_turn", [_FakeAnthropicBlock("not json")])
    monkeypatch.setattr(main.anthropic, "Anthropic", _FakeAnthropicClient(response))

    try:
        main.rank_and_summarize({"tech": [], "biz": []}, "fake-key")
        assert False, "RankingError가 발생해야 함"
    except main.RankingError:
        pass


def test_rank_and_summarize_raises_when_no_text_block(monkeypatch):
    response = _FakeAnthropicResponse("end_turn", [])
    monkeypatch.setattr(main.anthropic, "Anthropic", _FakeAnthropicClient(response))

    try:
        main.rank_and_summarize({"tech": [], "biz": []}, "fake-key")
        assert False, "RankingError가 발생해야 함"
    except main.RankingError:
        pass


def _make_ranked_item(idx: int) -> dict:
    return {
        "item_id": str(idx),
        "title_ko": f"제목 {idx}",
        "summary_ko": f"요약 {idx}",
        "url": f"https://example.com/{idx}",
        "points": idx,
        "num_comments": idx,
    }


def test_format_final_message_shows_zero_state_per_category():
    message = main.format_final_message("daily", {"tech": [], "biz": []})
    assert message.count("이번 기간 동안 발견된 소식이 없습니다.") == 2
    assert "일간" in message


def test_format_final_message_marks_shortage_under_five():
    items = [_make_ranked_item(i) for i in range(3)]
    message = main.format_final_message("weekly", {"tech": items, "biz": []})
    assert "후보가 3건뿐입니다" in message
    assert "주간" in message


def test_format_final_message_lists_all_items_without_shortage_note():
    items = [_make_ranked_item(i) for i in range(5)]
    message = main.format_final_message("daily", {"tech": items, "biz": []})
    assert "후보가" not in message
    for item in items:
        assert item["title_ko"] in message
        assert item["url"] in message

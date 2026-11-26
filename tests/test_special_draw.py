import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_marry_groupmate.src.command import draw
from astrbot_plugin_marry_groupmate.src.command.my_wife import cmd_show_history
from astrbot_plugin_marry_groupmate.src.core import (
    get_group_record_data,
    upsert_user_wife_record,
)
from astrbot_plugin_marry_groupmate.src.draw_records import daily_rounds
from astrbot_plugin_marry_groupmate.src.utils import load_json


@pytest.fixture
def game(tmp_path, monkeypatch):
    now = time.time()
    settings = {
        "enabled": True,
        "single_chance": 0,
        "double_chance": 100,
        "harem_chance": 0,
    }
    plugin = SimpleNamespace(
        config={"daily_limit": 1, "special_draw": settings},
        active_users={"g": {uid: now for uid in ("me", "a", "b", "c")}},
        records={},
        records_file=str(tmp_path / "draws.json"),
    )
    event = SimpleNamespace(
        is_private_chat=lambda: False,
        get_platform_name=lambda: "aiocqhttp",
        get_group_id=lambda: "g",
        get_sender_id=lambda: "me",
        get_sender_name=lambda: "Me",
        get_self_id=lambda: "bot",
        chain_result=lambda chain: "\n".join(
            c.text for c in chain if hasattr(c, "text")
        ),
        plain_result=lambda text: text,
    )
    monkeypatch.setattr(draw, "get_platform_members", AsyncMock(return_value=[]))
    monkeypatch.setattr(draw, "get_avatar_sources", AsyncMock(return_value={}))
    return plugin, event


@pytest.mark.asyncio
async def test_single_consumes_one_round_and_persists_without_relationship(game):
    plugin, event = game
    plugin.config["special_draw"].update(single_chance=100, double_chance=0)

    first = [response async for response in draw.cmd_draw_wife(plugin, event)]
    plugin.records = load_json(plugin.records_file, {})
    repeated = [response async for response in draw.cmd_draw_wife(plugin, event)]
    history = [response async for response in cmd_show_history(plugin, event)]

    group = get_group_record_data(plugin, "g")
    rounds = daily_rounds(group, "me")
    assert len(rounds) == 1
    assert rounds[0].outcome == "single"
    assert not group["records"]
    assert not daily_rounds(get_group_record_data(plugin, "another_group"), "me")
    assert "打光棍" in first[0] and "打光棍" in repeated[0] and "打光棍" in history[0]
    assert "剩余抽取次数：0次" in first[0]
    assert "(1/1)" in history[0]

    # A direct relationship uses the same slot even when it follows a single draw.
    upsert_user_wife_record(
        group,
        user_id="me",
        wife_id="a",
        wife_name="A",
        timestamp=rounds[0].timestamp,
        daily_limit=1,
    )
    assert len(daily_rounds(group, "me")) == 1
    assert not group["single_draws"]

    plugin.records["date"] = "2000-01-01"
    assert not daily_rounds(get_group_record_data(plugin, "g"), "me")


@pytest.mark.asyncio
async def test_multi_draw_is_one_round_under_concurrent_requests(game, monkeypatch):
    plugin, event = game
    both_started = asyncio.Event()
    arrivals = 0

    async def list_members(*_args):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            both_started.set()
        await both_started.wait()
        return [{"user_id": "a"}, {"user_id": "b"}]

    async def invoke():
        return [response async for response in draw.cmd_draw_wife(plugin, event)]

    monkeypatch.setattr(draw, "get_platform_members", list_members)
    replies = await asyncio.wait_for(asyncio.gather(invoke(), invoke()), timeout=2)

    group = load_json(plugin.records_file, {})["groups"]["g"]
    assert {row["wife_id"] for row in group["records"]} == {"a", "b"}
    assert len(group["records"]) == 2
    assert len(daily_rounds(group, "me")) == 1
    assert "c" in plugin.active_users["g"]
    assert all("你今天包了二奶！" in reply[0] for reply in replies)


@pytest.mark.asyncio
async def test_failed_draw_write_can_be_retried(game, monkeypatch):
    plugin, event = game
    with monkeypatch.context() as patch:
        patch.setattr(draw, "save_json", lambda *_args: False)
        replies = [response async for response in draw.cmd_draw_wife(plugin, event)]
    assert "保存抽取结果失败" in replies[0]
    assert not daily_rounds(get_group_record_data(plugin, "g"), "me")
    assert not Path(plugin.records_file).exists()

    replies = [response async for response in draw.cmd_draw_wife(plugin, event)]
    assert "你今天包了二奶！" in replies[0]
    assert len(daily_rounds(get_group_record_data(plugin, "g"), "me")) == 1


def test_outcomes_normalize_over_100_and_use_only_available_candidates(monkeypatch):
    settings = {
        "enabled": True,
        "single_chance": 80,
        "double_chance": 80,
        "harem_chance": 80,
    }

    def roll(total):
        assert total == 240
        return 170

    monkeypatch.setattr(draw.random, "randrange", roll)
    assert draw.choose_outcome(settings, 3) == ("harem", 3)
    assert draw.choose_outcome(settings, 2) == ("normal", 1)
    assert draw.choose_outcome({"enabled": False}, 10) == ("normal", 1)

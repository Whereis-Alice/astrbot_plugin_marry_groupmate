from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_marry_groupmate.src.command import draw
from astrbot_plugin_marry_groupmate.src.command.qq_official import images, message
from astrbot_plugin_marry_groupmate.src.command.qq_official.draw import draw_card
from astrbot_plugin_marry_groupmate.src.command.qq_official.pick_wife_official import (
    PICK_REPLY,
    build_keyboard,
)
from astrbot_plugin_marry_groupmate.src.draw_records import DrawRound
from PIL import Image


def event_for(platform="qq_official"):
    return SimpleNamespace(
        bot=SimpleNamespace(api=object()),
        get_group_id=lambda: "group",
        get_platform_name=lambda: platform,
        message_obj=SimpleNamespace(
            raw_message={"group_openid": "group"}, message_id="msg"
        ),
        _has_send_oper=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ("qq_official", "qq_official_webhook"))
async def test_rejected_keyboard_retries_the_same_message_without_keyboard(
    monkeypatch, platform
):
    posted = AsyncMock(side_effect=[403, 200])
    monkeypatch.setattr(message, "_post", posted)
    event = event_for(platform)
    keyboard = {"content": {"rows": [{"buttons": [{}]}]}}
    assert await message.send_markdown(
        SimpleNamespace(config={}), event, "内容", keyboard=keyboard
    )
    original, fallback = [call.args[2] for call in posted.await_args_list]
    assert original["msg_seq"] == fallback["msg_seq"]
    assert original["keyboard"] == keyboard
    assert fallback["markdown"] == original["markdown"]
    assert "keyboard" not in fallback
    assert event._has_send_oper


@pytest.mark.asyncio
async def test_buttons_switch_keeps_markdown_and_image_rejection_retries_with_text(
    monkeypatch,
):
    posted = AsyncMock(side_effect=[400, 200])
    monkeypatch.setattr(message, "_post", posted)
    plugin = SimpleNamespace(config={"qq_official_buttons": False})
    assert await message.send_markdown(
        plugin, event_for(), "内容", keyboard={"some": True}, image="![avatar](example)"
    )
    with_image, without_image = [call.args[2] for call in posted.await_args_list]
    assert all("keyboard" not in payload for payload in (with_image, without_image))
    assert with_image["markdown"]["force_verify_image_resource"]
    assert without_image["markdown"] == {"content": "内容"}


@pytest.mark.asyncio
async def test_timeout_does_not_send_a_duplicate(monkeypatch):
    posted = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr(message, "_post", posted)
    assert await message.send_markdown(SimpleNamespace(config={}), event_for(), "内容")
    posted.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "existing", "heading", "expected_quote"),
    [
        ("normal", False, "请好好对待她哦", "> 剩余抽取次数：0次"),
        ("single", False, "今天打光棍啦", "> 剩余抽取次数：0次"),
        ("double", False, "包二奶", "> 剩余抽取次数：0次"),
        ("normal", True, "你今天已经有老婆了哦", None),
    ],
)
async def test_draw_card_quotes_only_remaining_draws(
    monkeypatch, outcome, existing, heading, expected_quote
):
    posted = AsyncMock(return_value=200)
    monkeypatch.setattr(message, "_post", posted)
    monkeypatch.setattr(
        draw, "get_display_name", lambda *_args, **kwargs: kwargs["fallback"]
    )
    monkeypatch.setattr(draw, "get_avatar_sources", AsyncMock(return_value={}))
    matches = [] if outcome == "single" else [{"wife_id": "u2", "wife_name": "成员"}]
    if outcome == "double":
        matches.append({"wife_id": "u3", "wife_name": "另一位成员"})
    round_ = DrawRound(timestamp="", outcome=outcome, matches=matches)

    replies = [
        result
        async for result in draw._send_round(
            SimpleNamespace(config={}),
            event_for(),
            "user",
            round_,
            limit=1,
            used=1,
            existing=existing,
        )
    ]

    assert not replies
    content = posted.await_args.args[2]["markdown"]["content"]
    assert heading in content
    quotes = [line for line in content.splitlines() if line.startswith(">")]
    assert quotes == ([expected_quote] if expected_quote else [])
    assert content.count("剩余抽取次数") == int(expected_quote is not None)


@pytest.mark.asyncio
async def test_markdown_off_uses_plain_reply_with_legacy_result_type(monkeypatch):
    class LegacyResult:
        def __init__(self, text):
            self.text = text
            self.should_render_image = True

        def use_t2i(self, enabled):
            self.should_render_image = enabled
            return self

    posted = AsyncMock()
    monkeypatch.setattr(message, "_post", posted)
    event = event_for()
    event.plain_result = LegacyResult
    plugin = SimpleNamespace(config={"qq_official_markdown": False})
    replies = [
        result
        async for result in draw_card(
            plugin,
            event,
            "user",
            "[成员]",
            None,
            suffix="\n请好好对待她哦",
            remaining="\n剩余抽取次数：0次",
        )
    ]
    assert replies[0].text == "[成员]\n请好好对待她哦\n剩余抽取次数：0次"
    assert not replies[0].should_render_image
    posted.assert_not_awaited()


def test_six_candidates_fit_the_keyboard_and_only_owner_can_send():
    req = SimpleNamespace(
        user_id="owner", candidate_names=[str(i) for i in range(1, 7)]
    )
    rows = build_keyboard(req, reselect="重新挑选", abandon="放弃")["content"]["rows"]
    assert len(rows) == 4
    for row in rows:
        assert len(row["buttons"]) == 2
        for entry in row["buttons"]:
            action = entry["action"]
            assert action["type"] == 2
            assert action["permission"] == {"type": 0, "specify_user_ids": ["owner"]}
            assert message.reply_action(action["data"], PICK_REPLY)
    assert message.reply_action("1", PICK_REPLY) == ""


class ImageResponse:
    status = 200

    def __init__(self, data):
        self.content = self
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def iter_chunked(self, _):
        yield self.data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size", "rendered"),
    [
        ((960, 320), "#480px #160px"),
        ((300, 840), "#150px #420px"),
        ((120, 70), "#120px #70px"),
    ],
)
async def test_avatars_fit_both_display_limits_at_their_original_ratio(
    monkeypatch, size, rendered
):
    buffer = BytesIO()
    with Image.new("RGB", size) as avatar:
        avatar.save(buffer, format="PNG")
    response = ImageResponse(buffer.getvalue())

    class FakeSession(ImageResponse):
        def __init__(self, **_):
            pass

        def get(self, url, *, allow_redirects):
            assert url.startswith("https://thirdqq.qlogo.cn/")
            assert not allow_redirects
            return response

    monkeypatch.setattr(images.aiohttp, "ClientSession", FakeSession)
    result = await images.markdown_image("https://thirdqq.qlogo.cn/avatar(test).png")
    assert rendered in result
    assert "avatar%28test%29.png" in result

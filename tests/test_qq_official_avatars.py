from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_marry_groupmate.src.command.qq_official.images import markdown_image
from astrbot_plugin_marry_groupmate.src.platforms import (
    qq_official_avatars,
    user_profiles,
)
from PIL import Image


@pytest.mark.asyncio
async def test_stock_avatar_bytes_are_rejected_without_discarding_all_small_avatars(
    monkeypatch,
):
    def picture(color):
        with BytesIO() as buffer:
            with Image.new("RGB", (40, 40), color) as avatar:
                avatar.save(buffer, format="JPEG")
            return buffer.getvalue()

    placeholder = picture("white")
    face = picture("blue")
    monkeypatch.setattr(
        qq_official_avatars,
        "_STOCK_DIGESTS",
        {len(placeholder): sha256(placeholder).hexdigest()},
    )

    class Response:
        status = 200

        def __init__(self, data, headers):
            self.headers = headers
            self.data = data
            self.content = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def iter_chunked(self, _):
            yield self.data[:300]
            yield self.data[300:]

    class Session:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def get(self, url, *, allow_redirects):
            assert not allow_redirects
            if url.endswith("not-found"):
                return Response(face, {"X-FailNo": "-1208"})
            if url.endswith("missing"):
                return Response(face, {"X-Info": "notexist:-1208"})
            if url.endswith("not-found"):
                return Response(face, {"X-FailNo": "-1208"})
            return Response(placeholder if url.endswith("stock") else face, {})

    monkeypatch.setattr(qq_official_avatars.aiohttp, "ClientSession", Session)
    missing = "https://thirdqq.qlogo.cn/stock"
    present = "https://thirdqq.qlogo.cn/present"
    assert await markdown_image(missing) is None
    assert await markdown_image("https://thirdqq.qlogo.cn/not-found") is None
    assert await markdown_image("https://thirdqq.qlogo.cn/missing") is None
    assert "#40px #40px" in await markdown_image(present)


@pytest.mark.asyncio
async def test_official_avatar_lookup_uses_event_metadata_and_filters_cdn_fallback(
    monkeypatch,
):
    avatar_url = "https://thirdqq.qlogo.cn/real-avatar"
    app_id = "102000001"
    probe = AsyncMock(side_effect=lambda url: (100, 100) if url == avatar_url else None)
    monkeypatch.setattr(user_profiles, "avatar_dimensions", probe)
    monkeypatch.setattr(user_profiles, "_save_profiles", lambda _: None)
    plugin = SimpleNamespace(official_profiles={})
    event = SimpleNamespace(
        bot=SimpleNamespace(platform=SimpleNamespace(config={"appid": app_id})),
        get_group_id=lambda: "group",
        get_platform_id=lambda: "configured-platform",
        get_platform_name=lambda: "qq_official_webhook",
        get_sender_id=lambda: "sender",
        is_private_chat=lambda: False,
        message_obj=SimpleNamespace(
            raw_message={
                "author": {"nickname": "Sender"},
                "mentions": [{"member_openid": "mentioned", "avatar_url": avatar_url}],
            },
        ),
    )
    user_profiles.remember_user_profile(plugin, event)
    assert await user_profiles.get_avatar_sources(
        plugin, event, ["sender", "mentioned"]
    ) == {
        "sender": None,
        "mentioned": avatar_url,
    }
    probe.assert_any_await(f"https://thirdqq.qlogo.cn/qqapp/{app_id}/sender/640")
    probe.assert_any_await(avatar_url)


@pytest.mark.parametrize("platform", ("qq_official", "qq_official_webhook"))
def test_official_event_prefers_sender_avatar_and_group_nickname(monkeypatch, platform):
    monkeypatch.setattr(user_profiles, "_save_profiles", lambda _: None)
    avatar = "https://thirdqq.qlogo.cn/real-avatar"
    plugin = SimpleNamespace(official_profiles={})
    event = SimpleNamespace(
        get_group_id=lambda: "group",
        get_platform_id=lambda: "configured-platform",
        get_platform_name=lambda: platform,
        get_sender_id=lambda: "sender",
        get_sender_group_nickname=lambda: "群名片",
        get_extra=lambda name: {"sender_avatar": avatar}.get(name),
        is_private_chat=lambda: False,
        message_obj=SimpleNamespace(
            raw_message=SimpleNamespace(
                raw_data={
                    "author": {
                        "username": "帐号昵称",
                        "member_nickname": "事件群昵称",
                        "avatar": "https://thirdqq.qlogo.cn/older-avatar",
                    },
                    "mentions": [
                        {"member_openid": "other", "member_nickname": "另一位群友"}
                    ],
                }
            )
        ),
    )

    user_profiles.remember_user_profile(plugin, event)

    assert user_profiles.get_avatar_url(plugin, event, "sender") == avatar
    assert user_profiles.get_display_name(plugin, event, "sender") == "群名片"
    assert user_profiles.get_display_name(plugin, event, "other") == "另一位群友"

"""Native QQ group Markdown with text-command buttons and graceful fallback."""

import asyncio
import html
import random
import re

import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from botpy.errors import (
    AuthenticationFailedError,
    ForbiddenError,
    MethodNotAllowedError,
    NotFoundError,
    SequenceNumberError,
)
from botpy.http import Route

from ...platforms.user_profiles import is_qq_official_event


def escape_markdown(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~<])", r"\\\1", text)


def mention(user_id: str) -> str:
    return f'<qqbot-at-user id="{html.escape(user_id, quote=True)}" />'


def is_official_group(event: AstrMessageEvent) -> bool:
    if not is_qq_official_event(event):
        return False
    raw = getattr(event.message_obj, "raw_message", None)
    group_id = (
        raw.get("group_openid")
        if isinstance(raw, dict)
        else getattr(raw, "group_openid", None)
    )
    return bool(group_id) and str(group_id) == str(event.get_group_id())


def markdown_enabled(plugin, event: AstrMessageEvent) -> bool:
    return bool(
        plugin.config.get("qq_official_markdown", True) and is_official_group(event)
    )


def reply_action(text: str, prefix: str) -> str:
    words = text.strip().removeprefix("/").split(maxsplit=1)
    return words[1] if len(words) == 2 and words[0] == prefix else ""


def plain_result(event: AstrMessageEvent, text: str):
    result = event.plain_result(text).use_t2i(False)
    use_markdown = getattr(result, "use_markdown", None)
    if use_markdown is not None:
        use_markdown(False)
    return result


async def _post(api, group_id: str, payload: dict) -> int:
    # qq-botpy collapses 400 and 500 into one exception. Use its authenticated
    # session when available to distinguish a rejected card from a transient error.
    http = getattr(api, "_http", None)
    if callable(getattr(http, "check_session", None)):
        await http.check_session()
        route = Route(
            "POST", "/v2/groups/{group_openid}/messages", group_openid=group_id
        )
        route.is_sandbox = http.is_sandbox
        async with http._session.request(
            route.method,
            route.url,
            json=payload,
            headers=http._headers,
            timeout=aiohttp.ClientTimeout(total=8),
            allow_redirects=False,
        ) as response:
            return response.status

    send = getattr(api, "post_group_message", None)
    if send is None:
        return 400
    try:
        result = await send(group_openid=group_id, **payload)
    except (ForbiddenError, MethodNotAllowedError):
        return 403
    # Older SDK methods can return None after a timeout. Avoid sending a duplicate.
    return 200 if result else 504


async def send_markdown(
    plugin,
    event: AstrMessageEvent,
    content: str,
    *,
    keyboard: dict | None = None,
    image: str | None = None,
) -> bool:
    """Try a native card, then let the caller send plain text on explicit rejection."""
    if not markdown_enabled(plugin, event):
        return False
    message_id = str(event.message_obj.message_id or "")
    api = getattr(getattr(event, "bot", None), "api", None)
    if not message_id or api is None:
        return False
    if not plugin.config.get("qq_official_buttons", True):
        keyboard = None
    group_id = str(event.get_group_id())
    sequence = random.randint(1, 10000)
    markdown = {"content": f"{content}\n\n{image}" if image else content}
    if image:
        markdown["force_verify_image_resource"] = True
    payload = {"msg_id": message_id, "msg_seq": sequence, "msg_type": 2}
    if keyboard:
        payload["keyboard"] = keyboard
    while True:
        try:
            async with asyncio.timeout(10):
                status = await _post(api, group_id, {**payload, "markdown": markdown})
        except (
            AttributeError,
            TypeError,
            AuthenticationFailedError,
            NotFoundError,
            SequenceNumberError,
        ):
            return False
        except (TimeoutError, OSError, aiohttp.ClientError) as exc:
            logger.warning(
                f"[marry_groupmate] QQ Markdown 发送结果未确认 ({type(exc).__name__})"
            )
            event._has_send_oper = True
            return True
        except RuntimeError as exc:
            logger.warning(
                f"[marry_groupmate] QQ Markdown 发送结果未确认 ({type(exc).__name__})"
            )
            event._has_send_oper = True
            return True
        if 200 <= status < 300:
            event._has_send_oper = True
            return True
        if status >= 500:
            logger.warning(
                f"[marry_groupmate] QQ Markdown 发送结果未确认 (HTTP {status})"
            )
            event._has_send_oper = True
            return True
        if status in (401, 404, 429):
            return False
        if "keyboard" in payload:
            payload.pop("keyboard")
            continue
        if image and markdown["content"] != content:
            markdown = {"content": content}
            continue
        logger.debug(f"[marry_groupmate] QQ Markdown 已回退至普通消息 (HTTP {status})")
        return False


def command_button(button_id: str, user_id: str, text: str, label: str) -> dict:
    """Send a normal group command, restricted to the user who owns the action."""
    return {
        "id": f"marry_{button_id}",
        "render_data": {"label": label[:10], "style": 1},
        "action": {
            "type": 2,
            "permission": {"type": 0, "specify_user_ids": [user_id]},
            "data": text,
            "enter": True,
            "reply": False,
            "unsupport_tips": "请手动发送指令",
        },
    }

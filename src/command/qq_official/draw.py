"""QQ official group cards for draw results."""

from astrbot.api.event import AstrMessageEvent

from .images import markdown_image
from .message import (
    command_button,
    escape_markdown,
    markdown_enabled,
    mention,
    plain_result,
    send_markdown,
)


async def draw_card(
    plugin,
    event: AstrMessageEvent,
    user_id: str,
    text: str,
    avatar_url: str | None,
    *,
    suffix: str = "",
    wife_id: str | None = None,
):
    """Send a draw result and shortcuts, yielding a plain reply when required."""
    if markdown_enabled(plugin, event):
        subject = f"{mention(user_id)}\n\n{escape_markdown(text.strip())}"
        if wife_id:
            subject += f" {mention(wife_id)}"
        if suffix.strip():
            subject += f"\n\n{escape_markdown(suffix.strip())}"
        actions = (
            ("history", "群友抽取记录", "抽取记录"),
            ("help", "娶群友帮助", "玩法帮助"),
        )
        keyboard = {
            "content": {
                "rows": [
                    {
                        "buttons": [
                            command_button(key, user_id, f"/{command}", label)
                            for key, command, label in actions
                        ]
                    }
                ]
            }
        }
        if await send_markdown(
            plugin,
            event,
            subject,
            keyboard=keyboard,
            image=await markdown_image(avatar_url),
        ):
            return
    yield plain_result(event, f"{text.strip()}\n{suffix.strip()}".strip())

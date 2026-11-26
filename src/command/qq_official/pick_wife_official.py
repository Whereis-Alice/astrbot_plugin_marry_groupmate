"""QQ official group menus for the shared picking workflow."""

from astrbot.api.event import AstrMessageEvent

from ...i18n import tr
from .images import markdown_image
from .message import (
    command_button,
    escape_markdown,
    markdown_enabled,
    mention,
    plain_result,
    send_markdown,
)

PICK_REPLY = "群友挑选回复"


async def send_pick_confirmation(
    plugin, event: AstrMessageEvent, user_id: str, text: str, avatar_url: str | None
):
    """Show the selection without depending on the availability of its avatar."""
    if markdown_enabled(plugin, event) and await send_markdown(
        plugin,
        event,
        f"{mention(user_id)}\n\n{escape_markdown(text)}",
        image=await markdown_image(avatar_url),
    ):
        return
    yield plain_result(event, text)


def build_keyboard(req, *, reselect: str, abandon: str) -> dict:
    """Use at most five rows even at the maximum of six candidates."""

    def button(button_id, text, label):
        return command_button(button_id, req.user_id, f"/{PICK_REPLY} {text}", label)

    choices = [
        button(f"pick_{i}", str(i), f"{i}. {name}")
        for i, name in enumerate(req.candidate_names, 1)
    ]
    rows = [{"buttons": choices[i : i + 2]} for i in range(0, len(choices), 2)]
    rows.append(
        {
            "buttons": [
                button("pick_reselect", "重新挑选", reselect),
                button("pick_abandon", "放弃", abandon),
            ]
        }
    )
    return {"content": {"rows": rows}}


async def send_candidate_list(plugin, event: AstrMessageEvent, req, text: str) -> bool:
    if not markdown_enabled(plugin, event):
        return False
    title, *lines = text.splitlines()
    body = "\n".join(escape_markdown(line) + "  " for line in lines)
    return await send_markdown(
        plugin,
        event,
        f"{mention(req.user_id)}\n\n**{escape_markdown(title)}**\n\n{body}",
        keyboard=build_keyboard(
            req,
            reselect=tr(plugin, "pick_button_reselect"),
            abandon=tr(plugin, "pick_button_abandon"),
        ),
    )

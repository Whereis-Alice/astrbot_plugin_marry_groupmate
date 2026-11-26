"""QQ official proposal and rejection-confirmation menus."""

from astrbot.api.event import AstrMessageEvent

from ...i18n import tr
from .message import command_button, escape_markdown, mention, send_markdown

PROPOSE_REPLY = "群友求婚回复"
FORCE_REPLY = "群友强娶回复"


async def _send_menu(
    plugin, event: AstrMessageEvent, user_id: str, text: str, buttons: list[dict]
) -> bool:
    return await send_markdown(
        plugin,
        event,
        f"{mention(user_id)}\n\n{escape_markdown(text.strip())}",
        keyboard={"content": {"rows": [{"buttons": buttons}]}},
    )


async def send_proposal(
    plugin, event: AstrMessageEvent, target_id: str, text: str
) -> bool:
    """Only the proposal recipient can use the decision buttons."""
    return await _send_menu(
        plugin,
        event,
        target_id,
        text,
        [
            command_button(
                "propose_accept",
                target_id,
                f"/{PROPOSE_REPLY} 同意",
                tr(plugin, "propose_button_accept"),
            ),
            command_button(
                "propose_reject",
                target_id,
                f"/{PROPOSE_REPLY} 拒绝",
                tr(plugin, "propose_button_reject"),
            ),
        ],
    )


async def send_force_confirmation(
    plugin, event: AstrMessageEvent, proposer_id: str, text: str
) -> bool:
    """Only the proposer may decide whether to continue after rejection."""
    return await _send_menu(
        plugin,
        event,
        proposer_id,
        text,
        [
            command_button(
                "force_confirm",
                proposer_id,
                f"/{FORCE_REPLY} 是",
                tr(plugin, "force_button_confirm"),
            ),
            command_button(
                "force_abandon",
                proposer_id,
                f"/{FORCE_REPLY} 取消",
                tr(plugin, "force_button_abandon"),
            ),
        ],
    )

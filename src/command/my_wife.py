from datetime import datetime

from astrbot.api.event import AstrMessageEvent

from ..core import get_group_record_data
from ..draw_records import daily_rounds
from ..i18n import tr
from ..platforms.user_profiles import get_display_name
from ..utils import is_allowed_group


async def cmd_show_history(plugin_instance, event: AstrMessageEvent):
    if event.is_private_chat():
        yield event.plain_result(tr(plugin_instance, "group_only"))
        return
    group_id = str(event.get_group_id())
    if not is_allowed_group(group_id, plugin_instance.config):
        return

    user_id = str(event.get_sender_id())
    rounds = daily_rounds(get_group_record_data(plugin_instance, group_id), user_id)
    if not rounds:
        yield event.plain_result(tr(plugin_instance, "history_empty"))
        return

    daily_limit = plugin_instance.config.get("daily_limit", 1)
    res = [
        tr(
            plugin_instance,
            "history_title",
            used=len(rounds),
            limit=daily_limit,
        )
    ]
    for index, round_ in enumerate(rounds, 1):
        try:
            time_str = datetime.fromisoformat(round_.timestamp).strftime("%H:%M")
        except ValueError:
            time_str = "—"
        names = [
            get_display_name(
                plugin_instance, event, row["wife_id"], fallback=row["wife_name"]
            )
            for row in round_.matches
        ]
        if round_.outcome == "normal":
            line = tr(
                plugin_instance,
                "history_item",
                index=index,
                wife_name=names[0],
                time=time_str,
            )
        else:
            line = tr(
                plugin_instance,
                "history_special",
                index=index,
                outcome=tr(plugin_instance, f"draw_{round_.outcome}"),
                names=f" · {'、'.join(names)}" if names else "",
                time=time_str,
            )
        res.append(line)
    res.append(
        tr(
            plugin_instance,
            "history_remaining",
            remaining=max(0, daily_limit - len(rounds)),
        )
    )
    yield event.plain_result("\n".join(res))

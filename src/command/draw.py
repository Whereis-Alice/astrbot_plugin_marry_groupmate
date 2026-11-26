"""Random groupmate draws and their optional special outcomes."""

import random
from datetime import datetime
from uuid import uuid4

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent

from ...waifu_relations import maybe_add_other_half_record
from ..core import (
    auto_set_other_half_enabled,
    can_onebot_withdraw,
    cleanup_inactive,
    draw_excluded_users,
    get_active_user_days,
    get_group_record_data,
    schedule_onebot_delete_msg,
    send_onebot_message,
)
from ..debug import debug_log
from ..draw_records import DrawOutcome, DrawRound, daily_rounds
from ..i18n import tr
from ..platforms.telegram_support import is_telegram_event, self_user_id
from ..platforms.user_profiles import (
    avatar_image,
    get_avatar_sources,
    get_display_name,
    get_platform_members,
    is_qq_official_event,
    mention_user,
    platform_result,
)
from ..utils import is_allowed_group, resolve_member_name, save_json
from .pick_wife import has_pending_pick
from .qq_official.draw import draw_card


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def choose_outcome(settings: object, candidates: int) -> tuple[DrawOutcome, int]:
    if not isinstance(settings, dict) or not settings.get("enabled") or not candidates:
        return "normal", 1
    single = _bounded_int(settings.get("single_chance"), 25, 0, 100)
    double = _bounded_int(settings.get("double_chance"), 25, 0, 100)
    harem = _bounded_int(settings.get("harem_chance"), 5, 0, 100)
    roll = random.randrange(max(100, single + double + harem))
    if roll < single:
        return "single", 0
    if roll < single + double and candidates >= 2:
        return "double", 2
    if single + double <= roll < single + double + harem and candidates >= 3:
        maximum = _bounded_int(settings.get("harem_max"), 4, 3, 8)
        return "harem", random.randint(3, min(maximum, candidates))
    return "normal", 1


def _save_draw(
    plugin, group_id, group, outcome, matches, user_id, user_name, timestamp
) -> bool:
    """Publish the whole draw after a successful atomic write."""
    if outcome == "single":
        single_draws = group.get("single_draws", {})
        user_draws = [*single_draws.get(user_id, []), timestamp]
        new_group = {**group, "single_draws": {**single_draws, user_id: user_draws}}
    else:
        rows = list(group["records"])
        draw_id = uuid4().hex if outcome != "normal" else None
        for record in matches:
            if draw_id:
                record.update(draw_id=draw_id, draw_outcome=outcome)
            rows.append(record)
            wife_id = record["wife_id"]
            maybe_add_other_half_record(
                records=rows,
                user_id=user_id,
                user_name=user_name,
                wife_id=wife_id,
                wife_name=record["wife_name"],
                enabled=auto_set_other_half_enabled(plugin)
                and not group.get("single_draws", {}).get(wife_id),
                timestamp=timestamp,
            )
        new_group = {**group, "records": rows}

    # No await occurs between the final quota check, disk commit and memory update.
    groups = {**plugin.records["groups"], group_id: new_group}
    if not save_json(plugin.records_file, {**plugin.records, "groups": groups}):
        return False
    group.update(new_group)
    return True


async def _send_result(
    plugin, event, user_id, text, matches=(), *, suffix="", remaining="", notify=False
):
    ids = [record["wife_id"] for record in matches]
    official = is_qq_official_event(event)
    # Use one image. QQ can pick another match if the first has no accessible avatar.
    candidates = ids if official else ids[:1]
    if official and not plugin.config.get("qq_official_markdown", True):
        candidates = []
    avatars = await get_avatar_sources(plugin, event, candidates) if candidates else {}
    avatar_url = next((avatars[user] for user in candidates if avatars.get(user)), None)
    mentions = ids if notify and plugin.config.get("at_waifu", False) else []

    if official:
        async for result in draw_card(
            plugin,
            event,
            user_id,
            text,
            avatar_url,
            suffix=suffix,
            remaining=remaining,
            wife_id=mentions[0] if len(mentions) == 1 else None,
        ):
            yield result
        return

    footer = suffix + remaining
    if can_onebot_withdraw(plugin, event):
        message = [
            {"type": "at", "data": {"qq": user_id}},
            {"type": "text", "data": {"text": text}},
        ]
        for target in mentions:
            message.append({"type": "at", "data": {"qq": target}})
        if avatar_url:
            message.append({"type": "image", "data": {"file": avatar_url}})
        if footer:
            message.append({"type": "text", "data": {"text": footer}})
        message_id = await send_onebot_message(plugin, event, message=message)
        if message_id is not None:
            schedule_onebot_delete_msg(plugin, event.bot, message_id=message_id)
        return

    chain = [mention_user(plugin, event, user_id), Comp.Plain(text)]
    chain.extend(mention_user(plugin, event, target) for target in mentions)
    if avatar_url:
        chain.append(avatar_image(avatar_url))
    if footer:
        chain.append(Comp.Plain(footer))
    yield platform_result(event, chain)


async def _send_round(
    plugin, event, user_id, round_: DrawRound, *, limit, used, existing
):
    names = [
        get_display_name(plugin, event, row["wife_id"], fallback=row["wife_name"])
        if existing
        else row["wife_name"]
        for row in round_.matches
    ]
    remaining = (
        "" if existing else tr(plugin, "draw_remaining", remaining=max(0, limit - used))
    )
    suffix = ""
    if round_.outcome == "single":
        text = tr(plugin, "draw_single")
    elif round_.outcome in ("double", "harem"):
        heading = tr(plugin, f"draw_{round_.outcome}")
        lines = [f"{index}. 【{name}】" for index, name in enumerate(names, 1)]
        text = f"{heading}\n\n" + "\n".join(lines)
    else:
        key = "wife_existing" if existing else "draw_result"
        text = tr(plugin, key, wife_name=names[0])
        if not existing:
            suffix = tr(plugin, "draw_suffix")
    async for result in _send_result(
        plugin,
        event,
        user_id,
        text,
        round_.matches,
        suffix=suffix,
        remaining=remaining,
        notify=not existing,
    ):
        yield result


async def _send_limit(plugin, event, user_id, rounds, daily_limit):
    if daily_limit == 1:
        async for result in _send_round(
            plugin,
            event,
            user_id,
            rounds[-1],
            limit=daily_limit,
            used=len(rounds),
            existing=True,
        ):
            yield result
        return
    async for result in _send_result(
        plugin,
        event,
        user_id,
        tr(plugin, "daily_limit", count=len(rounds)),
    ):
        yield result


async def cmd_draw_wife(plugin, event: AstrMessageEvent):
    """在当前群聊抽取群友。"""
    if event.is_private_chat():
        yield event.plain_result(tr(plugin, "group_only"))
        return

    group_id = str(event.get_group_id())
    if not is_allowed_group(group_id, plugin.config):
        return
    user_id = str(event.get_sender_id())
    if has_pending_pick(group_id, user_id):
        yield event.plain_result(tr(plugin, "pick_in_progress"))
        return

    daily_limit = max(1, plugin.config.get("daily_limit", 1))
    rounds = daily_rounds(get_group_record_data(plugin, group_id), user_id)
    if len(rounds) >= daily_limit:
        async for result in _send_limit(plugin, event, user_id, rounds, daily_limit):
            yield result
        return

    cleanup_inactive(plugin, group_id)
    members = []
    try:
        members = await get_platform_members(plugin, event)
    except Exception as exc:  # noqa: BLE001 - adapter failures can have different types
        debug_log(plugin, "draw", f"member_list failed group={group_id} error={exc}")

    # The list can be partial. Use it to filter this draw without deleting activity records.
    bot_id = self_user_id(event)
    allow_bot = plugin.config.get("allow_marry_bot", False)
    excluded = draw_excluded_users(plugin) | {user_id, "0", ""}
    excluded.update(
        str(member.get("user_id", ""))
        for member in members
        if member.get("is_bot") and str(member.get("user_id")) != bot_id
    )
    if not allow_bot:
        excluded.add(bot_id)
    active = set(plugin.active_users.get(group_id, {}))
    if members or is_telegram_event(event):
        active.intersection_update(str(member.get("user_id", "")) for member in members)
    if allow_bot and bot_id:
        active.add(bot_id)
    pool = sorted(active - excluded)

    # Another command may have written records while the adapter returned members.
    group = get_group_record_data(plugin, group_id)
    rounds = daily_rounds(group, user_id)
    if len(rounds) >= daily_limit:
        async for result in _send_limit(plugin, event, user_id, rounds, daily_limit):
            yield result
        return
    if has_pending_pick(group_id, user_id):
        yield event.plain_result(tr(plugin, "pick_in_progress"))
        return
    if not pool:
        yield event.plain_result(
            tr(plugin, "wife_pool_empty", days=get_active_user_days(plugin))
        )
        return

    outcome, count = choose_outcome(plugin.config.get("special_draw"), len(pool))
    timestamp = datetime.now().astimezone().isoformat()
    matches = [
        {
            "user_id": user_id,
            "wife_id": candidate,
            "wife_name": resolve_member_name(
                members,
                user_id=candidate,
                fallback=get_display_name(plugin, event, candidate),
            ),
            "timestamp": timestamp,
        }
        for candidate in random.sample(pool, count)
    ]
    user_name = resolve_member_name(
        members,
        user_id=user_id,
        fallback=get_display_name(
            plugin, event, user_id, fallback=event.get_sender_name()
        ),
    )
    if not _save_draw(
        plugin, group_id, group, outcome, matches, user_id, user_name, timestamp
    ):
        yield event.plain_result(tr(plugin, "draw_save_failed"))
        return

    debug_log(
        plugin,
        "draw",
        f"group={group_id} user={user_id} result={outcome} matches={len(matches)}",
    )
    async for result in _send_round(
        plugin,
        event,
        user_id,
        DrawRound(timestamp, outcome, matches),
        limit=daily_limit,
        used=len(rounds) + 1,
        existing=False,
    ):
        yield result

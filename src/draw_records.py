"""Daily draw accounting shared by random draws, selection, and history."""

from dataclasses import dataclass, field
from typing import Literal

DrawOutcome = Literal["normal", "single", "double", "harem"]


@dataclass
class DrawRound:
    timestamp: str
    outcome: DrawOutcome = "normal"
    matches: list[dict] = field(default_factory=list)


def daily_rounds(group: dict, user_id: str) -> list[DrawRound]:
    """Collapse rows from one draw while preserving one slot per legacy record."""
    rounds: list[DrawRound] = []
    grouped: dict[str, DrawRound] = {}
    for record in group.get("records", []):
        if str(record.get("user_id")) != user_id:
            continue
        draw_id = record.get("draw_id")
        round_ = grouped.get(draw_id) if isinstance(draw_id, str) else None
        if round_ is None:
            round_ = DrawRound(
                timestamp=str(record.get("timestamp", "")),
                outcome=record.get("draw_outcome", "normal"),
            )
            rounds.append(round_)
            if isinstance(draw_id, str):
                grouped[draw_id] = round_
        round_.matches.append(record)
    rounds.extend(
        DrawRound(timestamp=timestamp, outcome="single")
        for timestamp in group.get("single_draws", {}).get(user_id, [])
    )
    return sorted(rounds, key=lambda round_: round_.timestamp)

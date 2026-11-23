"""Pure helpers for points math and Discord display."""
from datetime import datetime, timezone
import re

REWARD_COST = 5
REWARDS = {"Cash ($25)": "Cash ($25)", "Skin": "Skin"}


def point_word(n: int) -> str:
    return "point" if abs(n) == 1 else "points"


def points_summary(rows: list, *, max_rows: int = 20) -> str:
    if not rows:
        return "No points awarded yet."
    lines = []
    for index, r in enumerate(rows[:max_rows], start=1):
        lines.append(f"**{index}.** <@{r['user_id']}> — **{r['balance']}** {point_word(r['balance'])}")
    return "\n".join(lines)


def utc_label(ts: datetime | None = None) -> str:
    ts = ts or datetime.now(timezone.utc)
    return f"<t:{int(ts.timestamp())}:f>"


MAX_BULK_MEMBERS = 25
_MEMBER_TOKEN = re.compile(r"(?:<@!?(\d{15,22})>|(\d{15,22}))")

def parse_member_ids(raw: str) -> list[int]:
    """Parse comma/space/newline-separated Discord mentions or IDs, without silently dropping bad inputs."""
    parts = [part for part in re.split(r"[,\s;]+", raw.strip()) if part]
    if not parts:
        raise ValueError("Enter at least one member mention or Discord user ID.")
    result = []
    seen = set()
    for token in parts:
        match = _MEMBER_TOKEN.fullmatch(token)
        if not match:
            raise ValueError(f"Invalid member entry: {token[:40]!r}. Use @mentions or numeric Discord user IDs, separated by spaces or commas.")
        uid = int(match.group(1) or match.group(2))
        if uid not in seen:
            result.append(uid)
            seen.add(uid)
    if len(result) > MAX_BULK_MEMBERS:
        raise ValueError(f"Too many members. Maximum is {MAX_BULK_MEMBERS} unique members per command.")
    return result

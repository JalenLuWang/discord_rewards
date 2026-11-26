"""Pure helpers for points math and Discord display."""
from datetime import datetime, timezone
import re

# Rewards: 10 points unlocks cash redemptions; any whole-number amount
# of at least 10 points can be requested at $1 per point. A skin costs 20 points.
# The existing $20 maximum skin value remains unchanged in this release.
REWARD_COST = 10
MIN_CASH_POINTS = REWARD_COST
SKIN_POINTS = 20
SKIN_MAX_VALUE = 20
REWARDS = {
    "Cash ($1/point, minimum $10)": "Cash",
    "Skin (20 points each, up to $20)": "Skin",
}
LEADERBOARD_REWARDS = (
    "**Cash:** **1 point = $1**. The minimum cash payout is **10 points ($10)**. "
    "Once you have at least 10 points, redeem any **whole-dollar amount from $10 "
    "up to your available balance** (no $10 increments required).\n"
    f"**Skins:** **{SKIN_POINTS} points per skin** (up to **${SKIN_MAX_VALUE} value** each). "
    "All redemptions require admin approval."
)
BALANCE_REWARDS = (
    f"Cash: 1 point = $1; minimum payout ${MIN_CASH_POINTS}, "
    "then any whole-dollar amount of $10 or more up to your balance; "
    f"skins: {SKIN_POINTS} points each (up to ${SKIN_MAX_VALUE} value)"
)

def redemption_cost(reward: str, quantity: int = 1, points: int | None = None) -> int:
    """Cash: any whole-number amount >=10; skins: 20 points each."""
    if reward == "Cash":
        if quantity != 1:
            raise ValueError("For cash, use points rather than quantity (quantity must be 1).")
        amount = REWARD_COST if points is None else points
        if type(amount) is not int or not MIN_CASH_POINTS <= amount <= 2_147_483_647:
            raise ValueError("Cash withdrawals require at least 10 whole points ($10). You can request any whole-dollar amount above that minimum.")
        return amount
    if reward == "Skin":
        if points is not None:
            raise ValueError(f"For skins, use quantity ({SKIN_POINTS} points per skin); leave points empty.")
        if type(quantity) is not int or not 1 <= quantity <= 25:
            raise ValueError("Choose between 1 and 25 skins per request.")
        return SKIN_POINTS * quantity
    raise ValueError("Unknown reward type.")


SCRIM_PARTICIPATION = 1
SCRIM_FIRST = 9
MRC_WEEKLY_PARTICIPATION = 1
MRC_FIRST = 29
MRC_SECOND = 14
TOURNAMENT_PARTICIPATION = 1
TOURNAMENT_FIRST = 19
TOURNAMENT_SECOND = 9


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


def round_half_up(numerator: int, denominator: int) -> int:
    """Integer rounding of positive ratios; .5 goes up, without float precision issues."""
    if denominator < 1 or numerator < 0:
        raise ValueError("Invalid points or number of players")
    return (2 * numerator + denominator) // (2 * denominator)

def performance_awards(first: list[int], second: list[int], first_prize: int, second_prize: int = 0) -> dict[int, int]:
    """Award rank pools by count of occupied places, tied players share crossed prize slots.

    For example, two tied first place on MRC split (29+14), rounding 21.5 to 22 each.
    Three tied first split 43/3 = 14 each. If one first and two tied for second,
    each tied second receives 14/2 = 7. No lower-place pool is awarded.
    """
    if not first:
        if second:
            raise ValueError("Second-place players require a first-place player")
        return {}
    if len(set(first)) != len(first) or len(set(second)) != len(second) or set(first) & set(second):
        raise ValueError("Performance groups cannot contain duplicate players")
    if second_prize == 0 and second:
        raise ValueError("This event does not award second place")
    if len(first) > 1 and second:
        raise ValueError("When first place is tied, its players occupy the second-place slots. Leave second empty.")
    prizes = [first_prize] + ([second_prize] if second_prize else [])
    out = {}
    occupied = 0
    for group in (first, second):
        if not group:
            continue
        pool = sum(prizes[occupied:occupied + len(group)])
        if pool:
            amount = round_half_up(pool, len(group))
            out.update({uid: amount for uid in group})
        occupied += len(group)
    return out

def payout_breakdown(kind: str, participants: list[int], first: list[int], second: list[int] | None = None) -> dict[int, dict[str, int]]:
    """Create a member->component->points breakdown for a single event."""
    second = second or []
    participants_set = set(participants)
    if len(participants_set) != len(participants) or not participants_set:
        raise ValueError("Provide unique participating members")
    if not set(first + second).issubset(participants_set):
        raise ValueError("All performance winners must be in the participant list")
    if kind == "scrim":
        base, p1, p2 = SCRIM_PARTICIPATION, SCRIM_FIRST, 0
    elif kind == "tournament":
        base, p1, p2 = TOURNAMENT_PARTICIPATION, TOURNAMENT_FIRST, TOURNAMENT_SECOND
    elif kind == "mrc-stage":
        base, p1, p2 = 0, MRC_FIRST, MRC_SECOND
    else:
        raise ValueError("Unknown event type")
    awards = performance_awards(first, second, p1, p2)
    return {uid: ({"participation": base} if base else {}) | ({"performance": awards[uid]} if uid in awards and awards[uid] else {}) for uid in participants}

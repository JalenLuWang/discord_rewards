"""MRC Points Bot: persistent points ledger, approved redemptions and live leaderboard."""
import asyncio
import logging
import os
from datetime import date
from typing import Optional

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from points_core import REWARD_COST, REWARDS, points_summary, point_word, utc_label, parse_member_ids, MAX_BULK_MEMBERS, payout_breakdown, MRC_WEEKLY_PARTICIPATION

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("mrc-points")
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID") or 0)
if not TOKEN or not DATABASE_URL:
    raise SystemExit("Set DISCORD_TOKEN and DATABASE_URL in Railway Variables or your local .env file")

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id BIGINT PRIMARY KEY,
    log_channel_id BIGINT,
    leaderboard_channel_id BIGINT,
    leaderboard_message_id BIGINT,
    approvals_channel_id BIGINT
);
CREATE TABLE IF NOT EXISTS balances (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
    PRIMARY KEY(guild_id, user_id)
);
ALTER TABLE balances ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
CREATE TABLE IF NOT EXISTS player_status_events (
    id BIGSERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('removed','restored')),
    points_cleared INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL,
    actor_id BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS player_status_history_idx ON player_status_events (guild_id,user_id,id DESC);
CREATE TABLE IF NOT EXISTS point_transactions (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    delta INTEGER NOT NULL CHECK (delta <> 0),
    balance_after INTEGER NOT NULL CHECK (balance_after >= 0),
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_id BIGINT NOT NULL,
    redemption_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tx_history_idx ON point_transactions (guild_id, user_id, id DESC);
CREATE TABLE IF NOT EXISTS redemptions (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    reward TEXT NOT NULL,
    cost INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','denied')),
    reviewer_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS event_awards (
    guild_id BIGINT NOT NULL, event_key TEXT NOT NULL, user_id BIGINT NOT NULL, component TEXT NOT NULL,
    transaction_id BIGINT NOT NULL REFERENCES point_transactions(id),
    PRIMARY KEY(guild_id,event_key,user_id,component)
);
ALTER TABLE redemptions ADD COLUMN IF NOT EXISTS details TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_per_member ON redemptions (guild_id,user_id) WHERE status='pending';
"""

class PointsBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())
        self.pool: asyncpg.Pool | None = None
        self.refresh_locks: dict[int, asyncio.Lock] = {}

    async def setup_hook(self):
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, command_timeout=20)
        async with self.pool.acquire() as db:
            await db.execute(SCHEMA)
        if GUILD_ID:
            target = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=target)
            synced = await self.tree.sync(guild=target)
        else:
            synced = await self.tree.sync()
        log.info("Registered %s slash commands", len(synced))

    async def close(self):
        if self.pool:
            await self.pool.close()
        await super().close()

bot = PointsBot()
points = app_commands.Group(name="points", description="Track participation points and rewards")


def admin_only(interaction: discord.Interaction) -> bool:
    return bool(interaction.guild and isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild)

async def require_admin(interaction: discord.Interaction) -> bool:
    if admin_only(interaction):
        return True
    await interaction.response.send_message("You need **Manage Server** permission to do that.", ephemeral=True)
    return False

async def get_settings(guild_id: int):
    async with bot.pool.acquire() as db:
        return await db.fetchrow("SELECT * FROM guild_settings WHERE guild_id=$1", guild_id)

async def get_channel(guild: discord.Guild, channel_id: int | None):
    if not channel_id:
        return None
    ch = guild.get_channel(channel_id)
    if ch is None:
        try:
            ch = await guild.fetch_channel(channel_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return None
    return ch if isinstance(ch, discord.TextChannel) else None

async def notify_log(guild: discord.Guild, row):
    settings = await get_settings(guild.id)
    channel = await get_channel(guild, settings['log_channel_id'] if settings else None)
    if not channel:
        log.warning("Missing or unavailable points log for guild %s", guild.id)
        return
    sign = "+" if row['delta'] > 0 else ""
    embed = discord.Embed(title=f"Points update #{row['id']}", color=0x2ecc71 if row['delta'] > 0 else 0xe67e22)
    embed.description = f"**Member:** <@{row['user_id']}>\n**Change:** {sign}{row['delta']} {point_word(row['delta'])}\n**Balance:** {row['balance_after']}\n**Reason:** {discord.utils.escape_markdown(row['reason'])[:500]}\n**Action:** {row['action']}\n**By:** <@{row['actor_id']}>\n**When:** {utc_label(row['created_at'])}"
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not write transaction log for guild %s, tx %s", guild.id, row['id'])

async def leaderboard_embed(guild_id: int) -> discord.Embed:
    async with bot.pool.acquire() as db:
        rows = await db.fetch("SELECT user_id, balance FROM balances WHERE guild_id=$1 AND is_active=TRUE AND balance > 0 ORDER BY balance DESC, user_id ASC LIMIT 20", guild_id)
    embed = discord.Embed(title="🏆 MRC Points Leaderboard", description=points_summary(rows), color=0x5865f2)
    embed.add_field(name="🎁 Rewards", value="**1 point = $1** · **20 points = $20 cash or 1 skin (up to $20)**", inline=False)
    embed.set_footer(text="Updated automatically • Top 20 members • /points balance for your balance")
    return embed

async def update_leaderboard(guild: discord.Guild):
    lock = bot.refresh_locks.setdefault(guild.id, asyncio.Lock())
    async with lock:
        settings = await get_settings(guild.id)
        channel = await get_channel(guild, settings['leaderboard_channel_id'] if settings else None)
        if not channel:
            return
        embed = await leaderboard_embed(guild.id)
        message_id = settings['leaderboard_message_id']
        if message_id:
            try:
                msg = await channel.fetch_message(message_id)
                await msg.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.warning("Unable to edit previous leaderboard; will try to create a new one")
        try:
            msg = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            async with bot.pool.acquire() as db:
                await db.execute("UPDATE guild_settings SET leaderboard_message_id=$2 WHERE guild_id=$1", guild.id, msg.id)
            try:
                await msg.pin(reason="Official points leaderboard")
            except (discord.Forbidden, discord.HTTPException):
                log.warning("Could not pin leaderboard, need Manage Messages permission")
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not post leaderboard to guild %s", guild.id)

async def apply_delta(guild_id: int, user_id: int, delta: int, action: str, reason: str, actor_id: int, redemption_id=None):
    """Atomic balance update with row lock and append-only ledger."""
    async with bot.pool.acquire() as db:
        async with db.transaction():
            await db.execute("INSERT INTO balances(guild_id,user_id,balance) VALUES($1,$2,0) ON CONFLICT DO NOTHING", guild_id, user_id)
            balance = await db.fetchval("SELECT balance FROM balances WHERE guild_id=$1 AND user_id=$2 FOR UPDATE", guild_id, user_id)
            active = await db.fetchval("SELECT is_active FROM balances WHERE guild_id=$1 AND user_id=$2", guild_id, user_id)
            if not active:
                raise ValueError("This player was removed. An admin must restore the player before awarding or redeeming points.")
            after = balance + delta
            if after < 0:
                raise ValueError(f"Insufficient points: current balance is {balance}.")
            await db.execute("UPDATE balances SET balance=$3 WHERE guild_id=$1 AND user_id=$2", guild_id, user_id, after)
            return await db.fetchrow("INSERT INTO point_transactions(guild_id,user_id,delta,balance_after,action,reason,actor_id,redemption_id) VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *", guild_id,user_id,delta,after,action,reason,actor_id,redemption_id)

async def post_change(guild: discord.Guild, row):
    await notify_log(guild, row)
    await update_leaderboard(guild)

@points.command(name="setup", description="Set the transaction log, leaderboard and redemption approval channels")
@app_commands.describe(log_channel="Where point changes are logged", leaderboard_channel="Where the live leaderboard appears", approvals_channel="Where redemption requests appear")
async def setup(interaction: discord.Interaction, log_channel: discord.TextChannel, leaderboard_channel: discord.TextChannel, approvals_channel: discord.TextChannel):
    if not await require_admin(interaction): return
    await interaction.response.defer(ephemeral=True)
    async with bot.pool.acquire() as db:
        await db.execute("""INSERT INTO guild_settings(guild_id,log_channel_id,leaderboard_channel_id,approvals_channel_id,leaderboard_message_id) VALUES($1,$2,$3,$4,NULL)
        ON CONFLICT(guild_id) DO UPDATE SET log_channel_id=$2, leaderboard_message_id=CASE WHEN guild_settings.leaderboard_channel_id=$3 THEN guild_settings.leaderboard_message_id ELSE NULL END, leaderboard_channel_id=$3, approvals_channel_id=$4""", interaction.guild_id,log_channel.id,leaderboard_channel.id,approvals_channel.id)
    await update_leaderboard(interaction.guild)
    await interaction.followup.send(f"Configured! Changes → {log_channel.mention}; leaderboard → {leaderboard_channel.mention}; reward requests → {approvals_channel.mention}. Make sure I can read/send messages and embed links in all three channels. Pinning additionally needs Manage Messages.", ephemeral=True)

async def resolve_bulk_members(guild: discord.Guild, raw: str) -> list[int]:
    """Only permit verified guild members; reject entire batch if any entry is invalid."""
    ids = parse_member_ids(raw)
    for uid in ids:
        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                raise ValueError(f"Could not verify <@{uid}> as a member of this server. Nothing was changed.")
        if member.bot:
            raise ValueError(f"<@{uid}> is a bot. Nothing was changed.")
    return ids

async def apply_bulk_delta(guild_id: int, member_ids: list[int], delta: int, action: str, reason: str, actor_id: int):
    """All-or-nothing bulk awards/deductions, one ledger transaction per unique member."""
    if not member_ids or len(member_ids) > MAX_BULK_MEMBERS or len(set(member_ids)) != len(member_ids):
        raise ValueError("Invalid bulk member list.")
    # Sorted row-lock order prevents deadlocks across concurrent batches.
    async with bot.pool.acquire() as db:
        async with db.transaction():
            for uid in sorted(member_ids):
                await db.execute("INSERT INTO balances(guild_id,user_id,balance) VALUES($1,$2,0) ON CONFLICT DO NOTHING",guild_id,uid)
            current = {}
            for uid in sorted(member_ids):
                current[uid] = await db.fetchval("SELECT balance FROM balances WHERE guild_id=$1 AND user_id=$2 FOR UPDATE",guild_id,uid)
                active = await db.fetchval("SELECT is_active FROM balances WHERE guild_id=$1 AND user_id=$2", guild_id, uid)
                if not active:
                    raise ValueError(f"<@{uid}> was removed. Restore them before adjusting points. No balances were changed.")
                if current[uid] + delta < 0:
                    raise ValueError(f"<@{uid}> has only {current[uid]} points. No balances were changed.")
            rows = []
            for uid in member_ids:
                after = current[uid] + delta
                await db.execute("UPDATE balances SET balance=$3 WHERE guild_id=$1 AND user_id=$2",guild_id,uid,after)
                row = await db.fetchrow("INSERT INTO point_transactions(guild_id,user_id,delta,balance_after,action,reason,actor_id) VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING *",guild_id,uid,delta,after,action,reason,actor_id)
                rows.append(row)
            return rows

async def perform_bulk_adjustment(interaction: discord.Interaction, members: str, total_points: int, reason: str, *, deduct: bool):
    if not await require_admin(interaction): return
    await interaction.response.defer(ephemeral=True)
    try:
        member_ids = await resolve_bulk_members(interaction.guild, members)
        delta = -total_points if deduct else total_points
        rows = await apply_bulk_delta(interaction.guild_id,member_ids,delta,"adjustment" if deduct else "award",reason,interaction.user.id)
    except ValueError as e:
        await interaction.followup.send(str(e),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        return
    # Refresh once for the entire batch; every member gets an individual audit entry.
    log_failures=0
    for row in rows:
        try:
            await notify_log(interaction.guild,row)
        except Exception:
            log.exception("Unexpected log error for transaction %s",row['id'])
            log_failures+=1
    await update_leaderboard(interaction.guild)
    summary = "\n".join(f"• <@{r['user_id']}>: **{r['balance_after']}** points (transaction #{r['id']})" for r in rows)
    await interaction.followup.send(f"**{'Deducted' if deduct else 'Awarded'} {abs(delta)} points per member** for **{len(rows)}** member(s).\nReason: {discord.utils.escape_markdown(reason)}\n{summary}" + (f"\nWarning: {log_failures} log message(s) failed; database history remains saved." if log_failures else ""),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

@points.command(name="add", description="Admin: award points to one or several members")
@app_commands.describe(members="Paste @mentions or user IDs, separated by spaces/commas (max 25)", total_points="Points awarded to EACH listed member", reason="Event or achievement, e.g. Scrim attendance")
async def add(interaction: discord.Interaction, members: app_commands.Range[str,1,1800], total_points: app_commands.Range[int,1,10000], reason: app_commands.Range[str,2,200]):
    await perform_bulk_adjustment(interaction,members,total_points,reason,deduct=False)

@points.command(name="remove", description="Admin: deduct points from one or several members")
@app_commands.describe(members="Paste @mentions or user IDs, separated by spaces/commas (max 25)", total_points="Points deducted from EACH listed member", reason="Explanation for deduction")
async def remove(interaction: discord.Interaction, members: app_commands.Range[str,1,1800], total_points: app_commands.Range[int,1,10000], reason: app_commands.Range[str,2,200]):
    await perform_bulk_adjustment(interaction,members,total_points,reason,deduct=True)

async def award_event(guild_id: int, event_key: str, breakdown: dict[int, dict[str, int]], reason: str, actor_id: int):
    """Single atomic batch, transaction records + event idempotency in the same transaction."""
    if not event_key or len(event_key) > 150 or not reason.strip():
        raise ValueError("A valid event ID and reason are required.")
    if not breakdown or len(breakdown) > MAX_BULK_MEMBERS:
        raise ValueError("An event needs between 1 and 25 unique players.")
    rows = []
    async with bot.pool.acquire() as db:
        async with db.transaction():
            # Serialize all events for one guild+event ID, including concurrent command calls.
            await db.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",f"{guild_id}:{event_key}")
            exists = await db.fetchval("SELECT 1 FROM event_awards WHERE guild_id=$1 AND event_key=$2 LIMIT 1",guild_id,event_key)
            if exists:
                raise ValueError(f"Event `{event_key}` was already awarded. No points changed. Use /points add or remove for corrections.")
            for uid in sorted(breakdown):
                await db.execute("INSERT INTO balances(guild_id,user_id,balance) VALUES($1,$2,0) ON CONFLICT DO NOTHING",guild_id,uid)
            current = {}
            for uid in sorted(breakdown):
                player = await db.fetchrow("SELECT balance,is_active FROM balances WHERE guild_id=$1 AND user_id=$2 FOR UPDATE",guild_id,uid)
                if not player['is_active']:
                    raise ValueError(f"<@{uid}> was removed. Restore them before awarding. No points changed.")
                current[uid] = player['balance']
            for uid in sorted(breakdown):
                for component, delta in breakdown[uid].items():
                    if not delta: continue
                    current[uid] += delta
                    await db.execute("UPDATE balances SET balance=$3 WHERE guild_id=$1 AND user_id=$2",guild_id,uid,current[uid])
                    row = await db.fetchrow("INSERT INTO point_transactions(guild_id,user_id,delta,balance_after,action,reason,actor_id) VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING *",guild_id,uid,delta,current[uid],f"{component}_award",f"{event_key} | {reason} | {component}",actor_id)
                    await db.execute("INSERT INTO event_awards(guild_id,event_key,user_id,component,transaction_id) VALUES($1,$2,$3,$4,$5)",guild_id,event_key,uid,component,row['id'])
                    rows.append(row)
    return rows

async def run_event(interaction: discord.Interaction, event_key: str, participants: str, first: str, second: str, reason: str, kind: str):
    if not await require_admin(interaction): return
    await interaction.response.defer(ephemeral=True)
    try:
        all_ids = await resolve_bulk_members(interaction.guild, participants)
        first_ids = await resolve_bulk_members(interaction.guild, first) if first.strip() else []
        second_ids = await resolve_bulk_members(interaction.guild, second) if second.strip() else []
        if not first_ids:
            raise ValueError("Enter the top MVP/SVP winner(s) for the event.")
        if kind != 'scrim' and not first_ids and second_ids:
            raise ValueError("Second place requires first place.")
        breakdown = payout_breakdown(kind,all_ids,first_ids,second_ids)
        rows = await award_event(interaction.guild_id,f"{kind}:{event_key.strip().lower()}",breakdown,reason,interaction.user.id)
    except ValueError as exc:
        await interaction.followup.send(str(exc),ephemeral=True,allowed_mentions=discord.AllowedMentions.none());return
    for row in rows: await notify_log(interaction.guild,row)
    await update_leaderboard(interaction.guild)
    display = '\n'.join(f"• <@{uid}>: **+{sum(parts.values())}** ({', '.join(f'{k}: {v}' for k,v in parts.items())})" for uid,parts in breakdown.items())
    await interaction.followup.send(f"**{kind} awarded** · `{event_key}`\n{display}\nEach change has its own log entry. Re-running this event ID is blocked.",ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

@points.command(name="scrim", description="Admin: scrim BO3/BO5, +1 to all (including benched), +9 split for top MVP/SVP")
@app_commands.describe(event_id="Unique ID per BO3/BO5, e.g. 2026-11-24-bo3-a", members="All roster members including benched, @mentions/IDs (max 25)", first="Members tied for most MVPs/SVPs", reason="Required scrim name or opponent")
async def scrim(interaction: discord.Interaction,event_id: app_commands.Range[str,1,80],members: app_commands.Range[str,1,1800],first: app_commands.Range[str,1,1800],reason: app_commands.Range[str,2,200]):
    await run_event(interaction,event_id,members,first,'',reason,'scrim')

@points.command(name="mrc-week", description="Admin: +1 MRC weekly attendance, including benched, once per week")
@app_commands.describe(week="Any date in week (YYYY-MM-DD); week starts Monday", members="All attending/benched members, @mentions/IDs", reason="Required attendance explanation")
async def mrc_week(interaction: discord.Interaction,week: str,members: app_commands.Range[str,1,1800],reason: app_commands.Range[str,2,200]):
    if not await require_admin(interaction):return
    await interaction.response.defer(ephemeral=True)
    try:
        day=date.fromisoformat(week)
        from datetime import timedelta
        monday=day-timedelta(days=day.weekday())
        ids=await resolve_bulk_members(interaction.guild,members)
        # Weekly awards across multiple batches: keep existing members, prevent double grants.
        rows=await award_week(interaction.guild_id,monday.isoformat(),ids,reason,interaction.user.id)
    except ValueError as exc:
        await interaction.followup.send(f"Invalid attendance: {exc}",ephemeral=True);return
    for row in rows:await notify_log(interaction.guild,row)
    await update_leaderboard(interaction.guild)
    await interaction.followup.send(f"MRC participation for week of **{monday}**: **+1 point** each to {len(rows)} members. Already-awarded members are not charged twice.",ephemeral=True)

async def award_week(guild_id: int, week: str, ids: list[int], reason: str, actor_id: int):
    key=f"mrc-week:{week}"
    rows=[]
    async with bot.pool.acquire() as db:
        async with db.transaction():
            await db.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",f"{guild_id}:{key}")
            for uid in sorted(ids):
                await db.execute("INSERT INTO balances(guild_id,user_id,balance) VALUES($1,$2,0) ON CONFLICT DO NOTHING",guild_id,uid)
            for uid in sorted(ids):
                player=await db.fetchrow("SELECT balance,is_active FROM balances WHERE guild_id=$1 AND user_id=$2 FOR UPDATE",guild_id,uid)
                if not player['is_active']:
                    raise ValueError(f"<@{uid}> has been removed. No weekly points changed.")
            for uid in ids:
                exists=await db.fetchval("SELECT 1 FROM event_awards WHERE guild_id=$1 AND event_key=$2 AND user_id=$3",guild_id,key,uid)
                if exists:continue
                balance=await db.fetchval("UPDATE balances SET balance=balance+1 WHERE guild_id=$1 AND user_id=$2 RETURNING balance",guild_id,uid)
                row=await db.fetchrow("INSERT INTO point_transactions(guild_id,user_id,delta,balance_after,action,reason,actor_id) VALUES($1,$2,1,$3,'mrc_week_participation',$4,$5) RETURNING *",guild_id,uid,balance,f"{key} | {reason}",actor_id)
                await db.execute("INSERT INTO event_awards(guild_id,event_key,user_id,component,transaction_id) VALUES($1,$2,$3,'participation',$4)",guild_id,key,uid,row['id'])
                rows.append(row)
    return rows

@points.command(name="mrc-stage", description="Admin: per stage/day +29 first, +14 second, ties split pools")
@app_commands.describe(event_id="Unique stage/day ID, e.g. cq-day1 or rounds-1-3", members="Players in stage, @mentions or IDs", first="First-place players (ties allowed)", second="Second-place players (leave empty when first place tied)", reason="Required description")
async def mrc_stage(interaction: discord.Interaction,event_id: app_commands.Range[str,1,80],members: app_commands.Range[str,1,1800],first: app_commands.Range[str,1,1800],reason: app_commands.Range[str,2,200],second: str = ""):
    await run_event(interaction,event_id,members,first,second,reason,'mrc-stage')

@points.command(name="tournament", description="Admin: full-day +1 attendance, +19 first, +9 second, ties split")
@app_commands.describe(event_id="Unique tournament/day ID", members="Full-day participants only", first="First-place MVP/SVP players", second="Second-place MVP/SVP players, leave empty if first tied", reason="Required tournament name", full_day="Confirm listed players committed for full day")
async def tournament(interaction: discord.Interaction,event_id: app_commands.Range[str,1,80],members: app_commands.Range[str,1,1800],first: app_commands.Range[str,1,1800],reason: app_commands.Range[str,2,200],full_day: bool, second: str = ""):
    if not full_day:
        await interaction.response.send_message("Tournament attendance requires a full-day commitment. Set full_day to True only after verifying attendance.",ephemeral=True);return
    await run_event(interaction,event_id,members,first,second,reason,'tournament')

async def change_player_status(guild_id: int, member_ids: list[int], reason: str, actor_id: int, *, restore: bool):
    """Atomically remove (zero and hide) or restore players, retaining audit history."""
    if not reason.strip():
        raise ValueError("A reason is required.")
    results = []
    async with bot.pool.acquire() as db:
        async with db.transaction():
            for uid in sorted(member_ids):
                await db.execute("INSERT INTO balances(guild_id,user_id,balance) VALUES($1,$2,0) ON CONFLICT DO NOTHING", guild_id, uid)
            previous = {}
            for uid in sorted(member_ids):
                previous[uid] = await db.fetchrow("SELECT balance,is_active FROM balances WHERE guild_id=$1 AND user_id=$2 FOR UPDATE", guild_id, uid)
                if restore and previous[uid]['is_active']:
                    raise ValueError(f"<@{uid}> is already active. No players were changed.")
                if not restore and not previous[uid]['is_active']:
                    raise ValueError(f"<@{uid}> has already been removed. No players were changed.")
            for uid in member_ids:
                old = previous[uid]['balance']
                if restore:
                    await db.execute("UPDATE balances SET is_active=TRUE WHERE guild_id=$1 AND user_id=$2", guild_id, uid)
                else:
                    await db.execute("UPDATE balances SET balance=0,is_active=FALSE WHERE guild_id=$1 AND user_id=$2", guild_id, uid)
                    # Prevent approval of outstanding reward requests after removal.
                    await db.execute("UPDATE redemptions SET status='denied',reviewer_id=$3,resolved_at=now() WHERE guild_id=$1 AND user_id=$2 AND status='pending'", guild_id, uid, actor_id)
                    if old > 0:
                        await db.execute("INSERT INTO point_transactions(guild_id,user_id,delta,balance_after,action,reason,actor_id) VALUES($1,$2,$3,0,'player_removed',$4,$5)", guild_id,uid,-old,reason,actor_id)
                event = await db.fetchrow("INSERT INTO player_status_events(guild_id,user_id,action,points_cleared,reason,actor_id) VALUES($1,$2,$3,$4,$5,$6) RETURNING *", guild_id,uid,'restored' if restore else 'removed',0 if restore else old,reason,actor_id)
                results.append(event)
    return results

async def notify_status_event(guild: discord.Guild, event):
    settings = await get_settings(guild.id)
    channel = await get_channel(guild, settings['log_channel_id'] if settings else None)
    if not channel:
        log.warning("Cannot post player status event %s: no log channel", event['id'])
        return
    removed = event['action'] == 'removed'
    embed = discord.Embed(title=("Player removed" if removed else "Player restored") + f" · #{event['id']}", color=0xe74c3c if removed else 0x3498db)
    embed.description = (f"**Member:** <@{event['user_id']}>\n"
                         f"**Status:** {'Removed from leaderboard' if removed else 'Active (0 points)'}\n"
                         f"**Points cleared:** {event['points_cleared']}\n"
                         f"**Reason:** {discord.utils.escape_markdown(event['reason'])[:500]}\n"
                         f"**By:** <@{event['actor_id']}>\n**When:** {utc_label(event['created_at'])}")
    try:
        await channel.send(embed=embed,allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden,discord.HTTPException):
        log.exception("Could not post player status event %s",event['id'])

async def perform_status_change(interaction: discord.Interaction, members: str, reason: str, *, restore: bool):
    if not await require_admin(interaction): return
    await interaction.response.defer(ephemeral=True)
    try:
        ids = await resolve_bulk_members(interaction.guild, members)
        events = await change_player_status(interaction.guild_id, ids, reason, interaction.user.id, restore=restore)
    except ValueError as exc:
        await interaction.followup.send(str(exc),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        return
    for event in events:
        await notify_status_event(interaction.guild,event)
    await update_leaderboard(interaction.guild)
    summary = "\n".join(f"• <@{e['user_id']}>: {e['points_cleared']} points cleared" if not restore else f"• <@{e['user_id']}>: restored with 0 points" for e in events)
    await interaction.followup.send(f"**{'Restored' if restore else 'Removed'} {len(events)} member(s).**\nReason: {discord.utils.escape_markdown(reason)}\n{summary}",ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

@points.command(name="remove-player", description="Admin: remove members from leaderboard and permanently clear all their points")
@app_commands.describe(members="@mentions or user IDs (up to 25)", reason="Required explanation for the removal")
async def remove_player(interaction: discord.Interaction, members: app_commands.Range[str,1,1800], reason: app_commands.Range[str,2,200]):
    await perform_status_change(interaction,members,reason,restore=False)

@points.command(name="restore-player", description="Admin: restore removed members with zero points")
@app_commands.describe(members="@mentions or user IDs (up to 25)", reason="Required explanation for restoring")
async def restore_player(interaction: discord.Interaction, members: app_commands.Range[str,1,1800], reason: app_commands.Range[str,2,200]):
    await perform_status_change(interaction,members,reason,restore=True)

@points.command(name="balance", description="Check your points or another member's balance")
async def balance(interaction: discord.Interaction, member: Optional[discord.Member]=None):
    target = member or interaction.user
    async with bot.pool.acquire() as db:
        current = await db.fetchval("SELECT balance FROM balances WHERE guild_id=$1 AND user_id=$2",interaction.guild_id,target.id)
    async with bot.pool.acquire() as db:
        active = await db.fetchval("SELECT is_active FROM balances WHERE guild_id=$1 AND user_id=$2", interaction.guild_id,target.id)
    status = " **(removed from leaderboard)**" if active is False else ""
    await interaction.response.send_message(f"{target.mention} has **{current or 0}** points{status}. (1 point = $1; 20 points = $20 cash or 1 skin up to $20)",ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

@points.command(name="leaderboard", description="Show the current leaderboard")
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.send_message(embed=await leaderboard_embed(interaction.guild_id),allowed_mentions=discord.AllowedMentions.none())

@points.command(name="history", description="See recent point transactions for a member")
async def history(interaction: discord.Interaction, member: Optional[discord.Member]=None):
    target = member or interaction.user
    async with bot.pool.acquire() as db:
        rows = await db.fetch("SELECT * FROM point_transactions WHERE guild_id=$1 AND user_id=$2 ORDER BY id DESC LIMIT 10", interaction.guild_id,target.id)
    if not rows:
        await interaction.response.send_message(f"No point transactions for {target.mention} yet.",ephemeral=True)
        return
    lines=[f"`#{r['id']}` **{r['delta']:+}** → **{r['balance_after']}** · {discord.utils.escape_markdown(r['reason'])[:70]} · {utc_label(r['created_at'])}" for r in rows]
    await interaction.response.send_message(f"**Last {len(rows)} transactions for {target.mention}:**\n"+"\n".join(lines),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

@app_commands.choices(reward=[app_commands.Choice(name=k, value=v) for k,v in REWARDS.items()])
@points.command(name="redeem", description="Request $20 cash or a skin (up to $20) per 20 points, admin approval")
@app_commands.describe(quantity="Number of $20 cash units or skins (20 points each)", details="Skin name or optional payout note")
async def redeem(interaction: discord.Interaction, reward: app_commands.Choice[str], quantity: app_commands.Range[int,1,25] = 1, details: app_commands.Range[str,0,200] = ""):
    cost = REWARD_COST * quantity
    await interaction.response.defer(ephemeral=True)
    settings=await get_settings(interaction.guild_id)
    channel=await get_channel(interaction.guild,settings['approvals_channel_id'] if settings else None)
    if not channel:
        await interaction.followup.send("Redemptions aren't configured yet. Ask an admin to run `/points setup`.",ephemeral=True)
        return
    async with bot.pool.acquire() as db:
        async with db.transaction():
            await db.execute("INSERT INTO balances(guild_id,user_id,balance) VALUES($1,$2,0) ON CONFLICT DO NOTHING", interaction.guild_id,interaction.user.id)
            current=await db.fetchval("SELECT balance FROM balances WHERE guild_id=$1 AND user_id=$2 FOR UPDATE",interaction.guild_id,interaction.user.id) or 0
            active=await db.fetchval("SELECT is_active FROM balances WHERE guild_id=$1 AND user_id=$2",interaction.guild_id,interaction.user.id)
            if active is False:
                await interaction.followup.send("Your points membership was removed. Ask an admin to restore you before redeeming.",ephemeral=True)
                return
            if current < cost:
                await interaction.followup.send(f"You need **{cost} points** to redeem; you have **{current}**.",ephemeral=True)
                return
            existing=await db.fetchval("SELECT id FROM redemptions WHERE guild_id=$1 AND user_id=$2 AND status='pending'", interaction.guild_id, interaction.user.id)
            if existing:
                await interaction.followup.send(f"You already have pending redemption #{existing}. Ask an admin to resolve it first.",ephemeral=True)
                return
            try:
                async with db.transaction():  # savepoint handles two near-simultaneous requests safely
                    row=await db.fetchrow("INSERT INTO redemptions(guild_id,user_id,reward,cost,details) VALUES($1,$2,$3,$4,$5) RETURNING *",interaction.guild_id,interaction.user.id,reward.value,cost,details)
            except asyncpg.UniqueViolationError:
                await interaction.followup.send("You already have a pending redemption. Ask an admin to resolve it first.",ephemeral=True)
                return
    try:
        await channel.send(f"🎁 **New redemption request #{row['id']}**\nMember: <@{interaction.user.id}>\nReward: **{quantity} × {reward.value}**\nDetails: {discord.utils.escape_markdown(details) if details else "None"}\nCost: **{cost} points**\nAdmin: `/points approve request_id:{row['id']}` or `/points deny request_id:{row['id']}`\n*Points are deducted only after approval.*",allowed_mentions=discord.AllowedMentions.none())
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Failed to publish redemption request %s, admins can find it via /points requests", row['id'])
        await interaction.followup.send(f"Request **#{row['id']}** saved, but I couldn't post to the approvals channel. Tell an admin to check `/points requests`.",ephemeral=True)
        return
    await interaction.followup.send(f"Request **#{row['id']}** submitted for **{quantity} × {reward.value}** ({cost} points). Your points will only be deducted if an admin approves it.",ephemeral=True)

@points.command(name="requests", description="Admin: see the latest pending redemptions")
async def requests(interaction: discord.Interaction):
    if not await require_admin(interaction): return
    async with bot.pool.acquire() as db:
        rows=await db.fetch("SELECT id,user_id,reward,cost,created_at FROM redemptions WHERE guild_id=$1 AND status='pending' ORDER BY id LIMIT 15",interaction.guild_id)
    lines=[f"`#{r['id']}` <@{r['user_id']}> · **{r['reward']}** · {r['cost']} points · {utc_label(r['created_at'])}" for r in rows]
    await interaction.response.send_message("**Pending requests**\n"+("\n".join(lines) if lines else "None."),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

@points.command(name="approve", description="Admin: approve a redemption, deduct points and log it")
async def approve(interaction: discord.Interaction, request_id: int):
    if not await require_admin(interaction): return
    await interaction.response.defer(ephemeral=True)
    async with bot.pool.acquire() as db:
        async with db.transaction():
            req=await db.fetchrow("SELECT * FROM redemptions WHERE id=$1 AND guild_id=$2 FOR UPDATE",request_id,interaction.guild_id)
            if not req or req['status']!='pending':
                await interaction.followup.send("Request not found or already resolved.",ephemeral=True)
                return
            # Lock balance row inside same transaction; approval and debit must be all-or-nothing.
            await db.execute("INSERT INTO balances(guild_id,user_id,balance) VALUES($1,$2,0) ON CONFLICT DO NOTHING",interaction.guild_id,req['user_id'])
            current=await db.fetchval("SELECT balance FROM balances WHERE guild_id=$1 AND user_id=$2 FOR UPDATE",interaction.guild_id,req['user_id'])
            active=await db.fetchval("SELECT is_active FROM balances WHERE guild_id=$1 AND user_id=$2",interaction.guild_id,req['user_id'])
            if active is False:
                await interaction.followup.send("Cannot approve: this player was removed.",ephemeral=True)
                return
            if current < req['cost']:
                await interaction.followup.send(f"Cannot approve: member has {current} points but redemption costs {req['cost']}.",ephemeral=True)
                return
            after=current-req['cost']
            await db.execute("UPDATE balances SET balance=$3 WHERE guild_id=$1 AND user_id=$2",interaction.guild_id,req['user_id'],after)
            tx=await db.fetchrow("INSERT INTO point_transactions(guild_id,user_id,delta,balance_after,action,reason,actor_id,redemption_id) VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *",interaction.guild_id,req['user_id'],-req['cost'],after,"redemption",f"Approved redemption #{req['id']}: {req['reward']} ({req['cost']} points)",interaction.user.id,req['id'])
            await db.execute("UPDATE redemptions SET status='approved',reviewer_id=$2,resolved_at=now() WHERE id=$1",req['id'],interaction.user.id)
    await post_change(interaction.guild,tx)
    await interaction.followup.send(f"Approved request **#{request_id}**. Deducted **{req['cost']}** from <@{req['user_id']}>. Balance: **{after}**. **You must fulfill the cash or skin reward separately.**",ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
    try:
        user=await bot.fetch_user(req['user_id'])
        await user.send(f"Your MRC points redemption #{request_id} for **{req['reward']}** ({req['cost']} points) was approved. The server admins will arrange fulfillment. New points balance: **{after}**.")
    except (discord.HTTPException,discord.Forbidden):
        pass

@points.command(name="deny", description="Admin: deny a pending redemption without deducting points")
async def deny(interaction: discord.Interaction, request_id: int, reason: app_commands.Range[str,2,200]):
    if not await require_admin(interaction): return
    async with bot.pool.acquire() as db:
        async with db.transaction():
            req=await db.fetchrow("SELECT * FROM redemptions WHERE id=$1 AND guild_id=$2 FOR UPDATE",request_id,interaction.guild_id)
            if not req or req['status']!='pending':
                await interaction.response.send_message("Request not found or already resolved.",ephemeral=True)
                return
            await db.execute("UPDATE redemptions SET status='denied',reviewer_id=$2,resolved_at=now() WHERE id=$1",req['id'],interaction.user.id)
    await interaction.response.send_message(f"Denied request **#{request_id}**. No points deducted.",ephemeral=True)
    try:
        user=await bot.fetch_user(req['user_id'])
        await user.send(f"Your MRC points redemption #{request_id} was denied. Reason: {reason}. No points were deducted.")
    except (discord.HTTPException,discord.Forbidden):
        pass

@points.command(name="refresh", description="Admin: repost or refresh the live leaderboard")
async def refresh(interaction: discord.Interaction):
    if not await require_admin(interaction): return
    await interaction.response.defer(ephemeral=True)
    await update_leaderboard(interaction.guild)
    await interaction.followup.send("Leaderboard refresh attempted. Check the configured channel and bot permissions.",ephemeral=True)

@bot.event
async def on_ready():
    log.info("Logged in as %s (id=%s)",bot.user,bot.user.id)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.error("Slash command failed: %r",error,exc_info=(type(error),error,error.__traceback__))
    msg="Something went wrong. No duplicate action should be retried until an admin checks the points log and history."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg,ephemeral=True)
        else:
            await interaction.response.send_message(msg,ephemeral=True)
    except discord.HTTPException:
        pass

bot.tree.add_command(points)
if __name__ == "__main__":
    bot.run(TOKEN,log_handler=None)

# MRC Discord Rewards Bot — New Payout Structure (November 2026)

This release updates the existing Railway/Postgres bot. **Do not delete or recreate your Postgres database**; the new tables and columns are created during startup. Existing balances, history, and pending redemptions are retained, including their original costs. The pinned leaderboard now displays the updated rates.

## Payout policy

| Event | Participation | Most MVPs/SVPs | Second-most MVPs/SVPs |
|---|---:|---:|---:|
| Scrim (per BO3 or BO5) | +1 including benched | +9 | none |
| MRC (weekly) | +1 including benched, once per calendar week (Monday start) | n/a | n/a |
| MRC stage/day | none (weekly attendance is separate) | +29 | +14 |
| Full-day tournament outside MRC | +1, full-day only | +19 | +9 |

All participation and performance awards stack. Rewards: **1 point = $1; minimum redemption is 20 points**, for **$20 cash** or **one in-game skin valued up to $20**. Use `quantity:2` to request two units for 40 points. Cash and skin fulfillment is manual, with admin approval. Cash payout through Zelle, Venmo, or PayPal; no automatic payments.

**Ties:** Players who tie for a position evenly split the sum of the performance prizes occupying their rank slots, rounded half-up **for each player**. Two players tied for first in MRC split 29+14, so each earns 22 points (44 total after rounding). Four tied for first in a scrim split 9, so each gets 2 (8 total after rounding). As specified, independent rounding can make the total awarded slightly higher or lower than the original prize pool. When there is a first-place tie, do not supply a second-place group. When one first-place winner exists, tied second-place winners split the second-place pool.

## New event-specific commands (Admin / Manage Server)

- `/points scrim event_id:2026-11-24-bo3-a members:<all present or benched> first:<top MVP/SVP members> reason:Opponent Name` — +1 to every listed member, plus the split 9-point first-place prize. Use a **different event_id for every BO3/BO5**.
- `/points mrc-week week:2026-11-24 members:<roster including benched> reason:MRC week attendance` — +1 once per player per Monday-based week. Additional submissions for the same week award only members not already awarded.
- `/points mrc-stage event_id:qualifier-day-1 members:<eligible players> first:<first place> second:<second place> reason:Closed Qualifiers Day 1` — awards 29 and 14; accepts grouped rounds such as `rounds-1-3`. If there is a first-place tie, omit `second`. **MRC participation is not awarded here.**
- `/points tournament event_id:cup-day-1 members:<full-day participants> first:<first> second:<second> reason:Tournament Name full_day:True` — +1 attendance plus 19/9 performance; confirm full-day attendance explicitly. If first place tied, omit `second`.

For `members`, `first`, and `second`, paste Discord mentions or IDs separated by spaces or commas (up to 25 unique members in a batch). Every winner must be listed among `members`. `reason` is required for every event and is saved in **each individual's transaction log**. Event commands make atomic batch updates, write separate participation/performance audit rows, and refresh the pinned leaderboard. Duplicate event IDs for the same event type are rejected to prevent double payments. Manual `/points add` and `/points remove` remain for corrections.

## Existing commands

`/points add`, `/points remove`, `/points remove-player`, `/points restore-player`, `/points balance`, `/points history`, `/points leaderboard`, `/points setup`, `/points refresh`, `/points redeem`, `/points requests`, `/points approve`, `/points deny` continue to work. Point-changing operations require **Manage Server** permission, consistent with the previous bot version. Removed players are hidden and cannot earn or redeem points until an admin restores them. Removing a faction member is an **admin command**, not automatic Discord role tracking.

**Redeem:** `/points redeem reward:<Cash or Skin> quantity:1 details:<optional skin title>` creates a 20-point request. `quantity:5` creates a 100-point request. Points are deducted **only on approval**. The bot does not determine a skin's price or send funds; admins must verify the up-to-$20-per-skin limit and deliver the rewards.

## Upgrade on Railway

1. Back up Postgres first.
2. Upload the **files inside this ZIP's `mrc-discord-points-bot` folder** to the root of your GitHub repository, replacing the previous version of `bot.py`, `points_core.py`, `README.md`, and tests. Do **not** commit `.env`.
3. Railway should redeploy automatically. Use the same `DISCORD_TOKEN`, `DISCORD_GUILD_ID`, and Postgres `DATABASE_URL` reference. Do not recreate the database.
4. Verify the new `/points scrim`, `/points mrc-week`, `/points mrc-stage`, and `/points tournament` commands appear. Test a unique event ID with a small roster, then inspect `/points history` and your leaderboard.
5. Run `/points refresh` if the pinned message needs to be edited immediately. Check the most recent Railway Deploy Logs if command registration fails.

**Limitations:** Event results and attendance are entered by admins, **not read from Marvel Rivals automatically**. Weekly attendance is keyed to an ISO date's Monday, and event IDs must be unique within their event type. Existing historical point awards are **not recalculated**. Existing pending redemptions retain their original point cost, and should be reviewed or denied by an admin if they were requested under the old policy.

---

# MRC Points Bot — Deploy to Railway

A Discord bot for **point balances, scrim/MRC/tournament awards, transaction history, a pinned live leaderboard, and administrator-approved skin/$20 redemptions**.

## How it works

- **Admins** with **Manage Server** permission award and remove points.
- Members request rewards with `/points redeem`; an admin approves or denies the request.
- Approval atomically subtracts **20 points**, logs the transaction, and updates the live leaderboard.
- **Cash and skins are delivered manually by admins**, not by the bot.
- Balances, redemptions and an **append-only point transaction ledger** persist in PostgreSQL.
- Transaction entries show the member, amount, reason, current balance, actor, and timestamp.
- Records are scoped to each Discord server. The bot doesn't use message contents and does not need Message Content intent.

## Configure your Discord app

1. Visit https://discord.com/developers/applications and select **New Application**.
2. Open **Bot**, reset/generate a token. **Never put the token in GitHub or Discord messages.**
3. In **Installation**, enable **Guild Install** and configure the `bot` and `applications.commands` scopes.
4. Bot permissions: **View Channels, Send Messages, Embed Links, Read Message History, Manage Messages** (for pinning). Do not grant Administrator.
5. Use the generated installation URL to add the application to your Discord server. You need permission to install applications.
6. Get your **server ID** (Discord User Settings → Advanced → Developer Mode, then right-click server → Copy Server ID).

## Deploy 24/7 on Railway

These steps use **a background Python process plus a Railway PostgreSQL database**, not an ephemeral SQLite file.

1. Put these files in a **private GitHub repository**, or deploy via the Railway CLI. Do not commit `.env` or credentials.
2. Go to https://railway.com/new, create a project, and deploy the GitHub repository as a new service.
3. In the same project, choose **+ New → Database → PostgreSQL**.
4. Open the Python bot service → **Variables**, configure:

   | Variable | Value |
   | --- | --- |
   | `DISCORD_TOKEN` | Your secret token from Discord Developer Portal |
   | `DATABASE_URL` | Reference the Railway Postgres connection string, e.g. `${{Postgres.DATABASE_URL}}`, selecting the actual database service name |
   | `DISCORD_GUILD_ID` | Your numeric Discord server ID (recommended for immediate slash command sync) |

5. Confirm the **Start Command** is `python bot.py` (it is also in `railway.json`). **Do not configure a web domain/healthcheck.** Discord bots are gateway workers, not websites.
6. Deploy the staged variables and code. Watch the **Deploy Logs** for `Logged in as` and `Registered ... slash commands`.
7. In Discord, create `#points-log`, `#points-leaderboard`, and `#points-redemptions` channels. Set bot permissions in all three so it can view and send messages and embed links.
8. In your server run:

   `/points setup log_channel:#points-log leaderboard_channel:#points-leaderboard approvals_channel:#points-redemptions`

9. The bot posts a leaderboard and tries to pin it. If pinning fails, give **Manage Messages** in that channel or pin it yourself.
10. Test `/points add`, `/points history`, and `/points redeem` with a test member, then `/points approve` and `/points balance`.

**Important Railway settings:** Keep **one running instance** of this bot service (avoid multiple replicas), keep the PostgreSQL service enabled, and enable PostgreSQL **backups** in Railway. Railway hosting and persistent database resources can cost money, depending on your plan and usage. Your Railway account and billing belong to you. See https://docs.railway.com/databases/postgresql and https://docs.railway.com/volumes/backups.

## Commands

| Command | Who | Example |
| --- | --- | --- |
| `/points setup` | Manage Server admins | Select 3 channels using Discord channel pickers |
| `/points add` | Admins | `/points add members:@Alex @Sam total_points:5 reason:Scrim MVP/SVP leader` |
| `/points remove` | Admins | `/points remove members:@Alex @Sam total_points:1 reason:Incorrect attendance entry` (deduct some points) |
| `/points remove-player` | Admins | `/points remove-player members:@Alex @Sam reason:Left the team` (zero points and hide players) |
| `/points restore-player` | Admins | `/points restore-player members:@Alex reason:Rejoined team` (re-activate with 0 points) |
| `/points balance` | Everyone | `/points balance member:@Alex` |
| `/points history` | Everyone | `/points history member:@Alex` (10 most recent) |
| `/points leaderboard` | Everyone | See top 20 (the pinned message also auto-updates) |
| `/points redeem` | Everyone | Choose **Cash ($20)** or **Skin** |
| `/points requests` | Admins | Lists pending requests |
| `/points approve` | Admins | `/points approve request_id:3` |
| `/points deny` | Admins | `/points deny request_id:3 reason:Duplicate request` |
| `/points refresh` | Admins | Force a leaderboard refresh |

The `/points setup` command requires **Manage Server**, not just access to `#points-log`. Only admins should have this permission. The bot also independently checks admin permissions at runtime.

## Bulk member commands (new)

Both `/points add` and `/points remove` have exactly these input fields:

- **members**: One or more **Discord @mentions** (or numeric IDs), pasted together and separated by spaces or commas. Up to **25 unique members** per command. A Discord slash command cannot provide a native multi-member picker, so paste multiple mentions into this text input. Example: `<@123456789012345678> <@234567890123456789>` (replace with real member mentions/IDs).
- **total_points**: The number of points to add/deduct **for each selected member**, not a pot to split.
- **reason**: Shared reason recorded in each member's transaction history.

**Examples:**

- `/points add members:@Alex @Sam @Taylor total_points:1 reason:Scrim participation 11/23`
- `/points add members:@Alex @Sam total_points:5 reason:MRC stage 2 MVP/SVP leaders`
- `/points add members:@Alex @Sam @Taylor total_points:5 reason:Full-day tournament participation`
- `/points remove members:@Alex @Sam total_points:1 reason:Attendance correction`

The bot verifies all IDs belong to the server, ignores duplicate mentions, and applies the whole batch **atomically**. For deductions, if even one member lacks enough points, **none** are deducted. Every member gets an individual audit-log entry; leaderboard refreshes once at the end. Admin permission is required. For different point amounts, use separate batches.

## Remove a player completely (new)

Use `/points remove-player` with **members** and mandatory **reason**. You can include up to 25 server members. This is different from `/points remove`, which simply subtracts the entered number of points.

- All existing points are set to **0**, and the player is **removed from the leaderboard** (even if they earn points in a future season, they cannot be re-added unless restored).
- Any pending redemption requests are **denied automatically**. Already approved/fulfilled rewards are not reversed.
- The removal reason, admin, timestamp, and number of points cleared are recorded in an independent permanent `player_status_events` audit table. A `player_removed` negative points ledger entry is also recorded for nonzero balances.
- Removal shows up in `#points-log`, and the pinned leaderboard refreshes automatically.
- Bulk removals are **all-or-nothing**: if any person was already removed, no one in that batch is changed.
- To let someone participate again, use `/points restore-player members:@Alex reason:Rejoined team`. They come back with **0 points**, not their old balance. Past transaction and removal records remain preserved.
- The commands accept **existing server members** only, and require **Manage Server** permission.

**Example:** `/points remove-player members:@Alex @Sam reason:No longer on roster`.

## Your point rules

| Activity | Award |
| --- | ---: |
| Scrim participation | +1 |
| Scrim most MVPs/SVPs | +5 |
| MRC most MVPs/SVPs per stage/day | +5 |
| MRC second most MVPs/SVPs per stage/day | +5 |
| Full-day non-MRC tournament participation | +5 |
| Tournament most MVPs/SVPs | +10 |
| Tournament second most MVPs/SVPs | +5 |
| Skin or $25 redemption | -5 after approval |

Awards are **manual**, not automatic based on match results: this avoids needing access to match data. For a scrim MVP who also attended, run separate `/points add` entries (+1 attendance and +5 MVP), or one +6 award with a descriptive reason. Full-day tournament participation and performance awards also stack. Tie policy is an admin decision and is not enforced automatically.

## Local test (optional)

1. Install Python 3.11+ and PostgreSQL. Create a database.
2. Copy `.env.example` to `.env` and set `DISCORD_TOKEN`, `DATABASE_URL`, and optionally `DISCORD_GUILD_ID`.
3. `python -m pip install -r requirements.txt`
4. `python -m unittest discover -s tests -v`
5. `python bot.py`

## Operational notes

- The database is the source of truth. If Discord logging fails due to missing permissions, a transaction may still succeed in the database; `/points history` remains authoritative. Correct permissions promptly.
- Leaderboard displays **top 20 active non-zero balances**; all other balances are still retained and available via `/points balance`.
- Deleting a Discord log message does not delete the transaction record.
- Approving a reward **does not send money, deliver a skin, or validate proof of fulfillment**. Arrange delivery manually.
- There is currently no separate fulfillment-status field; approval means authorized and points deducted, not proof delivered.
- Redemptions are limited to **one pending request per user per server**. Two approvals cannot debit the same request twice.
- Usernames may change; records use immutable Discord user IDs.
- Reward cash value is **$5 per point**, equivalent to 5 points = $25.
- To restore after database loss, use PostgreSQL backups. Download and maintain offline backups if rewards have monetary value.
- If you change `DISCORD_GUILD_ID`, restart deployment and verify command registration in the new server.

## Security

Never share your Discord token in chat. Configure secrets in Railway Variables only. Use a **private** repo, restrict GitHub and Railway access, rotate exposed tokens, and enable 2FA on accounts managing cash rewards. The bot needs **no** privileged intents.

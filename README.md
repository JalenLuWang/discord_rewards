# Marvel Rivals Rewards Bot — $10 Cash Minimum, 20-Point Skins

This is the latest complete bot project, ready for your existing **Railway + PostgreSQL** deployment. It incorporates the previous bulk award, scrim/MRC/tournament, tie-handling, removal, redemption approval, transaction log, and auto-updating leaderboard features.

## Updated reward policy

> **1 point = $1.** Cash withdrawals become available once you've earned at least **10 points ($10)**. You may then request **any whole-dollar amount of $10 or more**, up to your available points balance. Cash withdrawals do not have to be in multiples of $10. For example, **11 points = $11**, **17 points = $17**, and **25 points = $25**.
> **Skins cost 20 points each**. Each skin retains the existing **up to $20** in-game purchase value limit. Every reward request needs admin approval. Cash is paid by the admins through Zelle, Venmo, or PayPal; skins are sent in-game. The bot does not automatically deliver rewards.

The *minimum* of 10 points applies to each cash redemption request; requesting less than $10 is not supported, even if a member has previously earned 10 or more points. This avoids approving sub-$10 cash payouts.

**Examples**

| Current points | Cash request | Skin request |
| ---: | --- | --- |
| 9 | Not yet eligible | Not yet eligible |
| 10 | $10 | Not eligible |
| 13 | $10–$13 | Not eligible |
| 17 | $10–$17 | Not eligible |
| 25 | $10–$25 | Up to 1 skin |
| 50 | $10–$50 | Up to 2 skins |

## Updated `/points redeem` command

- `/points redeem reward:Cash points:17` requests **$17 cash** at a cost of 17 points.
- `/points redeem reward:Cash` defaults to **$10 cash** at a cost of 10 points.
- `/points redeem reward:Skin quantity:1 details:Desired skin name` requests **one skin for 20 points**.
- `/points redeem reward:Skin quantity:2 details:Two skin names` requests **two skins for 40 points**.

Cash requires `points` to be a whole number of at least 10. For cash, leave `quantity` as 1. For skins, use `quantity` (1–25), leave `points` empty, and specify the requested skin(s) in `details`. Members must have enough points at request time and approval time. The bot allows one pending redemption per member; points are deducted only after an admin uses `/points approve`.

**Existing pending redemptions:** Their stored point costs are **not retroactively changed**. Review old requests before approving, and deny/recreate any request that should follow the new rates. Existing balances and transaction history are unaffected.

## Point earning rules (unchanged)

| Event | Participation | First MVP/SVP | Second MVP/SVP |
| --- | ---: | ---: | ---: |
| Scrim (per BO3/BO5) | +1 | +9 | — |
| MRC (weekly participation) | +1 once/week | — | — |
| MRC stage/day | — | +29 | +14 |
| Full-day tournament outside MRC | +1 | +19 | +9 |

Participation and performance awards stack. When players tie, their occupied performance prize slots are pooled, divided evenly and rounded to the nearest whole point (half points round up). For example, two players tied for MRC first split 29 + 14 = 43, earning 22 each. Four tied for scrim first split 9, earning 2 each. Rounding can make the total allocated greater or less than the nominal pot.

- `/points scrim`: Record roster participation, including benched members, and award first-place MVP/SVP performance points.
- `/points mrc-week`: Award +1 once per member in a Monday-based week.
- `/points mrc-stage`: Award first and second MVP/SVP points for a stage/day.
- `/points tournament`: Award full-day participation and performance bonuses.
- `/points add`, `/points remove`: Bulk manual point adjustments (up to 25 server members) with mandatory `reason`.
- `/points remove-player`: Clear all points and remove up to 25 members from the leaderboard, with reason.
- `/points restore-player`: Restore eligible members at 0 points, with reason.
- `/points leaderboard`, `/points balance`, `/points history`: View point records.
- `/points setup`, `/points refresh`: Manage auto-updating pinned leaderboard and logging channels.
- `/points redeem`, `/points requests`, `/points approve`, `/points deny`: Member redemptions and admin review.

Administrative commands use the **Manage Server** Discord permission in this code; this does not require the full Administrator permission. The bot does not automatically read Marvel Rivals matches: admins submit event results.

## Deploy this update to GitHub + Railway

1. **Back up your PostgreSQL database.** Do not delete your database, create a new one, or run any SQL migrations to change point prices.
2. Extract the ZIP and open the folder `mrc-discord-points-bot`.
3. In your GitHub repository, replace existing root-level `bot.py`, `points_core.py`, `README.md`, and the `tests/test_core.py` file with these updated versions. You can upload the entire folder's contents; **do not upload a nested folder**, your `.env`, or secrets.
4. Commit to the branch used by your existing Railway bot service. Railway should detect the commit and redeploy automatically.
5. In the Railway bot service logs, verify that the app starts successfully and synchronizes Discord slash commands.
6. In your Discord server, run `/points refresh` to immediately update the pinned leaderboard's rewards wording. Subsequent point adjustments should automatically refresh it.
7. Test `/points redeem` with a member balance of 10 or more points (e.g. 13), requesting Cash 13 or Skin quantity 1 only once the account has at least 20 points. Use `/points requests` to inspect the pending request; then approve or deny the test as appropriate. Avoid sending real payouts during testing.

Existing Railway variables **remain unchanged**:

| Variable | Source |
| --- | --- |
| `DISCORD_TOKEN` | Discord Developer Portal (secret; never commit) |
| `DATABASE_URL` | Reference to PostgreSQL in the same Railway project and environment (`${{Postgres.DATABASE_URL}}`, service name may differ) |
| `DISCORD_GUILD_ID` | Numeric Discord server ID |

The Railway start command remains `python bot.py`. This is a background Discord bot process, not a web service; it doesn't need a publicly exposed domain. Run one replica to avoid redundant command sync and race conditions.

## Setup for a new Discord server (optional)

1. Create an app in https://discord.com/developers/applications, enable Guild Install, and configure `bot` and `applications.commands` OAuth scopes. Install it in your server with View Channels, Send Messages, Embed Links, Read Message History, and Manage Messages (for pinning) permissions in its channels.
2. Create log, leaderboard and redemption approval channels. For example: `#points-log`, `#faction-rewards-tracker`, `#points-redemptions`.
3. Run `/points setup` and select all three channels. The bot publishes the pinned leaderboard embed, then updates it after point changes.
4. `#points-log` entries record each member, reason, actor, change, resulting balance and timestamp. PostgreSQL remains the source of truth even if a Discord message fails.

## Test locally

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m py_compile bot.py points_core.py
```

The pure-function tests do not require Discord or PostgreSQL. Live Railway deployment and Discord command registration must still be checked after uploading the project.

## Security and data retention

- Keep bot tokens and database credentials out of GitHub. Never paste them in chat.
- Existing balances, transaction history, member-removal records and approved redemption records are retained in PostgreSQL; new pricing only applies to **new** requests.
- Cash rewards still require a human admin to approve **and separately pay**; skins require manual in-game delivery. No payment provider integration is included.
- Removed members cannot request rewards until restored.
- Protect the `Manage Server` permission because it currently authorizes awarding, deducting, approving, and player removal actions.
- Keep a database backup for accounting and dispute resolution.

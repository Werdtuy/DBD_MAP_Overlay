# DBD Streak Sync Worker

This Cloudflare Worker powers shared Escape Streak lobbies on your own domain. The public app only needs a player tag such as `Nikko#3213`; the packaged app carries the Worker URL privately.

## Cloudflare Setup

1. Create a D1 database:

```powershell
npx wrangler d1 create dbd-streak-sync
```

2. Copy the returned `database_id` into `wrangler.jsonc` under the `STREAK_DB` binding.

3. Apply the schema:

```powershell
npx wrangler d1 migrations apply dbd-streak-sync
```

4. Add your private admin token secret:

```powershell
npx wrangler secret put STREAK_ADMIN_TOKEN
```

5. Deploy the Worker:

```powershell
npx wrangler deploy
```

6. In Cloudflare, route your domain or subdomain to this Worker, for example:

```text
https://streaks.your-domain.com
```

## App Packaging

Set this GitHub Actions secret before publishing beta builds:

```text
STREAK_SYNC_URL=https://your-streak-worker-domain
```

The build encrypts that URL into `streak_config.json` and ships it with the app. Users do not need to enter the Worker URL.

## User Flow

- Enter a player tag such as `Nikko#3213`.
- Create a lobby to generate a short code.
- Share the code with friends.
- Friends enter their own tag and join the lobby.
- The Worker stores the lobby and streak state in D1.

## Private Manager

Run the local manager from the project root:

```powershell
python scripts\streak_manager.py
```

Use the deployed Worker URL and the same `STREAK_ADMIN_TOKEN`. The manager can inspect, reset, update, and delete streak profiles/lobbies.

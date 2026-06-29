# DBD Streak Sync Worker

This optional Cloudflare Worker powers online Escape Streak lobbies.

## Setup

1. Create a Cloudflare KV namespace.
2. Put the KV namespace id into `wrangler.jsonc` under `STREAK_LOBBIES`.
3. Add a private admin token secret:

```powershell
npx wrangler secret put STREAK_ADMIN_TOKEN
```

4. Deploy with Wrangler:

```powershell
npx wrangler deploy
```

5. Copy the deployed Worker URL into the app's Streak tab as the sync server URL.

The Worker does not contain secrets. Lobby codes expire from KV after 7 days.

## Private Manager

Run the local manager from the project root:

```powershell
python scripts\streak_manager.py
```

Enter the deployed Worker URL and the same `STREAK_ADMIN_TOKEN` value. The manager can:

- list all active lobbies
- inspect streak/player/member state
- set a lobby streak
- reset a lobby
- delete a lobby

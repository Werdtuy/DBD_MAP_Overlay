# DBD Streak Sync Worker

This optional Cloudflare Worker powers online Escape Streak lobbies.

## Setup

1. Create a Cloudflare KV namespace.
2. Put the KV namespace id into `wrangler.jsonc` under `STREAK_LOBBIES`.
3. Deploy with Wrangler:

```powershell
npx wrangler deploy
```

4. Copy the deployed Worker URL into the app's Streak tab as the sync server URL.

The Worker does not contain secrets. Lobby codes expire from KV after 7 days.

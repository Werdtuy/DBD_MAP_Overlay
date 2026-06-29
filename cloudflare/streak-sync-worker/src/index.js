const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET,POST,PUT,OPTIONS",
  "access-control-allow-headers": "content-type",
};

const LOBBY_TTL_MS = 1000 * 60 * 60 * 24 * 7;
const CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const STATUS_VALUES = new Set(["Ready", "Escaped", "Dead", "Disconnected"]);

export default {
  async fetch(request, env, ctx) {
    try {
      if (request.method === "OPTIONS") {
        return new Response(null, { headers: JSON_HEADERS });
      }
      const url = new URL(request.url);
      if (request.method === "GET" && url.pathname === "/health") {
        ctx.waitUntil(ensureSchema(env));
        return json({ ok: true });
      }
      await ensureSchema(env);
      if (request.method === "POST" && url.pathname === "/api/players/check") {
        return await checkPlayerTag(request, env);
      }
      if (request.method === "POST" && url.pathname === "/api/players/register") {
        return await registerPlayerTag(request, env);
      }
      if (request.method === "POST" && url.pathname === "/api/lobbies") {
        return await createLobby(request, env);
      }
      const playerMatch = url.pathname.match(/^\/api\/players\/([^/]+)$/);
      if (playerMatch) {
        const tag = cleanTag(decodeURIComponent(playerMatch[1]));
        if (request.method === "GET") {
          return await getPlayerTag(env, tag);
        }
        if (request.method === "PUT") {
          return await updatePlayerTag(request, env, tag);
        }
        return json({ error: "Method not allowed." }, 405);
      }
      const lobbyMatch = url.pathname.match(/^\/api\/lobbies\/([A-Z0-9-]+)(\/join|\/leave)?$/);
      if (lobbyMatch) {
        const code = cleanCode(lobbyMatch[1]);
        if (request.method === "GET" && !lobbyMatch[2]) {
          return await getLobby(env, code);
        }
        if (request.method === "POST" && lobbyMatch[2] === "/join") {
          return await joinLobby(request, env, code);
        }
        if (request.method === "POST" && lobbyMatch[2] === "/leave") {
          return await leaveLobby(request, env, code);
        }
        if (request.method === "PUT" && !lobbyMatch[2]) {
          return await updateLobby(request, env, code);
        }
        return json({ error: "Method not allowed." }, 405);
      }
      return json({ error: "Not found." }, 404);
    } catch (error) {
      console.error(JSON.stringify({ message: "streak_sync_error", error: String(error) }));
      return json({ error: "Streak sync failed." }, 500);
    }
  },
};

async function ensureSchema(env) {
  if (!env.STREAK_DB) {
    throw new Error("D1 binding STREAK_DB is not configured.");
  }
  await env.STREAK_DB.prepare(
    `CREATE TABLE IF NOT EXISTS streak_profiles (
      tag TEXT PRIMARY KEY,
      tag_normalized TEXT NOT NULL UNIQUE,
      player_id TEXT NOT NULL,
      state_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )`,
  ).run();
  await env.STREAK_DB.prepare(
    "CREATE INDEX IF NOT EXISTS idx_streak_profiles_updated_at ON streak_profiles(updated_at)",
  ).run();
  await env.STREAK_DB.prepare(
    `CREATE TABLE IF NOT EXISTS streak_lobbies (
      code TEXT PRIMARY KEY,
      state_json TEXT NOT NULL,
      members_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      expires_at TEXT NOT NULL
    )`,
  ).run();
  await env.STREAK_DB.prepare(
    "CREATE INDEX IF NOT EXISTS idx_streak_lobbies_updated_at ON streak_lobbies(updated_at)",
  ).run();
  await env.STREAK_DB.prepare(
    "CREATE INDEX IF NOT EXISTS idx_streak_lobbies_expires_at ON streak_lobbies(expires_at)",
  ).run();
}

async function checkPlayerTag(request, env) {
  const body = await readJson(request);
  const tag = cleanTag(body.tag);
  const profile = await loadPlayer(env, tag);
  return json({ tag, available: !profile });
}

async function registerPlayerTag(request, env) {
  const body = await readJson(request);
  const tag = cleanTag(body.tag);
  const playerId = cleanPlayerId(body.player_id);
  const existing = await loadPlayer(env, tag);
  if (existing && existing.player_id !== playerId) {
    return json({ error: "That player tag is already taken.", available: false, tag }, 409);
  }
  const state = sanitizeState(body.state);
  state.sync_enabled = true;
  state.sync_player_tag = tag;
  state.lobby_code = tag;
  state.sync_revision = Number(existing?.state?.sync_revision || 0) + 1;
  const now = new Date().toISOString();
  const profile = {
    tag,
    tag_normalized: normalizeTag(tag),
    player_id: playerId,
    state,
    created_at: existing?.created_at || now,
    updated_at: now,
  };
  await savePlayer(env, profile);
  return json(publicPlayer(profile));
}

async function getPlayerTag(env, tag) {
  const profile = await loadPlayer(env, tag);
  if (!profile) {
    return json({ error: "Player tag was not found." }, 404);
  }
  return json(publicPlayer(profile));
}

async function updatePlayerTag(request, env, tag) {
  const body = await readJson(request);
  const playerId = cleanPlayerId(body.player_id);
  const profile = await loadPlayer(env, tag);
  if (!profile) {
    return json({ error: "Player tag was not found." }, 404);
  }
  if (profile.player_id !== playerId) {
    return json({ error: "This player tag belongs to another device." }, 403);
  }
  const state = sanitizeState(body.state);
  state.sync_enabled = true;
  state.sync_player_tag = profile.tag;
  state.lobby_code = profile.tag;
  state.sync_revision = Number(profile.state?.sync_revision || 0) + 1;
  profile.state = state;
  profile.updated_at = new Date().toISOString();
  await savePlayer(env, profile);
  return json(publicPlayer(profile));
}

async function createLobby(request, env) {
  const body = await readJson(request);
  const playerId = cleanPlayerId(body.player_id);
  const tag = cleanTag(body.player_tag || body.player_name);
  let code = "";
  for (let attempt = 0; attempt < 12; attempt += 1) {
    code = randomCode(4);
    const existing = await loadLobby(env, code);
    if (!existing) {
      break;
    }
    code = "";
  }
  if (!code) {
    return json({ error: "Could not create a unique lobby code. Try again." }, 503);
  }
  const state = sanitizeState(body.state);
  state.sync_enabled = true;
  state.sync_lobby_code = code;
  state.sync_player_tag = tag;
  state.lobby_code = code;
  state.sync_revision = 1;
  const now = new Date().toISOString();
  const lobby = {
    code,
    state,
    members: [
      {
        player_id: playerId,
        tag,
        host: true,
        last_seen: now,
      },
    ],
    created_at: now,
    updated_at: now,
    expires_at: lobbyExpiry(),
  };
  await saveLobby(env, lobby);
  await registerOrRefreshProfile(env, tag, playerId, state);
  return json(publicLobby(lobby));
}

async function joinLobby(request, env, code) {
  const body = await readJson(request);
  const playerId = cleanPlayerId(body.player_id);
  const tag = cleanTag(body.player_tag || body.player_name);
  const lobby = await loadLobby(env, code);
  if (!lobby) {
    return json({ error: "Lobby code was not found." }, 404);
  }
  const existingIndex = lobby.members.findIndex((member) => member.player_id === playerId || normalizeTag(member.tag) === normalizeTag(tag));
  if (existingIndex < 0 && lobby.members.length >= 4) {
    return json({ error: "This lobby already has 4 players." }, 409);
  }
  const now = new Date().toISOString();
  if (existingIndex >= 0) {
    lobby.members[existingIndex] = { ...lobby.members[existingIndex], player_id: playerId, tag, last_seen: now };
  } else {
    lobby.members.push({ player_id: playerId, tag, host: false, last_seen: now });
  }
  lobby.state.sync_enabled = true;
  lobby.state.sync_lobby_code = code;
  lobby.state.sync_player_tag = tag;
  lobby.state.lobby_code = code;
  lobby.updated_at = now;
  lobby.expires_at = lobbyExpiry();
  await saveLobby(env, lobby);
  await registerOrRefreshProfile(env, tag, playerId, lobby.state);
  return json(publicLobby(lobby));
}

async function leaveLobby(request, env, code) {
  const body = await readJson(request);
  const playerId = cleanPlayerId(body.player_id);
  const lobby = await loadLobby(env, code);
  if (!lobby) {
    return json({ error: "Lobby code was not found." }, 404);
  }
  lobby.members = lobby.members.filter((member) => member.player_id !== playerId);
  if (!lobby.members.length) {
    await env.STREAK_DB.prepare("DELETE FROM streak_lobbies WHERE code = ?").bind(code).run();
    return json({ ok: true, code, deleted: true });
  }
  lobby.members[0].host = true;
  lobby.updated_at = new Date().toISOString();
  lobby.expires_at = lobbyExpiry();
  await saveLobby(env, lobby);
  return json(publicLobby(lobby));
}

async function getLobby(env, code) {
  const lobby = await loadLobby(env, code);
  if (!lobby) {
    return json({ error: "Lobby code was not found." }, 404);
  }
  return json(publicLobby(lobby));
}

async function updateLobby(request, env, code) {
  const body = await readJson(request);
  const playerId = cleanPlayerId(body.player_id);
  const lobby = await loadLobby(env, code);
  if (!lobby) {
    return json({ error: "Lobby code was not found." }, 404);
  }
  const member = lobby.members.find((item) => item.player_id === playerId);
  if (!member) {
    return json({ error: "Join this lobby before updating it." }, 403);
  }
  const state = sanitizeState(body.state);
  state.sync_enabled = true;
  state.sync_lobby_code = code;
  state.sync_player_tag = member.tag;
  state.lobby_code = code;
  state.sync_revision = Number(lobby.state?.sync_revision || 0) + 1;
  lobby.state = state;
  member.last_seen = new Date().toISOString();
  lobby.updated_at = member.last_seen;
  lobby.expires_at = lobbyExpiry();
  await saveLobby(env, lobby);
  await registerOrRefreshProfile(env, member.tag, playerId, state);
  return json(publicLobby(lobby));
}

async function readJson(request) {
  const contentType = request.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    return {};
  }
  return await request.json();
}

async function loadPlayer(env, tag) {
  const row = await env.STREAK_DB.prepare(
    "SELECT tag, player_id, state_json, created_at, updated_at FROM streak_profiles WHERE tag_normalized = ?",
  )
    .bind(normalizeTag(tag))
    .first();
  return row ? rowToPlayer(row) : null;
}

async function registerOrRefreshProfile(env, tag, playerId, state) {
  const existing = await loadPlayer(env, tag);
  if (existing && existing.player_id !== playerId) {
    throw new Error("That player tag is already taken.");
  }
  const now = new Date().toISOString();
  await savePlayer(env, {
    tag,
    player_id: playerId,
    state: { ...sanitizeState(state), sync_player_tag: tag },
    created_at: existing?.created_at || now,
    updated_at: now,
  });
}

async function savePlayer(env, profile) {
  await env.STREAK_DB.prepare(
    `INSERT INTO streak_profiles (tag, tag_normalized, player_id, state_json, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT(tag_normalized) DO UPDATE SET
       tag = excluded.tag,
       player_id = excluded.player_id,
       state_json = excluded.state_json,
       updated_at = excluded.updated_at`,
  )
    .bind(
      profile.tag,
      normalizeTag(profile.tag),
      profile.player_id,
      JSON.stringify(profile.state),
      profile.created_at,
      profile.updated_at,
    )
    .run();
}

function rowToPlayer(row) {
  return {
    tag: row.tag,
    player_id: row.player_id,
    state: parseState(row.state_json),
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

function publicPlayer(profile) {
  return {
    tag: profile.tag,
    state: profile.state,
    created_at: profile.created_at,
    updated_at: profile.updated_at,
  };
}

async function loadLobby(env, code) {
  const row = await env.STREAK_DB.prepare(
    "SELECT code, state_json, members_json, created_at, updated_at, expires_at FROM streak_lobbies WHERE code = ? AND expires_at > ?",
  )
    .bind(code, new Date().toISOString())
    .first();
  return row ? rowToLobby(row) : null;
}

async function saveLobby(env, lobby) {
  await env.STREAK_DB.prepare(
    `INSERT INTO streak_lobbies (code, state_json, members_json, created_at, updated_at, expires_at)
     VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT(code) DO UPDATE SET
       state_json = excluded.state_json,
       members_json = excluded.members_json,
       updated_at = excluded.updated_at,
       expires_at = excluded.expires_at`,
  )
    .bind(
      lobby.code,
      JSON.stringify(lobby.state),
      JSON.stringify(lobby.members),
      lobby.created_at,
      lobby.updated_at,
      lobby.expires_at,
    )
    .run();
}

function rowToLobby(row) {
  return {
    code: row.code,
    state: parseState(row.state_json),
    members: parseMembers(row.members_json),
    created_at: row.created_at,
    updated_at: row.updated_at,
    expires_at: row.expires_at,
  };
}

function publicLobby(lobby) {
  return {
    code: lobby.code,
    state: lobby.state,
    members: lobby.members.map(publicMember),
    updated_at: lobby.updated_at,
    expires_at: lobby.expires_at,
  };
}

function publicMember(member) {
  return {
    tag: member.tag,
    host: Boolean(member.host),
    last_seen: member.last_seen,
  };
}

function parseState(raw) {
  try {
    return sanitizeState(JSON.parse(String(raw || "{}")));
  } catch {
    return sanitizeState({});
  }
}

function parseMembers(raw) {
  try {
    const members = JSON.parse(String(raw || "[]"));
    if (!Array.isArray(members)) {
      return [];
    }
    return members.slice(0, 4).map((member, index) => ({
      player_id: cleanText(member?.player_id, 64),
      tag: cleanTag(member?.tag),
      host: Boolean(member?.host || index === 0),
      last_seen: cleanText(member?.last_seen, 40),
    }));
  } catch {
    return [];
  }
}

function sanitizeState(input) {
  const state = input && typeof input === "object" ? input : {};
  const players = Array.isArray(state.players) ? state.players.slice(0, 4) : [];
  return {
    enabled: Boolean(state.enabled),
    lobby_code: cleanText(state.lobby_code, 40),
    streak: clampInteger(state.streak, 0, 999),
    sync_enabled: Boolean(state.sync_enabled),
    sync_server_url: "",
    sync_lobby_code: "",
    sync_player_tag: cleanText(state.sync_player_tag, 40),
    sync_player_id: "",
    sync_player_name: "",
    sync_revision: clampInteger(state.sync_revision, 0, 999999999),
    players: players.map((player) => ({
      name: cleanText(player?.name, 32),
      status: STATUS_VALUES.has(player?.status) ? player.status : "Ready",
    })),
  };
}

function cleanTag(value) {
  const cleaned = String(value || "").trim().replace(/\s+/g, " ");
  const match = cleaned.match(/^([A-Za-z0-9 _.-]{2,24})#(\d{4})$/);
  if (!match) {
    throw new Error("Enter a tag like Nikko#3213.");
  }
  return `${match[1].trim()}#${match[2]}`;
}

function cleanCode(value) {
  const cleaned = String(value || "").toUpperCase().replace(/[^A-Z0-9-]/g, "").slice(0, 16);
  if (!cleaned) {
    throw new Error("Missing lobby code.");
  }
  return cleaned;
}

function normalizeTag(tag) {
  return cleanTag(tag).toUpperCase();
}

function cleanPlayerId(value) {
  const cleaned = String(value || "").replace(/[^a-fA-F0-9-]/g, "").slice(0, 64);
  if (!cleaned) {
    throw new Error("Missing player id.");
  }
  return cleaned;
}

function cleanText(value, maxLength) {
  return String(value || "").trim().slice(0, maxLength);
}

function clampInteger(value, minimum, maximum) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) {
    return minimum;
  }
  return Math.max(minimum, Math.min(maximum, parsed));
}

function randomCode(length) {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => CODE_ALPHABET[byte % CODE_ALPHABET.length]).join("");
}

function lobbyExpiry() {
  return new Date(Date.now() + LOBBY_TTL_MS).toISOString();
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: JSON_HEADERS,
  });
}

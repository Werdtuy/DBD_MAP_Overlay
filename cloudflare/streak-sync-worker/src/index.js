const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET,POST,PUT,OPTIONS",
  "access-control-allow-headers": "authorization,content-type,x-streak-admin-token",
};

const LOBBY_TTL_SECONDS = 60 * 60 * 24 * 7;
const CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const STATUS_VALUES = new Set(["Ready", "Escaped", "Dead", "Disconnected"]);

export default {
  async fetch(request, env) {
    try {
      if (request.method === "OPTIONS") {
        return new Response(null, { headers: JSON_HEADERS });
      }
      const url = new URL(request.url);
      if (request.method === "GET" && url.pathname === "/health") {
        return json({ ok: true });
      }
      if (url.pathname.startsWith("/api/admin")) {
        return await handleAdminRequest(request, env, url);
      }
      if (request.method === "POST" && url.pathname === "/api/lobbies") {
        return await createLobby(request, env);
      }
      const match = url.pathname.match(/^\/api\/lobbies\/([A-Z0-9-]+)(\/join)?$/);
      if (!match) {
        return json({ error: "Not found." }, 404);
      }
      const code = cleanCode(match[1]);
      if (request.method === "GET" && !match[2]) {
        return await getLobby(env, code);
      }
      if (request.method === "POST" && match[2] === "/join") {
        return await joinLobby(request, env, code);
      }
      if (request.method === "PUT" && !match[2]) {
        return await updateLobby(request, env, code);
      }
      return json({ error: "Method not allowed." }, 405);
    } catch (error) {
      console.error(JSON.stringify({ message: "streak_sync_error", error: String(error) }));
      return json({ error: "Streak sync failed." }, 500);
    }
  },
};

async function handleAdminRequest(request, env, url) {
  const auth = await requireAdmin(request, env);
  if (auth) {
    return auth;
  }

  if (request.method === "GET" && url.pathname === "/api/admin/stats") {
    return await adminStats(env);
  }
  if (request.method === "GET" && url.pathname === "/api/admin/lobbies") {
    return await adminListLobbies(env, url);
  }
  const match = url.pathname.match(/^\/api\/admin\/lobbies\/([A-Z0-9-]+)$/);
  if (!match) {
    return json({ error: "Admin route not found." }, 404);
  }
  const code = cleanCode(match[1]);
  if (request.method === "GET") {
    return await adminGetLobby(env, code);
  }
  if (request.method === "DELETE") {
    return await adminDeleteLobby(env, code);
  }
  if (request.method === "PUT") {
    return await adminUpdateLobby(request, env, code);
  }
  return json({ error: "Method not allowed." }, 405);
}

async function adminStats(env) {
  let cursor = undefined;
  let lobbyCount = 0;
  let playerCount = 0;
  let maxStreak = 0;
  do {
    const page = await env.STREAK_LOBBIES.list({ prefix: "lobby:", cursor });
    for (const key of page.keys) {
      const lobby = await env.STREAK_LOBBIES.get(key.name, "json");
      if (!lobby) {
        continue;
      }
      lobbyCount += 1;
      playerCount += Object.keys(lobby.members || {}).length;
      maxStreak = Math.max(maxStreak, Number(lobby.state?.streak || 0));
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return json({ lobby_count: lobbyCount, player_count: playerCount, max_streak: maxStreak });
}

async function adminListLobbies(env, url) {
  const cursor = url.searchParams.get("cursor") || undefined;
  const limit = Math.min(Math.max(Number.parseInt(url.searchParams.get("limit") || "50", 10), 1), 100);
  const page = await env.STREAK_LOBBIES.list({ prefix: "lobby:", limit, cursor });
  const lobbies = [];
  for (const key of page.keys) {
    const lobby = await env.STREAK_LOBBIES.get(key.name, "json");
    if (lobby) {
      lobbies.push(publicAdminLobby(lobby));
    }
  }
  return json({
    lobbies,
    cursor: page.cursor || "",
    list_complete: Boolean(page.list_complete),
  });
}

async function adminGetLobby(env, code) {
  const lobby = await loadLobby(env, code);
  if (!lobby) {
    return json({ error: "Lobby code was not found." }, 404);
  }
  return json(publicAdminLobby(lobby));
}

async function adminDeleteLobby(env, code) {
  await env.STREAK_LOBBIES.delete(lobbyKey(code));
  return json({ ok: true, code });
}

async function adminUpdateLobby(request, env, code) {
  const lobby = await loadLobby(env, code);
  if (!lobby) {
    return json({ error: "Lobby code was not found." }, 404);
  }
  const body = await readJson(request);
  if (body.action === "reset") {
    lobby.state.streak = 0;
    lobby.state.players = (lobby.state.players || []).map((player) => ({
      name: cleanText(player?.name, 32),
      status: "Ready",
    }));
  } else {
    if (body.streak !== undefined) {
      lobby.state.streak = clampInteger(body.streak, 0, 999);
    }
    if (Array.isArray(body.players)) {
      lobby.state.players = sanitizeState({ players: body.players }).players;
    }
  }
  lobby.state.sync_revision = Number(lobby.state?.sync_revision || 0) + 1;
  lobby.updated_at = new Date().toISOString();
  await saveLobby(env, lobby);
  return json(publicAdminLobby(lobby));
}

async function createLobby(request, env) {
  const body = await readJson(request);
  const playerId = cleanPlayerId(body.player_id);
  const playerName = cleanText(body.player_name, 32);
  let code = "";
  for (let attempt = 0; attempt < 10; attempt += 1) {
    code = `BLOOD-${randomCode(4)}`;
    const existing = await env.STREAK_LOBBIES.get(lobbyKey(code));
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
  state.lobby_code = code;
  state.sync_revision = 1;
  const lobby = {
    code,
    state,
    members: {
      [playerId]: {
        name: playerName,
        last_seen: new Date().toISOString(),
      },
    },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
  await saveLobby(env, lobby);
  return json(publicLobby(lobby));
}

async function joinLobby(request, env, code) {
  const body = await readJson(request);
  const playerId = cleanPlayerId(body.player_id);
  const playerName = cleanText(body.player_name, 32);
  const lobby = await loadLobby(env, code);
  if (!lobby) {
    return json({ error: "Lobby code was not found." }, 404);
  }
  if (!lobby.members[playerId] && Object.keys(lobby.members).length >= 4) {
    return json({ error: "This lobby already has 4 players." }, 409);
  }
  lobby.members[playerId] = {
    name: playerName,
    last_seen: new Date().toISOString(),
  };
  lobby.updated_at = new Date().toISOString();
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
  if (!lobby.members[playerId]) {
    return json({ error: "Join this lobby before updating it." }, 403);
  }
  const state = sanitizeState(body.state);
  const nextRevision = Number(lobby.state?.sync_revision || 0) + 1;
  state.sync_enabled = true;
  state.sync_lobby_code = code;
  state.lobby_code = code;
  state.sync_revision = nextRevision;
  lobby.state = state;
  lobby.members[playerId].last_seen = new Date().toISOString();
  lobby.updated_at = new Date().toISOString();
  await saveLobby(env, lobby);
  return json(publicLobby(lobby));
}

async function readJson(request) {
  const contentType = request.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    return {};
  }
  return await request.json();
}

async function loadLobby(env, code) {
  const raw = await env.STREAK_LOBBIES.get(lobbyKey(code), "json");
  if (!raw || typeof raw !== "object") {
    return null;
  }
  raw.members ||= {};
  return raw;
}

async function saveLobby(env, lobby) {
  await env.STREAK_LOBBIES.put(lobbyKey(lobby.code), JSON.stringify(lobby), {
    expirationTtl: LOBBY_TTL_SECONDS,
  });
}

function publicLobby(lobby) {
  return {
    code: lobby.code,
    state: lobby.state,
    members: Object.values(lobby.members || {}).map((member) => ({
      name: member.name,
      last_seen: member.last_seen,
    })),
    updated_at: lobby.updated_at,
  };
}

function publicAdminLobby(lobby) {
  return {
    code: lobby.code,
    state: lobby.state,
    members: Object.entries(lobby.members || {}).map(([playerId, member]) => ({
      player_id: playerId,
      name: member.name,
      last_seen: member.last_seen,
    })),
    created_at: lobby.created_at,
    updated_at: lobby.updated_at,
  };
}

async function requireAdmin(request, env) {
  if (!env.STREAK_ADMIN_TOKEN) {
    return json({ error: "Admin token is not configured on this Worker." }, 503);
  }
  const auth = request.headers.get("authorization") || "";
  const bearer = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7) : "";
  const token = bearer || request.headers.get("x-streak-admin-token") || "";
  if (!(await timingSafeEqual(token, env.STREAK_ADMIN_TOKEN))) {
    return json({ error: "Unauthorized." }, 401);
  }
  return null;
}

async function timingSafeEqual(left, right) {
  const encoder = new TextEncoder();
  const leftBytes = encoder.encode(String(left || ""));
  const rightBytes = encoder.encode(String(right || ""));
  if (leftBytes.length !== rightBytes.length) {
    return false;
  }
  let diff = 0;
  for (let index = 0; index < leftBytes.length; index += 1) {
    diff |= leftBytes[index] ^ rightBytes[index];
  }
  return diff === 0;
}

function sanitizeState(input) {
  const state = input && typeof input === "object" ? input : {};
  const players = Array.isArray(state.players) ? state.players.slice(0, 4) : [];
  return {
    enabled: Boolean(state.enabled),
    lobby_code: cleanText(state.lobby_code, 24),
    streak: clampInteger(state.streak, 0, 999),
    sync_enabled: Boolean(state.sync_enabled),
    sync_server_url: "",
    sync_lobby_code: cleanText(state.sync_lobby_code, 24),
    sync_player_id: "",
    sync_player_name: "",
    sync_revision: clampInteger(state.sync_revision, 0, 999999999),
    players: players.map((player) => ({
      name: cleanText(player?.name, 32),
      status: STATUS_VALUES.has(player?.status) ? player.status : "Ready",
    })),
  };
}

function cleanCode(value) {
  const cleaned = String(value || "").toUpperCase().replace(/[^A-Z0-9-]/g, "");
  if (!cleaned) {
    throw new Error("Missing lobby code.");
  }
  return cleaned;
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

function lobbyKey(code) {
  return `lobby:${code}`;
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: JSON_HEADERS,
  });
}

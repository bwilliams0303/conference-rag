// Verifies that a request comes from a signed-in user.
//
// Two layers check the user's JWT (the Authorization: Bearer header that
// supabase-js sends after login):
//   1. The platform (verify_jwt = true in supabase/config.toml) rejects
//      requests without a valid JWT before this code runs.
//   2. This module confirms it is a *user* session, not just an API key.
//
// By default @supabase/server verifies the JWT locally against the
// project's public signing keys (SUPABASE_JWKS), with no network call.
// Trade-off: a session revoked by logging out stays valid until its JWT
// expires (at most an hour). Set the AUTH_FALLBACK secret to "getUser" to
// ask Supabase Auth on every request instead (slower, catches revocation).
//
// Neither path uses the legacy SUPABASE_ANON_KEY.
import { createSupabaseContext } from "npm:@supabase/server@1.9.1";
import {
  createClient,
  type SupabaseClient,
} from "npm:@supabase/supabase-js@2.117.3";

export type AuthedUser = {
  userId: string;
  // Acts as the user: RLS applies and auth.uid() is their id
  supabase: SupabaseClient;
};

type AuthResult =
  | { user: AuthedUser }
  | { error: { status: number; message: string } };

const USE_GET_USER = Deno.env.get("AUTH_FALLBACK") === "getUser";

export function requireUser(req: Request): Promise<AuthResult> {
  return USE_GET_USER ? viaGetUser(req) : viaServerSdk(req);
}

async function viaServerSdk(req: Request): Promise<AuthResult> {
  const { data: ctx, error } = await createSupabaseContext(req, {
    auth: "user",
  });
  if (error || !ctx?.userClaims) {
    return {
      error: { status: error?.status ?? 401, message: "Invalid or expired session" },
    };
  }
  return { user: { userId: ctx.userClaims.id, supabase: ctx.supabase } };
}

function publishableKey(): string {
  // JSON object of named keys, e.g. {"default": "sb_publishable_..."}
  const keys = JSON.parse(Deno.env.get("SUPABASE_PUBLISHABLE_KEYS") ?? "{}");
  return keys.default ?? Object.values(keys)[0] ?? "";
}

async function viaGetUser(req: Request): Promise<AuthResult> {
  const authHeader = req.headers.get("Authorization") ?? "";
  if (!authHeader.startsWith("Bearer ")) {
    return { error: { status: 401, message: "Missing session" } };
  }
  const supabase = createClient(Deno.env.get("SUPABASE_URL") ?? "", publishableKey(), {
    global: { headers: { Authorization: authHeader } },
    auth: { persistSession: false, autoRefreshToken: false },
  });
  // A publishable key in the header has no user behind it, so this fails
  const { data, error } = await supabase.auth.getUser(authHeader.slice(7));
  if (error || !data.user) {
    return { error: { status: 401, message: "Invalid or expired session" } };
  }
  return { user: { userId: data.user.id, supabase } };
}

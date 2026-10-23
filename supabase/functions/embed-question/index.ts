// embed-question: turns a question into a 1,536-number embedding.
//
// POST { "question": "..." }  ->  { "embedding": [...] }
// POST { "ping": true }       ->  { "ok": true }   (readiness check, no OpenAI call)
//
// The OpenAI key never leaves the server. Before spending it, the function
// checks, in order: the caller's origin (CORS), a signed-in user session,
// the request size, and the user's rate limit.
import { corsHeaders, jsonResponse } from "../_shared/cors.ts";
import { requireUser } from "../_shared/auth.ts";

const MAX_BODY_BYTES = 2048;
const MAX_QUESTION_CHARS = 500; // ~125 tokens, caps the cost per call
const OPENAI_TIMEOUT_MS = 10_000;
const EMBEDDING_DIMENSIONS = 1536;

Deno.serve(async (req) => {
  // Browser preflight: allowed origins get CORS headers, others get none
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders(req) });
  }
  if (req.method !== "POST") {
    return jsonResponse(req, { error: "Method not allowed" }, 405);
  }

  const auth = await requireUser(req);
  if ("error" in auth) {
    return jsonResponse(req, { error: auth.error.message }, auth.error.status);
  }

  // Reject oversized bodies before parsing them
  const declaredLength = Number(req.headers.get("Content-Length") ?? 0);
  const raw = declaredLength > MAX_BODY_BYTES ? "" : await req.text();
  if (declaredLength > MAX_BODY_BYTES || new TextEncoder().encode(raw).length > MAX_BODY_BYTES) {
    return jsonResponse(req, { error: "Request too large" }, 413);
  }

  let body: { question?: unknown; ping?: unknown };
  try {
    body = JSON.parse(raw);
  } catch {
    return jsonResponse(req, { error: "Body must be JSON" }, 400);
  }

  if (body?.ping === true) {
    return jsonResponse(req, { ok: true });
  }

  const question = typeof body?.question === "string" ? body.question.trim() : "";
  if (!question || question.length > MAX_QUESTION_CHARS) {
    return jsonResponse(
      req,
      { error: `'question' must be 1-${MAX_QUESTION_CHARS} characters` },
      400,
    );
  }

  // Rate limit, called with the user's own token: the database reads their
  // id from the JWT (auth.uid()), so this code can't charge someone else
  const { data: allowed, error: limitError } = await auth.user.supabase.rpc(
    "consume_rate_limit",
    { p_bucket: "embed-question" },
  );
  if (limitError) {
    console.error("rate limit check failed:", limitError.code, limitError.message);
    return jsonResponse(req, { error: "Service unavailable, try again shortly" }, 503);
  }
  if (allowed !== true) {
    return jsonResponse(
      req,
      { error: "Too many questions. Please wait a minute and try again." },
      429,
      { "Retry-After": "60" },
    );
  }

  const openaiKey = Deno.env.get("OPENAI_API_KEY");
  if (!openaiKey) {
    console.error("OPENAI_API_KEY secret is not set");
    return jsonResponse(req, { error: "Server is not configured" }, 500);
  }

  try {
    const response = await fetch("https://api.openai.com/v1/embeddings", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${openaiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ model: "text-embedding-3-small", input: question }),
      signal: AbortSignal.timeout(OPENAI_TIMEOUT_MS),
    });

    if (!response.ok) {
      // Log details server-side; don't pass OpenAI's error through to the browser
      console.error("OpenAI error:", response.status, (await response.text()).slice(0, 300));
      return jsonResponse(req, { error: "Embedding service error" }, 502);
    }

    const data = await response.json();
    const embedding = data?.data?.[0]?.embedding;
    if (!Array.isArray(embedding) || embedding.length !== EMBEDDING_DIMENSIONS) {
      console.error("Unexpected OpenAI response shape");
      return jsonResponse(req, { error: "Embedding service error" }, 502);
    }
    return jsonResponse(req, { embedding });
  } catch (err) {
    const timedOut = err instanceof DOMException && err.name === "TimeoutError";
    console.error(timedOut ? "OpenAI request timed out" : `OpenAI request failed: ${err}`);
    return jsonResponse(
      req,
      { error: timedOut ? "Embedding service timed out" : "Embedding service error" },
      timedOut ? 504 : 502,
    );
  }
});

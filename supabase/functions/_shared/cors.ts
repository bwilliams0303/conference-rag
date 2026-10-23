// CORS: which websites' JavaScript may call these functions from a browser.
//
// An origin is scheme + host + port, with no path, so every repo published
// on bwilliams0303.github.io shares the first entry. Change it to your own
// GitHub Pages origin. CORS only restricts browsers (curl ignores it); the
// real protection is the auth check and rate limit in each function.
const ALLOWED_ORIGINS = new Set([
  "https://bwilliams0303.github.io",
  "http://localhost:5500", // VS Code Live Server
  "http://127.0.0.1:5500",
]);

export function corsHeaders(req: Request): Record<string, string> {
  const origin = req.headers.get("Origin") ?? "";
  // Vary tells caches the response depends on the Origin header
  const headers: Record<string, string> = { Vary: "Origin" };
  if (ALLOWED_ORIGINS.has(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
    headers["Access-Control-Allow-Methods"] = "POST, OPTIONS";
    headers["Access-Control-Allow-Headers"] =
      "authorization, x-client-info, apikey, content-type";
    headers["Access-Control-Max-Age"] = "86400";
  }
  return headers;
}

export function jsonResponse(
  req: Request,
  body: unknown,
  status = 200,
  extraHeaders: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders(req),
      "Content-Type": "application/json",
      ...extraHeaders,
    },
  });
}

/**
 * Server-side helper for talking to the FastAPI backend from Next.js route
 * handlers. Browser code never calls FastAPI directly; it goes through
 * `/api/*` so the backend URL stays private and CORS is a non-issue.
 */

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";

export function getBackendUrl(): string {
  const url = process.env.FASTAPI_URL || process.env.NEXT_PUBLIC_API_URL || DEFAULT_BACKEND_URL;
  return url.replace(/\/+$/, "");
}

type ProxyOptions = {
  method?: string;
  body?: unknown;
  /** Extra headers forwarded to the backend (e.g. client IP / user agent). */
  headers?: Record<string, string>;
  /** Query string appended to the backend path. */
  search?: string;
};

/**
 * Forward a request to the backend and return a `Response` that mirrors the
 * backend's status and JSON body. When the backend is unreachable a 502 is
 * returned instead of throwing so callers get a consistent `{ error }` shape.
 */
export async function proxyToBackend(
  path: string,
  options: ProxyOptions = {}
): Promise<Response> {
  const url = `${getBackendUrl()}${path}${options.search ?? ""}`;

  let response: Response;
  try {
    response = await fetch(url, {
      method: options.method ?? "GET",
      headers: {
        ...(options.body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(options.headers ?? {}),
      },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      cache: "no-store",
    });
  } catch (error) {
    console.error(`Backend request failed: ${options.method ?? "GET"} ${url}`, error);
    return Response.json(
      { error: "Could not reach the form service. Is the backend running?" },
      { status: 502 }
    );
  }

  if (response.status === 204) {
    return new Response(null, { status: 204 });
  }

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { error: text };
    }
  }

  if (!response.ok) {
    return Response.json(normalizeError(payload, response.status), {
      status: response.status,
    });
  }

  return Response.json(payload, { status: response.status });
}

/**
 * FastAPI reports errors as `{ detail: string | ValidationError[] }`. Flatten
 * that into `{ error: string, details?: unknown }` for the UI.
 */
export function normalizeError(payload: unknown, status: number): { error: string; details?: unknown } {
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    if (typeof record.error === "string") {
      return { error: record.error, details: record.details };
    }
    const detail = record.detail;
    if (typeof detail === "string") {
      return { error: detail };
    }
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      const message = (detail as { message?: unknown }).message;
      return {
        error: typeof message === "string" ? message : `Request failed with status ${status}`,
        details: detail,
      };
    }
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => {
          if (item && typeof item === "object" && "msg" in item) {
            const msg = String((item as { msg: unknown }).msg).replace(/^Value error, /, "");
            const loc = Array.isArray((item as { loc?: unknown[] }).loc)
              ? ((item as { loc: unknown[] }).loc).filter((p) => p !== "body").join(".")
              : "";
            return loc ? `${loc}: ${msg}` : msg;
          }
          return String(item);
        })
        .filter(Boolean);
      return { error: messages.join("; ") || "Validation failed", details: detail };
    }
  }
  return { error: `Request failed with status ${status}` };
}

/** Pull client-identifying headers off the incoming request so they can be forwarded. */
export function clientHeaders(request: Request): Record<string, string> {
  const headers: Record<string, string> = {};
  const userAgent = request.headers.get("user-agent");
  const forwardedFor = request.headers.get("x-forwarded-for");
  const realIp = request.headers.get("x-real-ip");
  if (userAgent) headers["X-Client-User-Agent"] = userAgent;
  const ip = forwardedFor?.split(",")[0]?.trim() || realIp;
  if (ip) headers["X-Client-IP"] = ip;
  return headers;
}

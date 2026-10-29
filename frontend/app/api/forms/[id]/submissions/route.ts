import { clientHeaders, proxyToBackend } from "@/lib/server/backend";

type RouteContext = {
  params: Promise<{ id: string }>;
};

/** GET /api/forms/:id/submissions - submissions for one form. */
export async function GET(request: Request, context: RouteContext) {
  const { id } = await context.params;
  const { search } = new URL(request.url);
  return proxyToBackend(`/forms/${encodeURIComponent(id)}/submissions`, { search });
}

/** POST /api/forms/:id/submissions - save a submission. */
export async function POST(request: Request, context: RouteContext) {
  const { id } = await context.params;
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "Request body must be JSON" }, { status: 400 });
  }
  return proxyToBackend(`/forms/${encodeURIComponent(id)}/submissions`, {
    method: "POST",
    body,
    headers: clientHeaders(request),
  });
}

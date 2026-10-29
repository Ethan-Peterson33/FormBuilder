import { proxyToBackend } from "@/lib/server/backend";

type RouteContext = {
  params: Promise<{ id: string }>;
};

/** GET /api/forms/:id - one form configuration. */
export async function GET(_request: Request, context: RouteContext) {
  const { id } = await context.params;
  return proxyToBackend(`/forms/${encodeURIComponent(id)}`);
}

/** PUT /api/forms/:id - replace a form configuration. */
export async function PUT(request: Request, context: RouteContext) {
  const { id } = await context.params;
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "Request body must be JSON" }, { status: 400 });
  }
  return proxyToBackend(`/forms/${encodeURIComponent(id)}`, { method: "PUT", body });
}

/** DELETE /api/forms/:id */
export async function DELETE(_request: Request, context: RouteContext) {
  const { id } = await context.params;
  return proxyToBackend(`/forms/${encodeURIComponent(id)}`, { method: "DELETE" });
}

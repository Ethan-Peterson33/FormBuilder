import { proxyToBackend } from "@/lib/server/backend";

type RouteContext = {
  params: Promise<{ id: string }>;
};

/** GET /api/option-lists/:id */
export async function GET(_request: Request, context: RouteContext) {
  const { id } = await context.params;
  return proxyToBackend(`/option-lists/${encodeURIComponent(id)}`);
}

/** PUT /api/option-lists/:id - replace a list's name/options. */
export async function PUT(request: Request, context: RouteContext) {
  const { id } = await context.params;
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "Request body must be JSON" }, { status: 400 });
  }
  return proxyToBackend(`/option-lists/${encodeURIComponent(id)}`, { method: "PUT", body });
}

/** DELETE /api/option-lists/:id */
export async function DELETE(_request: Request, context: RouteContext) {
  const { id } = await context.params;
  return proxyToBackend(`/option-lists/${encodeURIComponent(id)}`, { method: "DELETE" });
}

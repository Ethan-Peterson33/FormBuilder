import { proxyToBackend } from "@/lib/server/backend";

type RouteContext = {
  params: Promise<{ id: string }>;
};

/** GET /api/submissions/:id - one submission. */
export async function GET(_request: Request, context: RouteContext) {
  const { id } = await context.params;
  return proxyToBackend(`/submissions/${encodeURIComponent(id)}`);
}

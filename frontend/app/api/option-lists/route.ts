import { proxyToBackend } from "@/lib/server/backend";

/** GET /api/option-lists - all reusable option lists. */
export async function GET() {
  return proxyToBackend("/option-lists");
}

/** POST /api/option-lists - save a new reusable option list. */
export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "Request body must be JSON" }, { status: 400 });
  }
  return proxyToBackend("/option-lists", { method: "POST", body });
}

import { proxyToBackend } from "@/lib/server/backend";

/** GET /api/forms - all saved form configurations. */
export async function GET() {
  return proxyToBackend("/forms");
}

/** POST /api/forms - save a new form configuration. */
export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "Request body must be JSON" }, { status: 400 });
  }
  return proxyToBackend("/forms", { method: "POST", body });
}

import { proxyToBackend } from "@/lib/server/backend";

/** GET /api/submissions?formId=&limit=&offset= - all submissions. */
export async function GET(request: Request) {
  const { search } = new URL(request.url);
  return proxyToBackend("/submissions", { search });
}

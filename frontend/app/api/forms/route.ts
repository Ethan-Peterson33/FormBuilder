<<<<<<< HEAD
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
=======
export async function GET() {
  const response = await fetch(
    `${process.env.FASTAPI_URL}/forms`,
    {
      cache: "no-store",
    }
  );

  if (!response.ok) {
    return Response.json(
      { error: "Failed to fetch forms" },
      { status: response.status }
    );
  }

  const data = await response.json();

  return Response.json(data);
}


export async function POST(request: Request) {
  const body = await request.json();

  const response = await fetch(
    `${process.env.FASTAPI_URL}/forms`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    }
  );

  const data = await response.json();

  return Response.json(data, {
    status: response.status,
  });
}
>>>>>>> 51796aca40e50a78169cad2477a96e359d52bd71

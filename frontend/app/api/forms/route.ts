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
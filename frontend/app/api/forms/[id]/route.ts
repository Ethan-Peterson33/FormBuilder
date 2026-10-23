type RouteContext = {
  params: Promise<{
    id: string;
  }>;
};

export async function GET(
  _request: Request,
  context: RouteContext
) {
  const { id } = await context.params;

  const response = await fetch(
    `${process.env.FASTAPI_URL}/forms/${encodeURIComponent(id)}`,
    {
      cache: "no-store",
    }
  );

  const data = await response.json();

  return Response.json(data);
}
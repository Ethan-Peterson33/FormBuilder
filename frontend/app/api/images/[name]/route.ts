type RouteContext = {
  params: Promise<{
    name: string;
  }>;
};

export async function GET(_request: Request, context: RouteContext) {
  try {
    const { name } = await context.params;

    const response = await fetch(
      `${process.env.FASTAPI_URL}/images/files/${encodeURIComponent(name)}`,
      {
        cache: "no-store",
      }
    );

    if (!response.ok) {
      return new Response("Image not found", {
        status: response.status,
      });
    }

    const contentType =
      response.headers.get("content-type") || "application/octet-stream";

    const imageBuffer = await response.arrayBuffer();

    return new Response(imageBuffer, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Cache-Control": "no-store",
      },
    });
  } catch (error) {
    console.error("Error in /api/images/[name]:", error);

    return new Response("Failed to proxy image", {
      status: 500,
    });
  }
}
export async function GET() {
  try {
    const response = await fetch(
      `${process.env.FASTAPI_URL}/images`,
      {
        cache: "no-store",
      }
    );

    if (!response.ok) {
      return Response.json(
        { error: "Failed to fetch images from FastAPI" },
        { status: response.status }
      );
    }

    const data = await response.json();

    const images = data.images.map(
      (image: { name: string }) => ({
        name: image.name,
        url: `/api/images/${encodeURIComponent(image.name)}`,
      })
    );

    return Response.json({
      count: images.length,
      images,
    });
  } catch (error) {
    console.error("Image proxy error:", error);

    return Response.json(
      { error: "Could not connect to image service" },
      { status: 500 }
    );
  }
}


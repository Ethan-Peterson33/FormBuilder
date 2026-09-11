from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

IMAGE_DIR = Path(__file__).parent / "images"
IMAGE_DIR.mkdir(exist_ok=True)

app.mount(
    "/images/files",
    StaticFiles(directory=IMAGE_DIR),
    name="images",
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/images")
def get_images(request: Request):
    allowed_extensions = {".png", ".jpg", ".jpeg", ".webp"}

    images = []

    for image_path in IMAGE_DIR.iterdir():
        if image_path.is_file() and image_path.suffix.lower() in allowed_extensions:
            images.append(
                {
                    "name": image_path.name,
                    "url": str(
                        request.url_for(
                            "images",
                            path=image_path.name,
                        )
                    ),
                }
            )

    return {
        "count": len(images),
        "images": images,
    }

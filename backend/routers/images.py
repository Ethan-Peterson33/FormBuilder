"""Image listing endpoint (static files are mounted in ``main.py``)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from core.config import get_image_dir

router = APIRouter(tags=["images"])

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


@router.get("/images")
def get_images(request: Request):
    images = []
    for image_path in sorted(get_image_dir().iterdir()):
        if image_path.is_file() and image_path.suffix.lower() in ALLOWED_EXTENSIONS:
            images.append(
                {
                    "name": image_path.name,
                    "url": str(request.url_for("images", path=image_path.name)),
                }
            )
    return {"count": len(images), "images": images}

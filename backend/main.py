from pathlib import Path
import json

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

FORMS_FILE = Path(__file__).parent / "data" / "forms.json"

@app.get("/forms")
def get_forms():
    with open(FORMS_FILE, "r") as file:
        forms = json.load(file)

    return forms

@app.get("/forms/{form_id}")
def get_form(form_id: str):
    with open(FORMS_FILE, "r") as file:
        forms = json.load(file)

    for form in forms:
        if form["id"] == form_id:
            return form

    return {"error": "Form not found"}

@app.post("/forms", status_code=201)
def create_form(form: dict):
    with open(FORMS_FILE, "r") as file:
        forms = json.load(file)

    for existing_form in forms:
        if existing_form["id"] == form["id"]:
            raise HTTPException(
                status_code=409,
                detail="A form with this ID already exists"
            )

    forms.append(form)

    with open(FORMS_FILE, "w") as file:
        json.dump(forms, file, indent=2)

    return form
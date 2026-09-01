"""
PDF Image Overlay API
----------------------
Accepts a PDF (any size, including A4, multi-page) and a PNG, then places
the PNG on one page, several pages, or every page, at given coordinates and
size, and returns the resulting PDF.

Run locally:
    uvicorn main:app --host 0.0.0.0 --port 8000

Docs (interactive test UI) once running:
    http://localhost:8000/docs
"""

import io
from typing import List, Optional

import pymupdf as fitz  # PyMuPDF (new import name; 'fitz' alias kept for brevity below)
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from PIL import Image

from auth import authenticate_user, create_access_token, get_current_user

app = FastAPI(
    title="PDF Image Overlay API",
    description="Place a PNG onto one, several, or all pages of a PDF at given coordinates/size.",
    version="1.2.0",
)

MAX_FILE_SIZE_MB = 25


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Login endpoint. Send username + password as form fields (standard OAuth2
    password flow), get back a JWT to use on protected endpoints.

    Example:
        curl -X POST http://localhost:8000/token \\
          -d "username=client1&password=their-password"

    Then use the returned token:
        curl -H "Authorization: Bearer <access_token>" ...
    """
    if not authenticate_user(form_data.username, form_data.password):
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(form_data.username)
    return {"access_token": token, "token_type": "bearer"}


def parse_pages(pages_str: str, total_pages: int) -> List[int]:
    """
    Parse a pages spec into a sorted list of 1-indexed page numbers.

    Accepted formats:
      - "all"        -> every page
      - "3"          -> just page 3
      - "1,3,5"      -> pages 1, 3 and 5
      - "1-3,7"      -> pages 1,2,3 and 7 (ranges + list combined)
    """
    pages_str = (pages_str or "").strip().lower()
    if not pages_str:
        raise HTTPException(400, "pages must not be empty")

    if pages_str in ("all", "*"):
        return list(range(1, total_pages + 1))

    result = set()
    for part in pages_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            bounds = part.split("-")
            if len(bounds) != 2:
                raise HTTPException(400, f"Invalid page range: '{part}'")
            try:
                start, end = int(bounds[0]), int(bounds[1])
            except ValueError:
                raise HTTPException(400, f"Invalid page range: '{part}'")
            if start > end:
                start, end = end, start
            result.update(range(start, end + 1))
        else:
            try:
                result.add(int(part))
            except ValueError:
                raise HTTPException(400, f"Invalid page number: '{part}'")

    if not result:
        raise HTTPException(400, "No valid page numbers found in 'pages'")

    out_of_range = [p for p in result if p < 1 or p > total_pages]
    if out_of_range:
        raise HTTPException(
            400,
            f"Page(s) {sorted(out_of_range)} do not exist "
            f"(PDF has {total_pages} page(s))",
        )

    return sorted(result)


def parse_optional_float(value: Optional[str], field_name: str) -> Optional[float]:
    """
    Parse a form field that may be omitted, an empty string, or a number.
    Treats None / "" / "auto" as "not provided" -> returns None.
    """
    if value is None:
        return None
    value = value.strip()
    if value == "" or value.lower() == "auto":
        return None
    try:
        return float(value)
    except ValueError:
        raise HTTPException(400, f"'{field_name}' must be a number, 'auto', or omitted")


@app.post("/place-image")
async def place_image(
    pdf: UploadFile = File(..., description="The base PDF file"),
    png: UploadFile = File(..., description="The PNG image to place"),
    x: float = Form(..., description="X coordinate (points, 1/72 inch) of the image's top-left corner"),
    y: float = Form(..., description="Y coordinate (points) of the image's top-left corner"),
    width: float = Form(..., description="Width of the placed image, in points"),
    height: Optional[str] = Form(
        None,
        description="Height of the placed image, in points. Leave empty, omit "
        "entirely, or send 'auto' to calculate it automatically from the PNG's "
        "own aspect ratio, so the image is never stretched.",
    ),
    pages: str = Form(
        "1",
        description="Which page(s) to place the image on. 'all' for every page, "
        "a single number like '3', a comma list like '1,3,5', a range like "
        "'1-3', or a combination like '1-3,7'.",
    ),
    origin: str = Form(
        "top-left",
        description="Coordinate origin: 'top-left' (default, y grows downward, "
        "matches most design tools) or 'bottom-left' (y grows upward, matches "
        "PDF/PostScript convention).",
    ),
    current_user: str = Depends(get_current_user),
):
    # --- Validate simple inputs -------------------------------------------
    if origin not in ("top-left", "bottom-left"):
        raise HTTPException(400, "origin must be 'top-left' or 'bottom-left'")
    if width <= 0:
        raise HTTPException(400, "width must be positive")

    height_val = parse_optional_float(height, "height")
    if height_val is not None and height_val <= 0:
        raise HTTPException(400, "height must be positive")

    pdf_bytes = await pdf.read()
    png_bytes = await png.read()

    if not pdf_bytes:
        raise HTTPException(400, "Empty or missing PDF file")
    if not png_bytes:
        raise HTTPException(400, "Empty or missing PNG file")

    max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
    if len(pdf_bytes) > max_bytes or len(png_bytes) > max_bytes:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE_MB}MB limit")

    # --- Validate PNG and derive height from aspect ratio if needed ------
    try:
        img = Image.open(io.BytesIO(png_bytes))
        img.verify()
        # re-open after verify() (verify() invalidates the file handle)
        img = Image.open(io.BytesIO(png_bytes))
        img_w, img_h = img.size
    except Exception:
        raise HTTPException(400, "Uploaded 'png' file is not a valid image")

    if height_val is None:
        height_val = width * (img_h / img_w)

    # --- Open PDF ----------------------------------------------------------
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        raise HTTPException(400, "Uploaded 'pdf' file is not a valid PDF")

    target_pages = parse_pages(pages, len(doc))  # raises 400 on bad input

    for page_num in target_pages:
        pg = doc[page_num - 1]
        page_rect = pg.rect  # fitz page rect: origin top-left, y grows downward

        place_x = x
        place_y = y
        if origin == "bottom-left":
            # convert a bottom-left-anchored y into fitz's top-left system
            place_y = page_rect.height - y - height_val

        rect = fitz.Rect(place_x, place_y, place_x + width, place_y + height_val)

        try:
            pg.insert_image(rect, stream=png_bytes)
        except Exception as e:
            doc.close()
            raise HTTPException(400, f"Failed to place image on page {page_num}: {e}")

    out = io.BytesIO()
    doc.save(out)
    doc.close()
    out.seek(0)

    return StreamingResponse(
        out,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="output.pdf"'},
    )

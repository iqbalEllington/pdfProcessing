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
    root_path="/python"
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


def find_text_matches(pg, text: str, case_sensitive: bool):
    """
    Find all occurrences of `text` on a page, returning a list of fitz.Rect.
    PyMuPDF's search_for() is case-insensitive by default; when
    case_sensitive=True we additionally verify the extracted text at each
    match actually matches case-for-case (guards against e.g. matching
    "Signature" when the PDF really has "SIGNATURE" and the caller cares).
    """
    matches = pg.search_for(text)
    if not case_sensitive:
        return matches

    exact = []
    for rect in matches:
        extracted = pg.get_textbox(rect).strip()
        if text.strip() in extracted or extracted in text.strip():
            exact.append(rect)
    return exact


def compute_anchor_rect(
    text_rect: "fitz.Rect",
    position: str,
    width: float,
    height: float,
    gap: float,
    dx: float,
    dy: float,
) -> "fitz.Rect":
    """
    Given the bounding box of an anchor text match, compute where the image
    rect should go for a given relative position. dx/dy are extra manual
    offsets applied after the automatic positioning (e.g. to nudge it a bit).
    """
    if position == "above":
        x0 = text_rect.x0
        y0 = text_rect.y0 - gap - height
    elif position == "below":
        x0 = text_rect.x0
        y0 = text_rect.y1 + gap
    elif position == "left":
        x0 = text_rect.x0 - gap - width
        y0 = text_rect.y0
    elif position == "right":
        x0 = text_rect.x1 + gap
        y0 = text_rect.y0
    elif position == "on":
        x0 = text_rect.x0
        y0 = text_rect.y0
    else:
        raise HTTPException(
            400, "anchor_position must be one of: above, below, left, right, on"
        )

    x0 += dx
    y0 += dy
    return fitz.Rect(x0, y0, x0 + width, y0 + height)


@app.post("/place-image")
async def place_image(
    pdf: UploadFile = File(..., description="The base PDF file"),
    png: UploadFile = File(..., description="The PNG image to place"),
    x: Optional[str] = Form(
        None,
        description="X coordinate (points) of the image's top-left corner. "
        "Required unless anchor_text is set, in which case it's an optional "
        "horizontal nudge (points, default 0) applied after auto-positioning.",
    ),
    y: Optional[str] = Form(
        None,
        description="Y coordinate (points) of the image's top-left corner. "
        "Required unless anchor_text is set, in which case it's an optional "
        "vertical nudge (points, default 0) applied after auto-positioning.",
    ),
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
        description="Coordinate origin for absolute x/y placement (ignored "
        "when anchor_text is set): 'top-left' (default, y grows downward) or "
        "'bottom-left' (y grows upward, PDF/PostScript convention).",
    ),
    anchor_text: Optional[str] = Form(
        None,
        description="Exact text to find on the page(s), e.g. 'Signature and "
        "Stamp of Agent'. When set, the image is placed automatically "
        "relative to this text instead of using absolute x/y coordinates.",
    ),
    anchor_position: str = Form(
        "above",
        description="Where to place the image relative to anchor_text: "
        "'above', 'below', 'left', 'right', or 'on' (directly over the "
        "text's own bounding box). Only used when anchor_text is set.",
    ),
    anchor_gap: float = Form(
        6.0,
        description="Gap in points between the anchor text and the image "
        "(ignored when anchor_position='on').",
    ),
    anchor_occurrence: str = Form(
        "first",
        description="'first' to use only the first (topmost) match of "
        "anchor_text on each page, or 'all' to place the image at every "
        "occurrence found on that page.",
    ),
    anchor_case_sensitive: bool = Form(
        False,
        description="If true, require anchor_text to match case-for-case. "
        "Default false (case-insensitive), which is usually what you want.",
    ),
    current_user: str = Depends(get_current_user),
):
    # --- Validate simple inputs -------------------------------------------
    if origin not in ("top-left", "bottom-left"):
        raise HTTPException(400, "origin must be 'top-left' or 'bottom-left'")
    if width <= 0:
        raise HTTPException(400, "width must be positive")
    if anchor_occurrence not in ("first", "all"):
        raise HTTPException(400, "anchor_occurrence must be 'first' or 'all'")
    if anchor_position not in ("above", "below", "left", "right", "on"):
        raise HTTPException(
            400, "anchor_position must be one of: above, below, left, right, on"
        )

    use_anchor = bool(anchor_text and anchor_text.strip())

    x_val = parse_optional_float(x, "x")
    y_val = parse_optional_float(y, "y")
    if use_anchor:
        x_val = x_val if x_val is not None else 0.0  # optional nudge
        y_val = y_val if y_val is not None else 0.0
    else:
        if x_val is None or y_val is None:
            raise HTTPException(
                400, "x and y are required when anchor_text is not provided"
            )

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

    # --- Compute every placement first (fail fast, nothing partially applied) --
    placements = []  # list of (page_num, fitz.Rect)

    if use_anchor:
        pages_missing_text = []
        for page_num in target_pages:
            pg = doc[page_num - 1]
            matches = find_text_matches(pg, anchor_text, anchor_case_sensitive)
            if not matches:
                pages_missing_text.append(page_num)
                continue
            chosen = matches if anchor_occurrence == "all" else matches[:1]
            for text_rect in chosen:
                rect = compute_anchor_rect(
                    text_rect, anchor_position, width, height_val, anchor_gap, x_val, y_val
                )
                placements.append((page_num, rect))

        if pages_missing_text:
            doc.close()
            raise HTTPException(
                400,
                f"anchor_text '{anchor_text}' was not found on page(s) "
                f"{pages_missing_text}",
            )
    else:
        for page_num in target_pages:
            pg = doc[page_num - 1]
            page_rect = pg.rect  # fitz page rect: origin top-left, y grows downward

            place_x = x_val
            place_y = y_val
            if origin == "bottom-left":
                place_y = page_rect.height - y_val - height_val

            rect = fitz.Rect(place_x, place_y, place_x + width, place_y + height_val)
            placements.append((page_num, rect))

    # --- Apply every placement ----------------------------------------------
    for page_num, rect in placements:
        pg = doc[page_num - 1]
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

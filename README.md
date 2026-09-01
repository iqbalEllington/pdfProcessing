# PDF Image Overlay API

A small FastAPI service: send it a PDF + a PNG + coordinates, get back a PDF
with the PNG placed on the page you specify.

## What it does

- Accepts any PDF (A4, multi-page, etc.) and any PNG.
- You tell it: which page, x, y, width, and (optionally) height.
- If you omit `height`, it's calculated automatically from the PNG's own
  aspect ratio, so the image is **never stretched or distorted**.
- Returns the modified PDF as a downloadable file.

## Assumptions made (adjust in `main.py` if you need something different)

- **Units**: `x`, `y`, `width`, `height` are all in **points** (1/72 inch —
  the PDF's native unit). An A4 page is 595 x 842 points. If your frontend
  works in pixels or mm, convert before calling the API (e.g. mm → points:
  `points = mm * 2.83465`).
- **Coordinate origin**: defaults to `top-left` (x,y = top-left corner of the
  image, y grows downward) — this matches how most design tools (Figma,
  browsers, canvas) think about coordinates. Pass `origin=bottom-left` if you
  want the traditional PDF/PostScript convention (y grows upward from the
  bottom of the page) instead.
- **Page numbers** are 1-indexed (`page=1` is the first page).

If your "coordinates and size" actually come from a UI where the user drags/
resizes the image as a **percentage of the page** (e.g. "40% across, 60%
down, 30% wide"), tell me the page size on your end and convert to points
before calling this API — happy to add a `unit=percent` mode instead if
that's a better fit for your input format.

## Run it locally

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000/docs` for an interactive test UI (Swagger),
or test with curl:

```bash
# single page, auto height
curl -o output.pdf \
  -F "pdf=@input.pdf" \
  -F "png=@logo.png" \
  -F "x=50" -F "y=50" -F "width=150" -F "pages=1" \
  http://localhost:8000/place-image

# stamp every page
curl -o output.pdf \
  -F "pdf=@input.pdf" -F "png=@logo.png" \
  -F "x=50" -F "y=50" -F "width=150" -F "pages=all" \
  http://localhost:8000/place-image

# specific pages: 1, 3 and 5
curl -o output.pdf \
  -F "pdf=@input.pdf" -F "png=@logo.png" \
  -F "x=50" -F "y=50" -F "width=150" -F "pages=1,3,5" \
  http://localhost:8000/place-image

# a range plus an extra page: 1 through 3, and 7
curl -o output.pdf \
  -F "pdf=@input.pdf" -F "png=@logo.png" \
  -F "x=50" -F "y=50" -F "width=150" -F "pages=1-3,7" \
  http://localhost:8000/place-image
```

## API reference

### `POST /place-image`

Multipart form fields:

| Field    | Type   | Required | Description                                                             |
|----------|--------|----------|---------------------------------------------------------------------------|
| `pdf`    | file   | yes      | The base PDF                                                             |
| `png`    | file   | yes      | The PNG to place                                                         |
| `x`      | float  | yes      | X position of the image's top-left corner (points)                      |
| `y`      | float  | yes      | Y position of the image's top-left corner (points)                      |
| `width`  | float  | yes      | Width of the placed image (points)                                      |
| `height` | float  | no       | Height (points). **Omit it, leave it blank, or send `auto`** to auto-preserve the PNG's aspect ratio (no distortion) |
| `pages`  | string | no       | Which page(s) to stamp. Default `1`. See formats below                  |
| `origin` | string | no       | `top-left` (default) or `bottom-left`                                   |

**`pages` formats:**

| You send      | What happens                                  |
|----------------|-----------------------------------------------|
| `1`            | just page 1                                   |
| `all`          | every page in the PDF                         |
| `1,3,5`        | pages 1, 3, and 5 only                        |
| `1-3`          | pages 1, 2, and 3                             |
| `1-3,7`        | pages 1, 2, 3, and 7 (mix ranges + list)      |

If any page number in the list doesn't exist in the PDF, the whole request
is rejected with a 400 and a message telling you exactly which page number(s)
were invalid — nothing is partially applied.

Response: `application/pdf` binary stream (or a JSON `{"detail": "..."}`
error with a 4xx status if something's wrong — bad file, out-of-range page,
non-positive size, etc.)

### `GET /health`

Simple liveness check, returns `{"status": "ok"}`. Useful for load balancer
/ uptime checks.

## Deploying on your own server

### Option A — Docker (recommended, simplest)

```bash
docker build -t pdf-overlay-api .
docker run -d -p 8000:8000 --name pdf-overlay-api pdf-overlay-api
```

Put it behind nginx/Caddy for TLS + a real domain, e.g. nginx reverse proxy:

```nginx
server {
    listen 443 ssl;
    server_name api.yourdomain.com;
    # ssl_certificate / ssl_certificate_key ...

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        client_max_body_size 30M;   # allow large PDF/PNG uploads
    }
}
```

### Option B — systemd (no Docker)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

`/etc/systemd/system/pdf-overlay-api.service`:

```ini
[Unit]
Description=PDF Overlay API
After=network.target

[Service]
WorkingDirectory=/opt/pdf-overlay-api
ExecStart=/opt/pdf-overlay-api/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
User=www-data

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now pdf-overlay-api
```

## Notes / things worth knowing

- `--workers N` in uvicorn (or a process manager like gunicorn with uvicorn
  workers) will let it handle concurrent requests — this app has no shared
  state so it scales horizontally without any changes.
- Max upload size is capped at 25MB per file in the code (`MAX_FILE_SIZE_MB`
  in `main.py`) — raise/lower as needed, and make sure your reverse proxy's
  `client_max_body_size` (nginx) matches.
- If the target page is smaller than where you place the image, PyMuPDF will
  just clip it rather than error — nothing crashes, but double check your
  coordinates match the actual page size if the image doesn't show fully.
- Everything runs in memory (no temp files written to disk), so there's
  nothing to clean up between requests.

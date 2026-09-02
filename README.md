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

## Authentication (JWT, no database)

The API is now protected. Clients log in once with a username + password
(stored as a bcrypt hash in an environment variable — no database) and get
back a JWT they use on every subsequent request.

- `GET /health` — public, no auth needed.
- `POST /token` — public, this is the login endpoint.
- `POST /place-image` — **protected**, requires `Authorization: Bearer <token>`.

### 1. Generate a password hash for each client

```bash
python3 generate_password_hash.py
```

It'll ask for a username and password and print a JSON snippet like:
```json
{"client1": "$2b$12$Ja8Qu1h9m5kpEmeOxJaT9ekDC.bjbucH7CYWc6Jb.viRibwoJe7I2"}
```

Run it again for each additional client and merge the keys into one object.

### 2. Set environment variables

Copy `.env.example` to `.env` and fill in:

```bash
JWT_SECRET_KEY=<generate with: python3 -c "import secrets; print(secrets.token_hex(32))">
JWT_EXPIRE_MINUTES=120
USERS_JSON={"client1": "$2b$12$Ja8Qu1h9m5kpEmeOxJaT9ekDC.bjbucH7CYWc6Jb.viRibwoJe7I2", "client2": "$2b$12$..."}
```

The app refuses to start if `JWT_SECRET_KEY` or `USERS_JSON` are missing, so
you can't accidentally deploy it unprotected.

### 3. Give each client their username + password

That's what they use to log in. They should **not** be given the bcrypt hash
— just the plaintext username/password you chose in step 1.

### 4. Client usage: log in, then call the API

```bash
# Step 1: log in to get a token (repeat this whenever the token expires)
curl -X POST https://api.yourdomain.com/token \
  -d "username=client1&password=their-actual-password"
# -> {"access_token": "eyJ...", "token_type": "bearer"}

# Step 2: use the token on the real endpoint
curl -o output.pdf \
  -H "Authorization: Bearer eyJ..." \
  -F "pdf=@input.pdf" -F "png=@logo.png" \
  -F "x=50" -F "y=50" -F "width=150" -F "pages=all" \
  https://api.yourdomain.com/place-image
```

Tokens expire after `JWT_EXPIRE_MINUTES` (default 120 minutes) — clients just
call `/token` again to get a new one. There's no logout/revoke endpoint since
there's no database to track sessions in; to fully revoke access for one
client, remove them from `USERS_JSON` and restart the service (their existing
un-expired tokens will then also be rejected, since the code checks the
username still exists in `USERS_JSON` on every request).

## Run it locally

```bash
pip install -r requirements.txt
cp .env.example .env   # then edit .env with your real JWT_SECRET_KEY and USERS_JSON
python3 generate_password_hash.py   # do this first to create your first user
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

### `POST /token`

Login. Send as `application/x-www-form-urlencoded`:

| Field      | Description         |
|------------|----------------------|
| `username` | Client's username    |
| `password` | Client's password    |

Response: `{"access_token": "...", "token_type": "bearer"}`, or 401 if wrong.

### `POST /place-image` *(requires auth)*

Header required: `Authorization: Bearer <token from /token>`

Multipart form fields:

| Field    | Type   | Required | Description                                                             |
|----------|--------|----------|---------------------------------------------------------------------------|
| `pdf`    | file   | yes      | The base PDF                                                             |
| `png`    | file   | yes      | The PNG to place                                                         |
| `x`      | float  | conditionally* | X position (points). *Required unless `anchor_text` is set, where it's an optional horizontal nudge (default 0) |
| `y`      | float  | conditionally* | Y position (points). *Required unless `anchor_text` is set, where it's an optional vertical nudge (default 0)   |
| `width`  | float  | yes      | Width of the placed image (points)                                      |
| `height` | float  | no       | Height (points). **Omit it, leave it blank, or send `auto`** to auto-preserve the PNG's aspect ratio (no distortion) |
| `pages`  | string | no       | Which page(s) to stamp. Default `1`. See formats below                  |
| `origin` | string | no       | `top-left` (default) or `bottom-left`. **Ignored when `anchor_text` is set.** |
| `anchor_text` | string | no  | Exact text to find on the page and position relative to (see below)     |
| `anchor_position` | string | no | `above` (default), `below`, `left`, `right`, or `on` (directly over the text) |
| `anchor_gap` | float | no    | Gap in points between the text and the image (default `6`)              |
| `anchor_occurrence` | string | no | `first` (default, topmost match only) or `all` (every match on that page) |
| `anchor_case_sensitive` | bool | no | `false` (default) or `true` — whether `anchor_text` must match case-for-case |

### Text-anchored placement

Instead of giving absolute coordinates, you can tell the API to find a
specific piece of text on the page (e.g. `"Signature and Stamp of Agent"`)
and place the image relative to it:

```bash
curl -o output.pdf \
  -H "Authorization: Bearer $TOKEN" \
  -F "pdf=@contract.pdf" -F "png=@signature.png" \
  -F "width=150" \
  -F "anchor_text=Signature and Stamp of Agent" \
  -F "anchor_position=above" \
  -F "pages=all" \
  http://localhost:8000/place-image
```

That places a 150pt-wide signature image, height auto-scaled, just above
every occurrence of that phrase on every page it appears on.

**`anchor_position` options** (relative to the found text's bounding box):
- `above` — image sits just above the text, left-aligned to it (good for "sign above this line")
- `below` — image sits just below the text
- `left` — image sits just to the left of the text
- `right` — image sits just to the right of the text
- `on` — image is placed directly over the text's own position (e.g. to stamp exactly where a placeholder word is)

**Nudging the position:** `x` and `y` become optional fine-tuning offsets
in anchor mode — e.g. `x=10&y=-5` shifts the auto-computed position 10pt
right and 5pt up from wherever it would otherwise land.

**Multiple matches:** if the text appears more than once on a page,
`anchor_occurrence=first` (default) only uses the top-most match;
`anchor_occurrence=all` stamps the image at every occurrence on that page.

**If the text isn't found:** the whole request fails with a 400 listing
exactly which page(s) didn't contain it — nothing is partially modified.
This also means with `pages=all`, every target page must contain the text;
if you only want pages that have the signature line to get stamped, you'll
need to pick those pages explicitly (e.g. `pages=1,3,5`) rather than `all`.


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
EnvironmentFile=/opt/pdf-overlay-api/.env
ExecStart=/opt/pdf-overlay-api/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
User=www-data

[Install]
WantedBy=multi-user.target
```

`EnvironmentFile` loads `JWT_SECRET_KEY`, `USERS_JSON`, etc. from your `.env`
file at startup. Make sure `/opt/pdf-overlay-api/.env` exists and is only
readable by `www-data`/root (`chmod 600 .env`) since it contains secrets.

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

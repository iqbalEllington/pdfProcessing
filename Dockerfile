FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py auth.py .

EXPOSE 8000

# JWT_SECRET_KEY and USERS_JSON must be provided at runtime, e.g.:
#   docker run --env-file .env -p 8000:8000 pdf-overlay-api
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

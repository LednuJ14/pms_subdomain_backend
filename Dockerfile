# Multi-stage build for Flask PMS Subdomain Backend
FROM python:3.12-slim AS builder
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt \
    && pip install --no-cache-dir --prefix=/install gunicorn

FROM python:3.12-slim AS runner
WORKDIR /app

COPY --from=builder /install /usr/local

COPY . .

# Create uploads directory
RUN mkdir -p instance/uploads

ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1
EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--timeout", "120", "run:app"]

FROM python:3.11-slim

# System dependencies: poppler (PDF -> image for OCR), tesseract with the
# French language pack (OCR itself), libreoffice-calc (Excel -> PDF
# conversion - the smaller Calc-only package, not the full office suite).
# Baking these into the image is the whole point of containerizing this
# service: every one of these was a real PATH/installer headache on
# Windows during development. In the image, they just work, every time,
# on every machine, forever.
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-fra \
    libreoffice-calc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8001

# Shell form (not exec/JSON form) so $PORT actually gets substituted.
# Render assigns its own PORT at runtime and expects the app to listen
# on it - a hardcoded port would silently break on Render while still
# working fine locally, which is exactly the kind of gap worth closing
# before it becomes a confusing production-only bug.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8001}

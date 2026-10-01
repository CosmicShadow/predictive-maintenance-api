# API + drift job image. Serving only: no pandas/scikit-learn, CPU-only PyTorch.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Microsoft ODBC Driver 18, needed by pyodbc to reach SQL Server / Azure SQL.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
      | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list \
      -o /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc \
 && apt-get purge -y gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU wheel of PyTorch is ~200 MB instead of ~2 GB for the CUDA build.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml ./
COPY src/ src/
RUN pip install .

# Run as an unprivileged user.
RUN useradd --system --uid 10001 --no-create-home appuser \
 && mkdir -p /app/model-cache && chown appuser /app/model-cache
USER 10001
ENV MODEL_CACHE_DIR=/app/model-cache

EXPOSE 8000
CMD ["uvicorn", "rul.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

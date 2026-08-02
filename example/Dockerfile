FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
COPY cas_hosting_adapter ./cas_hosting_adapter
COPY example ./example
RUN pip install --no-cache-dir .
RUN useradd --create-home --uid 10001 appuser
USER 10001
ENTRYPOINT ["python", "-m", "example.agent"]

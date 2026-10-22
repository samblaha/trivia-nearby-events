FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY events_api.py .

ENV EVENTS_API_HOST=0.0.0.0
# Render sets PORT at runtime (often 10000).
ENV PORT=10000

EXPOSE 10000

CMD ["python", "events_api.py"]

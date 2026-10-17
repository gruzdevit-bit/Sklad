FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
COPY . .
ENV DB_PATH=/app/data/sklad.db
EXPOSE 8080
CMD ["python", "app.py"]

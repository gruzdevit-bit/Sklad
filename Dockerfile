FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 SKLAD_DB=/app/data/sklad.db PORT=8080
COPY server.py ./
RUN mkdir -p /app/data
EXPOSE 8080
CMD ["python", "server.py"]

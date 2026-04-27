# ---- 阶段 1: 构建前端 ----
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 阶段 2: 运行后端 ----
FROM python:3.13-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py api_routes.py llm_client.py debate.py scrapers.py \
     rdt_client.py st_client.py quote_extractor.py web_search.py \
     feishu_client.py session_context.py ./
COPY prompts/ ./prompts/
COPY data/demo/ ./data/demo/

COPY --from=frontend-build /app/frontend/dist ./frontend/dist

RUN mkdir -p data/cache data/reports data/poc_evaluations data/trending data/sessions

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/config/status || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]

# 多阶段：先把前端打成静态文件，再放进 Python 运行时镜像
FROM node:22-alpine AS web
WORKDIR /w
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    ACCUCOUNT_DATA=/data

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=web /w/dist /app/frontend/dist

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8090

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090", "--proxy-headers"]

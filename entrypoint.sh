#!/bin/sh
# 容器入口：启动时先安装依赖，再启动 API 服务。
set -eu

echo "[entrypoint] 正在安装 Python 依赖（requirements.txt）..."
pip install --no-cache-dir -r /app/requirements.txt

PORT="${PORT:-8000}"
echo "[entrypoint] 启动 uvicorn，监听 0.0.0.0:${PORT} ..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"

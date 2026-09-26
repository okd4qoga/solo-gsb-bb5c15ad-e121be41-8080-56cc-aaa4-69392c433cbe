FROM python:3.11-slim

WORKDIR /app

# 依赖清单与入口脚本（依赖在“容器启动时”由 entrypoint 安装，而非构建时）
COPY requirements.txt entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

# 应用代码
COPY app /app/app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]

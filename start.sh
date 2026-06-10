#!/bin/bash
# FA Agent 已迁移到 docker compose 运行（见 docker-compose.prod.yml）。
# 启动/重建全部服务：fastapi + celery_worker + celery_beat。
# 改了 requirements.txt / Dockerfile 才需要加 --build。
cd /root/fa-agent || exit 1
docker compose -f docker-compose.prod.yml up -d "$@"
docker compose -f docker-compose.prod.yml ps

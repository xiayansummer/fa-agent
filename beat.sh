#!/bin/bash
# 重启 celery beat 容器（旧的 nohup+pkill 方式已废弃，会误杀容器进程）。
cd /root/fa-agent || exit 1
docker compose -f docker-compose.prod.yml restart celery_beat
docker compose -f docker-compose.prod.yml logs --tail 10 celery_beat

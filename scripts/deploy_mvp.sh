#!/bin/bash
# MVP 部署脚本
# 用法：bash scripts/deploy_mvp.sh
# 前提：已经把 /Users/summer/fa-agent 拉到最新 master

set -e

SERVER=39.107.14.53
SERVER_PWD='Investarget@2021!'
REMOTE_PATH=/root/fa-agent

echo "=== 1. 备份当前服务器代码 ==="
sshpass -p "$SERVER_PWD" ssh -o StrictHostKeyChecking=no root@$SERVER \
  "cd $REMOTE_PATH && tar czf ../fa-agent-backup-$(date +%Y%m%d-%H%M).tar.gz backend alembic"

echo "=== 2. 上传 backend 代码（覆盖） ==="
sshpass -p "$SERVER_PWD" scp -o StrictHostKeyChecking=no -r \
  /Users/summer/fa-agent/backend/ \
  root@$SERVER:$REMOTE_PATH/

echo "=== 3. 上传 alembic 迁移 ==="
sshpass -p "$SERVER_PWD" scp -o StrictHostKeyChecking=no -r \
  /Users/summer/fa-agent/alembic/ \
  root@$SERVER:$REMOTE_PATH/

echo "=== 4. 上传 scripts ==="
sshpass -p "$SERVER_PWD" scp -o StrictHostKeyChecking=no -r \
  /Users/summer/fa-agent/scripts/ \
  root@$SERVER:$REMOTE_PATH/

echo "=== 5. 在服务器上 ==="
echo "    a. 安装新依赖 (cryptography)"
echo "    b. 添加 TOKEN_ENCRYPT_KEY 到 /root/fa-agent/backend/.env"
echo "    c. 运行 alembic upgrade head"
echo "    d. 重启 uvicorn + celery worker + beat"
echo
echo "请人工执行以下命令："
cat <<'CMDS'
sshpass -p 'Investarget@2021!' ssh root@39.107.14.53 'bash -s' <<'REMOTE'
cd /root/fa-agent/backend
/root/fa-agent/venv/bin/pip install -r requirements.txt
# 生成 token encryption key（首次部署），后续部署不要重新生成
if ! grep -q "^TOKEN_ENCRYPT_KEY=" .env; then
  KEY=$(/root/fa-agent/venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
  echo "TOKEN_ENCRYPT_KEY=$KEY" >> .env
  echo "已生成并写入新的 TOKEN_ENCRYPT_KEY"
  echo "请把此 key 备份到 1Password 或类似工具：$KEY"
fi
cd /root/fa-agent
/root/fa-agent/venv/bin/alembic upgrade head
bash start.sh      # 重启 uvicorn
bash worker.sh     # 重启 celery worker
bash beat.sh       # 重启 celery beat
sleep 5
curl -s https://agentapi.investarget.com/health
REMOTE
CMDS

echo "=== 部署完成提示 ==="
echo "1. 用 admin curl /api/admin/users 录入第一个 IR 用户（带手机号）"
echo "2. 在小程序开发者工具里测试登录 → 绑定 → 4 个工作流"

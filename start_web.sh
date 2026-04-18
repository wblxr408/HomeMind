#!/bin/bash
# HomeMind 中央指令器启动脚本

echo "正在激活 conda used_pytorch 环境..."
source ~/anaconda3/etc/profile.d/conda.sh  # 调整路径
conda activate used_pytorch

cd "$(dirname "$0")"

echo "当前 Python:"
python --version
echo

# 安装依赖
echo "[安装依赖] flask flask-cors flask-socketio..."
pip install -q flask flask-cors flask-socketio python-socketio eventlet 2>/dev/null

echo
echo "=================================================="
echo "   HomeMind 中央指令器"
echo "=================================================="
echo
echo "  访问地址:"
echo "    - 控制面板: http://localhost:5000"
echo "    - API 状态:  http://localhost:5000/api/status"
echo
echo "  按 Ctrl+C 停止服务"
echo
echo "=================================================="
echo

python run_web.py

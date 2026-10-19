#!/usr/bin/env bash
# ==============================================================================
# nanoMuse Gateway 管理脚本 (基于 uv 运行环境)
# 用法:
#   ./start.sh start   - 后台启动网关服务
#   ./start.sh stop    - 停止网关服务
#   ./start.sh restart - 重启网关服务
#   ./start.sh status  - 查看网关运行状态
#   ./start.sh logs    - 实时查看网关日志
# ==============================================================================

set -e

# 定位脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PID_FILE="$SCRIPT_DIR/gateway.pid"
LOG_FILE="$SCRIPT_DIR/gateway.log"
PORT="${PORT:-8790}"
HOST="${HOST:-0.0.0.0}"

# 1. 检查 uv 环境
check_uv() {
    if ! command -v uv &> /dev/null; then
        echo -e "\033[31m[错误] 系统中未找到 uv 环境！\033[0m"
        echo -e "请通过官方命令安装 uv:"
        echo -e "  \033[36mcurl -LsSf https://astral.sh/uv/install.sh | sh\033[0m"
        echo -e "安装后请执行 source ~/.bashrc 重新加载环境变量。"
        exit 1
    fi
}

# 2. 检查服务是否已在运行
is_running() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

# 3. 启动服务
start_gateway() {
    check_uv

    if is_running; then
        echo -e "\033[33m[警告] nanoMuse Gateway 已经在运行中 (PID: $(cat "$PID_FILE"))\033[0m"
        return 0
    fi

    echo -e "\033[32m[启动] 正在通过 uv 启动 nanoMuse Gateway...\033[0m"
    echo -e "  - 监听地址: http://$HOST:$PORT"
    if [ -n "$GATEWAY_TOKEN" ]; then
        echo -e "  - 认证模式: 密钥保护模式 (GATEWAY_TOKEN 已设置)"
    else
        echo -e "  - 认证模式: 免密直连模式 (所有设备免登互通)"
    fi

    # 检查并自动释放端口占用
    if command -v fuser >/dev/null 2>&1; then
        if fuser "$PORT/tcp" >/dev/null 2>&1; then
            echo -e "\033[33m[提示] 检测到端口 $PORT 被占用，正在自动释放...\033[0m"
            fuser -k -9 "$PORT/tcp" >/dev/null 2>&1 || true
            sleep 0.5
        fi
    elif command -v lsof >/dev/null 2>&1; then
        OCCUPIED_PID=$(lsof -t -i:"$PORT" 2>/dev/null || true)
        if [ -n "$OCCUPIED_PID" ]; then
            echo -e "\033[33m[提示] 检测到端口 $PORT 被进程 (PID: $OCCUPIED_PID) 占用，正在自动释放...\033[0m"
            kill -9 $OCCUPIED_PID 2>/dev/null || true
            sleep 0.5
        fi
    fi

    # 使用 uv run 后台运行 (兼容运行环境)
    nohup uv run --no-project --python 3.12 --with fastapi --with "uvicorn[standard]" --with websockets python gateway.py > "$LOG_FILE" 2>&1 &
    PID=$!
    echo $PID > "$PID_FILE"

    sleep 1
    if ps -p "$PID" > /dev/null 2>&1; then
        echo -e "\033[32m[成功] 网关已成功启动！PID: $PID\033[0m"
        echo -e "运行日志输出在: \033[36m$LOG_FILE\033[0m"
        echo -e "健康检查接口: \033[36mhttp://127.0.0.1:$PORT/healthz\033[0m"
    else
        echo -e "\033[31m[失败] 网关未能成功启动，请查看日志: $LOG_FILE\033[0m"
        cat "$LOG_FILE"
        exit 1
    fi
}

# 4. 停止服务
stop_gateway() {
    if is_running; then
        PID=$(cat "$PID_FILE")
        echo -e "\033[33m[停止] 正在停止网关服务 (PID: $PID)...\033[0m"
        kill "$PID" 2>/dev/null || true
        for i in {1..10}; do
            if ! ps -p "$PID" > /dev/null 2>&1; then
                break
            fi
            sleep 0.5
        done
        rm -f "$PID_FILE"
        echo -e "\033[32m[成功] 网关已停止。\033[0m"
    else
        echo -e "\033[33m[提示] 网关未在运行。\033[0m"
        rm -f "$PID_FILE"
    fi
}

# 5. 查看状态
status_gateway() {
    if is_running; then
        PID=$(cat "$PID_FILE")
        echo -e "\033[32m[状态] nanoMuse Gateway 运行正常。\033[0m"
        echo -e "  - 进程 PID: $PID"
        echo -e "  - 监听端口: $PORT"
        echo -e "  - 日志文件: $LOG_FILE"
        
        # 尝试使用 curl 探测健康接口
        if command -v curl &> /dev/null; then
            HEALTH=$(curl -s "http://127.0.0.1:$PORT/healthz" || echo "")
            if [ -n "$HEALTH" ]; then
                echo -e "  - 状态详情: $HEALTH"
            fi
        fi
    else
        echo -e "\033[31m[状态] nanoMuse Gateway 未在运行。\033[0m"
    fi
}

# 6. 查看日志
view_logs() {
    if [ -f "$LOG_FILE" ]; then
        tail -n 50 -f "$LOG_FILE"
    else
        echo -e "\033[33m[提示] 暂无日志文件: $LOG_FILE\033[0m"
    fi
}

# 命令行路由
case "$1" in
    start)
        start_gateway
        ;;
    stop)
        stop_gateway
        ;;
    restart)
        stop_gateway
        sleep 1
        start_gateway
        ;;
    status)
        status_gateway
        ;;
    logs)
        view_logs
        ;;
    *)
        echo "用法: $0 {start|stop|restart|status|logs}"
        echo "环境变量可选参数:"
        echo "  PORT=8790             - 指定监听端口 (默认 8790)"
        echo "  GATEWAY_TOKEN=xxx     - 启用访问密钥 (默认免密)"
        exit 1
        ;;
esac

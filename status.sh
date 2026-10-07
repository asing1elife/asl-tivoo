#!/bin/bash
# Interactive controller for the current user's Tivoo LaunchAgent.
set -u

TIVOO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
TIVOO_LABEL='local.tivoo.codex-quota'
TIVOO_DOMAIN="gui/$(id -u)"
TIVOO_TARGET="$TIVOO_DOMAIN/$TIVOO_LABEL"
TIVOO_PLIST="$HOME/Library/LaunchAgents/$TIVOO_LABEL.plist"
TIVOO_LOG="$TIVOO_ROOT/output/service.log"

if [[ "$(uname -s)" != Darwin ]]; then
    echo '此脚本仅支持 macOS。' >&2
    exit 1
fi

is_loaded() {
    launchctl print "$TIVOO_TARGET" >/dev/null 2>&1
}

show_status() {
    local snapshot state pid
    printf '\n=== Tivoo 运行状态 ===\n'
    if snapshot="$(launchctl print "$TIVOO_TARGET" 2>/dev/null)"; then
        state="$(printf '%s\n' "$snapshot" | awk '$1 == "state" && $2 == "=" {print $3; exit}')"
        pid="$(printf '%s\n' "$snapshot" | awk '$1 == "pid" && $2 == "=" {print $3; exit}')"
        if [[ "$state" == running && -n "$pid" ]]; then
            printf '服务：运行中（PID %s）\n' "$pid"
            printf '已运行：%s\n' "$(ps -p "$pid" -o etime= | xargs)"
        else
            printf '服务：已加载，当前未运行（%s）\n' "${state:-未知状态}"
            printf '%s\n' "$snapshot" | awk '/last exit code =/ {sub(/^[ \t]+/, ""); print; exit}'
        fi
    elif [[ -f "$TIVOO_PLIST" ]]; then
        echo '服务：已停止（配置已保留）'
    else
        echo '服务：未安装'
    fi
    if [[ -f "$TIVOO_PLIST" ]] && [[ "$(/usr/libexec/PlistBuddy -c 'Print :RunAtLoad' "$TIVOO_PLIST" 2>/dev/null)" == true ]]; then
        echo '登录自启：已配置（菜单停止仅影响本次登录）'
    else
        echo '登录自启：未配置'
    fi
    printf '日志：%s\n' "$TIVOO_LOG"
    if [[ -s "$TIVOO_LOG" ]]; then
        echo '最近日志（历史记录，不代表此刻已连接音响）：'
        tail -n 4 "$TIVOO_LOG"
    else
        echo '暂无运行日志。'
    fi
}

start_service() {
    local mac
    if is_loaded; then
        # Also restarts a loaded agent which currently has no process.
        launchctl kickstart "$TIVOO_TARGET" || return 1
        echo '服务已启动；若原本正在运行，则保持运行。'
    elif [[ -f "$TIVOO_PLIST" ]]; then
        launchctl bootstrap "$TIVOO_DOMAIN" "$TIVOO_PLIST" || return 1
        echo '已使用保存的设备配置启动。'
    else
        if [[ ! -x "$TIVOO_ROOT/.venv/bin/python" || ! -x "$TIVOO_ROOT/build/tivoo_cmd" ]]; then
            echo '请先在项目目录运行 ./setup.sh 完成安装。' >&2
            return 1
        fi
        mac="${TIVOO_MAC:-}"
        if [[ -z "$mac" ]]; then
            read -r -p '首次启动，请输入音响蓝牙地址：' mac || return 1
        fi
        "$TIVOO_ROOT/.venv/bin/python" "$TIVOO_ROOT/service.py" start --mac "$mac" || return 1
    fi
}

while true; do
    show_status
    printf '\n1 启动\n2 重启\n3 停止\n回车刷新状态，q 退出\n'
    read -r -p '请选择：' choice || { printf '\n'; break; }
    case "$choice" in
        1)
            start_service || echo '启动失败，请查看上方错误。' >&2
            sleep 1
            ;;
        2)
            if is_loaded; then
                echo '正在重启，短时间内重复操作可能需要等待约 30 秒……'
                if launchctl kickstart -k "$TIVOO_TARGET"; then
                    echo '已请求重启服务。'
                else
                    echo '重启失败，请查看上方错误。' >&2
                fi
            else
                start_service || echo '启动失败，请查看上方错误。' >&2
            fi
            sleep 1
            ;;
        3)
            if is_loaded; then
                if launchctl bootout "$TIVOO_TARGET"; then
                    echo '服务已停止，屏幕会保留最后一次画面。'
                else
                    echo '停止失败，请查看上方错误。' >&2
                fi
            else
                echo '服务已经停止。'
            fi
            ;;
        '') ;;
        q|Q|0) break ;;
        *) echo '请输入 1、2、3；回车刷新，q 退出。' ;;
    esac
done

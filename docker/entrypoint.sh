#!/bin/bash
# shellcheck shell=bash
# shellcheck disable=SC2016
# shellcheck disable=SC2155

Green="\033[32m"
Red="\033[31m"
Yellow='\033[33m'
Font="\033[0m"
INFO="[${Green}INFO${Font}]"
ERROR="[${Red}ERROR${Font}]"
WARN="[${Yellow}WARN${Font}]"
function INFO() {
    echo -e "${INFO} ${1}"
}
function ERROR() {
    echo -e "${ERROR} ${1}"
}
function WARN() {
    echo -e "${WARN} ${1}"
}

# 校正设置目录
CONFIG_DIR="${CONFIG_DIR:-/config}"

# 更改 embytoolkit 用户ID和组ID
INFO "→ 设置用户权限..."
# ★★★ 修改点 1: 将 groupmod 的目标从 embyactor 改为 embytoolkit ★★★
groupmod -o -g "${PGID}" embytoolkit
# ★★★ 修改点 2: 将 usermod 的目标从 embyactor 改为 embytoolkit ★★★
usermod -o -u "${PUID}" embytoolkit

# 采用“精准 chown”策略，避免对大量小文件进行递归操作
INFO "→ 快速设置持久化目录权限..."

# 确保 HOME 和 CONFIG_DIR 目录本身是可写的
# ★★★ 修改点 3: 将 chown 的目标从 embyactor 改为 embytoolkit ★★★
chown embytoolkit:embytoolkit "${HOME}" "${CONFIG_DIR}"

# 使用 find 命令来智能地、非递归地修改权限
if [ -d "${CONFIG_DIR}" ]; then
    # ★★★ 修改点 4: 将 find -exec chown 的目标也改为 embytoolkit ★★★
    find "${CONFIG_DIR}" -maxdepth 1 -mindepth 1 -exec chown embytoolkit:embytoolkit {} +
fi

# 设置权限掩码
umask "${UMASK}"

# 启动应用
INFO "→ 启动应用服务..."
# ★★★ 修改点 5: 使用 gosu 切换到新用户 embytoolkit ★★★
exec dumb-init gosu embytoolkit:embytoolkit python3 /app/web_app.py
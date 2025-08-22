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

# 更改 embyactor 用户ID和组ID
INFO "→ 设置用户权限..."
groupmod -o -g "${PGID}" embyactor
usermod -o -u "${PUID}" embyactor

# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
# ★★★ 核心优化：只对需要写入的目录进行递归权限修改 ★★★
# ★★★ 移除了 /app 目录，因为应用代码只需要读取权限 ★★★
INFO "→ 更改持久化目录权限 (这可能需要一些时间)..."
chown -R embyactor:embyactor \
    "${HOME}" \
    "${CONFIG_DIR}"
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★

# 设置权限掩码
umask "${UMASK}"

# 启动应用
INFO "→ 启动应用服务..."
# ★★★ 推荐实践：明确指定 python 解释器的路径 ★★★
exec dumb-init gosu embyactor:embyactor python3 /app/web_app.py
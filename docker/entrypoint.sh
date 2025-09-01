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

# 1. 设置用户和权限 (这部分不变)
INFO "→ 设置用户权限..."
groupmod -o -g "${PGID}" embytoolkit
usermod -o -u "${PUID}" embytoolkit
INFO "→ 快速设置持久化目录权限..."
chown embytoolkit:embytoolkit "${HOME}" "${CONFIG_DIR}"
if [ -d "${CONFIG_DIR}" ]; then
    find "${CONFIG_DIR}" -maxdepth 1 -mindepth 1 -exec chown embytoolkit:embytoolkit {} +
fi
umask "${UMASK}"

# 2. 生成 Nginx 配置文件
INFO "→ 生成 Nginx 配置文件..."
# ★★★ 核心修正 ★★★
# 移除 gosu，让此命令以 root 身份运行，这样它才有权限写入 /etc/nginx 目录
python3 /app/web_app.py generate-nginx-config
INFO "→ Nginx 配置文件生成完毕。"

# 3. 启动 Nginx 服务
# Nginx 也需要以 root 身份启动
INFO "→ 在后台启动 Nginx 服务..."
nginx -g "daemon off;" &

# 4. 启动主应用
# 在这里，我们才使用 gosu 将权限降级为普通用户，以保证应用运行时的安全
INFO "→ 启动 Emby Toolkit 主应用服务..."
exec dumb-init gosu embytoolkit:embytoolkit python3 /app/web_app.py
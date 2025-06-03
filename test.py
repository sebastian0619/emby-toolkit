import requests
import json

# ====== 配置区域 ======
EMBY_URL = 'http://192.168.31.163:8096'
API_KEY = '8aca437c3df14b13a30ddb6ff1f98883'
ITEM_ID = '463793'
USER_ID = 'e274948e690043c9a86c9067ead73af4'

# 添加的演员信息（可以添加多个）
actors_to_add = [
    {
        "Name": "阿汤哥",
        "Role": "钢铁侠",
        "ProviderIds": {
            "Tmdb": "500"
        },
        "Type": "Actor"
    }
]

# 请求头
headers = {
    'Content-Type': 'application/json',
    'X-Emby-Token': API_KEY
}

# 获取原始数据（用于构造 PUT 请求体）
url_get = f"{EMBY_URL}/Items/{ITEM_ID}?UserId={USER_ID}"
response = requests.get(url_get, headers=headers)

if response.status_code != 200:
    print("获取元数据失败：", response.status_code, response.text)
    exit()

item_metadata = response.json()

# 构造新的 PUT 请求体（必须包含必需字段，否则返回 400）
update_payload = {
    "Name": item_metadata.get("Name"),
    "Overview": item_metadata.get("Overview", ""),
    "OfficialRating": item_metadata.get("OfficialRating", ""),
    "CommunityRating": item_metadata.get("CommunityRating", 0),
    "ProductionYear": item_metadata.get("ProductionYear"),
    "Genres": item_metadata.get("Genres", []),
    "Taglines": item_metadata.get("Taglines", []),
    "Studios": item_metadata.get("Studios", []),
    "People": actors_to_add,
    "LockData": True  # 可选：是否锁定元数据
}

# PUT 更新元数据（注意方法是 PUT，不是 POST）
url_update = f"{EMBY_URL}/Items/{ITEM_ID}"
response = requests.put(url_update, headers=headers, data=json.dumps(update_payload))

if response.status_code == 204:
    print("演员添加成功！")
else:
    print("修改失败：", response.status_code, response.text)

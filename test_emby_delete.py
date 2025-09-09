# test_emby_delete.py
import requests

# --- ▼▼▼ 请在这里填入你自己的信息 ▼▼▼ ---
EMBY_URL = "http://192.168.31.163:8096"  # 你的 Emby 服务器地址
API_KEY = "fca82e1dbd85479ebbea2589228e5c4a"   # 你的 API Key
USER_ID = "8bcbed26e7e64ec2a6fb895b1579359b"              # 你的 User ID (确保这个也填了)
ITEM_ID_TO_DELETE = "1415"                 # 找一个你确定可以删除的媒体项ID
# --- ▲▲▲ 请在这里填入你自己的信息 ▲▲▲ ---

print("--- 开始 Emby 删除接口终极测试 ---")

# --- 方案一：POST /Items/{Id}/Delete (社区推荐) ---
url1 = f"{EMBY_URL}/Items/{ITEM_ID_TO_DELETE}/Delete"
headers1 = {'X-Emby-Token': API_KEY}
data1 = {'UserId': USER_ID}
print(f"\n[1] 正在测试: POST {url1} (带UserId in data)")
try:
    r = requests.post(url1, headers=headers1, data=data1, timeout=10)
    print(f"  -> 状态码: {r.status_code}")
    print(f"  -> 响应: {r.text[:200]}")
    r.raise_for_status()
    print("  -> ✅ 成功!")
except Exception as e:
    print(f"  -> ❌ 失败: {e}")

# --- 方案二：DELETE /Items/{Id} (curl 示例1) ---
url2 = f"{EMBY_URL}/Items/{ITEM_ID_TO_DELETE}"
headers2 = {'X-Emby-Token': API_KEY}
params2 = {'Recursive': 'true'}
print(f"\n[2] 正在测试: DELETE {url2} (Header认证)")
try:
    r = requests.delete(url2, headers=headers2, params=params2, timeout=10)
    print(f"  -> 状态码: {r.status_code}")
    print(f"  -> 响应: {r.text[:200]}")
    r.raise_for_status()
    print("  -> ✅ 成功!")
except Exception as e:
    print(f"  -> ❌ 失败: {e}")

# --- 方案三：DELETE /Items/{Id} (curl 示例2) ---
url3 = f"{EMBY_URL}/Items/{ITEM_ID_TO_DELETE}"
params3 = {'api_key': API_KEY, 'Recursive': 'true'}
print(f"\n[3] 正在测试: DELETE {url3} (URL参数认证)")
try:
    r = requests.delete(url3, params=params3, timeout=10)
    print(f"  -> 状态码: {r.status_code}")
    print(f"  -> 响应: {r.text[:200]}")
    r.raise_for_status()
    print("  -> ✅ 成功!")
except Exception as e:
    print(f"  -> ❌ 失败: {e}")

# --- 方案四：POST /Items/{Id}/Delete (无 UserId) ---
url4 = f"{EMBY_URL}/Items/{ITEM_ID_TO_DELETE}/Delete"
headers4 = {'X-Emby-Token': API_KEY}
print(f"\n[4] 正在测试: POST {url4} (无UserId)")
try:
    r = requests.post(url4, headers=headers4, timeout=10)
    print(f"  -> 状态码: {r.status_code}")
    print(f"  -> 响应: {r.text[:200]}")
    r.raise_for_status()
    print("  -> ✅ 成功!")
except Exception as e:
    print(f"  -> ❌ 失败: {e}")

print("\n--- 测试结束 ---")
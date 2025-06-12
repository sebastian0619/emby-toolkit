import requests
import json

# --- 请在此处配置你的 Emby 信息 ---
EMBY_SERVER_URL = "http://192.168.31.163:8096"  # 替换为你的 Emby 服务器地址和端口
EMBY_API_KEY = "8aca437c3df14b13a30ddb6ff1f98883"             # 替换为你在 Emby 中生成的 API 密钥
ACTOR_NAME = "元华"                      # 替换为你想查询的演员姓名
# -----------------------------------------

def find_actor_id(server_url, api_key, actor_name):
    """
    第一步：根据演员姓名搜索，获取其 Emby 内部 ID。
    """
    # 构建请求 URL 和参数
    search_url = f"{server_url}/emby/Persons"
    params = {
        'api_key': api_key,
        'SearchTerm': actor_name,
        'Limit': 5  # 限制返回结果数量，以防有重名演员
    }
    
    print(f"🔍 正在搜索演员 '{actor_name}'...")
    
    try:
        response = requests.get(search_url, params=params, timeout=10)
        response.raise_for_status()  # 如果请求失败 (状态码 4xx 或 5xx)，则抛出异常
        
        results = response.json()
        
        if not results.get('Items'):
            print(f"❌ 未找到名为 '{actor_name}' 的演员。")
            return None
            
        # 为简单起见，我们默认选择第一个最相关的结果
        # 在实际应用中，你可能需要让用户从多个结果中选择
        first_result = results['Items'][0]
        actor_id = first_result['Id']
        actor_found_name = first_result['Name']
        
        print(f"✅ 成功找到演员: {actor_found_name} (ID: {actor_id})")
        
        # 如果搜索结果多于一个，给出提示
        if len(results['Items']) > 1:
            print(f"⚠️  注意: 找到了多个同名或相似名称的演员，已自动选择第一个。")

        return actor_id

    except requests.exceptions.RequestException as e:
        print(f"❌ 请求 Emby API 时出错: {e}")
        return None

def get_actor_external_ids(server_url, api_key, actor_id):
    """
    第二步：使用演员的内部 ID 获取其详细信息，包括外部 ID。
    """
    # 构建请求 URL 和参数
    details_url = f"{server_url}/emby/Users/{get_admin_user_id(server_url, api_key)}/Items/{actor_id}"
    # 注意: 获取 Person 详情通常需要一个 UserID 上下文，我们这里动态获取一个管理员用户ID
    # 也可以直接使用 /Persons/{actor_id}，但有时信息不全，用 Items 接口更可靠
    
    params = {
        'api_key': api_key
    }
    
    print(f"📄 正在获取 ID 为 '{actor_id}' 的演员详细信息...")
    
    try:
        response = requests.get(details_url, params=params, timeout=10)
        response.raise_for_status()
        
        actor_details = response.json()
        
        # 外部 ID 存储在 'ProviderIds' 字段中
        provider_ids = actor_details.get('ProviderIds', {})
        
        if not provider_ids:
            print(f"🤷 未找到演员 '{actor_details.get('Name')}' 的外部 ID。")
            return None
            
        return provider_ids

    except requests.exceptions.RequestException as e:
        print(f"❌ 获取演员详细信息时出错: {e}")
        return None

def get_admin_user_id(server_url, api_key):
    """
    辅助函数：获取一个管理员用户的 ID，用于构建 Item 查询 URL。
    """
    users_url = f"{server_url}/emby/Users"
    params = {'api_key': api_key}
    try:
        response = requests.get(users_url, params=params, timeout=5)
        response.raise_for_status()
        users = response.json()
        # 寻找第一个管理员用户
        for user in users:
            if user.get('Policy', {}).get('IsAdministrator'):
                return user['Id']
        # 如果没找到管理员，返回第一个用户
        return users[0]['Id'] if users else None
    except requests.exceptions.RequestException:
        return None


if __name__ == "__main__":
    # 检查配置是否已填写
    if "YOUR_API_KEY_HERE" in EMBY_API_KEY or "http://..." in EMBY_SERVER_URL:
        print("🛑 请先在脚本中配置你的 EMBY_SERVER_URL 和 EMBY_API_KEY。")
    else:
        # 第一步：查找演员的内部 ID
        internal_actor_id = find_actor_id(EMBY_SERVER_URL, EMBY_API_KEY, ACTOR_NAME)
        
        if internal_actor_id:
            print("-" * 30)
            # 第二步：获取该演员的外部 ID
            external_ids = get_actor_external_ids(EMBY_SERVER_URL, EMBY_API_KEY, internal_actor_id)
            
            if external_ids:
                print(f"🎉 成功获取到 '{ACTOR_NAME}' 的外部 ID:")
                # 使用 json.dumps 美化输出
                print(json.dumps(external_ids, indent=4))
                
                # 你也可以单独提取某个 ID
                tmdb_id = external_ids.get('Tmdb')
                imdb_id = external_ids.get('Imdb')
                
                if tmdb_id:
                    print(f"\nTMDB ID: {tmdb_id}")
                if imdb_id:
                    print(f"IMDb ID: {imdb_id}")
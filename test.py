import requests
import json
from urllib.parse import quote

server_url = "http://192.168.31.163:8096"
api_key = "4ddcb17cafaf49a5954838fedf5b9076"
user_id = "e274948e690043c9a86c9067ead73af4"

def check_server_connection():
    """检查服务器连接"""
    try:
        response = requests.get(f"{server_url}/System/Info/Public", timeout=5)
        if response.status_code == 200:
            print(f"✅ 服务器连接正常 (版本: {response.json().get('Version')})")
            return True
        print(f"❌ 服务器返回HTTP {response.status_code}")
    except Exception as e:
        print(f"❌ 连接失败: {str(e)}")
    return False

def search_items(name, item_type=None):
    """搜索项目（支持所有类型或指定类型）"""
    url = f"{server_url}/Items"
    params = {
        "Recursive": "true",
        "SearchTerm": quote(name),
        "api_key": api_key,
        "IncludeItemTypes": item_type if item_type else None
    }
    # 移除None值的参数
    params = {k: v for k, v in params.items() if v is not None}
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            return response.json().get("Items", [])
        print(f"搜索失败: HTTP {response.status_code}")
    except Exception as e:
        print(f"搜索出错: {str(e)}")
    return []

def get_item_details(item_id):
    """获取项目详情（自动尝试多个端点）"""
    endpoints = [
        f"/Users/{user_id}/Items/{item_id}",
        f"/Items/{item_id}",
        f"/Library/Items/{item_id}"
    ]
    
    for endpoint in endpoints:
        url = f"{server_url}{endpoint}"
        params = {"api_key": api_key, "Fields": "All"}
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                return response.json()
            print(f"端点 {endpoint} 返回HTTP {response.status_code}")
        except Exception as e:
            print(f"请求失败: {str(e)}")
    return None

def get_all_libraries():
    """获取所有媒体库信息"""
    url = f"{server_url}/Library/MediaFolders"
    params = {"api_key": api_key}
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            return response.json().get("Items", [])
    except Exception as e:
        print(f"获取媒体库失败: {str(e)}")
    return []

if __name__ == "__main__":
    if not check_server_connection():
        exit()
    
    # 1. 首先检查媒体库
    print("\n📚 媒体库列表:")
    libraries = get_all_libraries()
    for lib in libraries:
        print(f"- {lib.get('Name')} (类型: {lib.get('CollectionType')})")
    
    # 2. 搜索电影（明确指定Movie类型）
    search_name = "阿凡达"  # 改为你要搜索的电影名称
    print(f"\n🔍 正在搜索电影: {search_name}")
    movies = search_items(search_name, "Movie")
    
    if not movies:
        print(f"\n🔍 尝试全库搜索: {search_name}")
        all_items = search_items(search_name)
        movies = [item for item in all_items if item.get("Type") == "Movie"]
    
    if not movies:
        print("\n❌ 未找到匹配的电影")
        print("可能原因:")
        print("1. 电影不在媒体库中")
        print("2. 电影名称不匹配（尝试原英文名或别名）")
        print("3. 电影库未正确扫描")
        print("4. API密钥没有电影库的访问权限")
        exit()
    
    # 3. 显示搜索结果
    print("\n找到的电影:")
    for idx, movie in enumerate(movies, 1):
        print(f"{idx}. ID: {movie['Id']} | 名称: {movie['Name']} | 年份: {movie.get('ProductionYear', '未知')}")
    
    # 4. 获取电影详情
    selected_movie = movies[0]
    print(f"\n📋 获取详细信息: {selected_movie['Name']} (ID: {selected_movie['Id']})")
    details = get_item_details(selected_movie['Id'])
    
    if details:
        print("\n✅ 获取成功！电影信息摘要:")
        print(f"名称: {details.get('Name')}")
        print(f"类型: {details.get('Type')}")
        print(f"年份: {details.get('ProductionYear')}")
        print(f"IMDb ID: {details.get('ProviderIds', {}).get('Imdb', '无')}")
        print(f"概述: {details.get('Overview', '无')[:200]}...")
        
        # 保存完整信息
        filename = f"movie_{details.get('Name')}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(details, f, indent=2, ensure_ascii=False)
        print(f"\n完整信息已保存到 {filename}")
    else:
        print("❌ 获取详细信息失败")
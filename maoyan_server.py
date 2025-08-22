# maoyan_server.py
import logging
from flask import Flask, request, jsonify
from maoyan_fetcher import get_maoyan_rank_titles, match_titles_to_tmdb

# --- 日志记录设置 ---
logging.basicConfig(level=logging.INFO, format='[MaoyanService] [%(asctime)s] [%(levelname)s] - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/fetch', methods=['POST'])
def fetch_maoyan_data():
    """
    接收一个包含抓取参数的POST请求，执行抓取和匹配，并返回结果。
    """
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400

        api_key = data.get('api_key')
        num = data.get('num', 50)
        platform = data.get('platform', 'all')
        types = data.get('types', ['movie'])

        if not api_key:
            return jsonify({"error": "Missing required parameter: api_key"}), 400

        logger.info(f"Received fetch request: platform={platform}, types={types}, num={num}")

        # 1. 从猫眼获取标题 (复用 maoyan_fetcher.py 中的逻辑)
        movie_titles, tv_titles = get_maoyan_rank_titles(types, platform, num)
        
        # 2. 匹配电影
        matched_movies = match_titles_to_tmdb(movie_titles, 'Movie', api_key)
        
        # 3. 匹配剧集/综艺
        matched_series = match_titles_to_tmdb(tv_titles, 'Series', api_key)
        
        # 4. 合并并去重结果
        all_items = matched_movies + matched_series
        unique_items = list({f"{item['type']}-{item['id']}": item for item in all_items}.values())
        
        logger.info(f"Fetch successful. Returning {len(unique_items)} items.")
        return jsonify(unique_items)

    except Exception as e:
        logger.error(f"An error occurred during fetch: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred.", "details": str(e)}), 500

if __name__ == '__main__':
    # 在 Docker 环境中，通常使用 Gunicorn 或类似工具启动，但为了简单，我们直接用 Flask 开发服务器
    # 监听 0.0.0.0 以便容器内外通信
    app.run(host='0.0.0.0', port=5001)
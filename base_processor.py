# file: base_processor.py

# 从共享模块导入所有需要的东西
from processor_shared import (
    logger, constants, utils, AITranslator, DoubanApi,
    DOUBAN_API_AVAILABLE, sqlite3, Optional, Dict, Any, List
)

class BaseProcessor:
    """
    包含所有处理器共享逻辑的基类。
    """
    def __init__(self, config: Dict[str, Any]):
        """
        初始化所有处理器都需要的公共属性。
        注意：logger 现在是从共享模块导入的，无需作为参数传递。
        """
        self.config = config
        self.logger = logger  # 使用共享的 logger 实例
        
        # 共享的初始化逻辑
        self.db_path = self.config.get(constants.DB_FILE_PATH, 'database.db')
        self.stop_event = threading.Event()

        # 初始化共享的 API 客户端
        if DOUBAN_API_AVAILABLE:
            self.douban_api = DoubanApi(
                db_path=self.db_path,
                douban_cookie=self.config.get(constants.CONFIG_OPTION_DOUBAN_COOKIE)
            )
        else:
            self.douban_api = DoubanApi() # 假的实例

        # AI翻译器初始化
        self.ai_translation_enabled = self.config.get(constants.CONFIG_OPTION_AI_TRANSLATION_ENABLED, False)
        if self.ai_translation_enabled:
            self.ai_translator = AITranslator(
                provider=self.config.get(constants.CONFIG_OPTION_AI_PROVIDER),
                api_key=self.config.get(constants.CONFIG_OPTION_AI_API_KEY),
                base_url=self.config.get(constants.CONFIG_OPTION_AI_BASE_URL),
                model=self.config.get(constants.CONFIG_OPTION_AI_MODEL)
            )
        else:
            self.ai_translator = None

    def _get_db_connection(self) -> sqlite3.Connection:
        """共享的数据库连接方法。"""
        return sqlite3.connect(self.db_path)

    def _select_best_role(self, current_role: str, candidate_role: str) -> str:
        """共享的角色选择方法。"""
        # ... 您最终版本的代码 ...
        pass

    # ... 其他所有共享的方法都放在这里 ...

    def stop(self):
        """共享的停止处理方法。"""
        self.logger.info("接收到停止请求，将安全地终止处理...")
        self.stop_event.set()

    def is_stop_requested(self) -> bool:
        """检查是否已请求停止。"""
        return self.stop_event.is_set()
"""应用上下文 — 单例持有配置、缓存、密钥等共享状态"""

import atexit
import json
import os
import re

from .config import load_config, STATE_DIR
from .db_cache import DBCache
from .key_utils import strip_key_metadata
from .messages import find_msg_db_keys


class AppContext:
    """每次 CLI 调用初始化一次，被所有命令共享。"""

    def __init__(self, config_path=None):
        self.cfg = load_config(config_path)
        self.db_dir = self.cfg["db_dir"]
        self.decrypted_dir = self.cfg["decrypted_dir"]
        self.keys_file = self.cfg["keys_file"]

        if not os.path.exists(self.keys_file):
            raise FileNotFoundError(
                f"密钥文件不存在: {self.keys_file}\n"
                "请运行: wechat-cli init"
            )

        with open(self.keys_file, encoding="utf-8") as f:
            self.all_keys = strip_key_metadata(json.load(f))

        # 检查是否有新增分卷消息库 (例如 message_1.db 等) 缺失密钥，若有则自动增量更新
        self._check_and_update_missing_keys()

        self.cache = DBCache(self.all_keys, self.db_dir)
        atexit.register(self.cache.cleanup)

        self.msg_db_keys = find_msg_db_keys(self.all_keys)

        # 确保状态目录存在
        os.makedirs(STATE_DIR, exist_ok=True)

    def _check_and_update_missing_keys(self):
        try:
            from ..keys.common import collect_db_files
            from .key_utils import get_key_info
            db_files, _ = collect_db_files(self.db_dir)
            if any(re.search(r"message_\d+\.db$", rel) and get_key_info(self.all_keys, rel) is None for rel, *_ in db_files):
                from ..keys import extract_keys
                extract_keys(self.db_dir, self.keys_file)
                with open(self.keys_file, encoding="utf-8") as f:
                    self.all_keys = strip_key_metadata(json.load(f))
        except Exception:
            pass

    def display_name_fn(self, username, names):
        from .contacts import display_name_for_username
        return display_name_for_username(username, names, self.db_dir, self.cache, self.decrypted_dir)

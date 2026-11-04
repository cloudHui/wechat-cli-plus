# -*- coding: utf-8 -*-
"""
微信群聊增量总结引擎
支持：消息深度净化清洗（剔除XML与废话）、对接 Gemini/DeepSeek 大模型进行真正的高智商语义总结、按天归档。
"""

import os
import sys
import re
import json
import time
import shutil
import subprocess
from datetime import datetime
from fnmatch import fnmatch

# 彻底修复 Windows 中文与特殊 Emoji 乱码和编码崩溃，安全兼容 pythonw 无窗口环境
if os.name == 'nt':
    os.system('chcp 65001 >nul 2>&1')
if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stdin is not None and hasattr(sys.stdin, 'reconfigure'):
    sys.stdin.reconfigure(encoding='utf-8', errors='replace')

# 根目录与路径定义（自适应源码与 Nuitka 二进制编译模式）
def get_base_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    if "__compiled__" in globals():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()
STATE_FILE = os.path.join(BASE_DIR, "last_sync.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "summaries")
CONFIG_FILE = os.path.join(BASE_DIR, "user_config.json")
PYTHON_EXEC = sys.executable
ENTRY_SCRIPT = os.path.join(BASE_DIR, "entry.py")


def get_subprocess_flags() -> dict:
    """获取静默执行标志，消除 Windows 弹出的控制台黑框"""
    if os.name == 'nt':
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def load_full_config() -> dict:
    """动态读取用户完整配置，包含群列表、保存偏好等"""
    cfg = {
        "group_patterns": ["*北京移动*"],
        "monitored_groups": [],
        "auto_save_to_doc": False,
        "ai_api_url": "https://api.deepseek.com/chat/completions",
        "ai_api_key": os.getenv("AI_API_KEY", "")
    }

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_data = json.load(f)
                cfg.update(user_data)
        except Exception:
            pass

    # 若未设置具体监控群，但存在历史同步记录，则自动初始化具体群名单
    if not cfg.get("monitored_groups") and os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state_data = json.load(f)
                if state_data:
                    cfg["monitored_groups"] = [
                        {"name": k, "enabled": True} for k in state_data.keys()
                    ]
        except Exception:
            pass

    return cfg


def save_full_config(cfg: dict):
    """保存用户完整配置"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_user_config():
    """向下兼容的旧版读取配置接口"""
    cfg = load_full_config()
    return cfg.get("group_patterns", ["*北京移动*"]), cfg.get("ai_api_url", ""), cfg.get("ai_api_key", "")


GROUP_PATTERNS, AI_API_URL, AI_API_KEY = load_user_config()


def get_today_doc_path() -> str:
    """按天生成归档文件名，例如 summaries/2026-10-30.md"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(OUTPUT_DIR, f"{today_str}.md")


def load_state() -> dict:
    """加载各个群上次同步的最后时间戳"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state: dict):
    """保存时间戳状态"""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def run_cli_command(args: list) -> str:
    """调用 wechat-cli 获取 JSON 数据，优先内存调用以支持 Nuitka 编译与免子进程极速执行"""
    try:
        from click.testing import CliRunner
        from wechat_cli.main import cli as wechat_cli_app
        runner = CliRunner()
        res = runner.invoke(wechat_cli_app, args)
        if res.exit_code == 0 and res.output:
            return res.output.strip()
    except Exception:
        pass

    # 回退方式：通过 entry.py 静默子进程调用
    if os.path.exists(ENTRY_SCRIPT):
        cmd = [PYTHON_EXEC, ENTRY_SCRIPT] + args
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            kwargs = get_subprocess_flags()
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, **kwargs)
            if res.returncode == 0:
                return res.stdout.strip()
        except Exception:
            pass
    return ""


def get_target_sessions():
    """获取所有匹配的目标群聊
    优先匹配用户在具体监控群列表中勾选启用的群；
    若未配置具体群，则回退为通配符规则匹配。
    """
    raw_json = run_cli_command(["sessions", "--limit", "100", "--format", "json"])
    if not raw_json:
        return []
    try:
        sessions = json.loads(raw_json)
    except Exception:
        return []

    cfg = load_full_config()
    monitored_groups = cfg.get("monitored_groups", [])
    enabled_names = {
        g["name"].strip() for g in monitored_groups
        if isinstance(g, dict) and g.get("enabled") and g.get("name")
    }

    target_sessions = []
    for s in sessions:
        if not s.get("is_group"):
            continue
        chat_name = s.get("chat", "").strip()
        if not chat_name:
            continue

        if enabled_names:
            # 模式 A：具体群精准匹配
            if chat_name in enabled_names:
                target_sessions.append(s)
        else:
            # 模式 B：模糊通配符匹配
            patterns = cfg.get("group_patterns", ["*北京移动*"])
            if any(fnmatch(chat_name, pattern) for pattern in patterns):
                target_sessions.append(s)

    return target_sessions


def fetch_new_messages(chat_name: str, last_ts: int) -> list:
    """分批循环拉取上一次同步时间之后的全部增量消息，直到完全取完（零截断、零遗漏）"""
    all_batches = []
    batch_size = 200
    offset = 0

    if last_ts > 0:
        start_time_str = datetime.fromtimestamp(last_ts + 1).strftime("%Y-%m-%d %H:%M:%S")
    else:
        today_start = datetime.now().strftime("%Y-%m-%d 00:00:00")
        start_time_str = today_start

    while True:
        args = [
            "history", chat_name,
            "--limit", str(batch_size),
            "--offset", str(offset),
            "--format", "json",
            "--media",
            "--start-time", start_time_str
        ]

        raw_json = run_cli_command(args)
        if not raw_json:
            break
        try:
            data = json.loads(raw_json)
            msgs = data.get("messages", [])
            if not msgs:
                break
            all_batches.append(msgs)
            if len(msgs) < batch_size:
                # 抓取数量小于单批容量，说明已完全取尽
                break
            offset += len(msgs)
            print(f"   [分批拉取] 已累计拉取 {offset} 条，继续拉取下一批...", flush=True)
        except Exception:
            break

    if not all_batches:
        return []

    # history 批次从前往后是按最新倒序拉取的，反转各批次后拼接恢复从早到晚的时间正序
    all_messages = []
    for b in reversed(all_batches):
        all_messages.extend(b)

    return all_messages


def clean_messages(raw_messages: list) -> list:
    """
    深度清洗消息：彻底剥离底层 XML 标签、图片代码、剔除纯灌水无实质内容的垃圾发言
    """
    cleaned = []
    # 纯无意义灌水短语黑名单
    NOISE_PHRASES = {"1", "2", "ok", "OK", "好的", "能吧", "可以", "对", "没", "嗯", "啊", "哈", "收到", "[表情]", "[捂脸]", "[呲牙]", "[发呆]", "[破涕为笑]", "[吃瓜]", "[旺柴]"}

    for msg in raw_messages:
        # 1. 过滤系统撤回与拍一拍
        if "<sysmsg" in msg or "撤回了一条消息" in msg or "拍了拍" in msg:
            continue

        # 2. 如果包含 XML 卡片或图文消息，提取其中的核心标题或纯文本
        if "<?xml" in msg or "<msg" in msg:
            # 尝试提取分享链接的标题 <title>...</title>
            title_match = re.search(r'<title>(.*?)</title>', msg)
            if title_match:
                title_text = title_match.group(1).strip()
                # 提取发送人部分
                prefix = msg.split(":")[0] if ":" in msg else ""
                msg = f"{prefix}: [分享卡片] {title_text}"
            else:
                # 剔除纯图片 XML 乱码
                msg = re.sub(r'<\?xml.*', '[图片/多媒体]', msg).strip()

        # 3. 清洗回复引用的多余空行与 XML
        msg = re.sub(r'↳ 回复 .*?: <\?xml.*', '', msg).strip()
        msg = re.sub(r'\s+', ' ', msg).strip()

        # 4. 提取发言内容正文判断是否有价值
        content = msg.split(":", 1)[-1].strip() if ":" in msg else msg
        if content in NOISE_PHRASES or len(content) < 2:
            continue

        cleaned.append(msg)
    return cleaned


def get_stored_keys() -> dict:
    """读取本地已提取并保存的微信数据库密钥字典"""
    keys_file = os.path.expanduser("~/.wechat-cli/all_keys.json")
    if os.path.exists(keys_file):
        try:
            with open(keys_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def get_ai_engine_desc(cfg: dict = None) -> str:
    """统一获取当前生效的 AI 引擎描述，供 GUI 与控制台共用"""
    if cfg is None:
        cfg = load_full_config()
    if get_agy_path():
        return "官方 agy CLI (Gemini Flash，本地免 Key 静默驱动)"
    if get_antigravity_exe():
        return "本地 Antigravity 引擎 (Gemini Flash，本地免 Key 静默驱动)"
    if cfg.get("ai_api_key"):
        url = cfg.get("ai_api_url", "")
        short_url = url[:25] + "..." if len(url) > 25 else url
        return f"外部 API ({short_url})"
    return "本地离线清洗完整展示"


def build_summary_prompt(chat_name: str, messages: list) -> str:
    """构建用于大模型提炼的结构化总结 Prompt，消除多处硬编码冗余"""
    return (
        f"你是一名专业的信息分析专家。下面是微信群【{chat_name}】近期的增量原始发言记录。\n"
        f"请彻底过滤闲聊水话，直接输出高密度、有实质价值的要点提炼。\n"
        f"注意：直接根据下方文本结构化输出文字总结，不要调用任何工具：\n\n"
        f"1. 💡【核心议题与结论】：群友在讨论什么业务、资费套餐、优惠活动或操作细节？结论是什么？\n"
        f"2. 📌【关键信息与待办预警】：有什么重要的注意事项、资费变动、待办节点或避坑提醒？\n"
        f"3. 🔗【分享的资料/链接/图片】：若有分享群资料或图片，列出要点（若无则写“无”）。\n\n"
        f"聊天记录如下：\n"
        + "\n".join(messages)
    )


def _http_post_json(url: str, payload: dict, headers: dict, timeout: int = 40) -> dict:
    """通用 JSON POST 请求辅助函数，消除网络请求样板代码"""
    import urllib.request
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_llm_api(chat_name: str, messages: list, api_url: str = None, api_key: str = None) -> str:
    """调用外部大模型 API 进行语义提炼与结构化总结"""
    url = api_url or AI_API_URL
    key = api_key or AI_API_KEY
    prompt = build_summary_prompt(chat_name, messages)

    # 1. Google Gemini 官方 API 格式
    if "googleapis.com" in url or url.startswith("gemini"):
        target_url = url if "key=" in url else f"{url}?key={key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3}
        }
        res = _http_post_json(target_url, payload, {"Content-Type": "application/json"})
        return res["candidates"][0]["content"]["parts"][0]["text"].strip()

    # 2. 兼容 OpenAI / DeepSeek 标准格式
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    res = _http_post_json(url, payload, headers)
    return res["choices"][0]["message"]["content"].strip()


def get_agy_path() -> str:
    """自动定位官方 agy CLI 路径"""
    w = shutil.which("agy")
    if w:
        return w
    candidates = [
        os.path.expanduser("~/.gemini/bin/agy.exe"),
        os.path.expanduser("~/.gemini/bin/agy"),
        os.path.expandvars(r"%USERPROFILE%\.gemini\bin\agy.exe"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def call_agy_cli(chat_name: str, messages: list) -> tuple:
    """直接调用官方 agy CLI (-p) 进行深度提炼，免 API Key 极速总结
    返回: (summary_str, error_msg)
    """
    agy_path = get_agy_path()
    if not agy_path:
        return None, "未找到 agy CLI 命令"

    prompt = build_summary_prompt(chat_name, messages)
    print(f"⏳ 正在调用官方 agy CLI (Gemini 3.8 Flash) 提炼总结 (约需 3~10 秒，请稍候)...", flush=True)
    try:
        cmd = [agy_path, "--model", "gemini-3.8-flash-high", "-p", prompt]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        kwargs = get_subprocess_flags()
        res = subprocess.run(cmd, capture_output=True, env=env, timeout=60, **kwargs)
        if res.returncode == 0:
            text = res.stdout.decode("utf-8", errors="replace").strip()
            if text:
                return text, None
            return None, "agy 返回内容为空"
        else:
            err_msg = res.stderr.decode("utf-8", errors="replace").strip() if res.stderr else f"退出码 {res.returncode}"
            print(f"❌ [agy CLI 调用失败]: {err_msg[:200]}")
            return None, err_msg[:200]
    except subprocess.TimeoutExpired:
        err_msg = "agy 响应超时 (超过 60 秒)"
        print(f"❌ [agy CLI 超时]: {err_msg}")
        return None, err_msg
    except Exception as e:
        err_msg = f"agy 调用异常: {e}"
        print(f"❌ [agy CLI 异常]: {err_msg}")
        return None, err_msg


def get_antigravity_exe() -> str:
    """自动定位 Antigravity 内部的 language_server.exe CLI"""
    # 1. 尝试从 agentapi.bat 中提取真实路径
    bat_path = os.path.expanduser("~/.gemini/antigravity/bin/agentapi.bat")
    if os.path.exists(bat_path):
        try:
            with open(bat_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
                m = re.search(r'"([^"]*language_server\.exe)"', content)
                if m and os.path.exists(m.group(1)):
                    return m.group(1)
        except Exception:
            pass

    # 2. 默认安装路径探测
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\antigravity\resources\bin\language_server.exe"),
        os.path.expandvars(r"%APPDATA%\Local\Programs\antigravity\resources\bin\language_server.exe"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def call_antigravity_cli(chat_name: str, messages: list) -> str:
    """直接调用本地安装的 Antigravity 客户端底层 CLI (Gemini Flash)，免 API Key 极速总结"""
    exe_path = get_antigravity_exe()
    if not exe_path:
        return None

    prompt = build_summary_prompt(chat_name, messages)
    cmd = [exe_path, "agentapi", "new-conversation", "--model=flash", f"--title=微信总结:{chat_name}", prompt]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        kwargs = get_subprocess_flags()
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=20, **kwargs)
        if res.returncode != 0:
            return None
        data = json.loads(res.stdout)
        cid = data.get("response", {}).get("newConversation", {}).get("conversationId")
        if not cid:
            return None
    except Exception:
        return None

    log_file = os.path.expanduser(f"~/.gemini/antigravity/brain/{cid}/.system_generated/logs/transcript.jsonl")
    for _ in range(30):
        if os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        entry = json.loads(line)
                        # 必须是最终的回答（PLANNER_RESPONSE），且不含工具调用！
                        if entry.get("source") == "MODEL" and entry.get("type") == "PLANNER_RESPONSE":
                            if not entry.get("tool_calls") and entry.get("content"):
                                return entry["content"].strip()
            except Exception:
                pass
        time.sleep(1)
    return None


def _build_offline_fallback_summary(messages: list, last_error: str = None) -> str:
    """未配置 Key 或 AI 失败时的离线完整简报（杜绝 XML 垃圾，不设截断）"""
    meaningful_texts = []
    links = []
    images = 0

    for m in messages:
        if "[分享卡片]" in m or "[链接/文件]" in m:
            links.append(m.split(":", 1)[-1].strip())
        elif "[图片" in m:
            images += 1
        else:
            content = m.split(":", 1)[-1].strip()
            if len(content) >= 4:
                meaningful_texts.append(m)

    notice = f"⚠️ 【提示：AI 提炼未成功 ({last_error})，已降级为本地纯净简报】" if last_error else "⚠️ 【提示：尚未配置 AI API Key，当前为本地完整简报】"
    res = [notice, f"• 本次捕获增量有效发言：{len(messages)} 条"]
    if meaningful_texts:
        res.append("• 完整有效发言记录（不折叠全量展示）：")
        for t in meaningful_texts:
            res.append(f"  - {t}")
    if links:
        res.append("• 资料/卡片分享链接：")
        for link in links:
            res.append(f"  - {link}")
    if images:
        res.append(f"• 图片分享：共 {images} 张")

    return "\n".join(res)


def _summarize_single_chunk(chat_name: str, messages: list) -> str:
    """对单批次消息进行智能多级总结：1. 官方 agy CLI -> 2. 本地 Antigravity 引擎 -> 3. 外部 API Key -> 4. 本地脱敏清洗"""
    if not messages:
        return "无新消息。"

    last_error = None

    # 1. 优先调用官方 agy CLI (-p) 驱动（免 API Key，直接白嫖本地 Gemini Flash）！
    if get_agy_path():
        agy_res, agy_err = call_agy_cli(chat_name, messages)
        if agy_res:
            return agy_res
        last_error = f"官方 agy 报错: {agy_err}"

    # 2. 备用调用本地 Antigravity 内部 language_server CLI
    if get_antigravity_exe():
        print(f"正在尝试调用备用 Antigravity 引擎进行智能提炼...", flush=True)
        agy_summary = call_antigravity_cli(chat_name, messages)
        if agy_summary:
            return agy_summary

    # 3. 如果本地 CLI 未就绪且配置了外部 API Key，则调外部 API
    if AI_API_KEY:
        try:
            return call_llm_api(chat_name, messages)
        except Exception as e:
            last_error = f"外部 API 异常: {e}"
            print(f"[⚠️ 外部 API 调用失败: {e}]，自动降级为清洗模式。")

    # 4. 未配置 Key 或 AI 失败时的离线提炼（杜绝 XML 垃圾）
    return _build_offline_fallback_summary(messages, last_error)


def summarize_text(chat_name: str, messages: list) -> str:
    """智能增量总结入口：当消息量极大时自动分批送入大模型，保证完全取完并深度总结"""
    if not messages:
        return "无新消息。"

    CHUNK_SIZE = 300
    if len(messages) <= CHUNK_SIZE:
        return _summarize_single_chunk(chat_name, messages)

    # 超过 300 条发言时自动切批，分批深度提炼
    total_chunks = (len(messages) + CHUNK_SIZE - 1) // CHUNK_SIZE
    print(f"📦 本次捕获增量发言共 {len(messages)} 条，自动切分为 {total_chunks} 批进行全量深度总结...", flush=True)
    summaries = []
    for idx in range(total_chunks):
        chunk = messages[idx * CHUNK_SIZE : (idx + 1) * CHUNK_SIZE]
        print(f"   ⏳ 正在提炼第 {idx + 1}/{total_chunks} 批 (第 {idx * CHUNK_SIZE + 1}~{min((idx + 1) * CHUNK_SIZE, len(messages))} 条)...", flush=True)
        res = _summarize_single_chunk(f"{chat_name} (第{idx+1}批)", chunk)
        summaries.append(f"#### 📑 第 {idx + 1}/{total_chunks} 批提炼总结 (含 {len(chunk)} 条发言):\n\n{res}")

    return "\n\n---\n\n".join(summaries)


def append_to_doc(chat_name: str, summary_content: str, msg_count: int) -> str:
    """追加写入按天分割、按小时记录的总结文档"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    daily_doc = get_today_doc_path()
    today_str = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    hour_bucket = datetime.now().strftime("%H:00")

    file_exists = os.path.exists(daily_doc)
    with open(daily_doc, "a", encoding="utf-8") as f:
        if not file_exists:
            f.write(f"# 微信关键群聊总结归档 ({today_str})\n\n")
            f.write("> 本文档由 auto_summary.py 自动化增量抓取，每天独立成册。\n\n---\n")

        f.write(f"\n### 🕒 【{hour_bucket}】群聊：{chat_name} (新增 {msg_count} 条发言)\n\n")
        f.write(f"- 记录时间: `{now_str}`\n\n")
        f.write(summary_content + "\n\n---\n")
    return daily_doc


def ask_save_choice(prompt_str: str) -> bool:
    """单键极速响应：按 y/Y 确认保存，按任意其他键立即取消跳过（无需按回车确认）"""
    print(prompt_str, end="", flush=True)
    if os.name == 'nt':
        try:
            import msvcrt
            ch = msvcrt.getch()
            # 过滤 Windows 扩展键（方向键、功能键等 0x00 / 0xe0 前缀）
            if ch in (b'\x00', b'\xe0'):
                msvcrt.getch()
                print()
                return False
            if ch == b'\x03':  # Ctrl+C
                print("\n[退出]")
                sys.exit(0)
            char = ch.decode('utf-8', errors='ignore')
            if char in ('y', 'Y'):
                print(char)
                return True
            else:
                print()
                return False
        except Exception:
            pass

    # 非 Windows 或重定向管道时降级使用标准输入
    try:
        choice = input().strip()
        return choice.lower() in ("y", "yes")
    except Exception:
        return False


def run_incremental_summary(log_fn=print, ask_save_fn=None, auto_save=None, result_fn=None) -> dict:
    """增量总结执行引擎：可被控制台与 GUI 界面通用调用"""
    # 动态重新读取一次最新完整配置
    global GROUP_PATTERNS, AI_API_URL, AI_API_KEY
    cfg = load_full_config()
    GROUP_PATTERNS = cfg.get("group_patterns", ["*北京移动*"])
    AI_API_URL = cfg.get("ai_api_url", "https://api.deepseek.com/chat/completions")
    AI_API_KEY = cfg.get("ai_api_key", "")
    
    monitored_groups = cfg.get("monitored_groups", [])
    enabled_names = [g["name"] for g in monitored_groups if isinstance(g, dict) and g.get("enabled")]

    log_fn("=" * 60)
    log_fn(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 微信增量消息总结检测启动...")
    if enabled_names:
        log_fn(f"目标监控模式: 指定具体群聊 ({len(enabled_names)} 个启用: {', '.join(enabled_names)})")
    else:
        log_fn(f"目标监控模式: 模糊规则匹配 ({GROUP_PATTERNS})")

    log_fn(f"AI 模型引擎: {get_ai_engine_desc(cfg)}")
    log_fn("=" * 60)

    state = load_state()
    target_sessions = get_target_sessions()

    if not target_sessions:
        log_fn("未发现匹配的目标群聊，请确认群聊名称或监控规则。")
        return {"results": [], "saved_doc": None}

    # 1. 先筛选出哪些目标群有新增发言
    sessions_to_process = []
    for s in target_sessions:
        chat_name = s.get("chat", "")
        current_ts = s.get("timestamp", 0)
        last_ts = state.get(chat_name, 0)
        if current_ts > last_ts:
            sessions_to_process.append(s)
        else:
            log_fn(f"[跳过] 【{chat_name}】暂无新发言 (当前时间戳已同步)")

    if not sessions_to_process:
        log_fn("\n🏁 检测完成，所有目标群均无新增内容。")
        return {"results": [], "saved_doc": None}

    total_count = len(sessions_to_process)
    log_fn(f"\n📢 检测到 {total_count} 个群聊有新增发言，开始全量总结...\n")

    summarized_results = []

    # 2. 连续提炼所有群的总结并输出，不再中途打断
    for idx, s in enumerate(sessions_to_process, 1):
        chat_name = s.get("chat", "")
        current_ts = s.get("timestamp", 0)
        last_ts = state.get(chat_name, 0)

        log_fn(f"\n⚡ [{idx}/{total_count}] 正在拉取【{chat_name}】增量记录...")
        raw_msgs = fetch_new_messages(chat_name, last_ts)
        msgs = clean_messages(raw_msgs)

        if not msgs:
            log_fn(f"[提示] 【{chat_name}】无有效文本发言 (全部为撤回或纯表情)")
            state[chat_name] = current_ts
            continue

        log_fn(f"正在全量分析总结【{chat_name}】的全部 {len(msgs)} 条发言 (不设截断，完整送入 AI)...")
        summary = summarize_text(chat_name, msgs)

        # 区分处理结果与普通过程日志
        if result_fn:
            result_fn({
                "chat_name": chat_name,
                "summary": summary,
                "count": len(msgs),
                "index": idx,
                "total": total_count
            })
            log_fn(f"✅ [{idx}/{total_count}] 【{chat_name}】重点总结已提炼完成！")
        else:
            log_fn("\n" + "-" * 50)
            log_fn(f"📌 [{idx}/{total_count}] 【{chat_name}】重点总结 (新增 {len(msgs)} 条):")
            log_fn("-" * 50)
            log_fn(summary)
            log_fn("-" * 50)

        summarized_results.append({
            "chat_name": chat_name,
            "summary": summary,
            "count": len(msgs)
        })
        state[chat_name] = current_ts

    saved_doc = None
    # 3. 全部群总结完成后，统一决定是否写入今日归档文档
    if summarized_results:
        log_fn("\n" + "=" * 60)
        group_names = "、".join([f"【{item['chat_name']}】" for item in summarized_results])
        log_fn(f"🎉 本次共完成 {len(summarized_results)} 个群聊的总结：{group_names}")

        save_to_doc = False
        if auto_save is not None:
            save_to_doc = bool(auto_save)
        elif ask_save_fn:
            save_to_doc = ask_save_fn(len(summarized_results), group_names)
        else:
            prompt_str = f"\n💾 是否将上述 {len(summarized_results)} 个群聊的总结统一记录到今日文档？[按 y 保存 / 任意其他键取消]: "
            save_to_doc = ask_save_choice(prompt_str)

        if save_to_doc:
            for item in summarized_results:
                saved_doc = append_to_doc(item["chat_name"], item["summary"], item["count"])
            log_fn(f"✅ 已全部保存至今日文档：{saved_doc}")
            log_fn(f"\n✅ 增量总结完毕，已保存内容见今日文档：{saved_doc}")
        else:
            log_fn("⏭️ 已跳过文档记录（仅屏幕展示）。")
            log_fn("\n✅ 增量总结完毕（本次未保存到本地文档）。")
    else:
        log_fn("\n🏁 检测完成，无有效发言内容可生成总结。")

    # 4. 保存最新同步状态
    save_state(state)
    return {"results": summarized_results, "saved_doc": saved_doc}


def main():
    run_incremental_summary(log_fn=print, ask_save_fn=None, auto_save=None, result_fn=None)


if __name__ == "__main__":
    main()

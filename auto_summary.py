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


def load_user_config():
    """动态读取用户配置"""
    patterns = ["*北京移动*"]
    api_url = "https://api.deepseek.com/chat/completions"
    api_key = os.getenv("AI_API_KEY", "")

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                patterns = cfg.get("group_patterns", patterns)
                api_url = cfg.get("ai_api_url", api_url)
                api_key = cfg.get("ai_api_key", api_key) or api_key
        except Exception:
            pass
    return patterns, api_url, api_key


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

    # 回退方式：通过 entry.py 子进程调用
    if os.path.exists(ENTRY_SCRIPT):
        cmd = [PYTHON_EXEC, ENTRY_SCRIPT] + args
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
            if res.returncode == 0:
                return res.stdout.strip()
        except Exception:
            pass
    return ""


def get_target_sessions():
    """获取所有匹配的目标群聊"""
    raw_json = run_cli_command(["sessions", "--limit", "100", "--format", "json"])
    if not raw_json:
        return []
    try:
        sessions = json.loads(raw_json)
    except Exception:
        return []

    target_sessions = []
    for s in sessions:
        if not s.get("is_group"):
            continue
        chat_name = s.get("chat", "")
        # 匹配通配符
        matched = any(fnmatch(chat_name, pattern) for pattern in GROUP_PATTERNS)
        if matched:
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


def call_llm_api(chat_name: str, messages: list) -> str:
    """调用真正的大模型进行语义提炼与结构化总结"""
    import urllib.request

    prompt = (
        f"你是一名专业的信息分析专家。下面是微信群【{chat_name}】近期的原始发言记录。\n"
        f"请彻底过滤闲聊水话，提炼高密度、有实质价值的核心摘要。\n\n"
        f"要求按以下格式结构化输出：\n"
        f"1. 💡【核心议题与结论】：群友在热烈讨论什么业务、资费套餐、优惠活动或技术问题？达成了什么结论？\n"
        f"2. 📌【关键信息与待办/预警】：有什么重要的注意事项、资费变动、待办节点或操作避坑？\n"
        f"3. 🔗【分享的资料/链接/图片】：若有分享群资料或图片，列出要点（若无则写“无”）。\n\n"
        f"聊天记录如下：\n"
        + "\n".join(messages)
    )

    # 1. 如果是 Google Gemini 官方 API (包含 generativelanguage.googleapis.com)
    if "googleapis.com" in AI_API_URL or AI_API_URL.startswith("gemini"):
        url = AI_API_URL
        if "key=" not in url:
            url += f"?key={AI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {"temperature": 0.3}
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["candidates"][0]["content"]["parts"][0]["text"].strip()

    # 2. 默认兼容 OpenAI / DeepSeek 格式
    else:
        model_name = "deepseek-chat"
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            AI_API_URL, data=data,
            headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=40) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"].strip()


def get_agy_path() -> str:
    """自动定位官方 agy CLI 路径"""
    import shutil
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

    prompt = (
        f"你是一名专业的信息分析专家。下面是微信群【{chat_name}】近期的增量原始发言记录。\n"
        f"请彻底过滤闲聊水话，直接输出高密度、有实质价值的要点提炼。\n"
        f"注意：直接根据下方文本输出文字总结，不要调用任何工具：\n\n"
        f"1. 💡【核心议题与结论】：群友在讨论什么业务、资费套餐、优惠活动或操作细节？结论是什么？\n"
        f"2. 📌【关键信息与待办预警】：有什么重要的注意事项、资费变动、待办节点或避坑提醒？\n"
        f"3. 🔗【分享的资料/链接/图片】：若有分享群资料或图片，列出要点（若无则写“无”）。\n\n"
        f"聊天记录如下：\n"
        + "\n".join(messages)
    )

    print(f"⏳ 正在调用官方 agy CLI (Gemini 3.8 Flash) 提炼总结 (约需 3~10 秒，请稍候)...", flush=True)
    try:
        cmd = [agy_path, "--model", "gemini-3.8-flash-high", "-p", prompt]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        res = subprocess.run(cmd, capture_output=True, env=env, timeout=60)
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

    prompt = (
        f"你是一名专业的信息分析专家。下面是微信群【{chat_name}】近期的增量原始发言记录。\n"
        f"请彻底过滤闲聊水话，直接输出高密度、有实质价值的要点提炼。\n"
        f"注意：直接根据下方给出的文本输出文字总结，不要调用任何工具：\n\n"
        f"1. 💡【核心议题与结论】：群友在讨论什么业务、资费套餐、优惠活动或操作细节？结论是什么？\n"
        f"2. 📌【关键信息与待办预警】：有什么重要的注意事项、资费变动、待办节点或避坑提醒？\n"
        f"3. 🔗【分享的资料/链接/图片】：若有分享群资料或图片，列出要点（若无则写“无”）。\n\n"
        f"聊天记录如下：\n"
        + "\n".join(messages)
    )

    cmd = [exe_path, "agentapi", "new-conversation", "--model=flash", f"--title=微信总结:{chat_name}", prompt]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=20)
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
    meaningful_texts = []
    links = []
    images = 0

    for m in messages:
        if "[分享卡片]" in m or "[链接/文件]" in m:
            links.append(m.split(":", 1)[-1].strip())
        elif "[图片" in m:
            images += 1
        else:
            # 过滤纯简短回复
            content = m.split(":", 1)[-1].strip()
            if len(content) >= 4:
                meaningful_texts.append(m)

    notice = f"⚠️ 【提示：AI 提炼未成功 ({last_error})，已降级为本地纯净简报】" if last_error else "⚠️ 【提示：尚未配置 AI API Key，当前为本地过滤简报】"
    res = [
        notice,
        f"• 本次捕获增量有效发言：{len(messages)} 条",
    ]
    if meaningful_texts:
        res.append("• 过滤后关键讨论摘录：")
        for t in meaningful_texts[:8]:
            res.append(f"  - {t}")
        if len(meaningful_texts) > 8:
            res.append(f"  - ...(其余 {len(meaningful_texts)-8} 条简短发言已过滤)")
    if links:
        res.append(f"• 资料/卡片分享：{', '.join(links[:3])}")
    if images:
        res.append(f"• 图片分享：共 {images} 张")

    return "\n".join(res)


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


def run_incremental_summary(log_fn=print, ask_save_fn=None, auto_save=None) -> dict:
    """增量总结执行引擎：可被控制台与 GUI 界面通用调用"""
    # 动态重新读取一次最新配置
    global GROUP_PATTERNS, AI_API_URL, AI_API_KEY
    GROUP_PATTERNS, AI_API_URL, AI_API_KEY = load_user_config()

    log_fn("=" * 60)
    log_fn(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 微信增量消息总结检测启动...")
    log_fn(f"目标监控规则: {GROUP_PATTERNS}")
    if get_agy_path():
        log_fn("AI 模型引擎: 官方 agy CLI (Gemini Flash，本地免 Key 驱动)")
    elif get_antigravity_exe():
        log_fn("AI 模型引擎: 本地 Antigravity 引擎 (Gemini Flash，本地免 Key 驱动)")
    elif AI_API_KEY:
        log_fn(f"AI 模型引擎: 已启用外部 API ({AI_API_URL})")
    else:
        log_fn("AI 模型引擎: 本地离线清洗 (未配置 Key 与 CLI)")
    log_fn("=" * 60)

    state = load_state()
    target_sessions = get_target_sessions()

    if not target_sessions:
        log_fn("未发现匹配的目标群聊，请确认群聊名称是否匹配规则。")
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

        # 终端高亮展示每个群的总结
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
    run_incremental_summary(log_fn=print, ask_save_fn=None, auto_save=None)


if __name__ == "__main__":
    main()

"""微信图片 (.dat) 解密与解码模块

支持：
1. 传统单字节 XOR 异或（兼容旧版微信）
2. V1 格式（固定 AES-128 密钥 cfcd208495d565ef + XOR）
3. V2 格式（微信 4.0+，AES-128-ECB + XOR 复合加密，支持内存自动扫描提取密钥）
"""

import ctypes
from ctypes import wintypes
import glob
import json
import os
import re
import shutil
import struct
import subprocess

from Crypto.Cipher import AES

CACHE_DIR = os.path.expanduser("~/.wechat-cli/cache/images")
CONFIG_FILE = os.path.expanduser("~/.wechat-cli/config.json")

V2_MAGIC = b'\x07\x08\x56\x32'
V2_MAGIC_FULL = b'\x07\x08V2\x08\x07'
V1_MAGIC_FULL = b'\x07\x08V1\x08\x07'
V1_STATIC_KEY = b'cfcd208495d565ef'

IMAGE_MAGICS = {
    'jpg': b'\xff\xd8\xff',
    'png': b'\x89PNG',
    'gif': b'GIF8',
    'bmp': b'BM',
}


def detect_image_format(data: bytes) -> str:
    """根据文件头识别常见图片格式"""
    if data.startswith(b'RIFF') and len(data) >= 12 and data[8:12] == b'WEBP':
        return 'webp'
    for fmt, magic in IMAGE_MAGICS.items():
        if data.startswith(magic):
            return fmt
    return 'jpg'


def _read_json(path: str) -> dict:
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _write_json(path: str, data: dict):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_stored_image_keys() -> tuple:
    """读取保存的图片解密密钥"""
    cfg = _read_json(CONFIG_FILE)
    return cfg.get('image_aes_key'), cfg.get('image_xor_key', 0x32)


def _save_stored_image_keys(aes_key: str, xor_key: int):
    """持久化图片解密密钥"""
    cfg = _read_json(CONFIG_FILE)
    cfg['image_aes_key'], cfg['image_xor_key'] = aes_key, xor_key
    _write_json(CONFIG_FILE, cfg)


def _find_ciphertext_sample(attach_dir: str):
    """在 attach 目录下寻找任意有效的 V2 密文样本头"""
    if not attach_dir or not os.path.isdir(attach_dir):
        return None
    for f in glob.glob(os.path.join(attach_dir, "**", "*_t.dat"), recursive=True)[:50]:
        try:
            with open(f, 'rb') as fp:
                hdr = fp.read(31)
            if hdr[:6] == V2_MAGIC_FULL and len(hdr) >= 31:
                return hdr[15:31]
        except Exception:
            continue
    return None


def scan_image_keys_from_memory(attach_dir: str = None) -> tuple:
    """从 Weixin.exe 进程内存中提取微信 4.0 V2 图片 AES 密钥"""
    if os.name != 'nt':
        return None, 0x32

    ciphertext = _find_ciphertext_sample(attach_dir)
    if not ciphertext:
        return None, 0x32

    kernel32 = ctypes.windll.kernel32
    re_key = re.compile(rb'(?<![a-zA-Z0-9])[a-zA-Z0-9]{16,32}(?![a-zA-Z0-9])')

    class MBI(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", wintypes.DWORD), ("RegionSize", ctypes.c_size_t),
            ("State", wintypes.DWORD), ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD),
        ]

    cmd = ['tasklist.exe', '/FI', 'IMAGENAME eq Weixin.exe', '/FO', 'CSV', '/NH']
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=0x08000000)
    pids = [int(line.strip('"').split('","')[1]) for line in r.stdout.strip().split('\n') if 'Weixin.exe' in line]

    for pid in pids:
        h = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
        if not h:
            continue
        addr, mbi = 0, MBI()
        while addr < 0x7FFFFFFFFFFF:
            if kernel32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
                break
            if mbi.State == 0x1000 and (mbi.Protect & 0xCC) and mbi.RegionSize <= 50 * 1024 * 1024:
                buf, read_n = ctypes.create_string_buffer(mbi.RegionSize), ctypes.c_size_t(0)
                if kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, mbi.RegionSize, ctypes.byref(read_n)):
                    for m in re_key.finditer(buf.raw[:read_n.value]):
                        cand = m.group()[:16]
                        try:
                            dec = AES.new(cand, AES.MODE_ECB).decrypt(ciphertext)
                            if dec.startswith(b'\xff\xd8\xff') or dec.startswith(b'\x89PNG'):
                                key_str = cand.decode('ascii')
                                kernel32.CloseHandle(h)
                                _save_stored_image_keys(key_str, 0x32)
                                return key_str, 0x32
                        except Exception:
                            pass
            addr = mbi.BaseAddress + mbi.RegionSize if mbi.BaseAddress else addr + mbi.RegionSize
        kernel32.CloseHandle(h)

    return None, 0x32


def decode_v2_data(data: bytes, aes_key_str: str = None, xor_key: int = 0x32) -> bytes:
    """解密 V2/V1 混合加密数据"""
    if len(data) < 15 or data[:6] not in (V2_MAGIC_FULL, V1_MAGIC_FULL):
        return None
    aes_size, xor_size = struct.unpack_from('<LL', data, 6)
    key_bytes = V1_STATIC_KEY if data[:6] == V1_MAGIC_FULL else (aes_key_str.encode('ascii')[:16] if aes_key_str else None)
    if not key_bytes or len(key_bytes) < 16:
        return None

    aligned = aes_size - ~(~aes_size % 16)
    if 15 + aligned > len(data):
        return None

    try:
        dec_aes = AES.new(key_bytes[:16], AES.MODE_ECB).decrypt(data[15:15 + aligned])
        pad = dec_aes[-1]
        if 0 < pad <= 16 and dec_aes[-pad:] == bytes([pad]) * pad:
            dec_aes = dec_aes[:-pad]
    except Exception:
        return None

    raw_end = len(data) - xor_size
    raw_data = data[15 + aligned:raw_end] if 15 + aligned < raw_end else b''
    xor_data = bytes(b ^ xor_key for b in data[raw_end:]) if xor_size > 0 else b''
    return dec_aes + raw_data + xor_data


def decode_legacy_xor_data(data: bytes) -> bytes:
    """传统单字节 XOR 解密（针对 JPG、PNG、GIF）"""
    if len(data) < 4:
        return None
    for magic in (b'\xff\xd8\xff', b'\x89PNG', b'GIF8'):
        key = data[0] ^ magic[0]
        if all((data[i] ^ key) == magic[i] for i in range(1, len(magic))):
            return bytes(b ^ key for b in data)
    return None


def _save_image(data: bytes, dat_path: str, out_dir: str = None) -> str:
    """统一保存解码后的图片至指定目录"""
    fmt = detect_image_format(data)
    target_dir = out_dir or CACHE_DIR
    os.makedirs(target_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(dat_path))[0]
    out_path = os.path.join(target_dir, f"{base}.{fmt}")
    with open(out_path, 'wb') as fp:
        fp.write(data)
    return out_path


def decode_dat_file(dat_path: str, out_dir: str = None, attach_dir: str = None) -> str:
    """通用微信 .dat 文件解密入口，还原为可直接查看的 .jpg / .png"""
    if not dat_path or not os.path.isfile(dat_path):
        return dat_path
    if os.path.splitext(dat_path)[1].lower() in ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'):
        return dat_path

    try:
        with open(dat_path, 'rb') as f:
            raw = f.read()
    except Exception:
        return dat_path
    if len(raw) < 16:
        return dat_path

    # 1. 未加密裸流
    if raw.startswith(b'\xff\xd8\xff') or raw.startswith(b'\x89PNG'):
        return _save_image(raw, dat_path, out_dir)

    # 2. V2/V1 复合加密
    decrypted = None
    if raw.startswith(V2_MAGIC):
        aes_key, xor_key = _load_stored_image_keys()
        if not aes_key:
            aes_key, xor_key = scan_image_keys_from_memory(attach_dir or os.path.dirname(dat_path))
        if aes_key:
            decrypted = decode_v2_data(raw, aes_key, xor_key)

    # 3. 传统 XOR
    if not decrypted:
        decrypted = decode_legacy_xor_data(raw)

    return _save_image(decrypted, dat_path, out_dir) if decrypted else dat_path


def clear_image_cache() -> int:
    """清空已解码的图片临时缓存，返回清理的文件数量"""
    count = 0
    dirs = [CACHE_DIR, os.path.expanduser("~/.wechat-cli/decoded_images")]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            fp = os.path.join(d, fn)
            try:
                if os.path.isfile(fp) or os.path.islink(fp):
                    os.remove(fp)
                    count += 1
                elif os.path.isdir(fp):
                    shutil.rmtree(fp, ignore_errors=True)
                    count += 1
            except Exception:
                pass
    return count


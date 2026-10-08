#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OLM Tool Pro v20.1 by Crayz
- Bug fixes: BUG-01 → BUG-11 (full spec)
- Hotfix v3: HTML entities + option-strip + label preservation
- v19.1: PORT FROM USERSCRIPT "OLM Hack Pro - OpiCrayzz"
  * extractQuestionData khớp 100% bản gốc (12+ q_type)
  * ANTI_DETECT_JS đủ 11 tầng
  * DOM-fill text-matching + q5/6/9/10/13-multi
  * data_log / choose_log / ans / user_ans khớp payload gốc
  * chặn log=tab, cache TTL + telemetry
- v19.2: FIX ĐIỂM + XEM LẠI BÀI ĐÃ NỘP + TRANG KEY MỚI
  * FIX 11/10 điểm: tl_score = 0, kẹp điểm <= max_score (10)
  * Xem lại bài đã nộp: dùng /get-crt-ans-file + OlmEncode.decode
  * Trang đáp án render lại: đúng thứ tự, không lặp, không lệch nhãn
  * key_generator.html: 1 key/IP/máy/ngày, không reset, không lộ Supabase
- v19.3: FIX SQL 42883 + CHỈ PHÁT KEY CÒN HẠN >= 1 NGÀY
  * SQL: bỏ `pg_catalog.` trên COALESCE/MD5/EXTRACT... (gây lỗi 42883)
  * SQL: oltp_hash_ip(p_ip, p_salt) — salt qua tham số (IMMUTABLE hợp lệ)
  * SQL: chỉ phát key có expires_at > now() + min_valid_hours (mặc định 24h)
- v20.0: LÀM BÀI THẬT SỰ + LICENSE GATE + LIQUID GLASS
  * LÀM BÀI THẬT: mô phỏng chuột (pointer/mouse/focus/click) để TICK radio,
    tick checkbox, điền text, chọn select, kéo-thả sortable, gạch chân, kéo
    nhóm — rồi BẤM NÚT NỘP của chính OLM (hoặc gọi hàm nộp native), chờ OLM
    chấm & render kết quả. POST dữ liệu chỉ còn là ĐƯỜNG DỰ PHÒNG.
  * LICENSE GATE: màn khoá toàn màn hình khi chưa có license, có nhập key,
    copy HWID, thử lại và nút "Get Key Free".
  * GET KEY URL: link lấy key cấu hình được (settings.json > env > hằng số >
    trang đi kèm), API set_key_url/open_get_key/get_key_info.
  * LIQUID GLASS: thiết kế lại toàn bộ UI tool + trang key (aurora, kính mờ
    backdrop-filter, viền sáng, spring easing, sheen, noise, a11y).
  * CÁCH B mặc định cho trang key: gọi thẳng Supabase RPC, chỉ cần 2 giá trị.
- v20.1: LẤY COOKIE THẬT + QUÉT ĐƯỢC GIAO DIỆN OLM MỚI + LUẬT KEY <= 1 NGÀY
  * COOKIE: đọc đúng origin (ctx.cookies(OLM_BASE)), thu mọi domain OLM,
    XÁC MINH bằng request thật trước khi báo thành công.
  * KHỚP TRÌNH DUYỆT: tự dò phiên bản Chrome/Edge đang cài -> UA + impersonate
    + Client Hints cùng major (trước đây cứng Chrome/124 nên lệch fingerprint
    -> OLM coi như chưa đăng nhập, quét bài rỗng).
  * QUÉT BÀI: 4 tầng — bắt response JSON của trang, bóc state JS, quét DOM,
    GET HTML thô. Bóc JSON bằng thuật toán tổng quát (không phụ thuộc shape).
  * SLUG: nhận cả /chu-de/kiem-tra-15-phut-2131570928 (trước chỉ nhận số).
  * LUẬT KEY: 'min_valid_hours' (>= 24h) -> 'max_valid_hours' (<= 24h).
"""
from __future__ import annotations
import os, sys, re, json, time, uuid, base64, asyncio, threading, socket
import subprocess, io, zipfile, hashlib, platform, urllib.parse
import urllib.request, urllib.error, traceback
import html as html_module
from pathlib import Path
from typing import Optional, Any, List, Dict, Tuple, Callable
from datetime import datetime, timezone, timedelta


# ═══════════════════════════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════════════════════════
def _resolve_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resolve_resource_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


APP_DIR = _resolve_app_dir()
RES_DIR = _resolve_resource_dir()
DATA_DIR = Path(os.path.expanduser("~")) / ".olm_tool_pro"
BROWSERS_DIR = DATA_DIR / "browsers"
DATA_DIR.mkdir(parents=True, exist_ok=True)
BROWSERS_DIR.mkdir(parents=True, exist_ok=True)
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)

import webview
from curl_cffi.requests import AsyncSession as CurlAsyncSession

try:
    from playwright.async_api import async_playwright
    HAS_PW = True
except ImportError:
    HAS_PW = False


# ═══════════════════════════════════════════════════════════════
# TYPED EXCEPTIONS
# ═══════════════════════════════════════════════════════════════
class OLMError(Exception): pass
class CloudflareBlock(OLMError): pass
class ChromiumMissing(OLMError): pass
class LicenseError(OLMError): pass
class V1Required(OLMError): pass
class QuestionExtractionError(OLMError): pass
class ExamTypeDetectionError(OLMError): pass
class AnswerFetchError(OLMError): pass
class MediaFetchError(OLMError): pass
class MathRenderError(OLMError): pass


# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ywxwjzsoynbrinasvjsk.supabase.co")
SUPABASE_ANON_KEY = os.environ.get(
    "SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl3eHdqenNveW5icmluYXN2anNrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTA3NDc3ODMsImV4cCI6MjEwNjMyMzc4M30."
    "7omBqPWx6QceG1LnAkqnJDsze3OYWczg4iWLQjjjrwc")

OLM_BASE = "https://olm.vn"
XOR_KEY = b"1047823200"
V1_QUERY = "v=v1"
V1_SUFFIX = f"?{V1_QUERY}"
# v19.1: bump schema version -> cache cũ tự bị loại, tránh trộn dữ liệu
# trước/sau khi port extractQuestionData.
CURRENT_SCHEMA_VERSION = 7  # v20.2: clean MathType spans + exact Userscript Blob 61/68 data_log
APP_VERSION = "20.2"

# === v20.0 — LIÊN KẾT LẤY KEY MIỄN PHÍ ===
# Người dùng tự điền link trang lấy key vào ĐÂY (hoặc đặt biến môi trường
# OLM_GET_KEY_URL, hoặc sửa trong file cấu hình người dùng — xem GET_KEY_FILE).
# Để trống -> nút "Get Key Free" sẽ mở trang key đi kèm (key_generator.html).
GET_KEY_URL = os.environ.get("OLM_GET_KEY_URL", "").strip()

# === v20.0 — ENDPOINT CẤP KEY (CÁCH B: gọi thẳng Supabase RPC) ===
# Dùng cho key_generator.html khi phát hành dạng file tĩnh.
# Ví dụ: https://<project-ref>.supabase.co/rest/v1/rpc/claim_key
KEY_RPC_URL = os.environ.get("OLM_KEY_RPC_URL", "").strip()
# anon key của Supabase — LUÔN công khai trong mọi app Supabase; ở đây bị RLS +
# REVOKE chặn đọc bảng `key_claims`, chỉ được EXECUTE hàm `claim_key`.
KEY_RPC_ANON = os.environ.get("OLM_KEY_RPC_ANON", "").strip() or SUPABASE_ANON_KEY



def _url_v1(path: str) -> str:
    if not path or "/chu-de/" not in path: return path
    if re.search(r"[?&]v=v1(\b|$)", path): return path
    if re.search(r"[?&]v=v2(\b|$)", path):
        return re.sub(r"([?&])v=v2(\b|$)", r"\1v=v1\2", path)
    if "?" not in path: return path + V1_SUFFIX
    return path + "&" + V1_QUERY


SESSION_F = DATA_DIR / "session.json"
LICENSE_F = DATA_DIR / "license.json"
CRASH_F = DATA_DIR / "crash.log"
Q_CACHE_F = DATA_DIR / "questions_cache.json"
# === v20.0 — cấu hình người dùng (ghi được từ UI, không cần build lại) ===
SETTINGS_F = DATA_DIR / "settings.json"
HOTPATCH_DIR = DATA_DIR / "runtime_patch"
HOTPATCH_F = HOTPATCH_DIR / "olm_hotpatch.py"
DOWNLOAD_DIR = Path.home() / "Downloads"
CHROME_PROFILE = Path(os.path.expanduser("~")) / ".olm_tool_chrome_profile"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
HOTPATCH_DIR.mkdir(parents=True, exist_ok=True)


# === v20.0 - CẤU HÌNH NGƯỜI DÙNG ===
def load_settings() -> dict:
    """Đọc settings.json (link lấy key, tuỳ chọn khác)."""
    try:
        if SETTINGS_F.exists():
            d = json.loads(SETTINGS_F.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
    except Exception as e:
        _log_warn(f"load_settings failed: {e}")
    return {}


def save_settings(d: dict) -> None:
    try:
        cur = load_settings()
        cur.update(d or {})
        SETTINGS_F.write_text(json.dumps(cur, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        _chmod_private(SETTINGS_F)
    except Exception as e:
        _log_warn(f"save_settings failed: {e}")


def get_key_url() -> str:
    """Link trang lấy key miễn phí.

    Thứ tự ưu tiên:
      1. settings.json (người dùng sửa trong app) — khoá "get_key_url"
      2. biến môi trường OLM_GET_KEY_URL
      3. hằng số GET_KEY_URL trong file
      4. "" -> UI sẽ mở key_generator.html đi kèm
    """
    try:
        v = str(load_settings().get("get_key_url") or "").strip()
        if v:
            return v
    except Exception:
        pass
    return GET_KEY_URL


ICON_PNG = RES_DIR / "assets" / "icon.png"
ICON_ICO = RES_DIR / "app_icon.ico"
if not ICON_ICO.exists(): ICON_ICO = APP_DIR / "app_icon.ico"
if not ICON_PNG.exists(): ICON_PNG = APP_DIR / "assets" / "icon.png"

CDP_PORT = 9222
CDP_URL = f"http://localhost:{CDP_PORT}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
IMPERSONATE = "chrome124"


# ═══════════════════════════════════════════════════════════════════════════════
# v20.1 — KHỚP PHIÊN BẢN TRÌNH DUYỆT (sửa lỗi "không lấy đúng cookie thật")
# ═══════════════════════════════════════════════════════════════════════════════
# BUG CŨ: tool luôn gửi UA Chrome/124 + `impersonate="chrome124"` bất kể trình
# duyệt thật là bản nào. Chrome hiện tại (131+) tạo cookie gắn với fingerprint
# TLS/UA mới; khi tool gửi UA 124 cho cùng cookie đó, Cloudflare/OLM thấy
# mismatch -> coi như chưa đăng nhập, quét bài trả rỗng.
# FIX: đọc phiên bản Chrome/Edge ĐANG CÀI rồi sinh UA + impersonate khớp.
_BROWSER_VER_CACHE: Dict[str, Any] = {}


def _chrome_exe_candidates() -> List[str]:
    """Đường dẫn tới Chrome/Edge/Brave/Chromium đã cài (ưu tiên Chrome)."""
    env = os.environ
    cands: List[str] = []
    for c in [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.expandvars(
            r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
    ]:
        if c and os.path.exists(c):
            cands.append(c)
    # Playwright Chromium đi kèm
    pw = _find_chromium_exe()
    if pw:
        cands.append(str(pw))
    return cands


def detect_browser_version() -> Dict[str, Any]:
    """Đọc phiên bản trình duyệt thật -> dùng cho UA + impersonate.

    Trả {'major': int|None, 'full': str, 'impersonate': str,
         'exe': str|None, 'source': str}
    """
    if _BROWSER_VER_CACHE:
        return _BROWSER_VER_CACHE

    full = ""
    source = ""
    exe = None

    # (1) Windows: đọc trực tiếp version của file .exe (nhanh, chính xác nhất)
    for c in _chrome_exe_candidates():
        try:
            info = _win_file_version(c)
            if info:
                exe = c
                full = info
                source = "exe"
                break
        except Exception:
            continue

    # (2) Windows registry (App Paths)
    if not full and sys.platform == "win32":
        try:
            import winreg
            for hive, path, key in [
                (winreg.HKEY_CURRENT_USER,
                 r"SOFTWARE\Google\Chrome\BLBeacon", "version"),
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\Google\Chrome\BLBeacon", "version"),
                (winreg.HKEY_CURRENT_USER,
                 r"SOFTWARE\Microsoft\Edge\BLBeacon", "version"),
            ]:
                try:
                    k = winreg.OpenKey(hive, path)
                    v, _ = winreg.QueryValueEx(k, key)
                    if v:
                        full = str(v)
                        source = "registry"
                        break
                except Exception:
                    continue
        except Exception:
            pass

    # (3) Đọc "Local State" của profile (có last_version)
    if not full:
        for ls in (
            Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"))
            / "Local State",
            Path(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data"))
            / "Local State",
            CHROME_PROFILE / "Local State",
        ):
            try:
                if ls.exists():
                    d = json.loads(ls.read_text(encoding="utf-8",
                                                errors="replace"))
                    v = (d.get("variations_last_versions")
                         or d.get("last_version") or "")
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        v = v[0].get("version", "")
                    m = re.match(r"(\d+\.\d+\.\d+\.\d+)", str(v))
                    if m:
                        full = m.group(1)
                        source = "local_state"
                        break
            except Exception:
                continue

    major = None
    if full:
        m = re.match(r"(\d+)", full)
        if m:
            major = int(m.group(1))

    # impersonate: kiểm tra trực tiếp các target mà curl_cffi hiện tại hỗ trợ
    # (VD: curl_cffi 0.7.4 chỉ hỗ trợ tối đa chrome124, gọi chrome131 sẽ báo lỗi
    # "Impersonating chrome131 is not supported").
    imp = _supported_chrome_impersonate(major or 124)

    ident = "Chrome"
    if exe and "msedge" in exe.lower():
        ident = "Edg"
    elif exe and "brave" in exe.lower():
        ident = "Brave"

    _BROWSER_VER_CACHE.update({
        "major": major,
        "full": full,
        "impersonate": imp,
        "exe": exe,
        "source": source,
        "ident": ident,
    })
    return _BROWSER_VER_CACHE


def _supported_chrome_impersonate(desired_major: int = 124) -> str:
    """Trả về target chrome<N> cao nhất <= desired_major mà curl_cffi thực sự hỗ trợ."""
    supported_vers: List[int] = []
    try:
        from curl_cffi.requests import BrowserType
        names: List[str] = []
        if hasattr(BrowserType, "__members__"):
            names.extend(str(k) for k in BrowserType.__members__.keys())
            names.extend(str(getattr(v, "value", v)) for v in BrowserType.__members__.values())
        else:
            names.extend(str(k) for k in dir(BrowserType) if not k.startswith("_"))
        for n in names:
            m = re.fullmatch(r"chrome(\d+)", n.strip().lower())
            if m:
                supported_vers.append(int(m.group(1)))
    except Exception:
        pass
    if not supported_vers:
        supported_vers = [99, 100, 101, 104, 107, 110, 116, 119, 120, 123, 124]
    supported_vers = sorted(set(supported_vers))
    le = [v for v in supported_vers if v <= max(99, int(desired_major or 124))]
    chosen = le[-1] if le else supported_vers[-1]
    return f"chrome{chosen}"



def _win_file_version(path: str) -> str:
    """Đọc FileVersion của .exe trên Windows (không cần thư viện ngoài)."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        ver = ctypes.WinDLL("version")
        size = ver.GetFileVersionInfoSizeW(ctypes.c_wchar_p(path), None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(ctypes.c_wchar_p(path), 0, size, buf):
            return ""
        r = ctypes.c_void_p()
        ln = wintypes.UINT()
        if not ver.VerQueryValueW(buf, ctypes.c_wchar_p("\\"),
                                  ctypes.byref(r), ctypes.byref(ln)):
            return ""
        if not r.value:
            return ""

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD),
                ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD),
                ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        fi = ctypes.cast(r, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        ms, ls = fi.dwFileVersionMS, fi.dwFileVersionLS
        return "{}.{}.{}.{}".format(
            (ms >> 16) & 0xFFFF, ms & 0xFFFF,
            (ls >> 16) & 0xFFFF, ls & 0xFFFF)
    except Exception:
        return ""


def effective_ua() -> str:
    """UA khớp trình duyệt đang cài (fallback UA mặc định)."""
    info = detect_browser_version()
    if info.get("major"):
        plat = ("Windows NT 10.0; Win64; x64" if sys.platform == "win32"
                else "X11; Linux x86_64")
        ident = info.get("ident") or "Chrome"
        tail = "" if ident == "Chrome" else f" {ident}/{info['major']}.0.0.0"
        return (f"Mozilla/5.0 ({plat}) AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{info['major']}.0.0.0 Safari/537.36{tail}")
    return UA


def effective_impersonate() -> str:
    """impersonate khớp phiên bản Chrome thật (fallback chrome124)."""
    return detect_browser_version().get("impersonate") or IMPERSONATE


def effective_sec_ch_ua() -> Dict[str, str]:
    """Client Hints khớp phiên bản — Cloudflare so cả bộ này với UA.

    Lệch UA vs sec-ch-ua là dấu hiệu bot rõ ràng nhất.
    """
    info = detect_browser_version()
    major = info.get("major") or 124
    ident = info.get("ident") or "Chrome"
    brand = {"Chrome": "Google Chrome", "Edg": "Microsoft Edge",
             "Brave": "Brave"}.get(ident, "Google Chrome")
    return {
        "sec-ch-ua": f'"Chromium";v="{major}", "{brand}";v="{major}", '
                     f'"Not?A_Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"' if sys.platform == "win32"
                              else '"Linux"',
    }

QTYPE_LABEL = {
    1: "Trắc nghiệm", 2: "Điền khuyết", 3: "Điền khuyết", 5: "Sắp xếp",
    6: "Nối", 9: "Gạch chân", 10: "Nhóm", 11: "Chọn từ",
    13: "Đúng/Sai", 20: "Liệt kê", 21: "Tổng hợp", 22: "Tổng hợp",
}
EXAM_TYPE_LABEL = {
    1: "Video", 2: "Lý thuyết", 3: "Luyện tập",
    4: "Kiểm tra PDF", 5: "Video",
    10: "Kiểm tra PDF", 13: "Kiểm tra", 14: "Đề thông minh", 18: "Kiểm tra",
    21: "Đề kiểm tra", 22: "Đề kiểm tra",
}


# ═══════════════════════════════════════════════════════════════
# ANTI-DETECT JS — 11 LAYERS
# ═══════════════════════════════════════════════════════════════
ANTI_DETECT_JS = r"""
(function() {
'use strict';
if (window.__olm_patched_v1) return;
window.__olm_patched_v1 = true;

try { Object.defineProperty(navigator, 'webdriver', { get: () => false }); } catch(e) {}

const origParse = JSON.parse;
JSON.parse = function(text, reviver) {
  let data;
  try { data = origParse.call(this, text, reviver); }
  catch(e) { return origParse.call(this, text, reviver); }
  const forceOrder = (node) => {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node.children) && node.children.length > 0) {
      if ('shuffle' in node) node.shuffle = false;
      if ('random' in node) node.random = false;
      if (node.order || node.type === 'olm-list'
          || node.name === 'quiz-list' || node.name === 'true-false'
          || node.name === 'link-list' || node.name === 'group-list') {
        node.order = node.children.map((_, i) => i);
      }
    }
    if (Array.isArray(node.children)) node.children.forEach(forceOrder);
    if (node && typeof node === 'object' && !Array.isArray(node)) {
      try {
        Object.values(node).forEach(v => {
          if (v && typeof v === 'object' && !Array.isArray(v)) forceOrder(v);
        });
      } catch(e) {}
    }
  };
  if (data && (data.root || Array.isArray(data) || typeof data === 'object')) forceOrder(data);
  return data;
};

// L7b: chặn ghi quầy đếm VIP (khớp nguyên bản userscript).
// NGUYÊN BẢN chỉ chặn: 'prcc' + tiền tố 'tmp_count_q_categories'.
// KHÔNG chặn 'olm_intro_shown' để không phá UI OLM.
const origSet = Storage.prototype.setItem;
Storage.prototype.setItem = function(k, v) {
  if (k === 'prcc' || (typeof k === 'string'
      && k.indexOf('tmp_count_q_categories') === 0)) return;
  return origSet.apply(this, arguments);
};

try { window._shuffle = function(a) { return a; }; } catch(e) {}

window.__OLM_CAPTURED_QUESTIONS = window.__OLM_CAPTURED_QUESTIONS || [];
let _detectQuestion = window.detectQuestion;
try {
  Object.defineProperty(window, 'detectQuestion', {
    configurable: true,
    get: () => _detectQuestion,
    set: (originalFunc) => {
      if (typeof originalFunc !== 'function') { _detectQuestion = originalFunc; return; }
      _detectQuestion = function(...args) {
        let quizInstance;
        try { quizInstance = originalFunc.apply(this, args); }
        catch(e) { throw e; }
        try {
          if (quizInstance && typeof quizInstance === 'object') {
            if (quizInstance.p) {
              if (quizInstance.p.config) quizInstance.p.config.mix = false;
              quizInstance.p.shuffle = 0;
              quizInstance.p.random = 0;
            }
            window.__OLM_CAPTURED_QUESTIONS.push(quizInstance);
            const origGetMany = quizInstance.getMany;
            if (typeof origGetMany === 'function') {
              quizInstance.getMany = function() {
                if (this.p) this.p.shuffle = 0;
                const subs = origGetMany.apply(this, arguments);
                if (Array.isArray(subs)) {
                  subs.forEach(s => {
                    if (s && s.p) s.p.shuffle = 0;
                    window.__OLM_CAPTURED_QUESTIONS.push(s);
                  });
                }
                return subs;
              };
            }
          }
        } catch(e) {}
        return quizInstance;
      };
    }
  });
} catch(e) {}

let _examUI = window['EXAM_UI'];
try {
  Object.defineProperty(window, 'EXAM_UI', {
    configurable: true, enumerable: true,
    get: () => _examUI,
    set: (ui) => {
      if (ui && typeof ui.init === 'function' && !ui.__shuffleDisabled) {
        const originalInit = ui.init;
        ui.init = function(config) {
          if (config) { config.not_shuffle = 1; config.shuffle = 0; config.mix = false; }
          return originalInit.call(this, config);
        };
        ui.__shuffleDisabled = true;
      }
      _examUI = ui;
    }
  });
} catch(e) {}

const FAKE_VIP = { isVip: true, vipPersonal: true, isGuest: false,
  vip_day: 99999, type_vip: 1, limit: 99999, cate_is_olm: false };
const patchCateUI = () => {
  try {
    if (typeof window['CATE_UI'] === 'undefined') return;
    const c = window['CATE_UI'];
    if (typeof c.getData !== 'function') return;
    const state = c.getData();
    if (!state || state['_fakeVipApplied']) return;
    Object.assign(state, FAKE_VIP);
    state['_fakeVipApplied'] = true;
    if (typeof c.initVipLimit === 'function') c.initVipLimit = function() {};
    if (typeof c.showFooterToolbarPractice === 'function')
      try { c.showFooterToolbarPractice(); } catch(e) {}
  } catch(e) {}
};
const patchInterval = setInterval(patchCateUI, 100);
try {
  window.addEventListener('load', () => {
    patchCateUI();
    setTimeout(() => clearInterval(patchInterval), 5000);
  });
} catch(e) {}

const patchAuth = (obj) => {
  if (!obj || typeof obj !== 'object') return;
  try {
    obj['isVip'] = true; obj['vipPersonal'] = true; obj['isGuest'] = false;
    obj['vip_day'] = 99999; obj['limit'] = 99999; obj['type_vip'] = 1;
    Object.defineProperties(obj, {
      'isVip':       { value: true,  writable: false },
      'vipPersonal': { value: true,  writable: false },
      'isGuest':     { value: false, writable: false }
    });
  } catch(e) {}
};
if (window.auth) patchAuth(window.auth);
else {
  let _auth = undefined;
  try {
    Object.defineProperty(window, 'auth', {
      configurable: true, get: () => _auth,
      set: (val) => { patchAuth(val); _auth = val; }
    });
  } catch(e) {
    const ap = setInterval(() => {
      if (window.auth) { patchAuth(window.auth); clearInterval(ap); }
    }, 100);
    setTimeout(() => clearInterval(ap), 10000);
  }
}

const BLOCKED = ['visibilitychange', 'blur', 'contextmenu', 'selectstart', 'copy', 'cut', 'paste'];
try {
  const oD = Document.prototype.addEventListener;
  Document.prototype.addEventListener = function(t, l, o) {
    if (BLOCKED.includes(t)) return;
    return oD.call(this, t, l, o);
  };
} catch(e) {}
try {
  const oW = window.addEventListener.bind(window);
  window.addEventListener = function(t, l, o) {
    if (BLOCKED.includes(t)) return;
    return oW(t, l, o);
  };
} catch(e) {}
try {
  const oET = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function(t, l, o) {
    if (this === window && BLOCKED.includes(t)) return;
    return oET.call(this, t, l, o);
  };
} catch(e) {}
try {
  const nullFunc = () => {};
  Object.defineProperties(document, {
    'oncontextmenu': { set: nullFunc, get: () => null },
    'onselectstart': { set: nullFunc, get: () => null },
    'oncopy':        { set: nullFunc, get: () => null },
    'oncut':         { set: nullFunc, get: () => null },
    'onpaste':       { set: nullFunc, get: () => null }
  });
} catch(e) {}
try {
  Object.defineProperty(document, 'visibilityState', { get: () => 'visible', configurable: true });
  Object.defineProperty(document, 'hidden', { get: () => false, configurable: true });
} catch(e) {}

try {
  window.addEventListener('keydown', (e) => {
    if (e.key === 'F12'
        || (e.ctrlKey && (e.key === 'u' || e.key === 'U'))
        || (e.ctrlKey && e.shiftKey && (e.key === 'i' || e.key === 'I'))
        || (e.ctrlKey && (e.key === 's' || e.key === 'S'))) {
      e.stopImmediatePropagation();
      e.preventDefault();
    }
  }, true);
  window.addEventListener('contextmenu', (e) => e.stopImmediatePropagation(), true);
} catch(e) {}

const blockFS = () => Promise.reject('FS');
try {
  ['requestFullscreen', 'webkitRequestFullscreen', 'mozRequestFullScreen', 'msRequestFullscreen']
    .forEach(p => { if (Element.prototype[p]) Element.prototype[p] = blockFS; });
} catch(e) {}
try { if (document.exitFullscreen) document.exitFullscreen = () => {}; } catch(e) {}

const bve = (e) => { e.stopImmediatePropagation(); e.preventDefault(); };
try {
  window.addEventListener('onVipLimit', bve, true);
  window.addEventListener('onGuestLimit', bve, true);
} catch(e) {}

try {
  if (!window.__olmOlmDecode) {
    window.__olmOlmDecode = function(encoded) {
      if (!encoded) return '';
      try {
        const raw = atob(encoded);
        const keyLen = '1047823200'.length;
        let xored = '';
        for (let i = 0; i < raw.length; i++)
          xored += String.fromCharCode(
            raw.charCodeAt(i) ^ '1047823200'.charCodeAt(i % keyLen)
          );
        const bytes = new Uint8Array(xored.length);
        for (let i = 0; i < xored.length; i++) bytes[i] = xored.charCodeAt(i);
        return new TextDecoder('utf-8').decode(bytes);
      } catch (e) { return encoded; }
    };
  }
} catch(e) {}

})();
"""


# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════
# AUTOFILL JS — v19.1 (port autoAnswerAndFill + DOM fill đặc biệt)
# ═══════════════════════════════════════════════════════════════
AUTOFILL_JS = r"""
(function() {
'use strict';
if (window.__olm_autofill_v1) return;
window.__olm_autofill_v1 = true;

// fireInput: dispatch CẢ 'input' VÀ 'change' với bubbles:true.
// OLM thường attach listener ở form cha -> bubbles là bắt buộc.
const fireInput = (el) => {
  try { el.dispatchEvent(new Event('input', { bubbles: true })); } catch(e) {}
  try { el.dispatchEvent(new Event('change', { bubbles: true })); } catch(e) {}
};

const fireMouse = (el, type) => {
  try {
    el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true,
                                            view: window }));
  } catch(e) {}
  try { el.dispatchEvent(new Event(type, { bubbles: true })); } catch(e) {}
};

const normalize = (s) => String(s || '').toLowerCase().replace(/\s+/g, ' ').trim();

const SAFE_OBSERVE = (target, callback, opts) => {
  if (!target || !(target instanceof Node)) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded',
        () => SAFE_OBSERVE(document.body, callback, opts), { once: true });
    } else if (!document.body) {
      try {
        const obs2 = new MutationObserver((mut, o) => {
          if (document.body) { o.disconnect(); SAFE_OBSERVE(document.body, callback, opts); }
        });
        obs2.observe(document.documentElement || document, { childList: true, subtree: true });
      } catch(e) {}
    } else {
      SAFE_OBSERVE(document.body, callback, opts);
    }
    return null;
  }
  try {
    const obs = new MutationObserver(callback);
    obs.observe(target, opts);
    return obs;
  } catch (e) { return null; }
};

// ── Tìm đáp án theo so khớp 40 ký tự đầu của content (y userscript) ──
const findAnswerForQuestion = (questionText, type) => {
  const captured = window.__OLM_CAPTURED_QUESTIONS || [];
  for (const q of captured) {
    const content = (q && q.p && q.p.content) || '';
    if (!content) continue;
    if (questionText.includes(content.substring(0, 40))) {
      if (type === 'text' || type === 'fill') {
        if (q.correctAnswer) return q.correctAnswer;
        if (q.correctAnswers && q.correctAnswers.length) return q.correctAnswers[0];
      }
      if (type === 'radio') {
        if (q.correctIndex !== undefined) return String.fromCharCode(65 + parseInt(q.correctIndex));
        if (q.correctAnswers && q.correctAnswers.length) return q.correctAnswers[0];
      }
      if (type === 'checkbox') {
        if (q.correctAnswers && Array.isArray(q.correctAnswers)) return q.correctAnswers;
      }
    }
  }
  return null;
};

// ── AUTO ANSWER (bản gốc userscript) ──
const autoAnswerAndFill = () => {
  try {
    document.querySelectorAll(
      'input[type="text"], input[type="number"], textarea'
    ).forEach(input => {
      if (input.value.trim() !== '') return;
      const parent = input.closest(
        '.question-item, .quiz-question, .cau-hoi, [data-question-id]'
      );
      if (!parent) return;
      const ans = findAnswerForQuestion(parent.innerText.trim(), 'text');
      if (ans) { input.value = ans; fireInput(input); }
    });
  } catch(e) {}

  try {
    const groups = new Map();
    document.querySelectorAll('input[type="radio"]').forEach(r => {
      const n = r.getAttribute('name');
      if (n) groups.set(n, (groups.get(n) || []).concat(r));
    });
    groups.forEach((group, name) => {
      const parent = group[0].closest('.question-item, .quiz-question, .cau-hoi');
      if (!parent) return;
      const letter = findAnswerForQuestion(parent.innerText.trim(), 'radio');
      if (letter) {
        // so khớp theo value (y userscript), fallback theo index
        let target = group.find(r => String(r.value || '').toUpperCase()
                                     === String(letter).toUpperCase());
        if (!target && /^[A-Z]$/.test(String(letter))) {
          const idx = String(letter).toUpperCase().charCodeAt(0) - 65;
          target = group[idx];
        }
        if (target && !target.checked) { target.checked = true; fireInput(target); }
      }
    });
  } catch(e) {}

  try {
    const cg = new Map();
    document.querySelectorAll('input[type="checkbox"]').forEach(c => {
      const n = c.getAttribute('name');
      if (n) cg.set(n, (cg.get(n) || []).concat(c));
    });
    cg.forEach((group, name) => {
      const parent = group[0].closest('.question-item, .quiz-question, .cau-hoi');
      if (!parent) return;
      const ans = findAnswerForQuestion(parent.innerText.trim(), 'checkbox');
      if (Array.isArray(ans)) {
        ans.forEach((val, idx) => {
          if (val === '1' && group[idx] && !group[idx].checked) {
            group[idx].checked = true; fireInput(group[idx]);
          }
        });
      } else if (ans === '1' && group[0] && !group[0].checked) {
        group[0].checked = true; fireInput(group[0]);
      }
    });
  } catch(e) {}
};

// ── DOM FILL CHO CÁC q_type ĐẶC BIỆT (q5/6/9/10/20/13-multi/3-11/2) ──
window.__olm_fill_special = function(questions) {
  const out = { order: 0, match: 0, underline: 0, group: 0, pool: 0,
                checkbox: 0, errors: [], matchInfo: 0 };
  if (!Array.isArray(questions) || !questions.length) return out;

  const findByMatch = (selector, root) => {
    const scope = root || document;
    return Array.from(scope.querySelectorAll(selector));
  };

  // helper: so khớp câu hỏi với node DOM theo 40 ký tự đầu
  const hitQuestion = (el, questions) => {
    const pn = normalize(el && el.innerText);
    if (!pn) return null;
    for (const q of questions) {
      const qn = normalize(q.text);
      if (qn && pn.includes(qn.substring(0, 40))) return q;
    }
    return null;
  };

  // ── q_type 3/11: điền pool index vào từng ô text theo index ──
  try {
    document.querySelectorAll(
      'input[type="text"], input[type="number"], textarea'
    ).forEach(input => {
      if (input.value.trim() !== '') return;
      const parent = input.closest(
        '.question-item, .quiz-question, .cau-hoi, [data-question-id]');
      if (!parent) return;
      const q = hitQuestion(parent, questions);
      if (!q) return;
      if (!Array.isArray(q.choose_label)) return;
      const sib = Array.from(parent.querySelectorAll(
        'input[type="text"], input[type="number"], textarea'));
      const idx = sib.indexOf(input);
      const v = (q.choose_label[idx] !== undefined)
        ? q.choose_label[idx] : q.choose_label[0];
      input.value = String(v);
      fireInput(input);
      out.pool++;
    });
  } catch(e) { out.errors.push('pool:' + e); }

  // ── q_type 13 multi_select: tick đúng các checkbox ──
  try {
    const cg = new Map();
    document.querySelectorAll('input[type="checkbox"]').forEach(c => {
      const n = c.getAttribute('name');
      if (n) cg.set(n, (cg.get(n) || []).concat(c));
    });
    cg.forEach(group => {
      const parent = group[0].closest('.question-item, .quiz-question, .cau-hoi');
      if (!parent) return;
      const q = hitQuestion(parent, questions);
      if (!q) return;
      const label = q.choose_label;
      if (!Array.isArray(label)) return;
      label.forEach(i => {
        const box = group[i];
        if (box && !box.checked) { box.checked = true; fireInput(box); }
      });
      out.checkbox++;
    });
  } catch(e) { out.errors.push('checkbox:' + e); }

  // ── q_type 10: kéo thả nhóm — gán DOM theo answerIndices ──
  try {
    document.querySelectorAll(
      '.olm-list, [name="group-list"], .group-list, .question-item, .cau-hoi'
    ).forEach(container => {
      const q = hitQuestion(container, questions);
      if (!q || q.type !== 10 || !Array.isArray(q.answerIndices)) return;
      const cols = Array.from(container.querySelectorAll('.position-column'));
      if (!cols.length) return;
      const flatIdx = [];
      q.answerIndices.forEach(g => (g || []).forEach(i => flatIdx.push(i)));
      flatIdx.forEach((srcIdx, dstIdx) => {
        const src = cols[srcIdx], dst = cols[dstIdx];
        if (!src || !dst || src === dst) return;
        try {
          const box = dst.parentNode ? dst.parentNode : dst;
          box.insertBefore(src, dst);
          fireMouse(src, 'mousedown'); fireMouse(src, 'mouseup');
          out.group++;
        } catch(e) { out.errors.push('group:' + e); }
      });
    });
  } catch(e) { out.errors.push('group:' + e); }

  // ── q_type 5/20: sắp xếp lại DOM theo correctAnswers ──
  try {
    document.querySelectorAll(
      '.olm-list, [name="quiz-list"], .question-item, .cau-hoi')
      .forEach(container => {
        const q = hitQuestion(container, questions);
        if (!q || (q.type !== 5 && q.type !== 20)) return;
        const arr = q.correctAnswers;
        if (!Array.isArray(arr) || !arr.length) return;
        const kids = Array.from(container.children).filter(k => k.nodeType === 1);
        if (kids.length !== arr.length) return;
        const frag = document.createDocumentFragment();
        arr.forEach(i => { if (kids[i]) frag.appendChild(kids[i]); });
        container.appendChild(frag);
        fireMouse(container, 'mouseup');
        out.order++;
      });
  } catch(e) { out.errors.push('order:' + e); }

  // ── q_type 6: nối cặp — dispatch mousedown/mouseup theo order ──
  try {
    const link = document.querySelector('[name="link-list"], .link-list');
    if (link) {
      const q = (questions || []).find(x => x.type === 6);
      if (q) {
        Array.from(link.children || []).forEach((it, i) => {
          fireMouse(it, 'mousedown');
          fireMouse(it, 'mouseup');
          fireMouse(it, 'click');
          out.matchInfo++;
        });
        out.match++;
      }
    }
  } catch(e) { out.errors.push('match:' + e); }

  // ── q_type 9: gạch chân — click các .under-line theo index ──
  try {
    const uls = findByMatch('.under-line', document);
    if (uls.length) {
      const q = (questions || []).find(x => x.type === 9);
      if (q && Array.isArray(q.correctAnswers)) {
        q.correctAnswers.forEach(i => {
          const el = uls[i];
          if (el) { fireMouse(el, 'mousedown'); fireMouse(el, 'mouseup');
                    fireMouse(el, 'click'); out.underline++; }
        });
      }
    }
  } catch(e) { out.errors.push('underline:' + e); }

  return out;
};

window.__olm_autofill_run = function() {
  autoAnswerAndFill();
  return true;
};

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => setTimeout(autoAnswerAndFill, 1500));
} else {
  setTimeout(autoAnswerAndFill, 1500);
}

SAFE_OBSERVE(document.body, () => autoAnswerAndFill(),
             { childList: true, subtree: true });

SAFE_OBSERVE(document.body, () => {
  if (window.MathJax && window.MathJax.typesetPromise) {
    window.MathJax.typesetPromise().catch(() => {});
  }
}, { childList: true, subtree: true });
})();
"""


# === v20.0 - PORT FROM USERSCRIPT (DOM): LÀM BÀI THẬT SỰ ===
# Mục tiêu: KHÔNG chỉ POST dữ liệu. Phải TICK/CHỌN/KÉO THẢ thật trong DOM để
# OLM tự ghi nhận câu trả lời (ô được chọn, list được sắp, dòng được gạch chân),
# rồi bấm nút Nộp của chính OLM.
#
# Căn cứ từ userscript (blob 18 + blob 126/88):
#   blob 126:  "li.correctAnswer", "$input[data-accept]",
#              ".selecttext, .dragtext", ".under-line"
#   blob 88:   "ol:not(.true-false)", "ol.true-false", "li",
#              "$input[data-accept]", "data-accept", "split('||')"
#   => OLM render: <ol>/<li> cho quiz-list/true-false, input[data-accept] cho
#      điền khuyết, .selecttext/.dragtext cho chọn từ, .under-line cho gạch chân.
REAL_SUBMIT_JS = r"""
(function() {
'use strict';
if (window.__olm_real_submit) return;
window.__olm_real_submit = true;

/* ── tiện ích chuột: mô phỏng thao tác người dùng thật ── */
const centerOf = (el) => {
  try {
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2),
             y: Math.round(r.top + r.height / 2),
             w: Math.round(r.width), h: Math.round(r.height) };
  } catch (e) { return { x: 0, y: 0, w: 0, h: 0 }; }
};

const mouseAt = (el, type, pt, extra) => {
  const c = pt || centerOf(el);
  const opts = Object.assign({
    bubbles: true, cancelable: true, composed: true, view: window,
    clientX: c.x, clientY: c.y, screenX: c.x, screenY: c.y, button: 0
  }, extra || {});
  let ev;
  try { ev = new MouseEvent(type, opts); }
  catch (e) { ev = new Event(type, { bubbles: true, cancelable: true }); }
  try { el.dispatchEvent(ev); } catch (e) {}
  return ev;
};

const pointerAt = (el, type, pt, extra) => {
  const c = pt || centerOf(el);
  try {
    const ev = new PointerEvent(type, Object.assign({
      bubbles: true, cancelable: true, composed: true,
      clientX: c.x, clientY: c.y, pointerId: 1, pointerType: 'mouse',
      isPrimary: true, button: 0, buttons: type === 'pointerup' ? 0 : 1
    }, extra || {}));
    el.dispatchEvent(ev);
  } catch (e) { mouseAt(el, type.replace('pointer', 'mouse'), c, extra); }
};

/* click thật: pointerdown -> mousedown -> focus -> pointerup -> mouseup -> click */
const realClick = (el) => {
  if (!el) return false;
  try { el.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) {}
  const c = centerOf(el);
  pointerAt(el, 'pointerdown', c);
  mouseAt(el, 'mousedown', c);
  try { if (typeof el.focus === 'function') el.focus(); } catch (e) {}
  pointerAt(el, 'pointerup', c);
  mouseAt(el, 'mouseup', c);
  let ok = true;
  try {
    if (typeof el.click === 'function') el.click();
    else ok = false;
  } catch (e) { ok = false; }
  if (!ok) mouseAt(el, 'click', c);
  return true;
};

/* kéo–thả mô phỏng: dùng cho sortable / jQuery UI / HTML5 DnD */
const realDrag = (src, dst) => {
  if (!src || !dst || src === dst) return false;
  try { src.scrollIntoView({ block: 'center' }); } catch (e) {}
  const a = centerOf(src), b = centerOf(dst);
  pointerAt(src, 'pointerdown', a);
  mouseAt(src, 'mousedown', a);
  mouseAt(src, 'dragstart', a);
  try {
    const dt = new DataTransfer();
    dt.setData('text/plain', '1');
    src.dispatchEvent(new DragEvent('dragstart', {
      bubbles: true, cancelable: true, dataTransfer: dt }));
  } catch (e) {}
  // vài bước trung gian để thư viện kịp nhận
  for (let k = 1; k <= 6; k++) {
    const p = { x: Math.round(a.x + (b.x - a.x) * k / 6),
                y: Math.round(a.y + (b.y - a.y) * k / 6) };
    pointerAt(document, 'pointermove', p);
    mouseAt(document, 'mousemove', p);
    mouseAt(src, 'drag', p);
  }
  pointerAt(dst, 'pointermove', b);
  mouseAt(dst, 'mousemove', b);
  try {
    const dt2 = new DataTransfer();
    dt2.setData('text/plain', '1');
    dst.dispatchEvent(new DragEvent('dragover', {
      bubbles: true, cancelable: true, dataTransfer: dt2 }));
    dst.dispatchEvent(new DragEvent('drop', {
      bubbles: true, cancelable: true, dataTransfer: dt2 }));
  } catch (e) {}
  mouseAt(dst, 'mouseup', b);
  pointerAt(dst, 'pointerup', b);
  mouseAt(src, 'dragend', b);
  pointerAt(document, 'pointerup', b);
  return true;
};

/* gán giá trị + phát event như người dùng gõ */
const setNativeValue = (el, value) => {
  const v = String(value);
  try {
    const d = Object.getOwnPropertyDescriptor(el, 'value');
    if (d && d.set) d.set.call(el, v);
    else el.value = v;
  } catch (e) { try { el.value = v; } catch (e2) {} }
  ['input', 'change', 'keyup', 'blur'].forEach(t => {
    try { el.dispatchEvent(new Event(t, { bubbles: true })); } catch (e) {}
  });
  return el.value;
};

const fireInput = (el) => {
  try { el.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
  try { el.dispatchEvent(new Event('change', { bubbles: true })); } catch (e) {}
  try { el.dispatchEvent(new Event('keyup', { bubbles: true })); } catch (e) {}
};

const norm = (s) => String(s || '').toLowerCase().replace(/\s+/g, ' ').trim();

/* ghép câu hỏi (từ tool) với node DOM theo 40 ký tự đầu của đề bài */
const QSEL = '.question-item, .quiz-question, .cau-hoi, [data-question-id],' +
             ' .question, .q-item, li.question, .item-question';
const matchQuestion = (el, questions) => {
  if (!el) return null;
  const pn = norm(el.innerText);
  if (!pn) return null;
  for (const q of questions) {
    const qn = norm(q.text);
    if (qn && qn.length >= 8 && pn.indexOf(qn.substring(0, 40)) !== -1) return q;
  }
  return null;
};

/* list các "khối câu hỏi" trên trang — fallback khi không có class chuẩn */
const questionBlocks = () => {
  let blocks = Array.from(document.querySelectorAll(QSEL));
  blocks = blocks.filter(b => b.querySelector(
    'input, textarea, select, li, .under-line, [class*=drag], [class*=drop]'));
  if (blocks.length) return blocks;
  const ols = Array.from(document.querySelectorAll('ol, ul'));
  return ols.map(o => o.closest('div,li,section') || o).filter(Boolean);
};

/* pool text của OLM: input[data-accept] chứa "đáp án||nhiễu" */
const acceptPool = (scope) => {
  const out = [];
  (scope || document).querySelectorAll('input[data-accept]').forEach(i => {
    const raw = i.getAttribute('data-accept') || '';
    raw.split('||').forEach((s, k) => {
      const t = String(s).trim();
      if (t) out.push({ text: t, accept: i, index: out.length, part: k });
    });
  });
  return out;
};

/* ══════════════════════════════════════════════════════════════════
   FILL TOÀN BỘ — trả về báo cáo chi tiết
   ══════════════════════════════════════════════════════════════════ */
window.__olm_fill_real = function(questions) {
  const R = { text: 0, radio: 0, checkbox: 0, select: 0, pool: 0,
              dragPool: 0, matching: 0, underline: 0, order: 0,
              group: 0, lists: 0, errors: [], unmatched: 0 };

  const blocks = questionBlocks();
  const byQ = new Map();
  for (const b of blocks) {
    const q = matchQuestion(b, questions);
    if (q && !byQ.has(q.id)) byQ.set(q.id, { q, el: b });
  }
  R.matched = byQ.size;
  R.unmatched = Math.max(0, (questions || []).length - byQ.size);

  /* ── 1. INPUT TEXT / NUMBER / TEXTAREA ── */
  try {
    document.querySelectorAll(
      'input[type="text"], input[type="number"], textarea'
    ).forEach(inp => {
      if ((inp.value || '').trim() !== '') return;
      const host = inp.closest(QSEL) || inp.parentElement;
      const q = matchQuestion(host, questions);
      if (!q) return;
      let v;
      if (Array.isArray(q.choose_label)) {
        const sib = Array.from(
          (host || document).querySelectorAll(
            'input[type="text"], input[type="number"], textarea'));
        const idx = sib.indexOf(inp);
        v = (q.choose_label[idx] !== undefined) ? q.choose_label[idx]
                                                : q.choose_label[0];
      } else {
        v = q.choose_label;
      }
      if (v === undefined || v === null) return;
      setNativeValue(inp, v);
      R.text++;
    });
  } catch (e) { R.errors.push('text:' + e); }

  /* ── 2. RADIO (so khớp value trước, fallback index) ── */
  try {
    const groups = new Map();
    document.querySelectorAll('input[type="radio"]').forEach(r => {
      const n = r.getAttribute('name') || ('__r' + r.name);
      groups.set(n, (groups.get(n) || []).concat(r));
    });
    groups.forEach(group => {
      if (!group.length) return;
      const host = group[0].closest(QSEL);
      const q = matchQuestion(host, questions);
      if (!q) return;
      let label = q.choose_label;
      if (Array.isArray(label)) label = label[0];
      const letter = String(label == null ? '' : label).toUpperCase();
      let target = null;
      for (const r of group) {
        const rv = String(r.value == null ? '' : r.value).trim().toUpperCase();
        const ra = String(r.getAttribute('data-accept') || '').trim().toUpperCase();
        const ran = String(r.getAttribute('data-answer') || '').trim().toUpperCase();
        if (rv === letter || ra === letter || ran === letter) { target = r; break; }
      }
      if (!target && /^[A-Z]$/.test(letter)) {
        const i = letter.charCodeAt(0) - 65;
        if (group[i]) target = group[i];
      }
      if (target) {
        realClick(target);
        if (!target.checked) { target.checked = true; fireInput(target); }
        R.radio++;
      }
    });
  } catch (e) { R.errors.push('radio:' + e); }

  /* ── 3. CHECKBOX (q13 multi: label = mảng index đúng) ── */
  try {
    const groups = new Map();
    document.querySelectorAll('input[type="checkbox"]').forEach(c => {
      const n = c.getAttribute('name') || ('__c' + c.name);
      groups.set(n, (groups.get(n) || []).concat(c));
    });
    groups.forEach(group => {
      if (!group.length) return;
      const host = group[0].closest(QSEL);
      const q = matchQuestion(host, questions);
      if (!q) return;
      const label = q.choose_label;
      if (!Array.isArray(label)) return;
      label.forEach(i => {
        const box = group[i];
        if (!box) return;
        if (!box.checked) {
          realClick(box);
          if (!box.checked) { box.checked = true; fireInput(box); }
        }
        R.checkbox++;
      });
    });
  } catch (e) { R.errors.push('checkbox:' + e); }

  /* ── 4. SELECT ── */
  try {
    document.querySelectorAll('select').forEach(sel => {
      const host = sel.closest(QSEL) || sel.parentElement;
      const q = matchQuestion(host, questions);
      if (!q) return;
      let label = q.choose_label;
      if (Array.isArray(label)) label = label[0];
      const want = String(label == null ? '0' : label);
      let picked = false;
      for (const opt of Array.from(sel.options || [])) {
        if (String(opt.value) === want || norm(opt.text) === norm(want)) {
          sel.value = opt.value; picked = true; break;
        }
      }
      if (!picked) { try { sel.value = want; picked = true; } catch (e) {} }
      if (picked) { fireInput(sel); R.select++; }
    });
  } catch (e) { R.errors.push('select:' + e); }

  /* ── 5. CHỌN TỪ TRONG CÂU (.selecttext / <select> trong câu) ── */
  try {
    document.querySelectorAll('.selecttext, select.selecttext').forEach(el => {
      const host = el.closest(QSEL) || el.parentElement;
      const q = matchQuestion(host, questions);
      if (!q) return;
      const label = Array.isArray(q.choose_label) ? q.choose_label[0] : q.choose_label;
      const want = String(label == null ? '0' : label);
      if (el.tagName === 'SELECT') {
        let done = false;
        for (const opt of Array.from(el.options || [])) {
          if (String(opt.value) === want) { el.value = opt.value; done = true; break; }
        }
        if (!done) { try { el.value = want; done = true; } catch (e) {} }
        if (done) fireInput(el);
      } else {
        realClick(el);
      }
      R.text++;
    });
  } catch (e) { R.errors.push('selecttext:' + e); }

  /* ── 6. POOL: điền / kéo–thả từ kho có sẵn ── */
  try {
    const poolItems = acceptPool(document);
    if (poolItems.length) {
      const wantTexts = [];
      (questions || []).forEach(q => {
        if (q.type === 3 || q.type === 11) {
          const lb = q.choose_label;
          const idxs = Array.isArray(lb) ? lb : [0];
          const pool = q.pool || [];
          idxs.forEach(i => {
            if (pool[i] !== undefined && pool[i] !== null && pool[i] !== '') {
              wantTexts.push(String(pool[i]));
            }
          });
        }
      });

      // 6a. điền vào input có data-accept / input trống cạnh kho
      const inputs = Array.from(document.querySelectorAll(
        'input[data-accept], .fillme-input, input[type="text"]'));
      let k = 0;
      inputs.forEach(inp => {
        if ((inp.value || '').trim() !== '') return;
        if (!inp.closest(QSEL)) return;
        if (k >= wantTexts.length) return;
        const host = inp.closest(QSEL);
        const q = matchQuestion(host, questions);
        if (!q || (q.type !== 2 && q.type !== 3 && q.type !== 11)) return;
        setNativeValue(inp, wantTexts[k]);
        R.pool++; k++;
      });

      // 6b. kéo–thả từ kho vào ô (khi OLM dùng drag-drop)
      const dropZones = Array.from(document.querySelectorAll(
        '[class*=dragtext], [class*=drop], [class*=fill], input[data-accept]'));
      let d = 0;
      dropZones.forEach(z => {
        if (d >= wantTexts.length) return;
        const item = poolItems.find(p => p.text === wantTexts[d]) || poolItems[d];
        if (item && item.accept && item.accept !== z) {
          if (realDrag(item.accept, z)) { R.dragPool++; d++; }
        }
      });
    }
  } catch (e) { R.errors.push('pool:' + e); }

  /* ── 7. SẮP XẾP (q5 / q20): mô phỏng kéo từng item về đúng vị trí ── */
  try {
    const sortables = Array.from(document.querySelectorAll(
      'ol, ul, [class*=sortable], [class*=list-item], .olm-list'));
    sortables.forEach(list => {
      const host = list.closest(QSEL) || list.parentElement;
      const q = matchQuestion(host, questions);
      if (!q || (q.type !== 5 && q.type !== 20)) return;
      const arr = q.correctAnswers;
      if (!Array.isArray(arr) || arr.length < 2) return;
      let kids = Array.from(list.children).filter(n => n.nodeType === 1);
      if (kids.length !== arr.length) return;
      // kéo phần tử tại vị trí đích vào đúng chỗ, y như người dùng sắp
      for (let pos = 0; pos < arr.length; pos++) {
        const wantEl = kids[arr[pos]];
        const curEl = kids[pos];
        if (!wantEl || !curEl || wantEl === curEl) continue;
        realDrag(wantEl, curEl);
      }
      // đồng bộ DOM (một số lib không tự đổi DOM)
      kids = Array.from(list.children).filter(n => n.nodeType === 1);
      if (kids.length === arr.length) {
        const frag = document.createDocumentFragment();
        arr.forEach(i => { if (kids[i]) frag.appendChild(kids[i]); });
        list.appendChild(frag);
      }
      try { if (window.jQuery) window.jQuery(list).trigger('sortupdate'); } catch (e) {}
      R.order++; R.lists++;
    });
  } catch (e) { R.errors.push('order:' + e); }

  /* ── 8. NỐI CẶP (q6): click A rồi click 1 theo cặp ── */
  try {
    const linkList = document.querySelector(
      '[name="link-list"], .link-list, ol.link-list, [class*=link-list]');
    if (linkList) {
      const kids = Array.from(linkList.children).filter(n => n.nodeType === 1);
      kids.forEach(it => { realClick(it); R.matching++; });
      R.lists++;
    }
    // dạng 2 cột: click ô trái rồi ô phải
    const cols = document.querySelectorAll('[class*=match] > *, [class*=link] li');
    if (cols.length && !linkList) {
      cols.forEach(it => { realClick(it); R.matching++; });
    }
  } catch (e) { R.errors.push('matching:' + e); }

  /* ── 9. GẠCH CHÂN (q9): click .under-line theo index ── */
  try {
    const uls = Array.from(document.querySelectorAll('.under-line'));
    if (uls.length) {
      const q = (questions || []).find(x => x.type === 9);
      const idxs = (q && Array.isArray(q.correctAnswers)) ? q.correctAnswers : [];
      idxs.forEach(i => {
        const el = uls[i];
        if (el) { realClick(el); R.underline++; }
      });
      R.lists++;
    }
  } catch (e) { R.errors.push('underline:' + e); }

  /* ── 10. KÉO NHÓM (q10) ── */
  try {
    document.querySelectorAll(
      '[class*=group-list], [name="group-list"], [class*=position-column]'
    ).forEach(() => {});
    const groups = document.querySelectorAll('[class*=group-list], [name="group-list"]');
    groups.forEach(list => {
      const q = (questions || []).find(x => x.type === 10);
      if (!q || !Array.isArray(q.answerIndices)) return;
      const cols = Array.from(list.querySelectorAll('.position-column, [class*=column]'));
      if (!cols.length) return;
      const flat = [];
      q.answerIndices.forEach(g => (g || []).forEach(i => flat.push(i)));
      flat.forEach((srcIdx, dstIdx) => {
        const src = cols[srcIdx], dst = cols[dstIdx];
        if (src && dst && src !== dst) {
          if (realDrag(src, dst)) R.group++;
          else {
            try {
              const holder = dst.parentNode || dst;
              holder.insertBefore(src, dst);
              R.group++;
            } catch (e) {}
          }
        }
      });
    });
  } catch (e) { R.errors.push('group:' + e); }

  return R;
};

/* ══════════════════════════════════════════════════════════════════
   TÌM & BẤM NÚT NỘP CỦA CHÍNH OLM
   ══════════════════════════════════════════════════════════════════ */
const SUBMIT_TXT = /^(nộp bài|nộp|hoàn thành|kết thúc|lưu bài|trả lời|gửi bài|submit|finish|done)$/i;

window.__olm_find_submit = function() {
  const cands = [];
  document.querySelectorAll(
    'button, a, input[type="button"], input[type="submit"], [role="button"],' +
    ' .btn, [class*=submit], [id*=submit], [class*=nop-bai], [class*=nopbai]'
  ).forEach(el => {
    if (el.disabled) return;
    const t = norm(el.innerText || el.value || el.getAttribute('aria-label') || '');
    const cls = String(el.className || '') + ' ' + String(el.id || '');
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return;
    let score = 0;
    if (SUBMIT_TXT.test(t)) score += 10;
    if (/submit|nop-?bai|nopbai|finish|ketthuc|hoanthanh/i.test(cls)) score += 6;
    if (/nộp/i.test(t)) score += 4;
    if (el.tagName === 'BUTTON') score += 1;
    if (score > 0) cands.push({ el, score, text: t });
  });
  cands.sort((a, b) => b.score - a.score);
  return cands.length ? cands[0] : null;
};

/* trạng thái bài: đã nộp chưa / điểm bao nhiêu */
window.__olm_read_result = function() {
  const out = { submitted: false, verified_server_record: false,
                score: null, correct: null, total: null,
                text: '', hasAnswers: false };
  try {
    out.hasAnswers = !!document.querySelector(
      'li.correctAnswer:not(.olm-highlight), [class*=correct-answer]:not(.olm-highlight)');
  } catch (e) {}

  const body = norm((document.body && document.body.innerText) || '');

  // Chỉ bắt mẫu "Điểm: 8/10" hoặc "Đúng 8/10" (KHÔNG bắt "0/9 câu" của thanh tiến độ khi chưa nộp)
  let m = body.match(/điểm\s*(?:số|của bạn)?\s*[:\uFF1A]\s*(\d+(?:[.,]\d+)?)\s*\/\s*(\d+(?:[.,]\d+)?)/i);
  if (m) { out.score = parseFloat(m[1].replace(',', '.')); out.total = parseFloat(m[2]); }
  if (!m) {
    m = body.match(/số\s+câu\s+đúng\s*[:\uFF1A]?\s*(\d+)\s*\/\s*(\d+)/i);
    if (m) { out.correct = parseInt(m[1], 10); out.total = parseInt(m[2], 10); }
  }

  if (/nộp bài thành công|bạn đã nộp bài|đã hoàn thành bài|chúc mừng bạn đã hoàn thành/i
      .test(body)) {
    out.submitted = true;
  }
  if (out.score !== null || out.correct !== null) out.submitted = true;
  out.text = body.slice(0, 4000);
  return out;
};

/* đọc lại đáp án ĐÃ chọn trong DOM (kiểm chứng việc tick có hiệu lực) */
window.__olm_read_choices = function() {
  const c = { radio: [], checkbox: [], text: [], select: [], underline: 0 };
  try {
    document.querySelectorAll('input[type="radio"]').forEach(r => {
      if (r.checked) c.radio.push(String(r.value || '') + '|' +
                                   String(r.getAttribute('name') || ''));
    });
    document.querySelectorAll('input[type="checkbox"]').forEach(b => {
      if (b.checked) c.checkbox.push(String(b.value || '') + '|' +
                                      String(b.getAttribute('name') || ''));
    });
    document.querySelectorAll('input[type="text"], input[type="number"], textarea')
      .forEach(i => { if ((i.value || '').trim()) c.text.push(String(i.value).trim()); });
    document.querySelectorAll('select').forEach(s => c.select.push(String(s.value)));
    c.underline = document.querySelectorAll(
      '.under-line.active, .under-line.selected, .under-line.correctAnswer'
    ).length;
  } catch (e) {}
  return c;
};

/* gọi hàm nộp native nếu OLM phơi ra global */
window.__olm_native_submit = async function() {
  const names = ['submitQuiz', 'submit_quiz', 'submitExam', 'submit_exam',
                 'submitExercise', 'submitAnswer', 'finishQuiz', 'endExam',
                 'nopBai', 'submitTest'];
  for (const n of names) {
    try {
      if (typeof window[n] === 'function') {
        await window[n]();
        return { ok: true, via: 'global:' + n };
      }
    } catch (e) { return { ok: false, via: 'global:' + n, error: String(e) }; }
  }
  try {
    const E = window.EXAM_UI || window.QUIZ_UI;
    if (E && typeof E.submit === 'function') {
      await E.submit();
      return { ok: true, via: 'EXAM_UI.submit' };
    }
  } catch (e) { return { ok: false, via: 'EXAM_UI.submit', error: String(e) }; }
  try {
    const cap = window.__OLM_CAPTURED_QUESTIONS || [];
    for (const q of cap) {
      if (q && typeof q.finish === 'function') {
        await q.finish();
        return { ok: true, via: 'quiz.finish' };
      }
      if (q && typeof q.submit === 'function') {
        await q.submit();
        return { ok: true, via: 'quiz.submit' };
      }
    }
  } catch (e) { return { ok: false, via: 'quiz', error: String(e) }; }
  return { ok: false, via: null };
};

})();
"""


# === v20.0 - POST CHUẨN USERSCRIPT (/course/teacher-static) ===
# Trích từ luồng userscript (Blob 54-69): gửi form-urlencoded tới /course/teacher-static
# với X-CSRF-TOKEN đọc từ meta csrf-token hoặc cookie XSRF-TOKEN.
_PAGE_POST_JS = r"""async (args) => {
    const [idCate, payload] = args;
    try {
        let csrf = '';
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) {
            csrf = meta.content.trim();
        }
        if (!csrf) {
            const ck = document.cookie.split(';');
            for (const c of ck) {
                const t = c.trim();
                if (t.startsWith('XSRF-TOKEN=')) {
                    csrf = decodeURIComponent(t.substring(11));
                    break;
                }
            }
        }
        try {
            if (payload && payload.data_log) {
                window.localStorage.setItem('data', String(payload.data_log));
            }
        } catch(e) {}
        const params = new URLSearchParams();
        for (const [k, v] of Object.entries(payload)) {
            if (v === null || v === undefined) {
                params.append(k, '');
            } else if (Array.isArray(v)) {
                const arrKey = k.endsWith('[]') ? k : (k + '[]');
                for (const item of v) {
                    params.append(arrKey, String(item));
                }
            } else if (typeof v === 'object') {
                for (const [sk, sv] of Object.entries(v)) {
                    params.append(k + '[' + sk + ']', String(sv));
                }
            } else {
                params.append(k, String(v));
            }
        }
        const r = await fetch('/course/teacher-static', {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRF-TOKEN': csrf,
                'Accept': 'application/json, text/plain, */*',
                'Referer': 'https://olm.vn/chu-de/' + idCate + '?v=v1',
                'Origin': 'https://olm.vn'
            },
            body: params.toString()
        });
        const text = await r.text();
        let json = null;
        try { json = JSON.parse(text); } catch(e) {}
        return { status: r.status, body: text.slice(0, 500), json: json };
    } catch(e) {
        return { status: 0, error: String(e) };
    }
}"""


# HELPERS
# ═══════════════════════════════════════════════════════════════
def _msgbox(title: str, msg: str, style: int = 0) -> int:
    if sys.platform != "win32":
        print(f"[{title}] {msg}"); return 1
    try:
        import ctypes
        return ctypes.windll.user32.MessageBoxW(0, str(msg), str(title), style)
    except Exception:
        print(f"[{title}] {msg}"); return 1


def _log_crash(tb: str) -> None:
    try: CRASH_F.write_text(tb, encoding="utf-8")
    except Exception: pass


def _log_warn(msg: str) -> None:
    try: print(f"[WARN] {msg}")
    except Exception: pass


def _log_error(event: str, **kw) -> None:
    try:
        parts = [f"{k}={v}" for k, v in kw.items()]
        print(f"[ERROR] {event} | " + " | ".join(parts))
    except Exception: pass


def _chmod_private(p: Path) -> None:
    try:
        if sys.platform != "win32" and p.exists(): p.chmod(0o600)
    except Exception: pass


_bg_loop: Optional[asyncio.AbstractEventLoop] = None
_bg_ready = threading.Event()


def _start_bg_loop() -> None:
    global _bg_loop
    _bg_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_bg_loop)
    _bg_ready.set()
    _bg_loop.run_forever()


threading.Thread(target=_start_bg_loop, daemon=True, name="olm-bg").start()
_bg_ready.wait()


def run_async(coro, timeout: float = 300):
    fut = asyncio.run_coroutine_threadsafe(coro, _bg_loop)
    return fut.result(timeout=timeout)


def get_hwid() -> str:
    parts: List[str] = []
    if sys.platform == "win32":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
            guid, _ = winreg.QueryValueEx(k, "MachineGuid")
            parts.append(str(guid))
        except Exception: pass
    for fn in (lambda: str(uuid.getnode()), lambda: platform.node(), lambda: platform.machine()):
        try: parts.append(fn())
        except Exception: pass
    raw = "|".join(parts) or str(uuid.uuid4())
    return hashlib.sha256(raw.encode()).hexdigest()[:32].upper()


def b64x_decode(encoded: str) -> str:
    if not encoded: return ""
    try:
        raw = base64.b64decode(encoded)
        out = bytes(b ^ XOR_KEY[i % len(XOR_KEY)] for i, b in enumerate(raw))
        return out.decode("utf-8", errors="replace")
    except Exception: return ""


def try_json(s):
    try: return json.loads(s)
    except Exception: return None


def extract_id_from_input(s: str) -> Optional[str]:
    s = (s or "").strip()
    if not s: return None
    if re.fullmatch(r"\d{6,}", s): return s
    for pat in (r"/(?:chu-de|bai-tap|lam-bai)/[A-Za-z0-9\-_.]*?(\d{6,})(?:[/?#]|$)",
                r"/chu-de/(\d{6,})", r"/bai-tap/(\d{6,})", r"/lam-bai/(\d{6,})",
                r"[?&]id_cate=(\d{6,})", r"[?&]idCate=(\d{6,})",
                r"[?&]id_category=(\d{6,})"):
        m = re.search(pat, s, re.I)
        if m: return m.group(1)
    m = re.search(r"(\d{6,})", s)
    return m.group(1) if m else None


def _port_open(host="127.0.0.1", port=CDP_PORT, timeout=0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout): return True
    except Exception: return False


def _cdp_available() -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2) as r:
            return {"available": True, "browser": json.loads(r.read()).get("Browser", "?")}
    except Exception: return {"available": False}


def _find_chrome() -> Optional[str]:
    for c in [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]:
        if c and os.path.exists(c): return c
    return None


def _launch_chrome() -> Dict[str, Any]:
    if _port_open(): return {"ok": True, "already": True}
    exe = _find_chrome()
    if not exe: return {"ok": False, "error": "Không tìm thấy Chrome/Edge"}
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    args = [exe, f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={CHROME_PROFILE}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=AutomationControlled,Translate,OptimizationHints",
            "--disable-infobars", "--no-service-autorun",
            "--password-store=basic", "--use-mock-keychain", OLM_BASE]
    try:
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP \
            if sys.platform == "win32" else 0
        subprocess.Popen(args, creationflags=flags, close_fds=True)
    except Exception as e: return {"ok": False, "error": f"Launch lỗi: {e}"}
    for _ in range(40):
        if _port_open(): return {"ok": True, "already": False, "path": exe}
        time.sleep(0.5)
    return {"ok": False, "error": "Chrome không phản hồi CDP"}


def _find_chromium_exe() -> Optional[Path]:
    if not BROWSERS_DIR.exists(): return None
    for name in ["chrome.exe", "headless_shell.exe", "chrome", "headless_shell"]:
        for p in BROWSERS_DIR.rglob(name):
            if p.is_file(): return p
    return None


def check_chromium() -> Dict[str, Any]:
    if not HAS_PW: return {"ok": False, "installed": False, "error": "Playwright chưa cài"}
    exe = _find_chromium_exe()
    if exe: return {"ok": True, "installed": True, "path": str(exe)}
    return {"ok": True, "installed": False}


def install_chromium(progress_cb: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    if not HAS_PW: return {"ok": False, "error": "Playwright chưa cài"}
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
    except ImportError as e: return {"ok": False, "error": f"Import driver fail: {e}"}
    try: driver_executable, driver_cli = compute_driver_executable()
    except Exception as e: return {"ok": False, "error": f"Driver fail: {e}"}
    env = get_driver_env()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)
    env.setdefault("PLAYWRIGHT_SKIP_BROWSER_GC", "1")
    if progress_cb: progress_cb("Đang tải Chromium (~150MB)...")
    try:
        cf = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            [str(driver_executable), str(driver_cli), "install", "chromium"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=cf, text=True, encoding="utf-8", errors="replace")
        out: List[str] = []
        start = time.time()
        while True:
            line = proc.stdout.readline() if proc.stdout else ""
            if line:
                out.append(line.strip())
                if progress_cb: progress_cb(line.strip()[:200])
            if proc.poll() is not None: break
            if time.time() - start > 900:
                proc.kill()
                return {"ok": False, "error": "Timeout (>15 phút)"}
            if not line: time.sleep(0.1)
        if proc.returncode != 0:
            return {"ok": False, "error": f"Exit {proc.returncode}"}
        exe = _find_chromium_exe()
        if exe: return {"ok": True, "path": str(exe)}
        return {"ok": False, "error": "Không tìm thấy exe sau khi cài"}
    except Exception as e: return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# RICH CONTENT HELPERS — v1.0.1 hotfix v3
# ═══════════════════════════════════════════════════════════════
_IMG_TAG_RE = re.compile(r'<img\b[^>]*>', re.I)
_LATEX_INLINE_RE = re.compile(r'\\\((.*?)\\\)', re.S)
_LATEX_DISPLAY_RE = re.compile(r'\\\[(.*?)\\\]', re.S)
_LATEX_DOLLAR2_RE = re.compile(r'\$\$(.*?)\$\$', re.S)
_TAG_RE = re.compile(r'<[^>]+>')
_OPT_PREFIX_RE = re.compile(r'^\s*[A-Z]\s*[\.\)]\s', re.I)
_QUIZ_LIST_CLASS_RE = re.compile(
    r'class=["\'][^"\']*\b(?:quiz-list|true-false|options|choice|list-quiz)\b', re.I)


_MATHTYPE_SPAN_RE = re.compile(
    r'<span\b[^>]*?\bdata-latex-mathtype\s*=\s*(["\'])(.*?)\1[^>]*>(.*?)</span>',
    re.S | re.I,
)
_MATHTYPE_ATTR_RE = re.compile(
    r'\s+\bdata-(?:latex-mathtype|mathtype|wiris[a-z0-9_-]*)\s*=\s*(["\']).*?\1',
    re.S | re.I,
)


def _clean_mathtype_spans_in_html(s: str) -> str:
    """Loại bỏ hoàn toàn thuộc tính `data-mathtype="eyJYbWwi..."` (Base64 XML của MathType)
    và `data-latex-mathtype="..."` TRƯỚC khi tách công thức LaTeX `$$...$$` / `\\(...\\)`.

    Nguyên nhân lỗi (Hình 3, 4, 5): OLM nhúng công thức `$$...$$` ngay bên trong thuộc tính
    `<span class="math-q" data-latex-mathtype="$$F(x;y)$$" data-mathtype="eyJYbWwi...">$$F(x;y)$$</span>`.
    Nếu tách regex `$$...$$` trước khi gỡ thuộc tính HTML, thẻ `<span>` bị cắt đôi làm lộ
    hàng nghìn ký tự Base64 `data-mathtype="eyJYbWwi..."` ra màn hình và nhân đôi công thức.
    """
    if not s:
        return s
    low = s.lower()
    if "mathtype" not in low and "math-q" not in low and "wiris" not in low:
        return s

    # 1) Thay thế trọn vẹn `<span ... data-latex-mathtype="..." ...>inner</span>` bằng 1 bản LaTeX duy nhất TRƯỚC TIÊN
    def _repl_span(m):
        attr_latex = (m.group(2) or "").strip()
        inner = (m.group(3) or "").strip()
        inner_plain = _TAG_RE.sub(" ", inner).strip()
        if inner_plain:
            return f" {inner_plain} "
        if attr_latex:
            if not (attr_latex.startswith("$$") or attr_latex.startswith("\\(") or attr_latex.startswith("\\[")):
                return f" \\({attr_latex}\\) "
            return f" {attr_latex} "
        return " "

    for _ in range(3):
        next_s = _MATHTYPE_SPAN_RE.sub(_repl_span, s)
        if next_s == s:
            break
        s = next_s

    # 2) Xoá mọi thuộc tính data-mathtype / data-latex-mathtype / data-wiris còn sót trên bất kỳ thẻ nào
    s = _MATHTYPE_ATTR_RE.sub("", s)

    # 3) Dọn trường hợp chuỗi đã bị cắt nửa thẻ hoặc đã HTML-escape từ cache cũ
    if "&lt;span" in s.lower() or "data-mathtype" in s.lower() or "data-latex-mathtype" in s.lower():
        s = re.sub(
            r'&lt;span\b[^&>]*?\bdata-latex-mathtype=(?:&quot;|["\'])\s*',
            ' ',
            s,
            flags=re.I,
        )
        s = re.sub(
            r'(?:&quot;|["\'])?\s*\bdata-mathtype=(?:&quot;|["\'])[A-Za-z0-9+/=\s]+(?:&quot;|["\'])\s*(?:&gt;|>)',
            ' ',
            s,
            flags=re.I,
        )
        s = re.sub(
            r'<span\b[^>]*?\bdata-latex-mathtype\s*=\s*["\']?\s*',
            ' ',
            s,
            flags=re.I,
        )
        s = re.sub(
            r'["\']?\s*\bdata-mathtype\s*=\s*["\'][A-Za-z0-9+/=\s]*["\']?\s*>?',
            ' ',
            s,
            flags=re.I,
        )

    # 4) Khử công thức bị nhân đôi sát nhau do cả attribute lẫn inner text cùng chứa 1 công thức
    s = re.sub(r'(\\\([^\n\\]{1,120}?\\\))\s*(?:&lt;/span&gt;|</span>)?\s*\1', r'\1', s)
    return s


def _normalize_inline_math_str(s: str) -> str:
    """Convert OLM's $$...$$ inline math delimiters to \\(...\\) so sentences don't break into lines."""
    if not s:
        return s
    s = _clean_mathtype_spans_in_html(s)
    if "$$" in s:
        s = _LATEX_DOLLAR2_RE.sub(lambda m: "\\(" + m.group(1).strip() + "\\)", s)
    if "mathtype" in s.lower():
        s = _clean_mathtype_spans_in_html(s)
    return s


def _decode_html_entities(s: str) -> str:
    """Decode HTML entities: &agrave;→à, &nbsp;→space, &amp;→&, ..."""
    if not s:
        return s
    try:
        # &nbsp; needs special handling before unescape → replace with space
        s = re.sub(r'&nbsp;', ' ', s, flags=re.I)
        return html_module.unescape(s)
    except Exception:
        return s


_B64_STRICT_RE = re.compile(r'^[A-Za-z0-9+/]+={0,2}$')


def _decode_olm_content(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip()
    if s.startswith("<") and ">" in s:
        return _clean_mathtype_spans_in_html(s)
    if len(s) >= 8 and len(s) % 4 == 0 and _B64_STRICT_RE.match(s):
        try:
            decoded = b64x_decode(s)
            if (decoded
                    and "\ufffd" not in decoded
                    and not re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', decoded)
                    and ("<" in decoded or "\\" in decoded)):
                return _clean_mathtype_spans_in_html(decoded)
        except Exception:
            pass
    return _clean_mathtype_spans_in_html(s)


def _normalize_media_url(src: str) -> str:
    if not src:
        return ""
    s = src.strip()
    if s.startswith("//"):
        return "https:" + s
    if s.startswith("http") or s.startswith("data:"):
        return s
    if s.startswith("/"):
        return OLM_BASE + s
    return OLM_BASE + "/" + s.lstrip("/")


def _html_escape(s: str) -> str:
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _preserve_imgs_in_option_html(fragment: str) -> str:
    """Keep <img> tags inside option HTML as normalized <img src="..."> tokens before stripping tags."""
    if not fragment or "<img" not in fragment.lower():
        return fragment

    def _repl(m):
        tag = m.group(0)
        src_m = re.search(r'src=["\']([^"\']+)["\']', tag, re.I)
        if not src_m:
            return ""
        url = _normalize_media_url(src_m.group(1))
        return f' [[OLM_IMG:{url}]] '

    return _IMG_TAG_RE.sub(_repl, fragment)


def _restore_img_tokens_to_tag(s: str) -> str:
    if not s or "[[OLM_IMG:" not in s:
        return s
    return re.sub(
        r'\[\[OLM_IMG:([^\]]+)\]\]',
        lambda m: f'<img src="{m.group(1)}">',
        s,
    )


def _format_opt_html(txt: str) -> str:
    """Escape option text safely while preserving <img src="..."> tags and inline math."""
    s = _normalize_inline_math_str(str(txt or ""))
    if "<img " not in s and "[[OLM_IMG:" not in s:
        return _html_escape(s)
    s = _restore_img_tokens_to_tag(s)
    parts = []
    pos = 0
    for m in re.finditer(r'<img\s+src="([^"]+)"\s*/?>', s, re.I):
        before = s[pos:m.start()].strip()
        if before:
            parts.append(_html_escape(before))
        url = _html_escape(_normalize_media_url(m.group(1)))
        parts.append(
            f'<img src="{url}" alt="hình" loading="lazy" '
            f'onerror="this.style.display=\'none\'">'
        )
        pos = m.end()
    tail = s[pos:].strip()
    if tail:
        parts.append(_html_escape(tail))
    return " ".join(parts) if parts else _html_escape(s)


def _strip_option_html(html: str) -> str:
    """Remove <ol>/<ul> option lists from HTML (quiz-list, true-false, or >=2 <li> items)."""
    if not html:
        return html
    for _ in range(4):
        before = html
        for pat in [r'<ol\b[^>]*>.*?</ol>', r'<ul\b[^>]*>.*?</ul>']:
            matches = list(re.finditer(pat, html, re.S | re.I))
            for m in reversed(matches):
                block = m.group(0)
                open_tag = block.split(">", 1)[0] + ">"
                lis = re.findall(r'<li\b[^>]*>(.*?)</li>', block, re.S | re.I)
                if _QUIZ_LIST_CLASS_RE.search(open_tag) or len(lis) >= 2:
                    html = html[:m.start()] + html[m.end():]
        if html == before:
            break
    return html


def _extract_options_from_content(content_html: str) -> List[str]:
    """Parse `content` HTML để lấy text (hoặc ảnh) các option A/B/C/D."""
    if not content_html:
        return []
    content_html = _clean_mathtype_spans_in_html(content_html)
    items = re.findall(r'<li\b[^>]*>(.*?)</li>', content_html, re.S | re.I)
    if not items:
        candidates = re.findall(r'<(?:p|div)\b[^>]*>(.*?)</(?:p|div)>',
                                content_html, re.S | re.I)
        items = []
        for c in candidates:
            plain = _TAG_RE.sub("", c)
            plain = _decode_html_entities(plain).strip()
            if _OPT_PREFIX_RE.match(plain):
                items.append(c)
    out = []
    for it in items:
        it_clean = _clean_mathtype_spans_in_html(it)
        with_img = _preserve_imgs_in_option_html(it_clean)
        txt = _TAG_RE.sub(" ", with_img)
        txt = _decode_html_entities(txt)
        txt = _normalize_inline_math_str(txt)
        txt = re.sub(r'\s+', ' ', txt).strip()
        txt = re.sub(r'^[A-Za-z]\s*[\.\)]\s*', '', txt)
        txt = re.sub(r'^\d+\s*[\.\)]\s*', '', txt)
        txt = _restore_img_tokens_to_tag(txt).strip()
        if txt:
            out.append(txt[:500])
    return out


def _rich_from_text(html_fragment: str) -> List[dict]:
    """Trích text + LaTeX từ 1 fragment HTML (đã decode entity).
    Chuyển $$...$$ của OLM thành inline math \\(...\\) để câu văn liền mạch trên cùng dòng.
    """
    if not html_fragment:
        return []
    cleaned = _clean_mathtype_spans_in_html(html_fragment)
    normalized = _normalize_inline_math_str(cleaned)
    out: List[dict] = []
    parts = _LATEX_DISPLAY_RE.split(normalized)
    for i, p in enumerate(parts):
        if i % 2 == 1:
            m_txt = _decode_html_entities(p.strip())
            out.append({"kind": "math", "latex": "\\(" + m_txt + "\\)"})
            continue
        sub = _LATEX_INLINE_RE.split(p)
        for j, q in enumerate(sub):
            if j % 2 == 1:
                m_txt = _decode_html_entities(q.strip())
                out.append({"kind": "math", "latex": "\\(" + m_txt + "\\)"})
                continue
            plain = _TAG_RE.sub(" ", q)
            plain = _decode_html_entities(plain)
            plain = _clean_mathtype_spans_in_html(plain)
            plain = re.sub(r'[\u00a0\s]+', ' ', plain).strip()
            if plain:
                out.append({"kind": "text", "text": plain})
    return out


def _collect_rich_from_html(content_html: str) -> List[dict]:
    if not content_html:
        return []
    content_html = _clean_mathtype_spans_in_html(content_html)
    blocks: List[dict] = []
    pos = 0
    for m in _IMG_TAG_RE.finditer(content_html):
        before = content_html[pos:m.start()]
        if before.strip():
            blocks.extend(_rich_from_text(before))
        tag = m.group(0)
        src_m = re.search(r'src=["\']([^"\']+)["\']', tag, re.I)
        alt_m = re.search(r'alt=["\']([^"\']*)["\']', tag, re.I)
        if src_m:
            blocks.append({
                "kind": "image",
                "src": _normalize_media_url(src_m.group(1)),
                "alt": (alt_m.group(1) if alt_m else "") or "hình",
            })
        pos = m.end()
    tail = content_html[pos:]
    if tail.strip():
        blocks.extend(_rich_from_text(tail))
    return blocks


def _render_rich_to_html(blocks: List[dict]) -> str:
    parts = []
    for b in blocks:
        k = b.get("kind")
        if k == "image":
            src = _html_escape(b["src"])
            raw_alt = (b.get("alt") or "").strip()
            alt = _html_escape(raw_alt or "hình")
            cap = (f'<figcaption>{alt}</figcaption>'
                   if raw_alt and raw_alt.lower() not in ("hình", "image", "img")
                   else "")
            parts.append(
                f'<figure class="olm-figure">'
                f'<img src="{src}" alt="{alt}" loading="lazy" '
                f'onerror="this.parentElement.classList.add(\'img-error\')">'
                f'{cap}</figure>'
            )
        elif k == "math":
            latex = _normalize_inline_math_str(b.get("latex") or "")
            parts.append(f'<span class="olm-math">{_html_escape(latex)}</span>')
        else:
            txt = _normalize_inline_math_str(b.get("text") or "")
            parts.append(f'<span class="olm-text">{_html_escape(txt)}</span>')
    return " ".join(parts)


def _render_rich_to_text(blocks: List[dict]) -> str:
    parts = []
    for b in blocks:
        k = b.get("kind")
        if k == "text":
            parts.append(_normalize_inline_math_str(b.get("text") or ""))
        elif k == "math":
            parts.append(_normalize_inline_math_str(b.get("latex") or ""))
        elif k == "image":
            parts.append(f"[{b.get('alt') or 'hình'}]")
    return re.sub(r'\s+', ' ', " ".join(parts)).strip()


# ═══════════════════════════════════════════════════════════════
# SUPABASE CLIENT
# ═══════════════════════════════════════════════════════════════
class SupabaseClient:
    def __init__(self, url: str, anon_key: str):
        self.url = url.rstrip("/")
        self.anon_key = anon_key

    def _req(self, method, path, body=None, prefer="") -> Tuple[int, Any]:
        url = f"{self.url}/rest/v1/{path}"
        headers = {"apikey": self.anon_key,
                   "Authorization": f"Bearer {self.anon_key}",
                   "Content-Type": "application/json"}
        if prefer: headers["Prefer"] = prefer
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                txt = r.read().decode("utf-8", errors="replace")
                try: return r.status, (json.loads(txt) if txt else None)
                except Exception: return r.status, txt
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", errors="replace")
            try: return e.code, (json.loads(txt) if txt else None)
            except Exception: return e.code, txt
        except Exception as e: return 0, str(e)

    def select(self, table, params=""):
        return self._req("GET", f"{table}?{params}" if params else table)

    def patch(self, table, params, body):
        return self._req("PATCH", f"{table}?{params}", body=body, prefer="return=representation")

    def is_configured(self) -> bool:
        return ("YOUR-PROJECT" not in self.url and "YOUR-ANON-KEY" not in self.anon_key
                and self.url.startswith("http"))


# ═══════════════════════════════════════════════════════════════
# LICENSE MANAGER
# ═══════════════════════════════════════════════════════════════
class LicenseManager:
    def __init__(self):
        self.hwid = get_hwid()
        self.sb = SupabaseClient(SUPABASE_URL, SUPABASE_ANON_KEY)
        self.cache = self._load_cache()

    def _load_cache(self):
        try: return json.loads(LICENSE_F.read_text(encoding="utf-8"))
        except Exception: return {}

    def _save_cache(self):
        try:
            LICENSE_F.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
            _chmod_private(LICENSE_F)
        except Exception: pass

    def get_hwid(self) -> str: return self.hwid

    def _find_active_by_hwid(self):
        code, rows = self.sb.select("licenses",
            f"hwid=eq.{urllib.parse.quote(self.hwid)}&revoked=eq.false&select=*")
        if code != 200 or not isinstance(rows, list): return None
        now = datetime.now(timezone.utc)
        for r in rows:
            exp = r.get("expires_at")
            if not exp: continue
            try:
                e = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                if e > now: return r
            except Exception: continue
        return None

    def validate(self):
        if not self.sb.is_configured():
            return {"ok": False, "reason": "Supabase chưa cấu hình", "hwid": self.hwid}
        row = self._find_active_by_hwid()
        if not row:
            ce = self.cache.get("expires_at")
            if ce:
                try:
                    e = datetime.fromisoformat(ce.replace("Z", "+00:00"))
                    if e > datetime.now(timezone.utc):
                        secs = int((e - datetime.now(timezone.utc)).total_seconds())
                        return {"ok": True, "cached": True, "seconds_left": secs,
                                "expires_at": ce, "hwid": self.hwid, **self.cache}
                except Exception: pass
            return {"ok": False, "reason": "Chưa kích hoạt / hết hạn", "hwid": self.hwid}
        self.cache = {"key": row.get("key"), "expires_at": row.get("expires_at"),
                      "key_type": row.get("key_type"),
                      "duration_days": row.get("duration_days"),
                      "activated_at": row.get("activated_at")}
        self._save_cache()
        try:
            e = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            secs = int((e - datetime.now(timezone.utc)).total_seconds())
        except Exception: secs = 0
        return {"ok": True, "seconds_left": secs, "expires_at": row.get("expires_at"),
                "hwid": self.hwid, **self.cache}

    def activate(self, key: str):
        if not self.sb.is_configured():
            return {"ok": False, "error": "Supabase chưa cấu hình"}
        key = (key or "").strip()
        if not key: return {"ok": False, "error": "Key trống"}
        existing = self._find_active_by_hwid()
        if existing and existing.get("key") != key:
            return {"ok": False, "error": f"Máy đã khóa với key {existing.get('key')}."}
        code, rows = self.sb.select("licenses", f"key=eq.{urllib.parse.quote(key)}&select=*")
        if code != 200 or not isinstance(rows, list) or not rows:
            return {"ok": False, "error": "Key không tồn tại"}
        row = rows[0]
        if row.get("revoked"): return {"ok": False, "error": "Key đã bị hủy"}
        if row.get("hwid") and row["hwid"] != self.hwid:
            return {"ok": False, "error": "Key đã khóa máy khác"}
        if row.get("hwid") == self.hwid and row.get("expires_at"):
            try:
                e = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
                if e > datetime.now(timezone.utc):
                    self.cache = {"key": key, "expires_at": row["expires_at"],
                                  "key_type": row.get("key_type"),
                                  "duration_days": row.get("duration_days"),
                                  "activated_at": row.get("activated_at")}
                    self._save_cache()
                    return {"ok": True, "reactivated": True,
                            "expires_at": row["expires_at"], "key": key,
                            "key_type": row.get("key_type"),
                            "days": row.get("duration_days")}
            except Exception: pass
        days = int(row.get("duration_days") or 1)
        now = datetime.now(timezone.utc)
        exp = now + timedelta(days=days)
        body = {"hwid": self.hwid, "activated_at": now.isoformat(), "expires_at": exp.isoformat()}
        code, _ = self.sb.patch("licenses", f"key=eq.{urllib.parse.quote(key)}", body)
        if code not in (200, 204):
            return {"ok": False, "error": f"Lỗi cập nhật ({code})"}
        self.cache = {"key": key, "expires_at": exp.isoformat(),
                      "key_type": row.get("key_type"),
                      "duration_days": days, "activated_at": now.isoformat()}
        self._save_cache()
        return {"ok": True, "expires_at": exp.isoformat(), "key": key,
                "key_type": row.get("key_type"), "days": days}

    def deactivate(self):
        if not self.sb.is_configured():
            return {"ok": False, "error": "Supabase chưa cấu hình"}
        row = self._find_active_by_hwid()
        if not row:
            self.cache = {}; self._save_cache()
            return {"ok": True, "message": "Không có key nào đang khóa"}
        body = {"hwid": None, "activated_at": None, "expires_at": None, "revoked": True}
        code, _ = self.sb.patch("licenses", f"key=eq.{urllib.parse.quote(row['key'])}", body)
        self.cache = {}; self._save_cache()
        if code not in (200, 204):
            return {"ok": False, "error": f"Lỗi hủy ({code})"}
        return {"ok": True, "message": f"Đã hủy key {row['key']}"}


# ═══════════════════════════════════════════════════════════════
# OLM SESSION
# ═══════════════════════════════════════════════════════════════
class OLMSession:
    def __init__(self):
        self.cookies: Dict[str, str] = {}
        self.user_id: Optional[str] = None
        self.username: Optional[str] = None
        self._csrf: Optional[str] = None

    @classmethod
    def load(cls) -> "OLMSession":
        s = cls()
        if SESSION_F.exists():
            try:
                d = json.loads(SESSION_F.read_text(encoding="utf-8"))
                s.cookies = d.get("cookies", {})
                s.user_id = d.get("user_id")
                s.username = d.get("username")
            except Exception: pass
        return s

    def save(self) -> None:
        try:
            SESSION_F.write_text(json.dumps({
                "cookies": self.cookies,
                "user_id": self.user_id,
                "username": self.username,
            }, ensure_ascii=False), encoding="utf-8")
            _chmod_private(SESSION_F)
        except Exception: pass

    def clear(self) -> None:
        self.cookies = {}
        self.user_id = None
        self.username = None
        self._csrf = None
        try:
            if SESSION_F.exists(): SESSION_F.unlink()
        except Exception: pass

    def set_cookies(self, raw: str) -> None:
        self.cookies = {}
        for part in raw.split(";"):
            part = part.strip()
            if "=" not in part: continue
            n, v = part.split("=", 1)
            self.cookies[n.strip()] = v.strip()
        self._extract_user()
        self._csrf = None

    def set_cookie_dict(self, d: Dict[str, str]) -> None:
        self.cookies = dict(d)
        self._extract_user()
        self._csrf = None

    def _extract_user(self) -> None:
        raw = self.cookies.get("user")
        if raw:
            try:
                u = json.loads(urllib.parse.unquote(raw))
                self.user_id = str(u.get("_id") or u.get("id") or "")
                self.username = u.get("username") or ""
            except Exception: pass

    @staticmethod
    def _extract_uid_static(cd: Dict[str, str]) -> Optional[str]:
        raw = cd.get("user")
        if not raw: return None
        try:
            u = json.loads(urllib.parse.unquote(raw))
            return str(u.get("_id") or u.get("id") or "") or None
        except Exception: return None

    @staticmethod
    def _extract_uname_static(cd: Dict[str, str]) -> Optional[str]:
        raw = cd.get("user")
        if not raw: return None
        try: return json.loads(urllib.parse.unquote(raw)).get("username")
        except Exception: return None

    def has_auth(self) -> bool:
        keys = {k.lower() for k in self.cookies.keys()}
        has_s = any(k in keys for k in ("onlinemath_session", "laravel_session"))
        has_p = ("user" in keys or "refresh_token" in keys
                 or any(k.startswith("remember_web_") for k in keys))
        return has_s and has_p

    def csrf(self) -> str:
        if self._csrf: return self._csrf
        v = self.cookies.get("XSRF-TOKEN") or self.cookies.get("X-CSRF-TOKEN") or ""
        self._csrf = urllib.parse.unquote(v)
        return self._csrf

    async def client(self, impersonate_override: Optional[str] = None) -> CurlAsyncSession:
        # v20.1: UA + impersonate + Client Hints phải KHỚP phiên bản trình duyệt
        # đã tạo ra cookie. Lệch phiên bản -> Cloudflare/OLM coi là bot.
        hdrs = {
            "User-Agent": effective_ua(),
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,*/*;q=0.8"),
            "Accept-Encoding": "gzip, deflate, br",
            "Upgrade-Insecure-Requests": "1",
        }
        try:
            hdrs.update(effective_sec_ch_ua())
        except Exception:
            pass
        ident = {"Edg": "Microsoft Edge",
                 "Brave": "Brave"}.get(
                     detect_browser_version().get("ident") or "", "")
        if ident:
            hdrs["sec-ch-ua-full-version"] = (
                f'"{detect_browser_version().get("full") or ""}"')
        imp = impersonate_override or effective_impersonate()
        return CurlAsyncSession(
            base_url=OLM_BASE, cookies=self.cookies, timeout=30.0,
            impersonate=imp,
            headers=hdrs,
            allow_redirects=True)

    async def request(self, method: str, url: str, **kw):
        last_err = None
        for imp in (effective_impersonate(), "chrome124", "chrome120", "chrome110"):
            try:
                async with await self.client(impersonate_override=imp) as c:
                    r = await c.request(method, url, **kw)
                    try:
                        jar = c.cookies
                        newc = jar.get_dict() if hasattr(jar, "get_dict") else dict(jar)
                        for n, v in newc.items():
                            if n != "__cflb": self.cookies[n] = v
                    except Exception: pass
                    try:
                        ct = r.headers.get("content-type", "")
                        if "html" in ct and r.text and "<meta" in r.text:
                            m = re.search(r'name=["\']csrf-token["\'][^>]*content=["\']([^"\']+)', r.text)
                            if not m:
                                m = re.search(r'content=["\']([^"\']+)["\'][^>]*name=["\']csrf-token', r.text)
                            if m: self._csrf = m.group(1)
                    except Exception: pass
                    if _BROWSER_VER_CACHE and _BROWSER_VER_CACHE.get("impersonate") != imp:
                        _BROWSER_VER_CACHE["impersonate"] = imp
                    self.save()
                    return r
            except Exception as e:
                last_err = e
                es = str(e).lower()
                if "impersonat" in es or "not supported" in es:
                    continue
                raise
        raise last_err  # type: ignore[misc]

    async def refresh_csrf(self, id_cate: Optional[str] = None) -> str:
        path = _url_v1(f"/chu-de/{id_cate}") if id_cate else "/"
        try:
            r = await self.request("GET", path)
            txt = r.text or ""
            m = re.search(r'name=["\']csrf-token["\'][^>]*content=["\']([^"\']+)', txt)
            if not m:
                m = re.search(r'content=["\']([^"\']+)["\'][^>]*name=["\']csrf-token', txt)
            if m:
                self._csrf = m.group(1)
                return self._csrf
        except Exception: pass
        return self.csrf()


# ═══════════════════════════════════════════════════════════════
# CDP MANAGER
# ═══════════════════════════════════════════════════════════════
class CDPManager:
    def __init__(self):
        self.pw = None
        self.browser = None
        self._alock = None

    async def _lock(self):
        if self._alock is None: self._alock = asyncio.Lock()
        return self._alock

    async def attach(self):
        lock = await self._lock()
        async with lock:
            if self.browser is not None:
                try:
                    _ = self.browser.contexts
                    return {"ok": True, "browser": self.browser, "reused": True}
                except Exception: self.browser = None
            if not HAS_PW: return {"ok": False, "error": "Playwright chưa cài"}
            if not _cdp_available().get("available"):
                return {"ok": False, "error": "CDP không khả dụng"}
            if self.pw is None: self.pw = await async_playwright().start()
            try:
                self.browser = await self.pw.chromium.connect_over_cdp(CDP_URL, timeout=15000)
            except Exception as e:
                return {"ok": False, "error": f"Attach lỗi: {e}"}
            return {"ok": True, "browser": self.browser}

    async def get_olm_page(self):
        r = await self.attach()
        if not r.get("ok"): return None, r.get("error", "Attach failed")
        browser = r["browser"]
        if not browser.contexts: return None, "Chrome chưa có context"
        ctx = browser.contexts[0]
        page = None
        for p in ctx.pages:
            try:
                if "olm.vn" in (p.url or ""):
                    page = p; break
            except Exception: continue
        if not page:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        return page, None

    async def sync_cookies_to_session(self, sess: OLMSession) -> None:
        r = await self.attach()
        if not r.get("ok"): return
        browser = r["browser"]
        if not browser.contexts: return
        ctx = browser.contexts[0]
        try: cookies = await ctx.cookies()
        except Exception: return
        for c in cookies:
            try:
                if "olm.vn" in (c.get("domain") or ""):
                    sess.cookies[c["name"]] = c["value"]
            except Exception: continue
        sess._extract_user()
        sess.save()


# ═══════════════════════════════════════════════════════════════════════════════
# v20.1 — LẤY COOKIE THẬT  (sửa lỗi "không lấy đúng cookie thật")
# ═══════════════════════════════════════════════════════════════════════════════
# BUG CŨ (3 nguyên nhân chồng nhau):
#   1. `ctx.cookies()` gọi KHÔNG tham số -> chỉ trả cookie của những URL đang
#      mở trong context. Tab là about:blank thì trả RỖNG -> báo "Chưa login".
#   2. Lọc `"olm.vn" in domain` -> BỎ SÓT cookie đặt ở domain cha `.olm.vn`
#      hoặc subdomain khác của OLM.
#   3. Không kiểm chứng cookie bằng request thật -> báo "thành công" dù cookie
#      đã hết hạn, rồi mọi thao tác sau (quét bài) đều rỗng.
#
# FIX:
#   * `ctx.cookies(OLM_BASE)` -> cookie ĐÚNG origin OLM (có cả HttpOnly).
#   * Thu mọi domain thuộc OLM (không lọc cứng theo 1 chuỗi).
#   * XÁC MINH bằng request thật qua curl_cffi + UA khớp phiên bản trình duyệt.
#   * Báo lỗi nêu rõ nguyên nhân thay vì im lặng trả rỗng.
# ═══════════════════════════════════════════════════════════════════════════════

# Cookie phiên của OLM qua các phiên bản giao diện
OLM_SESSION_COOKIES = ("onlinemath_session", "laravel_session", "olm_session",
                       "olmvn_session", "connect.sid")
# Cookie chứng minh ĐÃ đăng nhập
OLM_AUTH_COOKIES = ("user", "refresh_token", "access_token", "remember_web",
                    "olm_user", "user_info", "llt")
# Domain thuộc OLM
OLM_DOMAIN_HINTS = ("olm.vn", "onlinemath.vn", "olm.com.vn")


def _cookie_belongs_to_olm(domain: str) -> bool:
    d = (domain or "").lower().lstrip(".")
    if not d:
        return True          # không gắn domain -> gửi cho mọi host
    return any(h in d for h in OLM_DOMAIN_HINTS)


def _cookie_jar_summary(jar: Dict[str, str]) -> Dict[str, Any]:
    keys = {k.lower() for k in jar}
    sess = [k for k in OLM_SESSION_COOKIES if k in keys]
    auth = [k for k in OLM_AUTH_COOKIES
            if k in keys or any(kk.startswith(k) for kk in keys)]
    return {"n": len(jar), "names": sorted(keys),
            "session": sess, "auth": auth}


async def _collect_cookies_from_context(ctx, url: str = OLM_BASE) -> Dict[str, str]:
    """Lấy cookie cho origin OLM, thử nhiều đường để không bỏ sót."""
    jar: Dict[str, str] = {}

    async def _merge(cks):
        for c in (cks or []):
            try:
                if _cookie_belongs_to_olm(c.get("domain")):
                    jar[c["name"]] = c["value"]
            except Exception:
                continue

    # (1) đúng origin — chính xác nhất, gồm cả HttpOnly
    for attempt in (url, None):
        try:
            cks = await (ctx.cookies(attempt) if attempt else ctx.cookies())
            await _merge(cks)
            if jar:
                break
        except Exception:
            continue

    # (2) quét thêm cookie của các tab OLM đang mở (subdomain khác)
    try:
        for p in list(getattr(ctx, "pages", []) or []):
            try:
                u = p.url or ""
                if u.startswith("http") and "olm.vn" in u:
                    await _merge(await ctx.cookies(u))
            except Exception:
                continue
    except Exception:
        pass

    return jar


async def verify_olm_cookies(jar: Dict[str, str],
                             timeout: float = 15.0) -> Dict[str, Any]:
    """Xác minh cookie bằng request THẬT (không đoán qua tên cookie).

    Trả {'ok', 'uid', 'uname', 'via', 'status', 'error'}
    """
    if not jar:
        return {"ok": False, "error": "Không có cookie nào", "via": "none"}

    out = {"ok": False, "uid": None, "uname": None, "via": None,
           "status": None, "error": None}
    try:
        out["uid"] = OLMSession._extract_uid_static(jar)
        out["uname"] = OLMSession._extract_uname_static(jar)
    except Exception:
        pass

    hdrs = {
        "User-Agent": effective_ua(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{OLM_BASE}/",
    }
    try:
        hdrs.update(effective_sec_ch_ua())
    except Exception:
        pass

    probes = [
        ("/user/get-info", "json"),
        ("/user/info", "json"),
        ("/api/user/me", "json"),
        ("/course/teacher-categories", "json"),
        ("/", "html"),
    ]
    imp_candidates = []
    for _imp in (effective_impersonate(), "chrome124", "chrome120", "chrome110"):
        if _imp and _imp not in imp_candidates:
            imp_candidates.append(_imp)
    for imp in imp_candidates:
        impersonate_failed = False
        try:
            async with CurlAsyncSession(
                    base_url=OLM_BASE, cookies=jar, timeout=timeout,
                    impersonate=imp, headers=hdrs,
                    allow_redirects=True) as c:
                for path, kind in probes:
                    try:
                        r = await c.get(path)
                    except Exception as e:
                        es = str(e)
                        if "impersonat" in es.lower() or "not supported" in es.lower():
                            impersonate_failed = True
                            break
                        out["error"] = f"{path}: {es}"[:200]
                        continue
                    out["status"] = r.status_code
                    if r.status_code != 200:
                        continue
                    txt = r.text or ""
                    low = txt[:4000].lower()
                    if kind == "html":
                        if ('"id_user"' in txt or "logout" in low
                                or "đăng xuất" in low):
                            out.update(ok=True, via="html")
                            return out
                        continue
                    try:
                        js = r.json()
                    except Exception:
                        js = None
                    if isinstance(js, dict):
                        for k in ("id_user", "user_id", "_id", "id", "username",
                                  "email", "name"):
                            if k in js and js.get(k):
                                out["uid"] = out["uid"] or str(js.get(k))
                                if k in ("username", "email", "name"):
                                    out["uname"] = str(js.get(k))
                                out.update(ok=True, via=f"json:{path}")
                                return out
                        if isinstance(js.get("data"), (dict, list)):
                            out.update(ok=True, via=f"json.data:{path}")
                            return out
                    elif isinstance(js, list):
                        out.update(ok=True, via=f"json.list:{path}")
                        return out
                    if txt.strip().startswith(("{", "[")):
                        out.update(ok=True, via=f"json:{path}")
                        return out
            if not impersonate_failed:
                break
        except Exception as e:
            es = str(e)
            if "impersonat" in es.lower() or "not supported" in es.lower():
                continue
            out["error"] = es[:200]
            break

    # Không xác minh được qua HTTP nhưng vẫn có cookie người dùng
    summ = _cookie_jar_summary(jar)
    if out["uid"] or out["uname"]:
        out.update(ok=True, via="cookie_user")
    elif summ["session"] and summ["auth"]:
        out.update(ok=True, via="cookie_names_only")
    return out


# ═══════════════════════════════════════════════════════════════
# CHROME COOKIE GRABBER
# ═══════════════════════════════════════════════════════════════
class ChromeCookieGrabber:
    def __init__(self, cdp_mgr: CDPManager):
        self.cdp = cdp_mgr

    async def grab(self):
        if not HAS_PW: return {"ok": False, "error": "Playwright chưa cài"}
        if not _port_open():
            r = _launch_chrome()
            if not r.get("ok"): return {"ok": False, "error": r.get("error", "Không mở được Chrome")}
            await asyncio.sleep(2)
        page, err = await self.cdp.get_olm_page()
        if err: return {"ok": False, "error": f"Không attach CDP: {err}"}
        try: cur_url = page.url or ""
        except Exception: cur_url = ""
        if "olm.vn" not in cur_url:
            try: await page.goto(OLM_BASE, wait_until="domcontentloaded", timeout=30000)
            except Exception: pass
            await asyncio.sleep(3)

        try:
            ctx = self.cdp.browser.contexts[0]
        except Exception as e:
            return {"ok": False, "error": f"Không có browser context: {e}"}

        # v20.1: nhiều đường lấy cookie + không lọc cứng domain
        cd = await _collect_cookies_from_context(ctx, OLM_BASE)

        # Nếu vẫn rỗng, đọc document.cookie (không thấy HttpOnly nhưng bù được)
        if not cd:
            try:
                raw = await page.evaluate("() => document.cookie || ''")
                for part in str(raw).split(";"):
                    if "=" in part:
                        n, v = part.split("=", 1)
                        cd[n.strip()] = v.strip()
            except Exception:
                pass

        info = _cookie_jar_summary(cd)
        if not info["session"] and not info["auth"]:
            return {"ok": False, "waiting_login": True,
                    "error": "Chrome chưa đăng nhập OLM. Hãy đăng nhập trong cửa "
                             "sổ Chrome vừa mở rồi bấm Lấy cookie lại.",
                    "cookies": info["names"][:40], "n": info["n"]}
        if not info["session"]:
            return {"ok": False, "waiting_login": True,
                    "error": "Có cookie người dùng nhưng THIẾU cookie phiên ("
                             + ", ".join(OLM_SESSION_COOKIES[:3])
                             + "...). Hãy đăng nhập lại trong cửa sổ Chrome.",
                    "cookies": info["names"][:40], "n": info["n"]}

        # v20.1: XÁC MINH bằng request thật trước khi báo thành công
        v = await verify_olm_cookies(cd)
        if not v.get("ok"):
            return {"ok": False, "waiting_login": True,
                    "error": "Cookie lấy được nhưng KHÔNG dùng được: "
                             + str(v.get("error") or f"HTTP {v.get('status')}")
                             + ". Hãy đăng nhập lại rồi thử lại.",
                    "cookies": info["names"][:40], "n": info["n"],
                    "verified": False}

        return {"ok": True, "cookies": cd,
                "user_id": v.get("uid") or OLMSession._extract_uid_static(cd),
                "username": v.get("uname") or OLMSession._extract_uname_static(cd),
                "n": info["n"], "verified": True, "verify_via": v.get("via"),
                "session_cookies": info["session"]}


# ═══════════════════════════════════════════════════════════════
# QUESTION PARSER HELPERS
# ═══════════════════════════════════════════════════════════════
def _collect_text(node, out, depth=0):
    if not node or depth > 20: return
    if isinstance(node, dict):
        t = node.get("text")
        if isinstance(t, str) and t.strip(): out.append(t.strip())
        ch = node.get("children")
        if isinstance(ch, list):
            for c in ch: _collect_text(c, out, depth + 1)
        else:
            for k, v in node.items():
                if k not in ("text", "children") and isinstance(v, (dict, list)):
                    _collect_text(v, out, depth + 1)
    elif isinstance(node, list):
        for c in node: _collect_text(c, out, depth + 1)


def find_nodes(node, type_, out):
    # [LEGACY v19.1] Giữ lại cho tương thích; code mới dùng `_find_all_nodes_js`
    # (bản port 1:1 findAllNodes của userscript).
    if not node: return
    if isinstance(node, dict):
        if node.get("type") == type_: out.append(node)
        ch = node.get("children")
        if isinstance(ch, list):
            for c in ch: find_nodes(c, type_, out)
        else:
            for k, v in node.items():
                if k not in ("type", "children"): find_nodes(v, type_, out)


def find_all_nodes_by_name(node, name, out):
    # [LEGACY v19.1] Xem ghi chú ở `find_nodes`.
    if not node: return
    if isinstance(node, dict):
        if node.get("type") == "olm-list" and node.get("name") == name:
            out.append(node)
        ch = node.get("children")
        if isinstance(ch, list):
            for c in ch: find_all_nodes_by_name(c, name, out)
        else:
            for k, v in node.items():
                if k not in ("type", "children"): find_all_nodes_by_name(v, name, out)


# === v19.1 - PORT FROM USERSCRIPT ===
def _find_all_nodes_js(node, type_, out):
    """Port 1:1 `findAllNodes` của userscript.

    Khác `find_nodes` (bản cũ luôn duyệt mọi key):
        JS:  if (node.children && Array.isArray(node.children)) recurse children
             else if (typeof node === 'object' && !Array.isArray(node))
                      for key in node (bỏ 'type','children') recurse
    Tức: khi node CÓ mảng `children` thì CHỈ đệ quy vào children, các key khác
    (kể cả object lồng ngoài children) bị bỏ qua. Giữ đúng semantics gốc để
    thứ tự / số lượng node khớp userscript.
    """
    if not node:
        return
    if isinstance(node, dict):
        if node.get("type") == type_:
            out.append(node)
        ch = node.get("children")
        if isinstance(ch, list):
            for c in ch:
                _find_all_nodes_js(c, type_, out)
        else:
            for k, v in node.items():
                if k in ("type", "children"):
                    continue
                _find_all_nodes_js(v, type_, out)
    elif isinstance(node, list):
        for c in node:
            _find_all_nodes_js(c, type_, out)


def _opt_text(child) -> str:
    if not child: return ""
    t = child.get("text")
    if isinstance(t, str) and t.strip(): return t.strip()[:300]
    txts = []; _collect_text(child, txts)
    if txts:
        if len(txts) > 1 and re.fullmatch(r'[A-Z]\.', txts[0] or ""):
            return " ".join(txts[1:])[:300]
        return " ".join(txts)[:300]
    return ""


def _opt_text_v2(child) -> str:
    """Extract option text — ưu tiên `content` (HTML đã b64x), fallback `text` hoặc ảnh."""
    if not child:
        return ""
    raw = child.get("content") or ""
    if raw:
        html = _decode_olm_content(raw)
        with_img = _preserve_imgs_in_option_html(html)
        plain = _TAG_RE.sub(" ", with_img)
        plain = _decode_html_entities(plain)
        plain = _normalize_inline_math_str(plain)
        plain = re.sub(r'[\u00a0\s]+', ' ', plain).strip()
        plain = _restore_img_tokens_to_tag(plain).strip()
        if plain:
            return plain[:500]
    t = child.get("text")
    if isinstance(t, str) and t.strip():
        return _normalize_inline_math_str(t.strip())[:500]
    txts = []
    _collect_text(child, txts)
    if txts:
        return _normalize_inline_math_str(" ".join(txts))[:500]
    # Fallback: check for image node inside AST child
    img_nodes: List[dict] = []
    _find_all_nodes_js(child, "image", img_nodes)
    _find_all_nodes_js(child, "olm-img", img_nodes)
    for im in img_nodes:
        src = im.get("src") or im.get("url") or im.get("content") or ""
        if src:
            return f'<img src="{_normalize_media_url(str(src))}">'
    return ""


def _split_group_html_segments(content_html: str, expected_count: int) -> List[Tuple[str, List[str]]]:
    """Split group question HTML (q_type 21/22) into (stem_html, options_list) per sub-question."""
    if not content_html:
        return []
    # Fallback 1: split by <hr id="..."> if present and matches expected_count
    if "<hr" in content_html.lower():
        parts = [p for p in re.split(r'<hr\b[^>]*>', content_html, flags=re.I) if p.strip()]
        if len(parts) >= expected_count:
            return [(_strip_option_html(p), _extract_options_from_content(p)) for p in parts]

    list_pat = re.compile(r'<(?:ol|ul)\b[^>]*>.*?</(?:ol|ul)>', re.S | re.I)
    matches = []
    for m in list_pat.finditer(content_html):
        block = m.group(0)
        open_tag = block.split(">", 1)[0] + ">"
        lis = re.findall(r'<li\b[^>]*>(.*?)</li>', block, re.S | re.I)
        if _QUIZ_LIST_CLASS_RE.search(open_tag) or len(lis) >= 2:
            matches.append(m)
    if matches:
        segs: List[Tuple[str, List[str]]] = []
        pos = 0
        for m in matches:
            stem_html = content_html[pos:m.start()]
            stem_html = re.sub(r'<hr\b[^>]*>', ' ', stem_html, flags=re.I)
            opts = _extract_options_from_content(m.group(0))
            segs.append((stem_html, opts))
            pos = m.end()
        tail = content_html[pos:].strip()
        if tail and len(segs) < expected_count:
            tail_clean = re.sub(r'<hr\b[^>]*>', ' ', tail, flags=re.I).strip()
            if tail_clean:
                segs.append((tail_clean, _extract_options_from_content(tail_clean)))
        return segs

    # Fallback 2: split by "Câu 1", "Câu 2", ... paragraphs
    cau_parts = re.split(
        r'(?=<(?:p|div)\b[^>]*>\s*(?:<[^>]+>\s*)*Câu\s*\d+\b)|(?=<\s*(?:b|strong)\b[^>]*>\s*Câu\s*\d+\b)',
        content_html,
        flags=re.I,
    )
    cau_parts = [p for p in cau_parts if p and p.strip()]
    if cau_parts:
        return [(_strip_option_html(p), _extract_options_from_content(p)) for p in cau_parts]
    return []


def _ensure_group_sub_questions(q: dict) -> List[dict]:
    """Ensure a q_type 21/22 dict has a populated `sub_questions` list of individual question dicts."""
    if not isinstance(q, dict) or q.get("q_type") not in (21, 22):
        return []
    existing = q.get("sub_questions")
    if isinstance(existing, list) and existing and all(
        isinstance(sq, dict) and (sq.get("text") or sq.get("text_html") or sq.get("options"))
        for sq in existing
    ):
        return existing

    sub_answers = q.get("sub_answers") or []
    if not sub_answers:
        return []
    sub_types = q.get("sub_types") or []
    sub_labels = q.get("sub_labels") or []
    sub_orders = q.get("sub_orders") or []
    sub_options_list = q.get("sub_options") or []
    sub_stems_list = q.get("sub_stems") or []
    total_sub = len(sub_answers)
    per_score = round(float(q.get("max_score") or total_sub) / max(1, total_sub), 2)

    built: List[dict] = []
    for k, sa in enumerate(sub_answers):
        st = int(sub_types[k]) if k < len(sub_types) else 1
        sl = str(sub_labels[k]) if k < len(sub_labels) else ""
        sord = sub_orders[k] if k < len(sub_orders) and isinstance(sub_orders[k], list) else []
        sopts = list(sub_options_list[k]) if k < len(sub_options_list) and isinstance(sub_options_list[k], list) else []
        stem_info = sub_stems_list[k] if k < len(sub_stems_list) and isinstance(sub_stems_list[k], dict) else {}
        stem_html = stem_info.get("text_html") or ""
        stem_text = stem_info.get("text") or ""
        stem_rich = stem_info.get("rich_content") or []

        if not stem_text and not stem_html:
            stem_text = f"Câu hỏi phần {k + 1}"
            stem_html = f'<span class="olm-text">{html_module.escape(stem_text)}</span>'

        sq: dict = {
            "id": f"{q.get('id')}_sub_{k+1}",
            "q_type": st,
            "type_label": QTYPE_LABEL.get(st, f"Type {st}"),
            "max_score": per_score,
            "text_html": stem_html,
            "text": stem_text,
            "rich_content": stem_rich,
            "options": sopts,
            "optionCount": len(sopts) or (
                (q.get("sub_option_counts") or [])[k]
                if k < len(q.get("sub_option_counts") or []) else 4
            ),
            "order": sord or list(range(max(len(sopts), 4))),
        }
        if st == 13:
            ca = sa if isinstance(sa, list) else [str(sa)]
            sq["correctAnswers"] = [str(x) for x in ca]
        elif st in (2, 3, 11):
            vals = [str(x) for x in (sa if isinstance(sa, list) else [sa]) if str(x).strip()]
            sq["correctAnswers"] = vals
            sq["answer_texts"] = vals
            if vals:
                sq["correctAnswer"] = vals[0]
        else:
            ca = [str(x) for x in (sa if isinstance(sa, list) else [sa])]
            sq["correctAnswers"] = ca
            if sl:
                sq["correctLabel"] = sl
            try:
                cidx = int(ca[0])
                sq["correctIndex"] = cidx
                if not sq.get("correctLabel"):
                    sq["correctLabel"] = chr(65 + cidx)
                if 0 <= cidx < len(sopts):
                    sq["correctAnswer"] = sopts[cidx]
            except Exception:
                pass
        built.append(sq)
    q["sub_questions"] = built
    return built


# ═══════════════════════════════════════════════════════════════
# EXTRACT QUESTIONS — v19.1 (port 1:1 từ extractQuestionData)
# ═══════════════════════════════════════════════════════════════
def extract_questions(data) -> List[dict]:
    """Port 1:1 từ `extractQuestionData` của userscript OLM Hack Pro.

    Khác biệt so với bản cũ (v1.0.1):
      * `findAllNodes` gốc chỉ đệ quy vào `children` khi node CÓ mảng
        `children`; nếu không mới duyệt mọi key khác. (`_find_all_nodes_js`)
      * `correctAnswers` của q_type 5/6/20 giữ nguyên `int` (0..n-1).
      * q_type 9: index tăng dần, chỉ push khi có ít nhất 1 correct.
      * q_type 21/22: `sub_orders` = node.order hoặc range(n) (khớp
        `Array.from(Array(numOptions).keys())`), đồng thời trích xuất
        đầy đủ `sub_questions` (stem + options + đáp án từng câu con).
      * q_type 3/11: `optionCount = len(inputs)` cho q_type 11 (KHÔNG phải poolSize).
      * Fallback q_type 1/13: `multi_select` khi true-false có >= 2 correct &
        >= 3 option (port yêu cầu #5 mục A).
      * `max_score` fallback 1 (như userscript `q.score || 1`).
    """
    if not isinstance(data, list):
        return []
    out = []
    for q in data:
        try:
            jc = q.get("json_content")
            if not jc or not q.get("id"):
                continue
            root_data = (
                jc if isinstance(jc, dict)
                else (try_json(jc) if str(jc).lstrip().startswith("{") else None)
                or try_json(b64x_decode(jc))
            )
            if not root_data or "root" not in root_data:
                continue
            root = root_data.get("root") or {}
            qtype = int(q.get("q_type") or q.get("type") or 1)
            max_score = q.get("score") or 1

            base = {
                "id": q["id"],
                "q_type": qtype,
                "type_label": QTYPE_LABEL.get(qtype, f"Type {qtype}"),
                "max_score": max_score,
            }

            # ── Content HTML decode (dùng cho text/option/explanation + list_idq) ──
            content_raw = q.get("content") or ""
            content_html = _decode_olm_content(content_raw) if content_raw else ""

            # ── Option lấy từ HTML content trước (khớp DOM OLM render) ──
            html_opts = _extract_options_from_content(content_html)

            # ── Bỏ list option khỏi content để lấy phần đề bài ──
            content_q = _strip_option_html(content_html) if content_html else ""

            rich = _collect_rich_from_html(content_q) if content_q else []
            if not rich:
                text_parts = []
                _collect_text(root, text_parts)
                rich = [{"kind": "text", "text": _normalize_inline_math_str(t)}
                        for t in text_parts if t.strip()]

            base["rich_content"] = rich
            base["text_html"] = _render_rich_to_html(rich)
            base["text"] = _render_rich_to_text(rich)[:500]
            base["text_all"] = [b.get("text") or b.get("latex") or ""
                                for b in rich if b.get("kind") in ("text", "math")]
            base["has_images"] = any(b["kind"] == "image" for b in rich)
            base["has_math"] = any(b["kind"] == "math" for b in rich)

            # ── findAllNodes(json.root, 'olm-list') — giống bản gốc ──
            all_lists: List[dict] = []
            _find_all_nodes_js(root, "olm-list", all_lists)

            target_list = next((l for l in all_lists
                                if l.get("name") in ("quiz-list", "true-false")), None)

            def _fill_options_from_list(lst):
                ch = lst.get("children") or []
                if not ch:
                    return False
                opts = [_opt_text_v2(c) for c in ch]
                opts = [o for o in opts if o]
                if opts:
                    base["options"] = opts
                    base["optionCount"] = len(ch)
                    return True
                return False

            if html_opts and target_list:
                n_ch = len(target_list.get("children") or [])
                if len(html_opts) == n_ch:
                    base["options"] = html_opts
                    base["optionCount"] = n_ch
                else:
                    _fill_options_from_list(target_list)
            elif html_opts:
                base["options"] = html_opts
                base["optionCount"] = len(html_opts)
            elif target_list:
                _fill_options_from_list(target_list)

            # ═══ NHÁNH 5 — SẮP XẾP (ordering) ═══
            # correctAnswers = order = [0..n-1] (int, y như bản gốc)
            if qtype == 5:
                if all_lists and all_lists[0].get("children"):
                    n = len(all_lists[0]["children"])
                    correct_order = list(range(n))
                    out.append({**base, "q_type": 5,
                                "correctAnswers": correct_order,
                                "order": correct_order,
                                "optionCount": n})
                    continue

            # ═══ NHÁNH 9 — GẠCH CHÂN (underline) ═══
            # index tăng dần của các node .under-line có correct === True
            if qtype == 9:
                underline_nodes: List[dict] = []
                _find_all_nodes_js(root, "under-line", underline_nodes)
                correct_indices = [i for i, n in enumerate(underline_nodes)
                                   if n.get("correct") is True]
                if correct_indices:
                    out.append({**base, "q_type": 9,
                                "correctAnswers": correct_indices})
                    continue

            # ═══ NHÁNH 21/22 — CÂU HỎI NHÓM (group / sub-answers) ═══
            if qtype in (21, 22):
                list_idq = (re.findall(r'<hr\b[^>]*\bid=["\']([^"\']+)["\']', content_html, flags=re.I)
                            if content_html else [])
                sub_answers: List[Any] = []
                sub_types: List[int] = []
                sub_labels: List[str] = []
                sub_option_counts: List[int] = []
                sub_orders: List[list] = []
                sub_options_ast: List[List[str]] = []
                ast_stem_buffer: List[str] = []
                ast_stems_per_sub: List[str] = []

                # processGroupRecursive — đệ quy cả paragraph/extended-text
                def process_group_recursive(nodes, depth=0):
                    if not isinstance(nodes, list) or depth > 12:
                        return
                    for node in nodes:
                        if not isinstance(node, dict):
                            continue
                        ntype = node.get("type")
                        if ntype == "olm-list":
                            children = node.get("children") or []
                            num_options = len(children)
                            order = node.get("order") or list(range(num_options))
                            node_opts = [_opt_text_v2(c) for c in children]
                            node_opts = [o for o in node_opts if o]
                            sub_options_ast.append(node_opts)
                            ast_stems_per_sub.append(" ".join(ast_stem_buffer).strip())
                            ast_stem_buffer.clear()
                            if node.get("name") == "true-false":
                                sub_answers.append(
                                    ["1" if it.get("correct") is True else "0"
                                     for it in children])
                                sub_types.append(13)
                                sub_labels.append("")
                                sub_option_counts.append(num_options)
                                sub_orders.append(order)
                            else:
                                correct_idx = [str(i) for i, it in enumerate(children)
                                               if it.get("correct") is True]
                                if not correct_idx:
                                    correct_idx = ["0"]
                                sub_answers.append(correct_idx)
                                sub_types.append(1)
                                sub_labels.append(
                                    chr(65 + int(correct_idx[0])))
                                sub_option_counts.append(num_options)
                                sub_orders.append(order)
                        elif ntype in ("paragraph", "extended-text"):
                            input_nodes: List[dict] = []
                            # findAllNodes CÓ THỂ trùng node — giữ đúng bản gốc
                            _find_all_nodes_js(node, "olm-input-text", input_nodes)
                            _find_all_nodes_js(node, "fillme-input", input_nodes)
                            if input_nodes:
                                vals = []
                                for inp in input_nodes:
                                    if inp.get("name") == "selecttext":
                                        vals.append("0")
                                    else:
                                        vals.append(
                                            str(inp.get("content") or "").split("||")[0])
                                sub_answers.append(vals)
                                sub_types.append(11)
                                sub_labels.append("")
                                sub_option_counts.append(4)
                                sub_orders.append([0, 1, 2, 3])
                                sub_options_ast.append([])
                                node_txts: List[str] = []
                                _collect_text(node, node_txts)
                                combined_stem = " ".join(ast_stem_buffer + node_txts).strip()
                                ast_stems_per_sub.append(combined_stem)
                                ast_stem_buffer.clear()
                            else:
                                child_lists: List[dict] = []
                                _find_all_nodes_js(node, "olm-list", child_lists)
                                if not child_lists:
                                    node_txts = []
                                    _collect_text(node, node_txts)
                                    if node_txts:
                                        ast_stem_buffer.append(" ".join(node_txts))
                        if node.get("children") and ntype != "olm-list":
                            process_group_recursive(node["children"], depth + 1)

                process_group_recursive(root.get("children"))
                if sub_answers:
                    html_segs = _split_group_html_segments(content_html, len(sub_answers))
                    sub_stems: List[dict] = []
                    sub_options_final: List[List[str]] = []
                    for k in range(len(sub_answers)):
                        ast_opts = sub_options_ast[k] if k < len(sub_options_ast) else []
                        seg_html, seg_opts = html_segs[k] if k < len(html_segs) else ("", [])
                        chosen_opts = seg_opts if (seg_opts and len(seg_opts) == len(ast_opts)) else (ast_opts or seg_opts)
                        sub_options_final.append(chosen_opts)

                        s_rich = _collect_rich_from_html(seg_html) if seg_html.strip() else []
                        if not s_rich and k < len(ast_stems_per_sub) and ast_stems_per_sub[k]:
                            s_rich = _rich_from_text(ast_stems_per_sub[k])
                        s_text_html = _render_rich_to_html(s_rich) if s_rich else ""
                        s_text = _render_rich_to_text(s_rich)[:500] if s_rich else ""
                        # Remove leading "Câu X." prefix if OLM already put it inside stem
                        s_text_clean = re.sub(r'^\s*Câu\s*\d+\s*[\.\:\)]\s*', '', s_text, flags=re.I).strip() or s_text
                        sub_stems.append({
                            "rich_content": s_rich,
                            "text_html": s_text_html,
                            "text": s_text_clean,
                        })

                    q_group = {
                        **base,
                        "q_type": qtype,
                        "sub_answers": sub_answers,
                        "sub_types": sub_types,
                        "sub_labels": sub_labels,
                        "sub_option_counts": sub_option_counts,
                        "sub_orders": sub_orders,
                        "sub_options": sub_options_final,
                        "sub_stems": sub_stems,
                        "list_idq": list_idq,
                        "list_ids": list_idq,
                        "optionCount": len(sub_answers),
                    }
                    _ensure_group_sub_questions(q_group)
                    out.append(q_group)
                    continue

            # ═══ NHÁNH 20 — SẮP XẾP ĐOẠN VĂN ═══
            if qtype == 20:
                list_nodes: List[dict] = []
                _find_all_nodes_js(root, "olm-list-item", list_nodes)
                if (list_nodes and list_nodes[0].get("children")
                        and list_nodes[0]["children"][0].get("text")):
                    correct_length = len(
                        list_nodes[0]["children"][0]["text"].split("||"))
                    idx_arr = list(range(correct_length))
                    out.append({**base, "q_type": 20,
                                "correctAnswers": idx_arr,
                                "optionCount": correct_length,
                                "order": idx_arr})
                    continue

            # ═══ NHÁNH 3/11 — ĐIỀN KHUYẾT (pool / select) ═══
            if qtype in (3, 11):
                input_nodes = []
                _find_all_nodes_js(root, "olm-input-text", input_nodes)
                if input_nodes:
                    pool_size = 0
                    correct_ids = []
                    # v19.2: giữ thêm TEXT đáp án (content.split('||')[0]) để
                    # trang đáp án hiển thị chữ thay vì chỉ số pool vô nghĩa.
                    answer_texts = []
                    for node in input_nodes:
                        head = str(node.get("content") or "").split("||")[0]
                        if qtype == 11:
                            correct_ids.append("0")
                            answer_texts.append(head)
                        else:
                            correct_ids.append(str(pool_size))
                            pool_size += len(str(node.get("content") or "").split("||"))
                            answer_texts.append(head)
                    # Bản gốc: finalSize = (qType === 11) ? inputNodes.length : poolSize
                    final_size = len(input_nodes) if qtype == 11 else pool_size
                    out.append({**base, "q_type": qtype,
                                "correctAnswers": correct_ids,
                                "answer_texts": answer_texts,
                                "order": list(range(final_size)),
                                "optionCount": final_size})
                    continue

            # ═══ NHÁNH 6 — NỐI CẶP (matching) ═══
            if qtype == 6:
                link_lists: List[dict] = []
                _find_all_nodes_js(root, "olm-list", link_lists)
                lst = next((n for n in link_lists if n.get("name") == "link-list"), None)
                if lst and lst.get("children"):
                    num = len(lst["children"])
                    out.append({**base, "q_type": 6,
                                "correctAnswers": list(range(num)),
                                "order": lst.get("order"),
                                "optionCount": num})
                    continue

            # ═══ NHÁNH 10 — KÉO THẢ NHÓM (group drag) ═══
            # answerIndices giữ nguyên int (tăng dần toàn cục) y bản gốc
            if qtype == 10:
                group_lists: List[dict] = []
                _find_all_nodes_js(root, "olm-list", group_lists)
                lst = next((n for n in group_lists if n.get("name") == "group-list"), None)
                if lst and lst.get("children"):
                    orig_idx = 0
                    answer_indices: List[List[int]] = []
                    for item in lst["children"]:
                        grp_idxs: List[int] = []
                        ans_nodes: List[dict] = []
                        _find_all_nodes_js(item, "position-column", ans_nodes)
                        for n in ans_nodes:
                            if n.get("position") == "group":
                                grp_idxs.append(orig_idx)
                                orig_idx += 1
                        if grp_idxs:
                            answer_indices.append(grp_idxs)
                    out.append({**base, "q_type": 10,
                                "answerIndices": answer_indices,
                                "order": lst.get("order"),
                                "optionCount": orig_idx})
                    continue

            # ═══ NHÁNH 2 — ĐIỀN KHUYẾT 1 Ô ═══
            if qtype == 2:
                inputs: List[dict] = []
                _find_all_nodes_js(root, "fillme-input", inputs)
                if inputs:
                    val = str(inputs[0].get("content") or "").split("||")[0]
                    out.append({**base, "q_type": 2,
                                "correctAnswer": val,
                                "correctAnswers": [val],
                                "max_score": 1})
                    continue

            # ═══ FALLBACK — quiz-list / true-false (q_type 1, 13) ═══
            list_nodes: List[dict] = []
            _find_all_nodes_js(root, "olm-list", list_nodes)
            lst = next((c for c in list_nodes
                        if c.get("name") in ("quiz-list", "true-false")), None)
            if lst and lst.get("children"):
                children = lst["children"]
                num_options = len(children)
                order = lst.get("order") or list(range(num_options))

                # Thông tin hiển thị (không có trong userscript, giữ cho UI/docx)
                opts = base.get("options") or [_opt_text_v2(c) for c in children]
                base["options"] = opts
                base["question_text"] = _render_rich_to_text(rich)[:500]
                expl_blocks = [b for b in rich if b.get("kind") == "text"
                               and "hướng dẫn giải" in (b.get("text") or "").lower()]
                base["explanation"] = expl_blocks[0]["text"][:500] if expl_blocks else ""

                if qtype == 13 or lst.get("name") == "true-false":
                    correct_answers = ["1" if it.get("correct") is True else "0"
                                       for it in children]
                    # multi_select: true-false có >= 2 mệnh đề đúng & >= 3 option
                    n_true = sum(1 for v in correct_answers if v == "1")
                    is_multi = (n_true >= 2 and num_options >= 3
                                and lst.get("name") == "true-false")
                    item = {**base, "q_type": 13,
                            "correctAnswers": correct_answers,
                            "order": order,
                            "optionCount": num_options,
                            "max_score": 1}
                    if is_multi:
                        item["multi_select"] = True
                    out.append(item)
                else:
                    correct_indices = [str(i) for i, it in enumerate(children)
                                       if it.get("correct") is True]
                    first = correct_indices[0] if correct_indices else "0"
                    out.append({
                        **base,
                        "q_type": qtype or 1,
                        "correctIndex": first,
                        "correctAnswers": correct_indices,
                        "correctLabel": chr(65 + int(first or 0)),
                        "order": order,
                        "optionCount": num_options,
                        "max_score": 1,
                    })
                    # Ghi chú: bản gốc push `correctIndex: correctIndices[0]` —
                    # nếu mảng rỗng thì JS push `undefined`; ở Python fallback "0"
                    # để không vỡ chr()/int() ở downstream.
        except Exception as e:
            _log_error("EXTRACT_FAILED", id=q.get("id"),
                       qtype=q.get("q_type"), error=str(e),
                       tb=traceback.format_exc()[:500])
            continue
    return out


def total_problem_count(questions: List[dict]) -> int:
    total = 0
    for q in questions:
        if q.get("q_type") in (21, 22) and q.get("sub_answers"):
            total += len(q["sub_answers"])
        else:
            total += 1
    return total


# === v19.2 - XEM LẠI BÀI ĐÃ NỘP (get-crt-ans-file) ===
_ANS_HTML_HINT_RE = re.compile(
    r'<\s*(?:p|div|span|ol|ul|li|br|table|tr|td|b|i|u|strong|em|img|hr)\b', re.I)


def _ans_looks_like_html(s: str) -> bool:
    """True nếu chuỗi trông như HTML hiển thị được (không phải JSON/base64)."""
    if not isinstance(s, str):
        return False
    t = s.strip()
    if not t or len(t) > 3_000_000:
        return False
    if t.startswith("{") or t.startswith("["):
        return False
    return bool(_ANS_HTML_HINT_RE.search(t))


def _ans_extract_q_list(obj, depth: int = 0):
    """Tìm mảng câu hỏi trong payload đã decode (nhiều shape khác nhau).

    OLM trả file đáp án ở nhiều dạng tuỳ loại bài:
      * list[{id, q_type, json_content, content, score}]
      * {data: [...]}, {result: [...]}, {questions: [...]}, {list: [...]}
      * {list_quiz: [...]}, {q_list: [...]}, {record: {list_quiz: [...]}}
      * list id  -> ["123","456"] (không có nội dung -> cần gọi lại API)
    Trả về (list_câu_hỏi_đã_parse, list_id_thuần).
    """
    if depth > 4 or obj is None:
        return [], []

    if isinstance(obj, str):
        parsed = try_json(obj)
        if parsed is None:
            try:
                parsed = try_json(b64x_decode(obj))
            except Exception:
                parsed = None
        if parsed is None:
            return [], []
        return _ans_extract_q_list(parsed, depth + 1)

    if isinstance(obj, list):
        # list câu hỏi thật?
        dicts = [x for x in obj if isinstance(x, dict)]
        if dicts and any(("json_content" in x or "q_type" in x) for x in dicts):
            return obj, []
        # list id thuần?
        if obj and all(isinstance(x, (str, int)) for x in obj):
            stems = [str(x) for x in obj if str(x).strip()]
            if stems and all(re.fullmatch(r'\d{3,}', s) for s in stems):
                return [], stems
        # list lồng -> đệ quy
        for x in obj:
            q, ids = _ans_extract_q_list(x, depth + 1)
            if q or ids:
                return q, ids
        return [], []

    if isinstance(obj, dict):
        for key in ("data", "result", "questions", "list", "q_list",
                    "list_quiz", "items", "quiz_list", "answer", "answers"):
            if key in obj:
                q, ids = _ans_extract_q_list(obj[key], depth + 1)
                if q or ids:
                    return q, ids
        if "record" in obj:
            q, ids = _ans_extract_q_list(obj["record"], depth + 1)
            if q or ids:
                return q, ids
        if "json_content" in obj and "id" in obj:
            return [obj], []
    return [], []


def _extract_pdf_ans_questions(obj: dict) -> List[dict]:
    """Port 1:1 Blob 80 của userscript: chuyển {fc, tf, dg} của Kiểm tra PDF (type 10)
    thành danh sách câu hỏi chuẩn để hiển thị đáp án, xuất Word và nộp bài."""
    if not isinstance(obj, dict):
        return []
    fc = obj.get("fc")
    tf = obj.get("tf")
    dg = obj.get("dg")
    if not (isinstance(fc, dict) or isinstance(tf, dict) or isinstance(dg, dict)):
        return []
    out: List[dict] = []
    if isinstance(fc, dict):
        for k, v in fc.items():
            idx = 0
            letter = "A"
            if isinstance(v, str) and re.fullmatch(r"[A-Za-z]", v.strip()):
                letter = v.strip().upper()
                idx = max(0, ord(letter) - 65)
            else:
                try:
                    idx = max(0, int(v))
                    letter = chr(65 + idx)
                except Exception:
                    idx = 0
                    letter = "A"
            out.append({
                "id": f"fc-{k}",
                "q_type": 1,
                "type_label": "Trắc nghiệm (PDF)",
                "max_score": 1,
                "text": f"Câu {k} — Phần I: Trắc nghiệm 4 lựa chọn",
                "question_text": f"Câu {k} — Phần I: Trắc nghiệm 4 lựa chọn",
                "options": ["Phương án A", "Phương án B", "Phương án C", "Phương án D"],
                "optionCount": 4,
                "order": [0, 1, 2, 3],
                "correctIndex": str(idx),
                "correctAnswers": [str(idx)],
                "correctLabel": letter,
                "_pdf_part": "fc",
                "_pdf_key": str(k),
                "_pdf_raw": v,
            })
    if isinstance(tf, dict):
        for k, sub in tf.items():
            opts: List[str] = []
            ca: List[str] = []
            if isinstance(sub, dict):
                for sk, sv in sub.items():
                    opts.append(f"Mệnh đề {sk})")
                    is_true = (sv is True or str(sv).strip().lower() in ("1", "true", "đ", "d", "đúng"))
                    ca.append("1" if is_true else "0")
            elif isinstance(sub, list):
                for si, sv in enumerate(sub):
                    opts.append(f"Mệnh đề {chr(97 + si)})")
                    is_true = (sv is True or str(sv).strip().lower() in ("1", "true", "đ", "d", "đúng"))
                    ca.append("1" if is_true else "0")
            if ca:
                out.append({
                    "id": f"tf-{k}",
                    "q_type": 13,
                    "type_label": "Đúng/Sai (PDF)",
                    "max_score": 1,
                    "text": f"Câu {k} — Phần II: Trắc nghiệm Đúng / Sai",
                    "question_text": f"Câu {k} — Phần II: Trắc nghiệm Đúng / Sai",
                    "options": opts,
                    "optionCount": len(opts),
                    "order": list(range(len(opts))),
                    "correctAnswers": ca,
                    "_pdf_part": "tf",
                    "_pdf_key": str(k),
                    "_pdf_raw": sub,
                })
    if isinstance(dg, dict):
        for k, v in dg.items():
            val = str(v if v is not None else "").strip()
            out.append({
                "id": f"dg-{k}",
                "q_type": 2,
                "type_label": "Trả lời ngắn (PDF)",
                "max_score": 1,
                "text": f"Câu {k} — Phần III: Trả lời ngắn (Điền đáp số)",
                "question_text": f"Câu {k} — Phần III: Trả lời ngắn (Điền đáp số)",
                "correctAnswer": val,
                "correctAnswers": [val],
                "_pdf_part": "dg",
                "_pdf_key": str(k),
                "_pdf_raw": v,
            })
    return out


def _parse_ans_file(decoded) -> Tuple[List[dict], str]:
    """Parse nội dung `/get-crt-ans-file` đã decode.

    Trả về (questions, html):
      * questions -> list câu hỏi đã chuẩn hoá bằng `extract_questions` hoặc PDF `{fc,tf,dg}`
      * html      -> HTML đáp án hiển thị thẳng (khi server trả HTML)
    """
    if decoded is None:
        return [], ""

    # 1) Chuỗi -> thử JSON / base64-XOR / HTML
    if isinstance(decoded, str):
        s = decoded.strip()
        if not s:
            return [], ""
        if s[0] == '"':
            try:
                s = json.loads(s)
                if not isinstance(s, str):
                    return _parse_ans_file(s)
            except Exception:
                pass
        if s.startswith("{") or s.startswith("["):
            j = try_json(s)
            if j is not None:
                return _parse_ans_file(j)
            try:
                j = try_json(b64x_decode(s))
                if j is not None:
                    return _parse_ans_file(j)
            except Exception:
                pass
        if _ans_looks_like_html(s):
            return [], _sanitize_ans_html(s)
        return [], ""

    # 1b) Kiểm tra PDF {fc, tf, dg} (Blob 80 userscript)
    if isinstance(decoded, dict):
        pdf_qs = _extract_pdf_ans_questions(decoded)
        if pdf_qs:
            return pdf_qs, ""

    # 2) list/dict -> tìm danh sách câu hỏi
    qlist, id_list = _ans_extract_q_list(decoded)
    if qlist:
        qs = extract_questions(qlist)
        if qs:
            return qs, ""
    if id_list:
        # server chỉ trả id -> không có nội dung để dựng câu hỏi
        # (fetch_questions sẽ gọi get-question-of-ids theo các id này)
        return [], ""
    # 3) dict có HTML bên trong?
    if isinstance(decoded, dict):
        for key in ("html", "content", "html_content", "answer_html", "data"):
            v = decoded.get(key)
            if isinstance(v, str) and _ans_looks_like_html(v):
                return [], _sanitize_ans_html(v)
    return [], ""


def _sanitize_ans_html(html: str) -> str:
    """Làm sạch HTML đáp án trả về từ server trước khi nhúng vào trang tool.

    Chỉ giữ thẻ hiển thị + ảnh; bỏ script/style/iframe/form và mọi thuộc tính
    `on*` để không thực thi mã lạ trong webview.
    """
    if not html:
        return ""
    s = html
    # bỏ hẳn script/style/iframe/object/embed/form
    s = re.sub(r'<(script|style|iframe|object|embed|form|link|meta)\b[^>]*>.*?</\1>',
               '', s, flags=re.S | re.I)
    s = re.sub(r'<(script|style|iframe|object|embed|form|link|meta)\b[^>]*/?>',
               '', s, flags=re.I)
    # bỏ thuộc tính on*
    s = re.sub(r'\son[a-z]+\s*=\s*"[^"]*"', '', s, flags=re.I)
    s = re.sub(r"\son[a-z]+\s*=\s*'[^']*'", '', s, flags=re.I)
    s = re.sub(r'\son[a-z]+\s*=\s*[^\s>]+', '', s, flags=re.I)
    # bỏ javascript: trong href/src
    s = re.sub(r'\b(href|src)\s*=\s*(["\'])\s*javascript:[^"\']*\2',
               r'\1=\2#\2', s, flags=re.I)
    return s


# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════
# BUILD HELPERS — v19.1 (port 1:1 từ userscript)
# ═══════════════════════════════════════════════════════════════
def _build_choose_label(q: dict):
    """Port 1:1 logic chọn nhãn đáp án để nhét vào data_log / choose_log.

    Trả về:
        str   -> "A"/"B"/... hoặc "0"/"1" (q_type 13 đơn)
        list  -> [int,...] (q_type 3/5/6/11) hoặc [int,...] (q_type 13 multi)
    """
    qt = q.get("q_type")
    if qt == 13:
        if q.get("multi_select"):
            ca = q.get("correctAnswers") or []
            idxs = [i for i, v in enumerate(ca) if v == '1']
            return idxs or [0]
        ca = q.get("correctAnswers") or ["1"]
        return ca[0] if ca[0] in ("0", "1") else "0"
    if qt in (5, 6):
        ca = q.get("correctAnswers") or []
        return [int(x) for x in ca] if ca else [0]
    if qt in (3, 11):
        ca = q.get("correctAnswers") or []
        return [int(x) for x in ca] if ca else [0]
    if q.get("correctLabel"):
        return q["correctLabel"]
    if q.get("correctIndex") is not None:
        try: return chr(65 + int(q["correctIndex"]))
        except Exception: pass
    ca = q.get("correctAnswers") or []
    if ca:
        v = ca[0]
        if isinstance(v, str) and re.match(r'^[A-Z]$', v): return v
        try: return chr(65 + int(v))
        except Exception: pass
    return "A"


def _label_to_str(label) -> str:
    """Chuẩn hóa label thành chuỗi gửi lên OLM ('A', '0', 'A,C', '0,1,2')."""
    if isinstance(label, (list, tuple)):
        return ",".join(str(x) for x in label)
    return str(label)


def _build_choose_log(questions, step_ms):
    """choose_log = [[{ind, t, l}, ...]] (mảng lồng, khớp userscript)."""
    inner = []
    for i, q in enumerate(questions):
        inner.append({
            "ind": i,
            "t": step_ms * (i + 1),
            "l": _build_choose_label(q),
        })
    return [inner]


def _build_data_log(questions, n_correct, per_q_max):
    """data_log — cấu trúc chuẩn userscript.

    Keys: _id, nx, _score, iframe, count_redo, correct, user_answer, choose.
    `user_answer` = null khi câu đó bị tính sai (OLM coi null = bỏ trống).
    """
    dl = []
    for i, q in enumerate(questions):
        is_correct = i < n_correct
        label = _build_choose_label(q)
        dl.append({
            "_id": str(q["id"]),
            "nx": i,
            "_score": per_q_max if is_correct else 0,
            "iframe": False,
            "count_redo": 0,
            "correct": 1 if is_correct else 0,
            "user_answer": label if is_correct else None,
            "choose": label,
        })
    return dl


def _build_answer_strings(questions, n_correct):
    """`ans` / `user_ans` — '|'.join các nhãn, nhãn nhiều giá trị nối bằng ','."""
    ans_parts = []
    user_parts = []
    for i, q in enumerate(questions):
        l_str = _label_to_str(_build_choose_label(q))
        ans_parts.append(l_str)
        user_parts.append(l_str if i < n_correct else "")
    return "|".join(ans_parts), "|".join(user_parts)


def _build_video_data_log(count_problems: int, time_spent: int) -> List[dict]:
    """Port 1:1 Blob 60 của userscript cho bài Video (type 5)."""
    n = max(1, int(count_problems or 5))
    rem = max(n, int(time_spent or 120))
    avg = max(1.0, rem / n)
    out = []
    for i in range(n):
        if i == n - 1:
            ts = max(1, round(rem))
        else:
            ts = max(1, round(avg))
            if rem - ts < (n - i - 1):
                ts = max(1, round(rem / (n - i)))
        rem = max(0, rem - ts)
        out.append({
            "q_params": '["{\\"js\\":\\"\\"}"]',
            "a_params": '["[\\"0\\"]"]',
            "result": 1,
            "correct": 1,
            "wrong": 0,
            "a_index": i,
            "time_spent": ts,
        })
    if rem > 0 and out:
        out[0]["time_spent"] += round(rem)
    return out


def _build_pdf_submit_data(questions: List[dict], n_correct: int) -> Tuple[str, str, str]:
    """Port 1:1 Blob 77 + 82 của userscript cho Kiểm tra PDF (type 10 / 4)."""
    orig_ans: Dict[str, dict] = {"fc": {}, "tf": {}, "dg": {}}
    user_ans: Dict[str, dict] = {"fc": {}, "tf": {}, "dg": {}}
    clog: List[str] = []
    for i, q in enumerate(questions):
        is_ok = i < n_correct
        part = q.get("_pdf_part")
        key = str(q.get("_pdf_key") or (i + 1))
        raw = q.get("_pdf_raw")
        if part == "fc":
            orig_ans["fc"][key] = raw if raw is not None else int(q.get("correctIndex") or 0)
            if is_ok:
                user_ans["fc"][key] = orig_ans["fc"][key]
                clog.append(f"fc-{key}:{q.get('correctLabel') or 'A'}")
            else:
                wrong_idx = (int(q.get("correctIndex") or 0) + 1) % 4
                user_ans["fc"][key] = wrong_idx
                clog.append(f"fc-{key}:{chr(65 + wrong_idx)}")
        elif part == "tf":
            orig_ans["tf"][key] = raw if isinstance(raw, (dict, list)) else {}
            if is_ok:
                user_ans["tf"][key] = orig_ans["tf"][key]
            else:
                user_ans["tf"][key] = {}
            clog.append(f"tf-{key}:DONE")
        elif part == "dg":
            orig_ans["dg"][key] = raw if raw is not None else (q.get("correctAnswer") or "")
            user_ans["dg"][key] = orig_ans["dg"][key] if is_ok else ""
            clog.append(f"dg-{key}:FILL")
    return (
        json.dumps(orig_ans, ensure_ascii=False, separators=(",", ":")),
        json.dumps(user_ans, ensure_ascii=False, separators=(",", ":")),
        ",".join(clog),
    )


def _build_userscript_exam_data_log(questions: List[dict], n_correct_problems: int) -> List[dict]:
    """Port 1:1 chuẩn từng byte từ Blob 68 + Blob 69 của Userscript cho bài Kiểm tra / Đề thông minh / Câu hỏi nhóm (type 13, 14, 18, 21, 22).

    Lý do quan trọng (Hình 1 vs Hình 2):
    Trang 'Xem bài làm' của OLM ghép từng câu trong `quiz_list[]` với phần tử trong `data_log`
    qua trường `idq` (ID câu hỏi), `params` (`{"js":"","order":[...]}`), `answer` (chuỗi JSON đáp án),
    `result` (mảng [1]/[0]), `type` ([q_type]), `label` (["A"]), `score` và `chk` (1/0).
    Nếu thiếu `idq`/`params`/`answer`/`type`, OLM chỉ hiện các ô số 1..15 màu xanh và 'Đúng'
    nhưng khung 'Câu hỏi và câu trả lời của Bạn' bên dưới bị trắng trơn.
    """
    dl: List[dict] = []
    prob_idx = 0
    for _idx, q in enumerate(questions):
        qt = int(q.get("q_type") or 1)
        qid = q.get("id")
        if qt in (21, 22) and q.get("sub_answers"):
            sub_ans = q.get("sub_answers") or []
            sub_orders = q.get("sub_orders") or []
            sub_types = [int(x) for x in (q.get("sub_types") or [1] * len(sub_ans))]
            sub_opt_counts = q.get("sub_option_counts") or []
            sub_labels = q.get("sub_labels") or []

            prs_list: List[str] = []
            ans_str_items: List[str] = []
            result_list: List[int] = []
            labels_out: List[str] = []

            for k, sa in enumerate(sub_ans):
                is_sub_ok = (prob_idx < n_correct_problems)
                prob_idx += 1
                st = sub_types[k] if k < len(sub_types) else 1
                opt_cnt = int(sub_opt_counts[k]) if (k < len(sub_opt_counts) and sub_opt_counts[k]) else 4
                s_ord = (
                    sub_orders[k]
                    if (k < len(sub_orders) and isinstance(sub_orders[k], list) and sub_orders[k])
                    else list(range(max(1, opt_cnt)))
                )
                if st in (1, 13, 11):
                    prs_list.append(json.dumps({"js": "", "order": s_ord}, separators=(",", ":"), ensure_ascii=False))
                else:
                    prs_list.append('{"js":""}')

                sa_arr = list(sa) if isinstance(sa, list) else [sa]
                if is_sub_ok:
                    chosen_sa = [str(x) for x in sa_arr]
                else:
                    if st == 1:
                        corr_idx = int(sa_arr[0]) if (sa_arr and str(sa_arr[0]).lstrip("-").isdigit()) else 0
                        wrong_idx = next((i for i in range(max(2, opt_cnt)) if i != corr_idx), 1)
                        chosen_sa = [str(wrong_idx)]
                    elif st == 13:
                        chosen_sa = [str(x) for x in (sa_arr or ["1"])]
                        chosen_sa[0] = "0" if chosen_sa[0] == "1" else "1"
                    elif st == 11:
                        chosen_sa = ["999999" for _ in (sa_arr or [""])]
                    else:
                        chosen_sa = ["0"]

                ans_str_items.append(json.dumps(chosen_sa, separators=(",", ":"), ensure_ascii=False))
                result_list.append(1 if is_sub_ok else 0)
                if k < len(sub_labels) and sub_labels[k]:
                    labels_out.append(str(sub_labels[k]))
                else:
                    labels_out.append("A" if st == 1 else "")

            params_str = json.dumps(
                {"js": "", "prs": prs_list, "order": list(range(len(sub_ans)))},
                separators=(",", ":"),
                ensure_ascii=False,
            )
            answer_str = json.dumps(ans_str_items, separators=(",", ":"), ensure_ascii=False)
            mresult_list = [[r] for r in result_list]
            max_sc = q.get("max_score") or len(sub_ans) or 1
            all_ok = all(r == 1 for r in result_list)
            dl.append({
                "answer": answer_str,
                "label": ["22C"],
                "labels": labels_out,
                "params": params_str,
                "result": result_list,
                "mresult": mresult_list,
                "wrong_skill": [],
                "correct_skill": [],
                "type": sub_types,
                "list_idq": q.get("list_idq") or q.get("list_ids") or [],
                "idq": qid,
                "score": int(max_sc) if float(max_sc).is_integer() else max_sc,
                "chk": 1 if all_ok else 0,
            })
            continue

        # Câu hỏi đơn (Blob 68 lines 5647-6337)
        is_ok = (prob_idx < n_correct_problems)
        prob_idx += 1
        max_sc = q.get("max_score") or 1
        max_sc_val = int(max_sc) if float(max_sc).is_integer() else max_sc
        opt_cnt = int(q.get("optionCount") or len(q.get("options") or []) or 4)
        ord_list = (
            q.get("order")
            if (isinstance(q.get("order"), list) and q.get("order"))
            else list(range(max(0, opt_cnt)))
        )
        params_with_order = json.dumps({"js": "", "order": ord_list}, separators=(",", ":"), ensure_ascii=False)

        if qt == 13:
            ca = [str(x) for x in (q.get("correctAnswers") or ["1"])]
            if not is_ok and ca:
                ca = list(ca)
                ca[0] = "0" if ca[0] == "1" else "1"
            dl.append({
                "answer": json.dumps(ca, separators=(",", ":"), ensure_ascii=False),
                "label": [],
                "params": params_with_order,
                "result": [1 if is_ok else 0 for _ in ca],
                "wrong_skill": [],
                "correct_skill": [],
                "type": [13],
                "idq": qid,
                "score": max_sc_val,
                "chk": 1 if is_ok else 0,
            })
        elif qt in (2, 18):
            ans_val = (
                q.get("correctAnswer")
                if (isinstance(q.get("correctAnswer"), str) and q.get("correctAnswer"))
                else "OK"
            )
            chosen = [ans_val] if is_ok else ["999999"]
            dl.append({
                "answer": json.dumps(chosen, separators=(",", ":"), ensure_ascii=False),
                "params": '{"js":""}',
                "result": [1 if is_ok else 0],
                "wrong_skill": [],
                "correct_skill": [],
                "type": [qt],
                "idq": qid,
                "score": max_sc_val,
                "chk": 1 if is_ok else 0,
            })
        elif qt == 20:
            ca = list(q.get("correctAnswers") or [])
            chosen = ca if is_ok else list(reversed(ca))
            dl.append({
                "answer": json.dumps(chosen, separators=(",", ":"), ensure_ascii=False),
                "params": params_with_order,
                "result": [1 if is_ok else 0 for _ in chosen],
                "wrong_skill": [],
                "correct_skill": [],
                "type": [20],
                "idq": qid,
                "score": max_sc_val,
                "chk": 1 if is_ok else 0,
            })
        elif qt == 9:
            ca = list(q.get("correctAnswers") or [])
            chosen = ca if is_ok else [99]
            dl.append({
                "answer": json.dumps(chosen, separators=(",", ":"), ensure_ascii=False),
                "params": '{"js":""}',
                "result": [1 if is_ok else 0 for _ in chosen],
                "wrong_skill": [],
                "correct_skill": [],
                "type": [9],
                "idq": qid,
                "score": max_sc_val,
                "chk": 1 if is_ok else 0,
            })
        elif qt in (3, 6, 11):
            ca = [str(x) if qt in (3, 11) else x for x in (q.get("correctAnswers") or [])]
            chosen = ca if is_ok else [str(opt_cnt + 1) if qt in (3, 11) else -1 for _ in ca]
            dl.append({
                "answer": json.dumps(chosen, separators=(",", ":"), ensure_ascii=False),
                "params": params_with_order,
                "result": [1 if is_ok else 0 for _ in chosen],
                "wrong_skill": [],
                "correct_skill": [],
                "type": [qt],
                "idq": qid,
                "score": max_sc_val,
                "chk": 1 if is_ok else 0,
            })
        elif qt == 10:
            ai = list(q.get("answerIndices") or [])
            chosen = ai if is_ok else [[] for _ in ai]
            dl.append({
                "answer": json.dumps(chosen, separators=(",", ":"), ensure_ascii=False),
                "params": params_with_order,
                "result": [1 if is_ok else 0],
                "wrong_skill": [],
                "correct_skill": [],
                "type": [10],
                "idq": qid,
                "score": max_sc_val,
                "chk": 1 if is_ok else 0,
            })
        else:
            ca = list(
                q.get("correctAnswers")
                if q.get("correctAnswers")
                else [str(q.get("correctIndex") if q.get("correctIndex") is not None else "0")]
            )
            if is_ok:
                chosen = [str(x) if qt != 5 else x for x in ca]
                lbl = str(q.get("correctLabel") or "A")
            else:
                corr_idx = int(ca[0]) if (ca and str(ca[0]).lstrip("-").isdigit()) else 0
                wrong_idx = next((i for i in range(max(2, opt_cnt)) if i != corr_idx), 1)
                chosen = [str(wrong_idx)]
                lbl = chr(65 + wrong_idx)
            dl.append({
                "answer": json.dumps(chosen, separators=(",", ":"), ensure_ascii=False),
                "label": [lbl],
                "params": params_with_order,
                "result": [1 if is_ok else 0],
                "wrong_skill": [],
                "correct_skill": [],
                "type": [qt or 1],
                "idq": qid,
                "score": max_sc_val,
                "chk": 1 if is_ok else 0,
            })
    return dl


def _build_userscript_practice_data_log(questions: List[dict], n_correct_problems: int, time_spent: int) -> List[dict]:
    """Port 1:1 chuẩn từng byte từ Blob 61 của Userscript cho bài Luyện tập (type 3)."""
    n = max(1, len(questions))
    rem = max(n, int(time_spent or 90))
    avg = max(1.0, rem / n)
    dl: List[dict] = []
    prob_idx = 0
    for idx, q in enumerate(questions):
        if idx == n - 1:
            ts = max(1, round(rem))
        else:
            ts = max(1, round(avg))
            if rem - ts < (n - idx - 1):
                ts = max(1, round(rem / (n - idx)))
        rem = max(0, rem - ts)
        qt = int(q.get("q_type") or 1)

        if qt in (21, 22) and q.get("sub_answers"):
            sub_ans = q.get("sub_answers") or []
            sub_orders = q.get("sub_orders") or []
            sub_types = [int(x) for x in (q.get("sub_types") or [1] * len(sub_ans))]
            sub_opt_counts = q.get("sub_option_counts") or []
            prs_list: List[str] = []
            ans_list: List[str] = []
            sub_ok_flags: List[bool] = []
            for k, sa in enumerate(sub_ans):
                is_sub_ok = (prob_idx < n_correct_problems)
                prob_idx += 1
                sub_ok_flags.append(is_sub_ok)
                st = sub_types[k] if k < len(sub_types) else 1
                opt_cnt = int(sub_opt_counts[k]) if (k < len(sub_opt_counts) and sub_opt_counts[k]) else 4
                s_ord = (
                    sub_orders[k]
                    if (k < len(sub_orders) and isinstance(sub_orders[k], list) and sub_orders[k])
                    else list(range(max(1, opt_cnt)))
                )
                if st in (1, 13, 11):
                    prs_list.append(json.dumps({"js": "", "order": s_ord}, separators=(",", ":"), ensure_ascii=False))
                else:
                    prs_list.append('{"js":""}')

                sa_arr = list(sa) if isinstance(sa, list) else [sa]
                if is_sub_ok:
                    chosen_sa = [str(x) for x in sa_arr]
                else:
                    if st == 1:
                        corr_idx = int(sa_arr[0]) if (sa_arr and str(sa_arr[0]).lstrip("-").isdigit()) else 0
                        wrong_idx = next((i for i in range(max(2, opt_cnt)) if i != corr_idx), 1)
                        chosen_sa = [str(wrong_idx)]
                    elif st == 13:
                        chosen_sa = [str(x) for x in (sa_arr or ["1"])]
                        chosen_sa[0] = "0" if chosen_sa[0] == "1" else "1"
                    else:
                        chosen_sa = ["999999" for _ in (sa_arr or [""])]
                ans_list.append(json.dumps(chosen_sa, separators=(",", ":"), ensure_ascii=False))

            q_params = json.dumps(
                [json.dumps({"js": "", "prs": prs_list, "order": list(range(len(sub_ans)))}, separators=(",", ":"), ensure_ascii=False)],
                separators=(",", ":"),
                ensure_ascii=False,
            )
            a_params = json.dumps(
                [json.dumps(ans_list, separators=(",", ":"), ensure_ascii=False)],
                separators=(",", ":"),
                ensure_ascii=False,
            )
            is_all_ok = all(sub_ok_flags)
            dl.append({
                "q_params": q_params,
                "a_params": a_params,
                "result": 1 if is_all_ok else 0,
                "correct": 1 if is_all_ok else 0,
                "wrong": 0 if is_all_ok else 1,
                "a_index": idx,
                "time_spent": ts,
            })
            continue

        is_ok = (prob_idx < n_correct_problems)
        prob_idx += 1
        opt_cnt = int(q.get("optionCount") or len(q.get("options") or []) or 4)
        ord_list = (
            q.get("order")
            if (isinstance(q.get("order"), list) and q.get("order"))
            else list(range(max(0, opt_cnt)))
        )
        q_params = json.dumps(
            [json.dumps({"js": "", "order": ord_list}, separators=(",", ":"), ensure_ascii=False)],
            separators=(",", ":"),
            ensure_ascii=False,
        )

        if qt == 13:
            ca = [str(x) for x in (q.get("correctAnswers") or ["0"])]
            if not is_ok and ca:
                ca = list(ca)
                ca[0] = "0" if ca[0] == "1" else "1"
            inner_ans = json.dumps(ca, separators=(",", ":"), ensure_ascii=False)
        elif qt == 2:
            val = (
                q.get("correctAnswer")
                if (isinstance(q.get("correctAnswer"), str) and q.get("correctAnswer"))
                else "..."
            )
            inner_ans = json.dumps([val if is_ok else "SAI_DAP_AN_999"], separators=(",", ":"), ensure_ascii=False)
        elif qt == 9:
            ca = list(q.get("correctAnswers") or [])
            inner_ans = json.dumps(ca if is_ok else [99], separators=(",", ":"), ensure_ascii=False)
        elif qt in (3, 6, 20, 11, 5):
            ca = list(q.get("correctAnswers") or ["0"])
            if is_ok:
                chosen = ca
            else:
                chosen = list(reversed(ca)) if qt in (5, 20, 6) else ["99" for _ in ca]
            inner_ans = json.dumps(chosen, separators=(",", ":"), ensure_ascii=False)
        elif qt == 10:
            ai = list(q.get("answerIndices") or [])
            inner_ans = json.dumps(ai if is_ok else [[] for _ in ai], separators=(",", ":"), ensure_ascii=False)
        else:
            ca = list(
                q.get("correctAnswers")
                if q.get("correctAnswers")
                else [str(q.get("correctIndex") if q.get("correctIndex") is not None else "0")]
            )
            if is_ok:
                chosen = [str(x) for x in ca]
            else:
                corr_idx = int(ca[0]) if (ca and str(ca[0]).lstrip("-").isdigit()) else 0
                wrong_idx = next((i for i in range(max(2, opt_cnt)) if i != corr_idx), 1)
                chosen = [str(wrong_idx)]
            inner_ans = json.dumps(chosen, separators=(",", ":"), ensure_ascii=False)

        a_params = json.dumps([inner_ans], separators=(",", ":"), ensure_ascii=False)
        dl.append({
            "q_params": q_params,
            "a_params": a_params,
            "result": 1 if is_ok else 0,
            "correct": 1 if is_ok else 0,
            "wrong": 0 if is_ok else 1,
            "a_index": idx,
            "time_spent": ts,
        })
    return dl


def _build_userscript_post_payload(
    *,
    exam_type: int,
    is_exam_quiz: bool,
    is_video: bool,
    is_pdf_questions: bool,
    dc: dict,
    id_cate: str,
    uid: str,
    questions: List[dict],
    n_correct: int,
    N: int,
    time_spent: int,
    now: int,
    data_log: List[dict],
    base_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Tạo POST payload `/course/teacher-static` khớp 100% từng trường của Userscript
    theo đúng loại bài (Blob 59 cho Kiểm tra, Blob 56 cho Luyện tập, Blob 55 cho Video, Blob 82 cho PDF).
    """
    data_log_json = json.dumps(data_log, separators=(",", ":"), ensure_ascii=False)
    id_cat_val = str(dc.get("id_category") or id_cate)
    id_grade_val = str(dc.get("id_grade") or "11")
    id_cw_val = str(dc.get("id_courseware") or "0")
    id_grp_val = str(dc.get("id_group") or "0")
    id_sch_val = str(dc.get("id_school") or "0")
    type_vip_val = str(dc.get("type_vip") or "0")

    # 1) Bài Kiểm tra / Bài GV giao / Câu hỏi nhóm (Blob 59)
    if is_exam_quiz and not is_pdf_questions:
        exam_max_raw = sum(float(q.get("max_score") or 1) for q in questions)
        if exam_max_raw <= 0:
            exam_max_raw = float(N or len(questions) or 1)
        if n_correct >= N:
            exam_cor_raw = exam_max_raw
        elif n_correct <= 0:
            exam_cor_raw = 0.0
        else:
            exam_cor_raw = round(exam_max_raw * n_correct / max(1, N), 2)
        exam_wrg_raw = max(0.0, round(exam_max_raw - exam_cor_raw, 2))

        def _fmt_num(v: float) -> str:
            return str(int(v)) if float(v).is_integer() else str(round(v, 2))

        p: Dict[str, Any] = {
            "id_user": str(uid),
            "id_cate": id_cat_val,
            "id_grade": id_grade_val,
            "id_courseware": id_cw_val,
            "id_group": id_grp_val,
            "id_school": id_sch_val,
            "type_vip": type_vip_val,
            "time_spent": str(int(time_spent)),
            "total_time": str(int(time_spent)),
            "current_time": "0",
            "tl_score": "0",
            "tn_score": _fmt_num(exam_cor_raw),
            "score": _fmt_num(exam_cor_raw),
            "max_score": _fmt_num(exam_max_raw),
            "correct": _fmt_num(exam_cor_raw),
            "wrong": _fmt_num(exam_wrg_raw),
            "ended": "1",
            "missed": "0",
            "date_end": str(now + int(time_spent)),
            "type_exam": "1",
            "save_star": "1",
            "data_log": data_log_json,
            "time_init": "",
            "name_user": "",
            "times": "0",
            "time_stored": str(now),
            "_id": "",
            "nx": "",
            "_score": "0",
            "iframe": "false",
            "count_redo": "1",
            "choose_log[0][ind]": "0",
            "choose_log[0][t]": str(now),
            "choose_log[0][l]": "1",
        }
        quiz_ids = [str(q.get("id") or "") for q in questions if q.get("id")]
        if quiz_ids:
            p["quiz_list[]"] = quiz_ids
        for q in questions:
            qid = str(q.get("id") or "")
            if qid:
                qmax = float(q.get("max_score") or 1)
                p[f"score_list[{qid}]"] = _fmt_num(qmax)
        return p

    # 2) Bài Luyện tập (Blob 56)
    if exam_type == 3 and not is_pdf_questions and not is_video:
        total_q = max(1, len(questions))
        correct_q = sum(1 for item in data_log if item.get("correct") == 1)
        wrong_q = max(0, total_q - correct_q)
        pct_score = round((correct_q / total_q) * 100)
        return {
            "id_user": str(uid),
            "id_cate": id_cat_val,
            "id_grade": id_grade_val,
            "id_courseware": id_cw_val,
            "id_group": id_grp_val,
            "id_school": id_sch_val,
            "type_vip": type_vip_val,
            "time_spent": str(int(time_spent)),
            "total_time": str(int(time_spent)),
            "current_time": "0",
            "tl_score": "0",
            "tn_score": str(correct_q),
            "score": str(pct_score),
            "max_score": str(total_q),
            "correct": str(correct_q),
            "wrong": str(wrong_q),
            "ended": "1",
            "missed": "0",
            "date_end": str(now),
            "type_exam": "1",
            "save_star": "1",
            "data_log": data_log_json,
            "time_init": "",
            "name_user": "",
            "times": "0",
        }

    # 3) Bài giảng Video tương tác (Blob 55)
    if is_video and all(q.get("_is_video_step") for q in questions):
        return {
            "id_user": str(uid),
            "id_cate": id_cat_val,
            "id_grade": id_grade_val,
            "id_courseware": id_cw_val,
            "id_group": id_grp_val,
            "id_school": id_sch_val,
            "type_vip": type_vip_val,
            "time_spent": str(int(time_spent)),
            "total_time": str(int(time_spent)),
            "current_time": str(int(time_spent)),
            "score": "100",
            "count_problems": str(N),
            "correct": str(n_correct),
            "ended": "1",
            "save_star": "1",
            "data_log": data_log_json,
            "totalq": "0",
        }

    return base_payload


# === v19.1 - PORT FROM USERSCRIPT (telemetry) ===
def _qtype_histogram(questions: List[dict]) -> Dict[str, int]:
    """Đếm phân bố q_type mỗi bài (dùng cho log/telemetry)."""
    hist: Dict[str, int] = {}
    for q in questions or []:
        k = f"q{q.get('q_type')}"
        hist[k] = hist.get(k, 0) + 1
    return hist


def _log_telemetry(event: str, **kw) -> None:
    """Log telemetry dạng thông tin (KHÔNG dùng mức ERROR/crash log).

    Dùng chung cho QTYPE_DIST / HOOK_* / DOM_FILL để không làm nhiễu crash.log.
    """
    try:
        parts = [f"{k}={v}" for k, v in kw.items()]
        _log_warn(f"[{event}] " + " | ".join(parts))
    except Exception:
        pass


def _log_qtype_distribution(id_cate: str, questions: List[dict]) -> None:
    """Log q_type distribution mỗi bài (yêu cầu M)."""
    try:
        hist = _qtype_histogram(questions)
        _log_telemetry("QTYPE_DIST", id_cate=str(id_cate),
                       total=len(questions or []),
                       dist=json.dumps(hist, ensure_ascii=False))
    except Exception:
        pass


def _log_hook(event: str, **kw) -> None:
    """Log mỗi lần hook detect (XHR/fetch/JSON.parse/detectQuestion) — yêu cầu M."""
    try:
        _log_telemetry("HOOK_" + str(event).upper(), **kw)
    except Exception:
        pass


def _log_dom_fill(result: Any) -> None:
    """Log DOM fill result {text, radio, checkbox, errors} — yêu cầu M."""
    try:
        if isinstance(result, dict):
            _log_telemetry("DOM_FILL",
                           text=result.get("text"), radio=result.get("radio"),
                           checkbox=result.get("checkbox"),
                           select=result.get("select"),
                           order=result.get("order"), match=result.get("match"),
                           errors=json.dumps(result.get("errors") or [],
                                             ensure_ascii=False)[:400])
        else:
            _log_telemetry("DOM_FILL", raw=str(result)[:400])
    except Exception:
        pass


# CACHE CONSTANTS
# ═══════════════════════════════════════════════════════════════
CACHE_TTL_SEC = 3600
CACHE_CLEAN_SEC = 86400


# ═══════════════════════════════════════════════════════════════
# OLM CLIENT
# ═══════════════════════════════════════════════════════════════
class OLMClient:
    def __init__(self, sess: OLMSession, cdp_mgr: CDPManager):
        self.sess = sess
        self.cdp = cdp_mgr
        self._hl_pw = None
        self._hl_browser = None
        self.prefer_cdp = False
        self._qcache: Dict[str, dict] = {}
        self._load_qcache()

    def _load_qcache(self):
        try:
            if Q_CACHE_F.exists():
                d = json.loads(Q_CACHE_F.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    now = time.time()
                    cleaned = {}
                    for k, v in d.items():
                        # v19.1: auto-clean cache > CACHE_CLEAN_SEC (24h)
                        if (isinstance(v, dict)
                                and now - v.get("ts", 0) < CACHE_CLEAN_SEC
                                and v.get("schema_version", 0) == CURRENT_SCHEMA_VERSION):
                            cleaned[k] = v
                    self._qcache = cleaned
                    if len(cleaned) != len(d):
                        self._save_qcache()
        except Exception as e:
            _log_warn(f"_load_qcache failed: {e}")

    def _save_qcache(self):
        try:
            Q_CACHE_F.write_text(json.dumps(self._qcache, ensure_ascii=False),
                                 encoding="utf-8")
        except Exception as e:
            _log_warn(f"_save_qcache failed: {e}")

    # === v19.1 - PORT FROM USERSCRIPT (cache TTL + khoá id_cate+q_count) ===
    @staticmethod
    def _qcache_key(id_cate: str, q_count: Optional[int] = None) -> str:
        """Cache key = `id_cate` + số câu.

        Tránh stale khi OLM cập nhật đề (thêm/bớt câu) mà `id_cate` không đổi.
        """
        if q_count is None:
            return str(id_cate)
        return f"{id_cate}#{int(q_count)}"

    def _qcache_get(self, id_cate: str, q_count: Optional[int] = None,
                    force: bool = False):
        """Lấy cache theo TTL; trả None nếu miss/hết hạn/bị force refresh."""
        if force:
            return None
        now = time.time()
        keys = [self._qcache_key(id_cate, q_count)] if q_count is not None \
            else [self._qcache_key(id_cate)]
        # luôn thử cả khoá trần để tương thích cache cũ
        keys.append(self._qcache_key(id_cate))
        for k in keys:
            entry = self._qcache.get(k)
            if not isinstance(entry, dict):
                continue
            if now - entry.get("ts", 0) >= CACHE_TTL_SEC:
                continue
            return entry
        return None

    def _qcache_put(self, id_cate: str, q_count: Optional[int],
                    questions: List[dict], data_cate: dict) -> None:
        now = time.time()
        entry = {
            "ts": now,
            "questions": questions,
            "data_cate": data_cate,
            "q_count": (len(questions) if q_count is None else int(q_count)),
            "schema_version": CURRENT_SCHEMA_VERSION,
        }
        self._qcache[self._qcache_key(id_cate)] = entry
        if q_count is not None:
            self._qcache[self._qcache_key(id_cate, q_count)] = entry
        # dọn entry quá hạn ngay khi ghi
        cutoff = now - CACHE_CLEAN_SEC
        for k in [k for k, v in self._qcache.items()
                  if not isinstance(v, dict) or v.get("ts", 0) < cutoff]:
            self._qcache.pop(k, None)
        self._save_qcache()

    def clear_qcache(self, id_cate: Optional[str] = None) -> int:
        """Force refresh: xoá cache của 1 bài hoặc toàn bộ (nút 'Làm lại')."""
        if id_cate is None:
            n = len(self._qcache)
            self._qcache = {}
        else:
            pref = str(id_cate)
            keys = [k for k in self._qcache
                    if k == pref or k.startswith(pref + "#")]
            n = len(keys)
            for k in keys:
                self._qcache.pop(k, None)
        self._save_qcache()
        _log_telemetry("QCACHE_CLEARED", id_cate=str(id_cate), removed=n)
        return n

    async def _ensure_headless(self):
        if not HAS_PW: raise ChromiumMissing("Playwright chưa cài")
        exe = _find_chromium_exe()
        if not exe: raise ChromiumMissing("CHROMIUM_MISSING: Chưa có Chromium.")
        if self._hl_browser is not None:
            try:
                _ = self._hl_browser.contexts
                return self._hl_browser
            except Exception: self._hl_browser = None
        if self._hl_pw is None: self._hl_pw = await async_playwright().start()
        try:
            self._hl_browser = await self._hl_pw.chromium.launch(
                headless=True, executable_path=str(exe),
                args=["--disable-blink-features=AutomationControlled",
                      "--disable-features=AutomationControlled,Translate",
                      "--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-gpu", "--no-first-run",
                      "--no-default-browser-check", "--disable-infobars",
                      "--mute-audio"])
        except Exception as e:
            raise ChromiumMissing(f"Không mở được chromium: {e}")
        return self._hl_browser

    async def _new_headless_context(self):
        b = await self._ensure_headless()
        ctx = await b.new_context(
            user_agent=UA, locale="vi-VN",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8"})
        try: await ctx.add_init_script(ANTI_DETECT_JS)
        except Exception: pass
        try: await ctx.add_init_script(AUTOFILL_JS)
        except Exception: pass
        # v20.0: lớp LÀM BÀI THẬT SỰ (tick DOM + bấm nút Nộp của OLM)
        try: await ctx.add_init_script(REAL_SUBMIT_JS)
        except Exception: pass
        cookies = [{"name": k, "value": v, "domain": ".olm.vn", "path": "/"}
                   for k, v in self.sess.cookies.items()]
        if cookies:
            try: await ctx.add_cookies(cookies)
            except Exception: pass
        return ctx

    async def _page(self):
        if self.prefer_cdp or _cdp_available().get("available"):
            page, err = await self.cdp.get_olm_page()
            if not err and page is not None:
                try: await page.add_init_script(ANTI_DETECT_JS)
                except Exception: pass
                try: await page.add_init_script(REAL_SUBMIT_JS)
                except Exception: pass
                try: await page.evaluate(ANTI_DETECT_JS)
                except Exception: pass
                try: await page.evaluate(AUTOFILL_JS)
                except Exception: pass
                try: await page.evaluate(REAL_SUBMIT_JS)
                except Exception: pass

                async def _cleanup_cdp():
                    try: await self.cdp.sync_cookies_to_session(self.sess)
                    except Exception: pass
                return _cleanup_cdp, page

        ctx = await self._new_headless_context()
        page = await ctx.new_page()

        async def _cleanup_hl():
            try: await ctx.close()
            except Exception: pass
        return _cleanup_hl, page

    async def close(self):
        try:
            if self._hl_browser: await self._hl_browser.close()
        except Exception: pass
        self._hl_browser = None
        try:
            if self._hl_pw: await self._hl_pw.stop()
        except Exception: pass
        self._hl_pw = None

    async def _goto(self, page, url, timeout=60000, wait_cf=45):
        url = _url_v1(url)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        except Exception as e:
            raise OLMError(f"goto: {e}") from e
        start = time.time()
        while time.time() - start < wait_cf:
            try:
                title = (await page.title()) or ""
                cur = page.url or ""
            except Exception:
                await asyncio.sleep(1); continue
            in_cf = ("just a moment" in title.lower()
                     or "cloudflare" in title.lower()
                     or "challenges.cloudflare.com" in cur
                     or "attention required" in title.lower())
            if not in_cf:
                return {"ok": True, "url": cur}
            await asyncio.sleep(1.5)
        raise CloudflareBlock("CF_BLOCK: Cloudflare timeout")

    # ═══════════════════════════════════════════════════════════════════════════
    # v20.1 — QUÉT BÀI CHO GIAO DIỆN OLM MỚI (v2)
    # ═══════════════════════════════════════════════════════════════════════════
    # BUG CŨ: chỉ quét DOM theo danh sách selector của giao diện CŨ
    # (tr.my-given-courseware-item, table.table-striped tbody tr...). Giao diện
    # OLM v2 render bằng JS/SPA và không dùng các class đó -> quét ra RỖNG.
    # Không có API nào của OLM được công bố, nên ĐOÁN selector/endpoint là vô
    # vọng. Cách chắc chắn: BẮT response XHR/fetch mà chính trang gọi, rồi bóc
    # JSON bằng thuật toán tổng quát.
    #
    # Chiến lược 4 tầng, dừng ở tầng nào ra kết quả trước:
    #   T1. Mở trang bài tập + BẮT MỌI response JSON -> bóc đệ quy (không phụ
    #       thuộc tên endpoint hay shape).
    #   T2. Nếu trang đã có sẵn dữ liệu JS (window.data_cate / INITIAL_STATE /
    #       __NEXT_DATA__ / CATE_UI), bóc thẳng từ đó.
    #   T3. Quét DOM bằng selector cũ + selector v2 + quét <a href> tổng quát.
    #   T4. GET HTML thô bằng curl_cffi rồi regex.
    # ═══════════════════════════════════════════════════════════════════════════

    # Từ khoá nhận diện URL API bài tập (dùng cho cả bắt response lẫn tự gọi)
    _ASSIGN_URL_HINTS = (
        "assignment", "courseware", "bai-tap", "homework", "exercise",
        "teacher-categories", "categories", "chu-de", "lesson", "course",
        "my-given", "given-courseware", "de-thi", "test", "quiz", "class",
    )
    # Từ khoá nhận diện object là 1 bài tập (ưu tiên id_category/id_cate trước)
    _ASSIGN_PRIMARY_ID_KEYS = ("id_category", "id_cate", "cate_id")
    _ASSIGN_KEY_HINTS = (
        "id_category", "id_cate", "cate_id", "id_courseware", "id_homework",
        "id_assignment", "id_exercise", "courseware_id",
    )
    _ASSIGN_NAME_KEYS = (
        "name", "title", "name_category", "category_name", "cate_name",
        "courseware_name", "name_courseware", "label", "subject",
    )
    _ASSIGN_DONE_KEYS = (
        "done", "is_done", "completed", "is_completed", "finished",
        "is_finished", "da_lam", "submitted", "is_submit", "status",
        "ended", "count_done", "times",
    )
    _ASSIGN_SCORE_KEYS = (
        "score", "score_10", "tn_score", "diem", "point", "points", "mark",
        "my_score", "score_get", "total_score", "result",
    )
    _ASSIGN_TYPE_KEYS = ("type", "type_cate", "kind", "cate_type", "type_vip")
    _ASSIGN_MAX_KEYS = ("total", "totalq", "count_problems", "max_score",
                        "total_score", "max_point", "score_max",
                        "num_question", "count_question")

    @staticmethod
    def _norm_assign_title(s: str) -> str:
        t = str(s or "").strip()
        t = re.sub(r"^\s*\[[^\]]{1,30}\]\s*", "", t)
        t = re.sub(r"\s+", " ", t).strip().lower()
        return t

    def _walk_json_for_assignments(self, obj, base_url: str = "") -> List[dict]:
        """Duyệt ĐỆ QUY mọi shape JSON, nhặt ra các object trông như bài tập.

        Không phụ thuộc cấu trúc: OLM v2 có thể trả
        {data:[...]}, {data:{items:[...]}}, [[...]], {list:...}, v.v.
        Đồng thời chống nhân đôi bài giả khi object cha (id_courseware / id 11-12 số)
        bọc quanh object con (category.id_category).
        """
        found: List[dict] = []
        by_cid: Dict[str, dict] = {}
        _NON_ASSIGN_KEYS = ("email", "username", "password", "avatar",
                            "phone", "role_id", "birthday", "gender")
        _ASSIGN_SIGNAL_KEYS = (
            self._ASSIGN_DONE_KEYS + self._ASSIGN_SCORE_KEYS
            + self._ASSIGN_TYPE_KEYS + self._ASSIGN_MAX_KEYS
            + ("url", "href", "link", "slug", "path", "time_spent",
               "list_id_quiz", "id_grade", "id_subject", "record", "user_record")
        )
        _URL_ID_RE = re.compile(
            r"/(?:chu-de|bai-tap|lam-bai)/[A-Za-z0-9\-_.]*?(\d{5,})(?:[/?#]|$)"
        )

        def _extract_url_cid(o: dict) -> Optional[str]:
            for k in ("url", "href", "link", "slug", "path"):
                v = o.get(k)
                if isinstance(v, str) and v:
                    m = _URL_ID_RE.search(v)
                    if m:
                        return m.group(1)
            return None

        def _primary_cid(o: dict) -> Optional[str]:
            if any(k in o for k in _NON_ASSIGN_KEYS):
                return None
            u_id = _extract_url_cid(o)
            if u_id:
                return u_id
            for k in self._ASSIGN_PRIMARY_ID_KEYS:
                v = o.get(k)
                if v is not None:
                    s = str(v).strip()
                    if re.fullmatch(r"\d{5,}", s):
                        return s
            return None

        def _child_primary_cid(o: dict) -> Optional[str]:
            for v in o.values():
                if isinstance(v, dict):
                    pc = _primary_cid(v)
                    if pc:
                        return pc
            return None

        def _cid(o: dict) -> Optional[str]:
            if any(k in o for k in _NON_ASSIGN_KEYS):
                return None
            pc = _primary_cid(o)
            if pc:
                return pc
            # Nếu object hiện tại là wrapper bọc 1 object con có id_category chuẩn
            # (VD: {id: 118873987861, id_courseware: 118873987861, category: {id_category: 5201418752}})
            # thì KHÔNG lấy id_courseware/id của wrapper làm bài riêng, mà gộp vào id_category con!
            child_pc = _child_primary_cid(o)
            if child_pc:
                return child_pc
            for k in self._ASSIGN_KEY_HINTS:
                v = o.get(k)
                if v is None:
                    continue
                s = str(v).strip()
                if re.fullmatch(r"\d{5,}", s):
                    return s
            # `id` trần chỉ nhận khi có cả tên bài tập lẫn dấu hiệu bài tập và <= 10 chữ số
            # (ID bảng phân phối/giao bài 11-12 chữ số như 118873987861 không phải id_cate)
            v = o.get("id") or o.get("_id")
            if v is not None:
                s = str(v).strip()
                if (re.fullmatch(r"\d{6,10}", s)
                        and any(k in o for k in self._ASSIGN_NAME_KEYS)
                        and any(k in o for k in _ASSIGN_SIGNAL_KEYS)):
                    return s
            return None

        def _name(o: dict) -> str:
            for k in self._ASSIGN_NAME_KEYS:
                v = o.get(k)
                if isinstance(v, str) and v.strip():
                    t = re.sub(r"\s+", " ", v.strip())
                    t = re.sub(r"^\s*\[(?:Luyện tập|Video|Lý thuyết|Kiểm tra|Đề thi|Bài giảng)\]\s*",
                               "", t, flags=re.I)
                    return t[:150]
            return ""

        def _truthy(v) -> bool:
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return v >= 1
            if isinstance(v, str):
                s = v.strip().lower()
                if s in ("1", "2", "true", "done", "completed", "finished", "yes",
                         "đã làm", "da lam", "hoàn thành", "xong", "submitted",
                         "đã nộp", "da nop", "xem bài làm", "xem bai lam"):
                    return True
            return False

        def _num(v):
            if isinstance(v, bool):
                return None
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                m = re.search(r"([\d]+(?:[.,]\d+)?)", v)
                if m:
                    try:
                        return float(m.group(1).replace(",", "."))
                    except Exception:
                        return None
            return None

        def _extract_meta(node: dict, rec: dict):
            # href
            for k in ("url", "href", "link", "slug", "path"):
                v = node.get(k)
                if isinstance(v, str) and v:
                    m = _URL_ID_RE.search(v)
                    if m:
                        if v.startswith("http"):
                            rec["href"] = v
                        else:
                            rec["href"] = (OLM_BASE.rstrip("/") + "/"
                                           + v.lstrip("/"))
                        break
            # done
            for k in self._ASSIGN_DONE_KEYS:
                if k in node and _truthy(node[k]):
                    rec["done"] = True
            # điểm (chỉ lấy score > 0 hoặc khi đã xác nhận rec["done"] == True;
            # OLM khởi tạo score=0 cho mọi bài chưa làm)
            for k in self._ASSIGN_SCORE_KEYS:
                if k in node and node[k] is not None and node[k] != "":
                    n = _num(node[k])
                    if n is not None:
                        if n > 0 or rec.get("done"):
                            rec["score"] = n
                        if n > 0:
                            rec["done"] = True
                        break
            # tổng điểm / số câu
            for k in self._ASSIGN_MAX_KEYS:
                if k in node and node[k] is not None and node[k] != "":
                    n = _num(node[k])
                    if n is not None:
                        rec["total"] = n
                        break
            # câu đúng
            for k in ("correct", "num_correct", "count_correct"):
                if k in node and node[k] is not None and node[k] != "":
                    n = _num(node[k])
                    if n is not None:
                        rec["correct"] = int(n)
                        break
            # bóc thêm từ object con `record` / `user_record` / `result` nếu có
            for rk in ("record", "user_record", "my_record", "static", "teacher_static"):
                rv = node.get(rk)
                if isinstance(rv, dict) and rv:
                    if rv.get("messageStatic") or _truthy(rv.get("ended")) or _truthy(rv.get("done")):
                        rec["done"] = True
                    for sk in ("score_10", "score", "tn_score", "point", "diem"):
                        if sk in rv and rv[sk] is not None and rv[sk] != "":
                            sn = _num(rv[sk])
                            if sn is not None:
                                rec["score"] = sn
                                rec["done"] = True
                                break
                    if rv.get("correct") is not None:
                        cn = _num(rv.get("correct"))
                        if cn is not None:
                            rec["correct"] = int(cn)
                            rec["done"] = True
                    if rv.get("totalq") is not None and rec.get("total") is None:
                        tn = _num(rv.get("totalq"))
                        if tn is not None:
                            rec["total"] = int(tn)
                    if rv.get("time_spent") and _num(rv.get("time_spent")) and _num(rv.get("time_spent")) > 0:
                        rec["done"] = True
            # loại bài
            for k in self._ASSIGN_TYPE_KEYS:
                v = node.get(k)
                if isinstance(v, (int, str)) and str(v).strip().isdigit():
                    t_val = int(str(v).strip())
                    if t_val in EXAM_TYPE_LABEL or rec.get("type") is None:
                        rec["type"] = t_val
                        rec["type_label"] = EXAM_TYPE_LABEL.get(t_val, "")
                        break
            if rec["score"] is not None:
                rec["done"] = True

        def _rec(node, depth: int):
            if depth > 12 or node is None:
                return
            if isinstance(node, list):
                for it in node:
                    _rec(it, depth + 1)
                return
            if not isinstance(node, dict):
                return

            cid = _cid(node)
            if cid:
                if cid not in by_cid:
                    rec = {
                        "id": cid,
                        "title": _name(node) or f"Bài {cid}",
                        "href": "",
                        "type": None,
                        "done": False,
                        "score": None,
                        "correct": None,
                        "total": None,
                    }
                    _extract_meta(node, rec)
                    by_cid[cid] = rec
                    found.append(rec)
                else:
                    ex = by_cid[cid]
                    nm = _name(node)
                    if nm and (not ex.get("title") or re.fullmatch(r"Bài\s+\d+", ex["title"], re.I)):
                        ex["title"] = nm
                    _extract_meta(node, ex)

            for v in node.values():
                if isinstance(v, (dict, list)):
                    _rec(v, depth + 1)

        _rec(obj, 0)
        return found

    async def _scan_via_network(self, page, url: str) -> List[dict]:
        """Mở `url` và BẮT mọi response JSON mà trang gọi -> bóc bài tập.

        Playwright Python: `page.on("response", handler)` chấp nhận cả handler
        async; handler async sẽ được await trước khi đóng trang, nên ta gom
        coroutine `resp.text()` rồi await tất cả ở cuối.
        """
        bodies: List[Any] = []
        pending: List[Any] = []
        seen_bodies: set = set()

        async def _on_response(resp):
            try:
                u = (resp.url or "").lower()
                try:
                    ct = (resp.headers or {}).get("content-type", "") or ""
                except Exception:
                    ct = ""
                if any(x in u for x in (".js", ".css", ".png", ".jpg", ".jpeg",
                                        ".gif", ".svg", ".woff", ".woff2",
                                        ".ico", ".map", "google", "facebook",
                                        "analytics", "gtag", "sentry",
                                        "cloudflare", "doubleclick")):
                    return
                is_api = ("json" in ct or "/api/" in u or "/course/" in u
                          or "/user/" in u
                          or any(h in u for h in self._ASSIGN_URL_HINTS))
                if not is_api:
                    return
                try:
                    body = await resp.text()
                except Exception:
                    return
                body = str(body or "")
                if len(body) < 2 or len(body) > 8_000_000:
                    return
                if body[:1] not in "[{":
                    return
                if body in seen_bodies:
                    return
                seen_bodies.add(body)
                try:
                    bodies.append(json.loads(body))
                except Exception:
                    return
            except Exception:
                return

        def _register():
            # thu cả 2 dạng: coroutine (async) và giá trị trả ngay
            def _sync(resp):
                try:
                    r = _on_response(resp)
                    if hasattr(r, "__await__"):
                        pending.append(asyncio.ensure_future(r))
                except Exception:
                    pass
            page.on("response", _sync)

        try:
            _register()
        except Exception:
            return []

        try:
            await self._goto(page, url, timeout=60000, wait_cf=45)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            for _ in range(10):
                try:
                    await page.evaluate("window.scrollBy(0, 900)")
                except Exception:
                    break
                await asyncio.sleep(0.45)
            try:
                await page.evaluate("window.scrollTo(0, 0)")
            except Exception:
                pass
            try:
                await page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            await asyncio.sleep(1.2)
        except CloudflareBlock:
            raise
        except Exception:
            pass

        # chờ nốt các handler đang đọc body
        if pending:
            try:
                await asyncio.wait(pending, timeout=8)
            except Exception:
                pass

        items: List[dict] = []
        for body in bodies:
            items.extend(self._walk_json_for_assignments(body, url))
        return items

    async def _scan_via_page_state(self, page) -> List[dict]:
        """Bóc dữ liệu bài tập đã có sẵn trong biến JS của trang."""
        JS = r"""() => {
            const out = [];
            const seen = new Set();
            const push = (o) => {
                try { out.push(JSON.parse(JSON.stringify(o))); } catch(e) {}
            };
            const cands = [];
            const names = ['data_cate', 'INITIAL_STATE', '__NEXT_DATA__',
                           '__NUXT__', 'APP_STATE', 'cate_data',
                           'list_cate', 'listCate', 'assignments',
                           'myAssignments', 'coursewares'];
            for (const n of names) {
                try {
                    const v = window[n];
                    if (v) cands.push(v);
                } catch(e) {}
            }
            // script JSON nhúng trong DOM
            try {
                document.querySelectorAll(
                    'script[type="application/json"], script#__NEXT_DATA__'
                ).forEach(s => {
                    try { cands.push(JSON.parse(s.textContent || '')); }
                    catch(e) {}
                });
            } catch(e) {}
            // biến toàn cục kiểu window.X với X chứa 'cate'/'assignment'
            try {
                for (const k of Object.keys(window)) {
                    if (/cate|assign|courseware|homework/i.test(k)) {
                        try {
                            const v = window[k];
                            if (v && typeof v === 'object') cands.push(v);
                        } catch(e) {}
                    }
                }
            } catch(e) {}
            for (const c of cands) {
                try { push(c); } catch(e) {}
            }
            return out;
        }"""
        try:
            states = await page.evaluate(JS)
        except Exception:
            return []
        items: List[dict] = []
        for st in (states or []):
            items.extend(self._walk_json_for_assignments(st))
        return items

    def _dedup_assignments(self, items: List[dict]) -> List[dict]:
        """Khử hoàn toàn các bài giả / bài trùng lặp (wrapper ID 11-12 số vs id_category thật)."""
        if not items:
            return []
        by_norm_title: Dict[str, List[dict]] = {}
        for it in items:
            nt = self._norm_assign_title(it.get("title") or "")
            if nt and not re.fullmatch(r"bài\s+\d+", nt):
                by_norm_title.setdefault(nt, []).append(it)

        drop_ids: set = set()
        for nt, group in by_norm_title.items():
            if len(group) < 2:
                continue
            # Chấm điểm độ tin cậy của từng item trong nhóm cùng tên
            def _rank(x: dict) -> Tuple[int, int, int, int]:
                cid = str(x.get("id") or "")
                has_chu_de = 1 if ("/chu-de/" in str(x.get("href") or "")) else 0
                has_type = 1 if x.get("type") else 0
                reasonable_len = 1 if (6 <= len(cid) <= 10) else 0
                has_done = 1 if x.get("done") or (x.get("score") is not None) else 0
                return (has_chu_de, reasonable_len, has_type, has_done)

            group_sorted = sorted(group, key=_rank, reverse=True)
            best = group_sorted[0]
            best_rank = _rank(best)
            for other in group_sorted[1:]:
                other_id = str(other.get("id") or "")
                other_has_href = "/chu-de/" in str(other.get("href") or "")
                # Nếu `other` không có link /chu-de/ riêng (hoặc trùng link với `best`)
                # và kém điểm tin cậy hơn (VD: ID 11-12 chữ số #118873987861 hoặc không có type)
                if (not other_has_href or other.get("href") == best.get("href")) and (
                    len(other_id) >= 11 or not other.get("type") or _rank(other) < best_rank
                ):
                    if other.get("done"):
                        best["done"] = True
                    if other.get("score") is not None and best.get("score") is None:
                        best["score"] = other["score"]
                    if other.get("correct") is not None and best.get("correct") is None:
                        best["correct"] = other["correct"]
                    if other.get("total") is not None and best.get("total") is None:
                        best["total"] = other["total"]
                    if other.get("type") and not best.get("type"):
                        best["type"] = other["type"]
                        best["type_label"] = other.get("type_label") or EXAM_TYPE_LABEL.get(other["type"], "")
                    drop_ids.add(other_id)

        return [it for it in items if str(it.get("id") or "") not in drop_ids]

    async def scan_assignments(self) -> List[dict]:
        """Quét danh sách bài tập chuẩn xác theo cấu trúc DOM + API của OLM."""
        JS_SCAN = r"""() => {
            const out = []; const seen = new Set(); const processedEls = new Set();
            const ACTION_RE = /^(làm bài|làm lại|luyện tập lại|xem lại|xem bài làm|xem bai lam|tải word|xóa|nộp bài|chi tiết|bắt đầu|tiếp tục|xem kết quả|thống kê|vào học|vào thi)$/i;
            const META_LINE_RE = /^(giao bởi|hạn nộp|ngày giao|thời gian|lớp\s*:|môn\s*:|\d+(?:[.,]\d+)?\s*điểm\b|điểm\s*[:：]|chưa làm|đã làm|đã nộp|hoàn thành)/i;
            const extractId = (h) => {
                if (!h) return null;
                let m = h.match(/(?:\/chu-de\/|\/bai-tap\/|\/lam-bai\/)[A-Za-z0-9\-_.]*?(\d{6,})(?:[/?#]|$)/);
                if (m) return m[1];
                m = h.match(/[?&](?:id_category|id_cate|cate_id)=(\d{6,})/);
                return m ? m[1] : null;
            };
            const cleanTitle = (s) => {
                if (!s) return '';
                const lines = String(s).split(/\r?\n/).map(l => l.trim()).filter(Boolean);
                for (let line of lines) {
                    line = line.replace(/^\s*\[(?:Luyện tập|Video|Lý thuyết|Kiểm tra|Kiểm tra PDF|Đề thi|Bài giảng)\]\s*/i, '').trim();
                    if (!line) continue;
                    if (!ACTION_RE.test(line) && !META_LINE_RE.test(line) && !/^\d+$/.test(line)) {
                        return line.replace(/\s+/g, ' ').slice(0, 160);
                    }
                }
                return '';
            };
            const detectType = (el) => {
                if (!el) return null;
                const cl = (el.className || '').toLowerCase();
                if (/pdf/.test(cl)) return 10;
                const txt = (el.innerText || '').toLowerCase();
                if (/\[kiểm tra pdf\]|kiểm tra pdf|kiem tra pdf/.test(txt)) return 10;
                if (/\[video\]|video|bài giảng|bai giang/.test(txt)) return 5;
                if (/\[lý thuyết\]|\[ly thuyet\]|lý thuyết|ly thuyet|tương tác|tuong tac/.test(txt)) return 2;
                if (/\[luyện tập\]|\[luyen tap\]/.test(txt)) return 3;
                if (/\[kiểm tra\]|kiểm tra|kiem tra|đề thi|de thi/.test(txt)) return 13;
                return null;
            };
            const parseInfo = (el) => {
                const res = { done: false, score: null, correct: null, total: null };
                if (!el) return res;
                const txt = (el.innerText || '');
                const lower = txt.toLowerCase();
                const cl = el.className || '';
                const CLS_DONE = /(^|[\s_-])(done|completed|finished|đã[\s_-]?làm|da[\s_-]?lam)([\s_-]|$)/i;
                if (CLS_DONE.test(cl)) res.done = true;
                for (const attr of ['data-done', 'data-completed', 'data-finished']) {
                    const v = el.getAttribute(attr);
                    if (v === '1' || v === 'true' || v === '') res.done = true;
                }
                if (/đã làm|đã nộp|hoàn thành|xem lại|xem bài làm|xem bai lam|xem kết quả|làm lại|luyện tập lại|done|completed|finished/i.test(lower))
                    res.done = true;
                if (el.querySelector(
                        '.done, .completed, .finished, [class*="da-lam"],'
                        + '[class*="da_lam"], [data-done="1"], [data-done="true"]'))
                    res.done = true;
                let m;
                m = txt.match(/Điểm\s*[:：]?\s*(\d+(?:[.,]\d+)?)/i);
                if (m) { res.score = parseFloat(m[1].replace(',', '.')); res.done = true; }
                if (res.score === null) {
                    m = txt.match(/\b(\d+(?:[.,]\d+)?)\s*điểm\b/i);
                    if (m) { res.score = parseFloat(m[1].replace(',', '.')); res.done = true; }
                }
                m = txt.match(/Đúng\s+(\d+)\s*\/\s*(\d+)/i);
                if (m) { res.correct = parseInt(m[1]); res.total = parseInt(m[2]); res.done = true; }
                if (res.score === null) {
                    m = txt.match(/(\d+(?:[.,]\d+)?)\s*\/\s*10\b/);
                    if (m) { res.score = parseFloat(m[1].replace(',', '.')); res.done = true; }
                }
                const scoreEl = el.querySelector('[class*=score], [class*=diem], .badge-success, .badge-primary');
                if (scoreEl && res.score === null) {
                    const sm = (scoreEl.innerText || '').match(/(\d+(?:[.,]\d+)?)/);
                    if (sm) { res.score = parseFloat(sm[1].replace(',', '.')); res.done = true; }
                }
                return res;
            };
            const push = (id, title, href, type, info) => {
                if (!id || seen.has(String(id))) return;
                seen.add(String(id));
                const t = cleanTitle(title);
                out.push({
                    id: String(id),
                    title: t || ('Bài ' + id),
                    href: href || '',
                    type: type || null,
                    done: !!(info && info.done),
                    score: info ? info.score : null,
                    correct: info ? info.correct : null,
                    total: info ? info.total : null,
                });
            };

            // 1) Bảng bài tập chuẩn của OLM (Userscript Blob 29 + 30: my-gived-courseware-item & my-given-courseware-item)
            const v1Sels = [
                'table.table-striped.table-bordered tbody tr.my-gived-courseware-item',
                'table.table-striped tbody tr.my-gived-courseware-item',
                'table tbody tr.my-gived-courseware-item',
                'tr.my-gived-courseware-item',
                '.my-gived-courseware-item',
                'table.table-striped.table-bordered tbody tr.my-given-courseware-item',
                'table.table-striped tbody tr.my-given-courseware-item',
                'table.table-bordered tbody tr.my-given-courseware-item',
                'table tbody tr.my-given-courseware-item',
                'tr.my-given-courseware-item',
                '.my-given-courseware-item',
                'tr[data-cate]', 'tr[data-id-cate]', 'tr[data-id]'
            ];
            for (const sel of v1Sels) {
                document.querySelectorAll(sel).forEach(tr => {
                    if (processedEls.has(tr)) return;
                    let cid = null, href = '', title = '', typ = null;
                    const dcCate = tr.getAttribute('data-cate') || tr.getAttribute('data-id-cate');
                    const dcGeneric = tr.getAttribute('data-id');
                    if (dcCate && /^\d{5,}$/.test(dcCate.trim())) cid = dcCate.trim();

                    // Lấy tên chuẩn từ td.align-middle > div > span:first-child (Userscript Blob 29)
                    const tdMid = tr.querySelector('td.align-middle');
                    if (tdMid) {
                        const sp = tdMid.querySelector('div > span:first-child, span.font-weight-bold, strong, b, a');
                        if (sp) title = cleanTitle(sp.innerText || sp.textContent || '');
                        if (!title) title = cleanTitle(tdMid.innerText || tdMid.textContent || '');
                    }
                    if (!title) {
                        const cells = tr.querySelectorAll('td');
                        for (const c of cells) {
                            const ct = cleanTitle(c.innerText || '');
                            if (ct && !/^\d+$/.test(ct)) { title = ct; break; }
                        }
                    }

                    tr.querySelectorAll('a[href]').forEach(a => {
                        const id = extractId(a.href);
                        if (id) {
                            // Link /chu-de/...-ID luôn là id_category thật, ưu tiên hơn data-id!
                            if (!cid) cid = id;
                            if (!href) href = a.href;
                            const at = cleanTitle(a.innerText || a.getAttribute('title') || '');
                            if (at && (!title || /^Bài\s+\d+$/i.test(title))) title = at;
                        }
                    });
                    if (!cid && dcGeneric && /^\d{5,10}$/.test(dcGeneric.trim())) {
                        cid = dcGeneric.trim();
                    }

                    const dt = tr.getAttribute('data-type');
                    if (dt && /^\d+$/.test(dt)) typ = parseInt(dt);
                    if (!typ) typ = detectType(tr);
                    if (cid) {
                        processedEls.add(tr);
                        push(cid, title, href, typ, parseInfo(tr));
                    }
                });
            }

            // 2) Các thẻ bài tập giao diện mới (OLM v2 cards/items)
            const v2Sels = [
                '[class*="assignment-item"]', '[class*="courseware-item"]',
                '[class*="homework-item"]', '[class*="exercise-item"]',
                '[class*="bai-tap-item"]', '[class*="lesson-item"]',
                '[data-assignment-id]', '[data-courseware-id]',
                '[data-homework-id]', '[data-exercise-id]'
            ];
            for (const sel of v2Sels) {
                document.querySelectorAll(sel).forEach(el => {
                    if (processedEls.has(el)) return;
                    for (const p of processedEls) {
                        if (p.contains(el) || el.contains(p)) return;
                    }
                    let cid = null, href = '', title = '', typ = null;
                    const dcCate = el.getAttribute('data-cate') || el.getAttribute('data-id-cate');
                    if (dcCate && /^\d{5,}$/.test(dcCate.trim())) cid = dcCate.trim();

                    el.querySelectorAll('a[href]').forEach(a => {
                        const id = extractId(a.href);
                        if (id) {
                            // Ưu tiên tuyệt đối ID từ link /chu-de/... (tránh lấy nhầm data-courseware-id #118873987861)
                            if (!cid) cid = id;
                            href = href || a.href;
                            const t = cleanTitle(a.innerText || a.getAttribute('title') || '');
                            if (t && !title) title = t;
                        }
                    });
                    if (!cid) {
                        for (const attr of ['data-assignment-id', 'data-courseware-id',
                                            'data-homework-id', 'data-exercise-id', 'data-id']) {
                            const v = el.getAttribute(attr);
                            if (v && /^\d{5,10}$/.test(v.trim())) { cid = v.trim(); break; }
                        }
                    }
                    if (!title) {
                        const h = el.querySelector('h1,h2,h3,h4,.title,.name,td.align-middle');
                        if (h) title = cleanTitle(h.innerText || '');
                    }
                    if (!title) title = cleanTitle(el.innerText || '');
                    typ = detectType(el);
                    if (cid) {
                        processedEls.add(el);
                        push(cid, title, href, typ, parseInfo(el));
                    }
                });
            }

            // 3) Chỉ quét fallback a[href] trong vùng nội dung chính nếu chưa tìm thấy bài nào ở bảng/card
            if (out.length === 0) {
                document.querySelectorAll('a[href]').forEach(a => {
                    if (a.closest('nav, header, footer, aside, .navbar, .sidebar, .menu, .breadcrumb')) return;
                    const h = a.href || '';
                    if (!/(?:\/chu-de\/|\/bai-tap\/|\/lam-bai\/)[A-Za-z0-9\-_.]*\d{6,}/.test(h)) return;
                    if (/\/bg\/|\/lop\/|khoa-hoc|google|facebook|youtube|cloudflare/i.test(h)) return;
                    const id = extractId(h);
                    if (id) {
                        let parent = a.closest('tr, li, div[class*="item"], div[class*="card"]');
                        let t = cleanTitle(a.innerText || a.getAttribute('title') || '');
                        if (!t && parent) t = cleanTitle(parent.innerText || '');
                        push(id, t, h, detectType(parent), parseInfo(parent));
                    }
                });
            }
            return out;
        }"""
        cleanup, page = await self._page()
        all_items: List[dict] = []
        by_id: Dict[str, dict] = {}
        diag: List[str] = []
        _HREF_ID_RE = re.compile(
            r"/(?:chu-de|bai-tap|lam-bai)/[A-Za-z0-9\-_.]*?(\d{6,})(?:[/?#]|$)"
        )

        def _absorb(items, tier: str, url: str = ""):
            n0 = len(all_items)
            for it in (items or []):
                try:
                    i = str(it.get("id") or "").strip()
                except Exception:
                    continue
                # Nếu item có href chứa ID /chu-de/...-ID thì chuẩn hoá id về đúng ID trong href
                href_str = str(it.get("href") or "").strip()
                if href_str:
                    mh = _HREF_ID_RE.search(href_str)
                    if mh:
                        i = mh.group(1)
                if not i or not re.fullmatch(r"\d{5,}", i):
                    continue
                it["id"] = i
                if it.get("type") and not it.get("type_label"):
                    try:
                        it["type_label"] = EXAM_TYPE_LABEL.get(int(it["type"]), "")
                    except Exception:
                        pass
                if i in by_id:
                    ex = by_id[i]
                    new_t = str(it.get("title") or "").strip()
                    old_t = str(ex.get("title") or "").strip()
                    if new_t and not re.fullmatch(r"Bài\s+\d+", new_t, re.I):
                        if not old_t or re.fullmatch(r"Bài\s+\d+", old_t, re.I):
                            ex["title"] = new_t
                    if it.get("href") and not ex.get("href"):
                        ex["href"] = it["href"]
                    if it.get("type") and not ex.get("type"):
                        ex["type"] = it["type"]
                        ex["type_label"] = it.get("type_label") or EXAM_TYPE_LABEL.get(it["type"], "")
                    if it.get("done"):
                        ex["done"] = True
                    if it.get("score") is not None and ex.get("score") is None:
                        ex["score"] = it["score"]
                    if it.get("correct") is not None and ex.get("correct") is None:
                        ex["correct"] = it["correct"]
                    if it.get("total") is not None and ex.get("total") is None:
                        ex["total"] = it["total"]
                    continue
                by_id[i] = it
                all_items.append(it)
            added = len(all_items) - n0
            diag.append(f"{tier}:{added}" + (f"@{url}" if url else ""))
            return added

        try:
            # ══ Quét trang bài tập được giao: kết hợp DOM chuẩn + Page State + Network JSON ══
            scan_urls = [
                f"{OLM_BASE}/bai-tap-duoc-giao?private=1",
                f"{OLM_BASE}/bai-tap-duoc-giao",
                f"{OLM_BASE}/quan-ly-bai-tap",
                f"{OLM_BASE}/bai-tap-giao-vien/",
            ]
            for url in scan_urls:
                try:
                    net_found = await self._scan_via_network(page, url)
                except CloudflareBlock:
                    raise
                except Exception as e:
                    diag.append(f"net_err@{url}:{str(e)[:60]}")
                    net_found = []

                # Chờ selector bảng bài tập xuất hiện nếu có
                for sel in ('tr.my-gived-courseware-item',
                            'tr.my-given-courseware-item',
                            'tr[data-cate]',
                            'table.table-striped tbody tr',
                            '[class*="courseware-item"]',
                            '[class*="assignment-item"]'):
                    try:
                        await page.wait_for_selector(sel, timeout=2200)
                        break
                    except Exception:
                        continue

                # Ưu tiên 1: Quét DOM chuẩn từ bảng giao bài (tên bài + trạng thái chính xác nhất)
                try:
                    dom_data = await page.evaluate(JS_SCAN)
                except Exception:
                    dom_data = []
                _absorb(dom_data, "T_dom", url)

                # Ưu tiên 2: Quét HTML thô của trang hiện tại
                try:
                    raw_html = await page.content()
                    _absorb(self._parse_assignment_html(raw_html), "T_html", url)
                except Exception:
                    pass

                # Ưu tiên 3: Quét biến JS trên trang (data_cate, list_cate, ...)
                try:
                    state_data = await self._scan_via_page_state(page)
                    _absorb(state_data, "T_state", url)
                except Exception:
                    pass

                # Ưu tiên 4: Bổ sung từ JSON response mạng bắt được
                _absorb(net_found, "T_net", url)

                if all_items:
                    return self._dedup_assignments(all_items)

            # ══ Fallback cuối: GET HTML thô bằng curl_cffi (không cần browser) ══
            for u in ["/bai-tap-duoc-giao?private=1",
                      "/bai-tap-duoc-giao/",
                      "/quan-ly-bai-tap"]:
                try:
                    r = await self.sess.request("GET", u)
                    if r.status_code != 200:
                        continue
                    html = r.text or ""
                    if len(html) < 400:
                        continue
                    _absorb(self._parse_assignment_html(html), "T4_http", u)
                    if all_items:
                        return self._dedup_assignments(all_items)
                except Exception:
                    continue

            if not all_items:
                _log_warn("[SCAN_EMPTY] " + " | ".join(diag[-12:]))
            return self._dedup_assignments(all_items)
        finally:
            try: await cleanup()
            except Exception: pass

    def _parse_assignment_html(self, html):
        out = []
        by_id = {}
        P = re.compile(r'/(?:chu-de|bai-tap|lam-bai)/[A-Za-z0-9\-_.]*?(\d{6,})(?:[/?#"\'\s]|$)')
        ACTION_RE = re.compile(
            r'^(?:làm bài|làm lại|luyện tập lại|xem lại|xem bài làm|xem bai lam|'
            r'xem kết quả|xem chi tiết|tải word|xóa|nộp bài|chi tiết|bắt đầu|'
            r'tiếp tục|vào học|vào thi|thực hiện|ôn tập|chưa làm|đã làm|đã nộp|hoàn thành)$',
            re.I)
        STATUS_LINE_RE = re.compile(
            r'^(?:đã\s+(?:làm|nộp)|chưa\s+làm|hoàn\s+thành|điểm\s*[:：]|'
            r'\d+(?:[.,]\d+)?\s*điểm\b|đúng\s+\d+|\d+\s*/\s*\d+\s*câu|'
            r'giao\s+bởi|hạn\s+nộp|ngày\s+giao)',
            re.I)

        # Loại bỏ vùng điều hướng/menu để không bắt nhầm link hướng dẫn, menu
        body_html = re.sub(
            r'<(?:nav|header|footer|aside)\b[^>]*>.*?</(?:nav|header|footer|aside)>',
            '', html or '', flags=re.I | re.S)

        def _detect_type_from_text(txt: str) -> Optional[int]:
            low = (txt or "").lower()
            if "kiểm tra pdf" in low or "kiem tra pdf" in low:
                return 10
            if "[video]" in low or "bài giảng" in low:
                return 5
            if "[lý thuyết]" in low or "[ly thuyet]" in low or "lý thuyết" in low:
                return 2
            if "[luyện tập]" in low or "[luyen tap]" in low:
                return 3
            if "[kiểm tra]" in low or "kiểm tra" in low or "đề thi" in low:
                return 13
            return None

        def _extract_title_from_chunk(chunk_html: str) -> str:
            plain = re.sub(r'<[^>]+>', '\n', chunk_html or '')
            for line in plain.splitlines():
                t = re.sub(r'\s+', ' ', line).strip()
                t = re.sub(
                    r'^\s*\[(?:Luyện tập|Video|Lý thuyết|Kiểm tra|Kiểm tra PDF|Đề thi|Bài giảng)\]\s*',
                    '', t, flags=re.I).strip()
                if (t and not ACTION_RE.match(t)
                        and not STATUS_LINE_RE.match(t)
                        and not t.isdigit()):
                    return t[:150]
            return ""

        def _parse_row_status(row_plain: str):
            typ = _detect_type_from_text(row_plain)
            done = bool(re.search(
                r'Điểm\s*[:：]|\b\d+(?:[.,]\d+)?\s*điểm\b|Đúng\s+\d+\s*/\s*\d+|'
                r'\d+\s*/\s*\d+\s*câu|Đã\s+(?:làm|nộp)|Hoàn\s+thành|'
                r'Xem\s+(?:lại|bài\s+làm|bai\s+lam|kết\s+quả)|'
                r'Làm\s+lại|Luyện\s+tập\s+lại',
                row_plain, re.I))
            score = None
            ms = (re.search(r'Điểm\s*[:：]?\s*([\d]+(?:[.,]\d+)?)', row_plain, re.I)
                  or re.search(r'\b(\d+(?:[.,]\d+)?)\s*điểm\b', row_plain, re.I))
            if ms:
                try:
                    score = float(ms.group(1).replace(',', '.'))
                    done = True
                except Exception:
                    pass
            correct = None
            total = None
            mc = re.search(r'(?:Đúng\s+)?(\d+)\s*/\s*(\d+)\s*(?:câu|đúng)?', row_plain, re.I)
            if mc:
                try:
                    correct = int(mc.group(1))
                    total = int(mc.group(2))
                    done = True
                except Exception:
                    pass
            return typ, done, score, correct, total

        # 1) Bóc các dòng <tr ...>...</tr> HOẶC khối <div/li class="...courseware-item...">
        found_table_rows = False
        row_blocks = list(re.finditer(r'(<tr\b[^>]*>)(.*?)</tr>', body_html, re.I | re.S))
        if not row_blocks:
            row_blocks = list(re.finditer(
                r'(<(?:div|li)\b[^>]*class=["\'][^"\']*(?:courseware-item|assignment-item|homework-item|bai-tap-item|cate-item|task-item)[^"\']*["\'][^>]*>)(.*?)(?=<(?:div|li)\b[^>]*class=["\'][^"\']*(?:courseware-item|assignment-item|homework-item|bai-tap-item|cate-item|task-item)|\Z)',
                body_html, re.I | re.S))

        for m in row_blocks:
            tr_open = m.group(1) or ""
            row_html = m.group(2) or ""
            cid = None
            href = ""
            m_dc_cate = re.search(r'data-(?:cate|id-cate)=["\'](\d{6,})["\']', tr_open, re.I)
            m_dc_id = re.search(r'data-id=["\'](\d{6,10})["\']', tr_open, re.I)
            if m_dc_cate:
                cid = m_dc_cate.group(1)

            for m_a in re.finditer(
                    r'href=["\']([^"\']*(?:/chu-de/|/bai-tap/|/lam-bai/)[^"\']*?)["\'][^>]*>(.*?)</a>',
                    row_html, re.I | re.S):
                h = m_a.group(1)
                mid = P.search(h)
                if mid:
                    # Ưu tiên tuyệt đối ID từ link /chu-de/... (tránh lấy nhầm data-id của dòng bảng)
                    if not cid:
                        cid = mid.group(1)
                    if not href:
                        href = (OLM_BASE.rstrip("/") + h) if h.startswith("/") else h

            if not cid and m_dc_id:
                cid = m_dc_id.group(1)
            if not cid:
                continue
            found_table_rows = True

            title = ""
            # Ưu tiên tuyệt đối cột tiêu đề td.align-middle hoặc .courseware-title (Userscript Blob 29/30)
            for m_td in re.finditer(
                    r'<(?:td|div|span)\b[^>]*(?:align-middle|courseware-title|assignment-title)[^>]*>(.*?)</(?:td|div|span)>',
                    row_html, re.I | re.S):
                title = _extract_title_from_chunk(m_td.group(1))
                if title:
                    break
            if not title:
                for m_td in re.finditer(r'<td\b[^>]*>(.*?)</td>', row_html, re.I | re.S):
                    title = _extract_title_from_chunk(m_td.group(1))
                    if title:
                        break
            if not title:
                title = _extract_title_from_chunk(row_html)

            row_plain = re.sub(r'<[^>]+>', ' ', row_html)
            typ, done, score, correct, total = _parse_row_status(row_plain)

            if cid not in by_id:
                rec = {"id": cid, "title": title or f"Bài {cid}", "href": href,
                       "type": typ, "type_label": EXAM_TYPE_LABEL.get(typ, "") if typ else "",
                       "done": done, "score": score,
                       "correct": correct, "total": total}
                by_id[cid] = rec
                out.append(rec)

        # 2) Fallback cho thẻ có data-cate / data-id-cate (KHÔNG bắt data-id trần để tránh bài giả #118873987861)
        for m in re.finditer(r'<(?:tr|div|li)[^>]*data-(?:cate|id-cate)=["\'](\d{6,})["\']',
                             body_html, re.I):
            cid = m.group(1)
            if cid in by_id:
                continue
            rec = {"id": cid, "title": f"Bài {cid}", "href": "",
                   "type": None, "type_label": "", "done": False, "score": None,
                   "correct": None, "total": None}
            by_id[cid] = rec
            out.append(rec)

        # 3) Bóc các thẻ <a href="/chu-de/..."> để bổ sung href/title hoặc fallback khi trang không dùng <tr>
        for m in re.finditer(
                r'href=["\']([^"\']*(?:/chu-de/|/bai-tap/|/lam-bai/)[^"\']*?)["\']'
                r'[^>]*>([^<]*)', body_html, re.I):
            href = m.group(1)
            raw_link_txt = re.sub(r'\s+', ' ', (m.group(2) or "").strip())
            title = re.sub(
                r'^\s*\[(?:Luyện tập|Video|Lý thuyết|Kiểm tra|Kiểm tra PDF|Đề thi|Bài giảng)\]\s*',
                '', raw_link_txt, flags=re.I).strip()[:150]
            if ACTION_RE.match(title) or STATUS_LINE_RE.match(title):
                title = ""
            mid = P.search(href)
            if not mid:
                continue
            cid = mid.group(1)
            if href.startswith("/"):
                href = OLM_BASE.rstrip("/") + href
            if cid in by_id:
                ex = by_id[cid]
                if not ex.get("href"):
                    ex["href"] = href
                if title and re.fullmatch(r"Bài\s+\d+", ex.get("title") or "", re.I):
                    ex["title"] = title
                continue
            if found_table_rows:
                continue
            # Lấy ngữ cảnh quanh thẻ <a> để nhận diện tên bài, [Loại bài], điểm và trạng thái
            ctx_html = body_html[max(0, m.start() - 450):min(len(body_html), m.end() + 250)]
            if not title:
                title = _extract_title_from_chunk(ctx_html)
            ctx_plain = re.sub(r'<[^>]+>', ' ', ctx_html)
            typ, done, score, correct, total = _parse_row_status(ctx_plain)
            rec = {"id": cid, "title": title or f"Bài {cid}",
                   "href": href, "type": typ,
                   "type_label": EXAM_TYPE_LABEL.get(typ, "") if typ else "",
                   "done": done, "score": score, "correct": correct, "total": total}
            by_id[cid] = rec
            out.append(rec)
        return self._dedup_assignments(out)

    # === v19.2 - XEM LẠI BÀI ĐÃ NỘP ===
    async def _fetch_ans_file(self, page, id_cate: str
                              ) -> Tuple[Optional[str], Any]:
        """Lấy + decode `/course/teacher-categories/{id}/get-crt-ans-file`.

        Đây chính là đường mà userscript dùng để hiện đáp án khi bài ĐÃ NỘP:
            const r = await window['_apiGet']("/course/teacher-categories/"
                      + id + "/get-crt-ans-file?t=" + Date.now());
            if (c['OlmEncode']) out = c['OlmEncode']['decode'](r);
            else if (r.startsWith('"')) out = atob(JSON.parse(r));
            b.innerText = typeof out === 'object' ? JSON.stringify(out) : out;

        OLM tự sinh và TỰ GIẢI MÃ file này (`OlmEncode` là hàm của OLM, không
        nằm trong userscript), nên ở đây gọi thẳng `CATE_UI.OlmEncode.decode`
        trong ngữ cảnh trang. Trả về (raw, decoded).
        """
        raw = None
        try:
            raw = await page.evaluate("""async (id) => {
                const urls = [
                    '/course/teacher-categories/' + id + '/get-crt-ans-file?t=' + Date.now(),
                    '/course/teacher-categories/' + id + '/get-crt-ans-file'
                ];
                for (const u of urls) {
                    try {
                        // 1) dùng _apiGet của OLM nếu có (y userscript)
                        if (typeof window['_apiGet'] === 'function') {
                            const r = await window['_apiGet'](u);
                            if (r !== null && r !== undefined) {
                                return (typeof r === 'string') ? r : JSON.stringify(r);
                            }
                        }
                    } catch(e) {}
                    try {
                        const r = await fetch(u, {
                            credentials: 'include',
                            headers: {'X-Requested-With': 'XMLHttpRequest',
                                      'Accept': 'application/json, text/plain, */*'}
                        });
                        if (!r.ok) continue;
                        const t = await r.text();
                        if (t && t.length) return t;
                    } catch(e) {}
                }
                return null;
            }""", id_cate)
        except Exception:
            raw = None

        if not raw:
            # fallback qua curl_cffi (session cookie)
            try:
                r = await self.sess.request(
                    "GET",
                    f"/course/teacher-categories/{id_cate}"
                    f"/get-crt-ans-file?t={int(time.time()*1000)}",
                    headers={"X-Requested-With": "XMLHttpRequest",
                             "Accept": "application/json, text/plain, */*"})
                if r.status_code == 200 and r.text:
                    raw = r.text
            except Exception:
                raw = None

        if not raw:
            return None, None

        # ── decode bằng chính OlmEncode của trang (nếu có) ──
        decoded = None
        try:
            dec = await page.evaluate("""(raw) => {
                try {
                    const c = window['CATE_UI'];
                    if (c && c['OlmEncode']
                        && typeof c['OlmEncode']['decode'] === 'function') {
                        const out = c['OlmEncode']['decode'](raw);
                        return (typeof out === 'string') ? out : JSON.stringify(out);
                    }
                } catch(e) {}
                return null;
            }""", raw)
            if dec:
                decoded = dec
        except Exception:
            decoded = None

        if decoded is None:
            # heuristic: '"<base64>"' -> atob
            s = raw.strip()
            if s.startswith('"'):
                try:
                    decoded = base64.b64decode(json.loads(s)).decode(
                        "utf-8", errors="replace")
                except Exception:
                    decoded = raw
            else:
                decoded = raw
        return raw, decoded

    async def _extract_data_cate(self, page) -> dict:
        dc = None
        try:
            dc = await page.evaluate("() => window.data_cate || null")
        except Exception:
            pass
        if not dc:
            try:
                dc = await page.evaluate("""() => {
                    const c = window.CATE_UI;
                    if (c && c.getData) return {
                        id_category: c.getData('id_category'),
                        id_grade: c.getData('id_grade'),
                        id_courseware: c.getData('id_courseware'),
                        id_group: c.getData('id_group'),
                        id_school: c.getData('id_school'),
                        type: c.getData('type'),
                        type_vip: c.getData('type_vip'),
                        id_user: c.getData('id_user'),
                        count_problems: c.getData('count_problems'),
                    };
                    return null;
                }""")
            except Exception:
                pass
        if not dc:
            try:
                scripts = await page.evaluate(
                    "() => [...document.scripts].map(s=>s.textContent).join('\\n')")
                m = re.search(r'var\s+data_cate\s*=\s*(\{.*?\});', scripts, re.S)
                if m:
                    dc = try_json(m.group(1))
            except Exception:
                pass
        dc = dc or {}

        try:
            flex = await page.evaluate("""() => {
                const heads = document.querySelectorAll(
                    'h1,h2,h3,.title,.page-title,[class*=title]');
                for (const h of heads) {
                    if (/linh\\s*ho\\s*ạt/i.test(h.innerText || '')) return true;
                }
                const tabs = document.querySelectorAll(
                    '[class*=tab], .nav a, [role=tab]');
                for (const t of tabs) {
                    if (/linh\\s*ho\\s*ạt/i.test(t.innerText || '')) return true;
                }
                if (document.querySelector('[class*=pdf], [data-pdf="1"]')) return 'pdf';
                return false;
            }""")
            if flex == "pdf":
                if not dc.get("type") or int(dc.get("type") or 0) in (2, 3):
                    dc["type"] = 10
                    dc["_is_pdf"] = True
            elif flex is True:
                if not dc.get("type") or int(dc.get("type") or 0) in (2, 3):
                    dc["type"] = 5
                dc["_is_flexible"] = True
        except Exception:
            pass

        return dc

    async def _detect_exam_type(self, page) -> int:
        try:
            t = await page.evaluate("""() => {
                let t = 0;
                try {
                    const c = window.CATE_UI, d = window.data_cate;
                    if (c && c.getData) t = parseInt(c.getData('type')) || 0;
                    else if (d && d.type) t = parseInt(d.type) || 0;
                    const cat = (c && c.getData && c.getData('category')) || null;
                    const nm = (cat && cat.name) || (d && (d.name || d.title)) || '';
                    if (/linh\\s*ho\\s*ạt/i.test(nm) && t === 2) return 5;
                    const flex = (cat && cat.is_flexible) || (d && d.is_flexible);
                    if (flex && (t === 2 || t === 0)) return 5;
                    if (document.querySelector('[class*=pdf], [data-pdf="1"]')) {
                        if (t === 3 || t === 0) return 10;
                    }
                } catch(e) {}
                return t || 0;
            }""")
            return int(t or 0)
        except Exception:
            return 0

    async def fetch_questions(self, id_cate: str, force: bool = False
                              ) -> Tuple[List[dict], dict]:
        # v19.1: cache TTL + khoá id_cate (kèm biến thể id_cate#q_count khi đã biết)
        cached = self._qcache_get(id_cate, force=force)
        if cached:
            _log_hook("cache_hit", id_cate=str(id_cate),
                      age=int(time.time() - cached.get("ts", 0)))
            return cached.get("questions", []), cached.get("data_cate", {})

        cleanup, page = await self._page()
        captured = {"body": None, "ans_body": None}
        data_cate: dict = {}
        try:
            async def on_resp(resp):
                try:
                    u = resp.url.lower()
                    if "get-question-of-ids" in u and not captured["body"]:
                        captured["body"] = await resp.text()
                    elif "get-crt-ans" in u and not captured["ans_body"]:
                        captured["ans_body"] = await resp.text()
                except Exception:
                    pass
            page.on("response", lambda r: asyncio.create_task(on_resp(r)))

            url_v1 = _url_v1(f"{OLM_BASE}/chu-de/{id_cate}")
            await self._goto(page, url_v1, timeout=60000, wait_cf=45)
            await asyncio.sleep(2)
            for _ in range(35):
                if captured["body"] or captured["ans_body"]:
                    break
                await asyncio.sleep(0.35)

            data_cate = await self._extract_data_cate(page)
            exam_type = await self._detect_exam_type(page)
            if not data_cate:
                raise V1Required("V1_REQUIRED: Không tìm thấy window.data_cate.")
            if exam_type:
                data_cate["type"] = exam_type

            if not captured["body"] and captured["ans_body"]:
                captured["body"] = captured["ans_body"]

            if not captured["body"]:
                try:
                    txt = await page.evaluate("""async (id) => {
                        try {
                            const r = await fetch(
                                '/course/teacher-categories/' + id
                                + '/get-crt-ans-file?t=' + Date.now(),
                                {
                                    credentials: 'include',
                                    headers: {
                                        'X-Requested-With': 'XMLHttpRequest',
                                        'Accept': 'application/json, text/plain, */*'
                                    }
                                });
                            if (!r.ok) return null;
                            const text = await r.text();
                            try {
                                const c = window.CATE_UI;
                                if (c && c.OlmEncode
                                    && typeof c.OlmEncode.decode === 'function') {
                                    let out = c.OlmEncode.decode(text);
                                    if (typeof out === 'object' && out !== null)
                                        return JSON.stringify(out);
                                    if (typeof out === 'string') return out;
                                }
                            } catch(e) {}
                            if (typeof text === 'string'
                                && text.length > 2 && text[0] === '"') {
                                try {
                                    return atob(JSON.parse(text));
                                } catch(e) {}
                            }
                            return text;
                        } catch(e) { return null; }
                    }""", id_cate)
                    if txt:
                        captured["body"] = txt
                except Exception:
                    pass

            if not captured["body"]:
                try:
                    r = await self.sess.request(
                        "GET",
                        f"/course/teacher-categories/{id_cate}"
                        f"/get-crt-ans-file?t={int(time.time()*1000)}",
                        headers={"X-Requested-With": "XMLHttpRequest"})
                    if r.status_code == 200 and r.text:
                        captured["body"] = r.text
                except Exception:
                    pass

            qs: List[dict] = []
            if captured["body"]:
                body = captured["body"]
                data = try_json(body)
                if not data:
                    data = try_json(b64x_decode(body))
                if not data and isinstance(body, str):
                    try:
                        inner = try_json(body.strip('"'))
                        if inner is not None:
                            data = inner
                    except Exception:
                        pass

                arr = None
                if isinstance(data, list):
                    arr = data
                elif isinstance(data, dict):
                    arr = (data.get("data") or data.get("result")
                           or data.get("questions") or data.get("q_list")
                           or data.get("list"))
                    if not isinstance(arr, list) and data.get("json_content") and data.get("id"):
                        arr = [data]

                if not isinstance(arr, list):
                    try:
                        m = re.search(r'\[.*\]', str(body), re.S)
                        if m:
                            inner = try_json(m.group(0))
                            if isinstance(inner, list):
                                arr = inner
                    except Exception:
                        pass

                if isinstance(arr, list):
                    qs = extract_questions(arr)
                if not qs and data is not None:
                    pdf_qs = _extract_pdf_ans_questions(data)
                    if pdf_qs:
                        qs = pdf_qs
                        data_cate["_is_pdf"] = True

            # Luôn đọc window.data_cate.record (kể cả khi đã lấy được qs) để biết chính xác bài đã làm hay chưa!
            try:
                rec_json = await page.evaluate("""() => {
                    const d = window.data_cate;
                    const c = window.CATE_UI;
                    if (!d && !c) return null;
                    const r = (d && d.record) || (c && c.getData && c.getData('record')) || null;
                    const out = {
                        title: (d && (d.title || d.name || d.title_cate)) || '',
                        count_problems: (d && d.count_problems) || 0,
                        record: r ? {
                            correct: r.correct,
                            wrong: r.wrong,
                            totalq: r.totalq,
                            score: (r.score_10 !== undefined && r.score_10 !== null && r.score_10 !== '') ? r.score_10 : (r.score !== undefined ? r.score : null),
                            ended: r.ended,
                            times: r.times,
                            time_spent: r.time_spent,
                            messageStatic: r.messageStatic || '',
                            list_quiz: r.list_quiz || [],
                            data_log: r.data_log || [],
                            user_ans: r.user_ans || '[]',
                        } : null,
                        list_id_quiz: (d && d.list_id_quiz) || (r && r.list_quiz) || [],
                    };
                    return JSON.stringify(out);
                }""")
                if rec_json:
                    rec = try_json(rec_json)
                    if rec:
                        if rec.get("title"):
                            data_cate["_title"] = rec["title"]
                        if rec.get("count_problems"):
                            data_cate["_count_problems"] = rec["count_problems"]
                        if rec.get("record"):
                            dc_rec = rec["record"]
                            raw_sc = dc_rec.get("score")
                            raw_cor = dc_rec.get("correct")
                            sc_val = None
                            cor_val = None
                            try:
                                if raw_sc is not None and str(raw_sc).strip() != "":
                                    sc_val = float(raw_sc)
                            except Exception:
                                sc_val = None
                            try:
                                if raw_cor is not None and str(raw_cor).strip() != "":
                                    cor_val = int(float(raw_cor))
                            except Exception:
                                cor_val = None
                            # OLM khởi tạo record rỗng {score: 0, correct: 0, ended: 0, times: 0, messageStatic: ""}
                            # cho bài CHƯA LÀM -> chỉ coi là đã làm khi có ended=1, times>0, messageStatic, hoặc điểm > 0
                            has_done_signal = bool(
                                (dc_rec.get("messageStatic") and str(dc_rec.get("messageStatic")).strip())
                                or str(dc_rec.get("ended") or "").strip().lower() in ("1", "true")
                                or (dc_rec.get("times") and int(dc_rec.get("times") or 0) > 0)
                                or (sc_val is not None and sc_val > 0)
                                or (cor_val is not None and cor_val > 0)
                            )
                            data_cate["_record"] = {
                                "done": has_done_signal,
                                "correct": cor_val if has_done_signal else None,
                                "wrong": dc_rec.get("wrong") if has_done_signal else None,
                                "totalq": dc_rec.get("totalq"),
                                "score": sc_val if has_done_signal else None,
                                "ended": dc_rec.get("ended"),
                                "times": dc_rec.get("times"),
                                "messageStatic": dc_rec.get("messageStatic") or ("done" if has_done_signal else ""),
                            }
                        list_id = rec.get("list_id_quiz") or []
                        if list_id and not qs:
                            try:
                                ids_param = "&".join(
                                    [f"ids[]={i}" for i in list_id])
                                qbody = await page.evaluate("""async (ids) => {
                                    try {
                                        const r = await fetch(
                                            '/course/teacher-questions/get-question-of-ids?' + ids,
                                            {credentials: 'include',
                                             headers: {'X-Requested-With': 'XMLHttpRequest'}});
                                        if (r.ok) return await r.text();
                                    } catch(e) {}
                                    return null;
                                }""", ids_param)
                                if qbody:
                                    qdata = try_json(qbody)
                                    qarr = None
                                    if isinstance(qdata, list):
                                        qarr = qdata
                                    elif isinstance(qdata, dict):
                                        qarr = (qdata.get("data") or qdata.get("result")
                                                or qdata.get("questions"))
                                    if isinstance(qarr, list):
                                        qs = extract_questions(qarr)
                            except Exception:
                                pass
            except Exception:
                pass

            # ══ v19.2: CHẾ ĐỘ XEM LẠI BÀI (đã nộp) & ĐỀ PDF ══
            if not qs:
                try:
                    ans_raw, ans_decoded = await self._fetch_ans_file(page, id_cate)
                    if ans_decoded is not None:
                        aq, ahtml = _parse_ans_file(ans_decoded)
                        if aq:
                            qs = aq
                            data_cate["_from_ans_file"] = True
                            _log_telemetry("ANS_FILE_OK", id_cate=str(id_cate),
                                           mode="structured", n=len(aq))
                        elif ahtml:
                            data_cate["_ans_html"] = ahtml
                            data_cate["_from_ans_file"] = True
                            _log_telemetry("ANS_FILE_OK", id_cate=str(id_cate),
                                           mode="html", chars=len(ahtml))
                        elif ans_raw:
                            data_cate["_ans_text"] = str(ans_decoded)[:20000]
                            data_cate["_from_ans_file"] = True
                            _log_telemetry("ANS_FILE_OK", id_cate=str(id_cate),
                                           mode="text")
                except Exception as e:
                    _log_telemetry("ANS_FILE_FAIL", id_cate=str(id_cate), error=str(e)[:200])

            # ══ Fallback cho bài Lý thuyết / Tương tác (Userscript Blob 88: OLM_SNIF_CONTENTS) ══
            if not qs and not data_cate.get("_ans_html"):
                try:
                    snif_html = await page.evaluate("""() => {
                        if (Array.isArray(window.OLM_SNIF_CONTENTS) && window.OLM_SNIF_CONTENTS.length > 0) {
                            return window.OLM_SNIF_CONTENTS.join('<hr>');
                        }
                        const el = document.querySelector('#olm-content, .courseware-content, .lesson-content, .theory-content');
                        if (el && (el.innerText || '').trim().length > 40) return el.innerHTML;
                        return null;
                    }""")
                    if snif_html:
                        data_cate["_ans_html"] = snif_html
                except Exception:
                    pass

            dc = data_cate or {}

            title = (dc.get("title") or dc.get("name") or dc.get("title_cate") or "")
            if not title:
                try:
                    title = await page.evaluate("""() => {
                        const badRe = /^(báo cáo học liệu|báo lỗi|thông báo|đăng nhập|xác nhận|hướng dẫn|bình luận)$/i;
                        const nodes = document.querySelectorAll(
                            '.courseware-title, .cate-title, .page-title, h1, h2.title, .title');
                        for (const el of nodes) {
                            if (el.closest && el.closest('.modal, [role="dialog"]')) continue;
                            const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                            if (t && t.length > 3 && !badRe.test(t)) return t;
                        }
                        return (document.title || '').replace(/\\s*[-|].*$/, '').trim();
                    }""")
                except Exception:
                    pass
            if title and not re.match(r'^(báo cáo học liệu|báo lỗi|thông báo)$', title.strip(), re.I):
                dc["_title"] = title

            exam_type = int(dc.get("type") or 3)
            if not dc.get("type") and any(q.get("q_type") in (21, 22) for q in (qs or [])):
                exam_type = 21
            if dc.get("_record"):
                rec = dc["_record"]
                if rec.get("totalq"):
                    dc["_count_problems"] = rec.get("totalq")
                if rec.get("done") and rec.get("correct") is not None:
                    dc["_correct_done"] = rec.get("correct")
            dc["_exam_type"] = exam_type
            if dc.get("_is_flexible"):
                dc["_type_label"] = "Luyện tập linh hoạt"
            else:
                dc["_type_label"] = EXAM_TYPE_LABEL.get(exam_type, f"Loại {exam_type}")

            # v19.1: cache khoá theo id_cate + q_count + log phân bố q_type
            self._qcache_put(id_cate, len(qs), qs, dc)
            _log_qtype_distribution(id_cate, qs)
            return qs, dc
        finally:
            try: await cleanup()
            except Exception: pass

    async def _submit_via_dom_fill(self, page, id_cate, questions,
                                   click_submit: bool = True,
                                   wait_seconds: float = 3.0):
        """LÀM BÀI THẬT SỰ trong DOM rồi bấm nút Nộp của chính OLM.

        Trả về dict: fill / choices / native / clicked / result / path.
        """
        report: Dict[str, Any] = {
            "ok": False, "path": None, "fill": None, "choices": None,
            "native": None, "clicked": None, "result": None, "errors": [],
        }
        try:
            # ── 0. gọi lại autofill của userscript (nếu có) ──
            try:
                rerun = await page.evaluate("""() => {
                    if (typeof window.__olm_autofill_run === 'function') {
                        try { window.__olm_autofill_run(); return { ok: true }; }
                        catch(e) { return { ok: false, error: String(e) }; }
                    }
                    return { ok: false, error: 'no_autofill_fn' };
                }""")
            except Exception:
                rerun = {"ok": False}
            report["rerun"] = rerun

            await asyncio.sleep(1.0)

            # ── 1. bảng câu hỏi gửi xuống DOM (mở rộng cả sub_questions cho q_type 21/22) ──
            js_questions: List[dict] = []
            for q in questions:
                if q.get("q_type") in (21, 22):
                    sub_qs = _ensure_group_sub_questions(q)
                    for sq in sub_qs:
                        s_label = _build_choose_label(sq)
                        s_text = (sq.get("question_text") or sq.get("text") or "")[:120]
                        js_questions.append({
                            "id": str(sq.get("id") or q["id"]),
                            "text": s_text,
                            "choose_label": s_label,
                            "type": sq.get("q_type"),
                            "correctAnswers": sq.get("correctAnswers"),
                            "answerIndices": sq.get("answerIndices"),
                            "correctLabel": sq.get("correctLabel"),
                            "correctIndex": sq.get("correctIndex"),
                            "pool": list(sq.get("answer_texts") or []),
                        })
                label = _build_choose_label(q)
                qtext = (q.get("question_text") or q.get("text") or "")[:120]
                pool = list(q.get("answer_texts") or [])
                js_questions.append({
                    "id": str(q["id"]),
                    "text": qtext,
                    "choose_label": label,
                    "type": q.get("q_type"),
                    "correctAnswers": q.get("correctAnswers"),
                    "answerIndices": q.get("answerIndices"),
                    "correctLabel": q.get("correctLabel"),
                    "correctIndex": q.get("correctIndex"),
                    "pool": pool,
                })

            # ── 2. ĐIỀN THẬT vào DOM ──
            fill: Dict[str, Any] = {}
            try:
                fill = await page.evaluate(
                    "(qs) => (typeof window.__olm_fill_real === 'function')"
                    " ? window.__olm_fill_real(qs)"
                    " : { errors: ['no_fill_real'], matched: 0 }",
                    js_questions)
            except Exception as e:
                report["errors"].append(f"fill_real: {e}")
                fill = {"errors": [str(e)], "matched": 0}

            # bổ sung pass cũ (phòng khi trang lạ) — không ghi đè kết quả
            try:
                legacy = await page.evaluate(
                    "(qs) => (typeof window.__olm_fill_special === 'function')"
                    " ? window.__olm_fill_special(qs) : {}",
                    js_questions)
                if isinstance(legacy, dict):
                    fill["legacy"] = legacy
            except Exception:
                pass

            report["fill"] = fill
            _log_dom_fill(fill)
            await asyncio.sleep(0.5)

            # ── 3. ĐỌC LẠI DOM để kiểm chứng việc tick có thật ──
            try:
                report["choices"] = await page.evaluate(
                    "() => (typeof window.__olm_read_choices === 'function')"
                    " ? window.__olm_read_choices() : null")
            except Exception as e:
                report["errors"].append(f"read_choices: {e}")

            if not click_submit:
                report["ok"] = True
                report["path"] = "fill_only"
                return report

            # ── 4. BẤM NÚT NỘP CỦA OLM ──
            clicked = None
            try:
                clicked = await page.evaluate("""() => {
                    if (typeof window.__olm_native_submit === 'function') {
                        // chỉ báo là CÓ hàm; không await ở đây để còn bấm nút
                    }
                    if (typeof window.__olm_find_submit !== 'function') return null;
                    const f = window.__olm_find_submit();
                    if (!f) return { found: false };
                    const el = f.el;
                    try { el.scrollIntoView({ block: 'center' }); } catch(e) {}
                    const r = el.getBoundingClientRect();
                    const cx = Math.round(r.left + r.width / 2);
                    const cy = Math.round(r.top + r.height / 2);
                    const mk = (t, extra) => {
                        let e;
                        try { e = new MouseEvent(t, Object.assign({
                            bubbles: true, cancelable: true, composed: true,
                            view: window, clientX: cx, clientY: cy, button: 0
                        }, extra || {})); }
                        catch (err) { e = new Event(t, { bubbles: true }); }
                        return e;
                    };
                    try {
                        el.dispatchEvent(new PointerEvent('pointerdown', {
                            bubbles: true, clientX: cx, clientY: cy,
                            pointerId: 1, pointerType: 'mouse', isPrimary: true }));
                    } catch(e) {}
                    el.dispatchEvent(mk('mousedown'));
                    try { el.focus(); } catch(e) {}
                    try {
                        el.dispatchEvent(new PointerEvent('pointerup', {
                            bubbles: true, clientX: cx, clientY: cy,
                            pointerId: 1, pointerType: 'mouse', isPrimary: true }));
                    } catch(e) {}
                    el.dispatchEvent(mk('mouseup'));
                    let viaClick = false;
                    try { el.click(); viaClick = true; } catch(e) {}
                    if (!viaClick) el.dispatchEvent(mk('click'));
                    return { found: true, text: f.text, score: f.score,
                             disabled: !!el.disabled };
                }""")
            except Exception as e:
                report["errors"].append(f"click_submit: {e}")

            # Nếu OLM bật modal xác nhận ("Bạn có chắc chắn muốn nộp bài?"), tự động bấm Đồng ý / Nộp
            if isinstance(clicked, dict) and clicked.get("found"):
                await asyncio.sleep(0.4)
                try:
                    await page.evaluate("""() => {
                        const confirmSelectors = [
                            '.swal2-confirm', '.bootbox-accept',
                            '.modal.show .btn-primary', '.modal.in .btn-primary',
                            '.modal.show .btn-success', '.modal.in .btn-success',
                            '.ui-dialog-buttonset button:first-child'
                        ];
                        for (const sel of confirmSelectors) {
                            const btn = document.querySelector(sel);
                            if (btn && !btn.disabled) {
                                try { btn.click(); return true; } catch(e) {}
                            }
                        }
                        const btns = document.querySelectorAll('.modal button, [role="dialog"] button, .swal2-actions button');
                        for (const b of btns) {
                            const t = (b.innerText || '').trim().toLowerCase();
                            if (/^(đồng ý|xác nhận|nộp bài|nộp|ok|yes|có)$/i.test(t)) {
                                try { b.click(); return true; } catch(e) {}
                            }
                        }
                        return false;
                    }""")
                except Exception:
                    pass

            # nếu KHÔNG tìm thấy nút -> gọi hàm nộp native
            if not (isinstance(clicked, dict) and clicked.get("found")):
                try:
                    report["native"] = await page.evaluate("""async () => {
                        if (typeof window.__olm_native_submit !== 'function')
                            return { ok: false, via: null };
                        return await window.__olm_native_submit();
                    }""")
                except Exception as e:
                    report["errors"].append(f"native_submit: {e}")
            report["clicked"] = clicked

            # ── 5. CHỜ OLM CHẤM & RENDER KẾT QUẢ ──
            result = None
            deadline = time.time() + max(0.8, float(wait_seconds))
            while time.time() < deadline:
                await asyncio.sleep(0.8)
                try:
                    result = await page.evaluate(
                        "() => (typeof window.__olm_read_result === 'function')"
                        " ? window.__olm_read_result() : null")
                except Exception:
                    result = None
                if isinstance(result, dict) and result.get("submitted"):
                    break
            report["result"] = result

            submitted = bool(isinstance(result, dict) and result.get("submitted"))
            clicked_ok = bool(isinstance(clicked, dict) and clicked.get("found"))
            native_ok = bool(isinstance(report.get("native"), dict)
                             and report["native"].get("ok"))

            if submitted:
                report["ok"] = True
                report["path"] = ("dom_click" if clicked_ok else "dom_native"
                                  if native_ok else "dom")
            elif clicked_ok or native_ok:
                report["ok"] = True
                report["path"] = ("dom_click_pending" if clicked_ok
                                  else "dom_native_pending")
            else:
                report["ok"] = False
                report["path"] = "no_submit_control"
                report["errors"].append(
                    "Không tìm thấy nút Nộp của OLM và không có hàm nộp native.")
            return report
        except Exception as e:
            report["errors"].append(f"dom_fill: {e}")
            _log_dom_fill({"errors": [str(e)]})
            return report

    async def submit(self, id_cate: str, mode: str, custom: float,
                     time_spent: int, force_redo: bool = False) -> dict:
        if not self.sess.has_auth():
            return {"ok": False, "error": "Chưa đăng nhập"}
        if not self.sess.user_id:
            return {"ok": False, "error": "user_id rỗng"}

        questions, dc = await self.fetch_questions(id_cate, force=force_redo)
        dc = dc or {}
        exam_type = int(dc.get("type") or dc.get("_exam_type") or 3)
        if not dc.get("type") and any(q.get("q_type") in (21, 22) for q in (questions or [])):
            exam_type = 21
        is_video = (exam_type in (1, 5) and not dc.get("_is_flexible"))
        is_pdf = (exam_type in (4, 10) or bool(dc.get("_is_pdf"))
                  or any(q.get("_pdf_part") for q in (questions or [])))

        if not questions and is_video:
            # Bài giảng Video không có câu hỏi trắc nghiệm riêng -> tạo log hoàn thành video (Userscript Blob 55 + 60)
            vid_n = int(dc.get("_count_problems") or dc.get("count_problems") or 2)
            vid_n = max(1, vid_n)
            questions = [{
                "id": f"vid_{i + 1}",
                "text": f"Mốc tương tác video #{i + 1}",
                "question_text": f"Mốc tương tác video #{i + 1}",
                "q_type": 0,
                "correctIndex": 0,
                "correctLabel": "A",
                "correctAnswers": ["Hoàn thành"],
                "options": [{"label": "A", "text": "Hoàn thành", "correct": True}],
                "_is_video_step": True,
            } for i in range(vid_n)]

        if not questions:
            return {"ok": False, "error": "Không lấy được câu hỏi"}

        # Với bài kiểm tra nhóm (q_type 21/22), tổng số câu thực tế là tổng số câu con (total_problem_count)
        N = total_problem_count(questions) or len(questions)
        if N <= 0:
            return {"ok": False, "error": "Bài không có câu hỏi nào"}
        try:
            custom = float(custom)
        except Exception:
            custom = 9.0

        # ── v19.2 FIX điểm ──
        # 1) max_score LUÔN = 10 (thang điểm OLM), không phụ thuộc số câu.
        # 2) mode full  -> đúng hết -> 10.0
        #    mode minus1-> sai 1 câu, trừ theo điểm/câu (không âm)
        #    mode custom-> user nhập tay (0..10)
        # 3) total = số điểm GỬI LÊN, kẹp trong [0, max_score] -> hết 11/10.
        max_score = 10.0
        if mode == "full":
            n_correct = N
        elif mode == "minus1":
            n_correct = max(0, N - 1)
        else:
            c = max(0.0, min(max_score, custom))
            n_correct = max(0, min(N, round(c / max_score * N)))
        n_wrong = N - n_correct
        per_q_max = round(max_score / N, 6)
        # điểm = đúng * điểm/câu, kẹp trần max_score, làm tròn 2 chữ số
        raw_score = n_correct * per_q_max
        score = round(min(max_score, max(0.0, raw_score)), 2)
        total = score
        if total > max_score:
            _log_telemetry("SCORE_CLAMPED", id_cate=str(id_cate),
                           raw=raw_score, clamped=score)
        step_ms = max(1500, (int(time_spent) * 1000) // max(1, N))

        # ── v19.1: log q_type distribution mỗi bài ──
        _log_qtype_distribution(id_cate, questions)
        _log_telemetry("SCORE_CALC", id_cate=str(id_cate), mode=str(mode),
                       n=N, correct=n_correct, wrong=n_wrong,
                       per_q=per_q_max, score=score, max_score=max_score)

        csrf = await self.sess.refresh_csrf(id_cate)
        if not csrf:
            return {"ok": False, "error": "Không refresh được CSRF"}

        now = int(time.time())
        is_pdf_questions = bool(is_pdf and any(q.get("_pdf_part") for q in questions))
        has_group_questions = any(q.get("q_type") in (21, 22) for q in questions)
        is_exam_quiz = (exam_type in (13, 14, 18, 21, 22) or has_group_questions)

        if is_video and all(q.get("_is_video_step") for q in questions):
            data_log = _build_video_data_log(N, int(time_spent))
            choose_log: Any = []
            ans_str = ""
            user_ans_str = "[]"
        elif is_pdf_questions:
            # Userscript Blob 77 + 82: _build_pdf_submit_data trả 3-tuple (orig_ans_json, user_ans_json, clog_str)
            ans_str, user_ans_str, clog_str = _build_pdf_submit_data(questions, n_correct)
            data_log = []
            choose_log = clog_str
        else:
            data_log = _build_data_log(questions, n_correct, per_q_max)
            choose_log = _build_choose_log(questions, step_ms)
            ans_str, user_ans_str = _build_answer_strings(questions, n_correct)
            if is_exam_quiz:
                # Userscript Blob 68/69: chuẩn hóa data_log cho Kiểm tra / Đề thông minh / Câu hỏi nhóm (21/22)
                data_log = _build_userscript_exam_data_log(questions, n_correct)
            elif exam_type == 3:
                # Userscript Blob 61: chuẩn hóa data_log cho Luyện tập (type 3)
                data_log = _build_userscript_practice_data_log(questions, n_correct, int(time_spent))

        # Userscript Blob 59: Với bài kiểm tra loại 21/22 (Teacherquiz), OLM nhận type_exam = "18"
        type_exam = "18" if exam_type in (18, 21, 22) else str(exam_type)

        payload: Dict[str, Any] = {
            "id_user": str(self.sess.user_id or dc.get("id_user") or ""),
            "id_cate": str(dc.get("id_category") or id_cate),
            "id_grade": str(dc.get("id_grade") or "7"),
            "id_courseware": str(dc.get("id_courseware") or ""),
            "id_group": str(dc.get("id_group") or "0"),
            "id_school": str(dc.get("id_school") or "0"),
            "type_vip": str(dc.get("type_vip") or "1"),
            "time_spent": str(int(time_spent)),
            "total_time": str(int(time_spent)),
            "current_time": str(now),
            # ── v19.2 FIX "11/10 điểm" ──
            "tl_score": "0",
            "tn_score": str(total),
            "score": str(total),
            "max_score": str(max_score),
            "correct": str(n_correct),
            "wrong": str(n_wrong),
            "missed": "0",
            "count_problems": str(N),
            "totalq": str(N),
            "ended": "1",
            "date_end": str(now) if is_pdf_questions else str(now + 1000),
            "type_exam": type_exam,
            "save_star": "0" if exam_type == 3 else "1",
            "data_log": json.dumps(data_log, separators=(",", ":"),
                                   ensure_ascii=False),
            "time_init": str(now - int(time_spent)),
            "time_end": str(now),
            "name_user": "0",
            "times": "1",
            "time_stored": str(now) if is_pdf_questions else "0",
            "choose_log": (choose_log if isinstance(choose_log, str)
                           else json.dumps(choose_log, separators=(",", ":"),
                                           ensure_ascii=False)),
            "ans": ans_str,
            "user_ans": user_ans_str,
            "count_redo": "1",
            "skill_list": "[]",
            "list_skill": "[]",
            "passed": "0",
            "bonus": "0",
        }
        # v19.1: choose_log dạng flatten choose_log[i][ind|t|l] (biến thể B).
        if not (is_video and all(q.get("_is_video_step") for q in questions)) and not is_pdf_questions:
            for _i, _q in enumerate(questions):
                _l = _build_choose_label(_q)
                payload[f"choose_log[{_i}][ind]"] = str(_i)
                payload[f"choose_log[{_i}][t]"] = str(step_ms * (_i + 1))
                payload[f"choose_log[{_i}][l]"] = _label_to_str(_l)
            if questions:
                payload["_id"] = str(questions[-1]["id"])
                payload["nx"] = str(len(questions) - 1)
            # Userscript Blob 58 & 59: Với bài kiểm tra / câu hỏi nhóm (13, 14, 18, 21, 22), đính kèm quiz_list[] & score_list[id]
            if is_exam_quiz:
                quiz_ids = [str(_q.get("id") or "") for _q in questions if _q.get("id")]
                if quiz_ids:
                    payload["quiz_list[]"] = quiz_ids
                for _i, _q in enumerate(questions):
                    _qid = str(_q.get("id") or "")
                    if _qid:
                        _qmax = float(_q.get("max_score") or (len(_q.get("sub_answers") or []) if _q.get("q_type") in (21, 22) else per_q_max) or 1)
                        payload[f"score_list[{_qid}]"] = (
                            str(int(_qmax) if _qmax.is_integer() else round(_qmax, 4))
                        )

        # === v20.2: Chuẩn hoá payload POST 1:1 theo Userscript Blob 55/56/59/82 + Blob 61/68 ===
        post_payload = _build_userscript_post_payload(
            exam_type=exam_type,
            is_exam_quiz=is_exam_quiz,
            is_video=is_video,
            is_pdf_questions=is_pdf_questions,
            dc=dc,
            id_cate=str(id_cate),
            uid=str(self.sess.user_id or dc.get("id_user") or ""),
            questions=questions,
            n_correct=n_correct,
            N=N,
            time_spent=int(time_spent),
            now=now,
            data_log=data_log,
            base_payload=payload,
        )

        referer_v1 = f"{OLM_BASE}/chu-de/{id_cate}{V1_SUFFIX}"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-TOKEN": csrf,
            "Accept": "application/json, text/plain, */*",
            "Referer": referer_v1, "Origin": OLM_BASE,
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        }

        delete_result = None
        try:
            delete_result = await self.delete_work(id_cate)
        except Exception as e:
            delete_result = {"ok": False, "error": str(e)}

        dom_fill_result = None
        page_post_status = 0
        page_post_body = ""
        page_post_json = None
        page_blocked_tab = 0
        http_post_status = 0
        http_post_body = ""
        http_post_json = None

        if HAS_PW:
            try:
                cleanup, page = await self._page()
                try:
                    # ══ v19.1: chặn log=tab bằng page.route ══
                    blocked_tab = {"count": 0}

                    async def _route_static(route):
                        try:
                            req = route.request
                            url = req.url or ""
                            method = (req.method or "").upper()
                            if ("teacher-static" in url and method == "POST"):
                                body = req.post_data or ""
                                if "log=tab" in body or "log%3Dtab" in body:
                                    blocked_tab["count"] += 1
                                    await route.fulfill(
                                        status=200,
                                        content_type="application/json",
                                        body=json.dumps({"blocked": True}))
                                    return
                        except Exception:
                            pass
                        try:
                            await route.continue_()
                        except Exception:
                            try:
                                await route.fallback()
                            except Exception:
                                pass

                    try:
                        await page.route("**/course/teacher-static*", _route_static)
                    except Exception:
                        pass

                    url_v1 = _url_v1(f"{OLM_BASE}/chu-de/{id_cate}")
                    await self._goto(page, url_v1, timeout=60000, wait_cf=45)
                    # Userscript Blob 54: dọn sạch local record ('data', 'time_spent', 'time_init')
                    try:
                        await page.evaluate("""() => {
                            try {
                                const c = window.CATE_UI;
                                if (c && typeof c.delLocalRecord === 'function') {
                                    c.delLocalRecord('data');
                                    c.delLocalRecord('time_spent');
                                    c.delLocalRecord('time_init');
                                }
                            } catch(e) {}
                            try {
                                localStorage.removeItem('data');
                                localStorage.removeItem('time_spent');
                                localStorage.removeItem('time_init');
                            } catch(e) {}
                        }""")
                    except Exception:
                        pass
                    await asyncio.sleep(2.0)

                    # ══ v20.2: ĐIỀN ĐÁP ÁN TRONG DOM (click_submit=False để tránh OLM tự POST
                    # bản ghi rỗng trước khi _PAGE_POST_JS gửi data_log đầy đủ chuẩn Userscript) ══
                    dom_fill_result = await self._submit_via_dom_fill(
                        page, id_cate, questions, click_submit=False)
                    dom_path = str((dom_fill_result or {}).get("path") or "")
                    # Luôn gửi _PAGE_POST_JS (/course/teacher-static) giống Userscript để đảm bảo
                    # server OLM ghi nhận đầy đủ data_log (câu hỏi + phương án + đáp án đã chọn)
                    dom_submitted = bool(
                        dom_fill_result and dom_fill_result.get("ok")
                        and dom_path.startswith("dom")
                        and dom_fill_result.get("verified_server_post") is True)

                    if dom_submitted:
                        _log_telemetry("SUBMIT_PATH", id_cate=str(id_cate),
                                       path=dom_path, native_post="skipped")
                    else:
                        _log_telemetry("SUBMIT_PATH", id_cate=str(id_cate),
                                       path=dom_path or "fallback_post",
                                       native_post="used")
                        js_res = await page.evaluate(
                            _PAGE_POST_JS, [id_cate, post_payload])
                        page_post_status = js_res.get("status", 0)
                        page_post_body = js_res.get("body", "") or ""
                        page_post_json = js_res.get("json")
                        if page_post_status == 200:
                            dom_path = (dom_path + "+post") if dom_path else "post"

                    page_blocked_tab = int(blocked_tab["count"] or 0)
                finally:
                    try: await cleanup()
                    except Exception: pass
            except Exception as e:
                _log_telemetry("SUBMIT_PAGE_FAIL", id_cate=str(id_cate),
                               error=str(e)[:200])

        if page_blocked_tab > 0:
            _log_telemetry("LOG_TAB_BLOCKED", id_cate=str(id_cate),
                           count=page_blocked_tab)

        # ── HTTP POST dự phòng cuối (khi _PAGE_POST_JS chưa trả 200) ──
        _done = bool(
            (dom_fill_result or {}).get("ok")
            and str((dom_fill_result or {}).get("path") or "").startswith("dom")
            and (dom_fill_result or {}).get("verified_server_post") is True
        ) or page_post_status == 200
        if not _done:
            try:
                encoded_body = urllib.parse.urlencode(post_payload, doseq=True)
                r = await self.sess.request("POST", "/course/teacher-static",
                                            data=encoded_body, headers=headers)
                http_post_status = r.status_code
                http_post_body = r.text or ""
                http_post_json = try_json(http_post_body)
            except Exception as e:
                http_post_body = f"POST exception: {e}"
        else:
            http_post_body = "skipped (đã nộp qua DOM/OLM)"

        # Xoá cache câu hỏi cũ để lần tải tiếp theo đọc record mới nhất từ OLM
        try:
            self.clear_qcache(id_cate)
        except Exception:
            pass

        page_ok = (
            page_post_status == 200
            and isinstance(page_post_json, dict)
            and (page_post_json.get("ok") or page_post_json.get("success")
                 or page_post_json.get("score") is not None
                 or page_post_json.get("id") is not None)
        )
        http_ok = (
            http_post_status == 200
            and isinstance(http_post_json, dict)
            and (http_post_json.get("ok") or http_post_json.get("success")
                 or http_post_json.get("score") is not None)
        )
        # ── v20.0: kết quả đọc từ chính DOM sau khi OLM chấm ──
        dr = dom_fill_result or {}
        dom_result = dr.get("result") if isinstance(dr, dict) else None
        dom_choices = dr.get("choices") if isinstance(dr, dict) else None
        dom_verified = False
        if isinstance(dom_choices, dict):
            dom_verified = bool(
                (dom_choices.get("radio") or [])
                or (dom_choices.get("checkbox") or [])
                or (dom_choices.get("text") or [])
                or (dom_choices.get("select") or [])
                or int(dom_choices.get("underline") or 0) > 0)
        _dom_ok = bool(dr.get("ok") and str(dr.get("path") or "").startswith("dom"))

        ok = bool(page_ok or http_ok
                  or page_post_status == 200
                  or http_post_status == 200
                  or _dom_ok)

        return {
            "ok": ok,
            "status": page_post_status if page_post_status == 200 else http_post_status,
            "response": (
                page_post_json if isinstance(page_post_json, dict)
                else http_post_json if isinstance(http_post_json, dict)
                else page_post_body[:400] or http_post_body[:400]
            ),
            "questions": N,
            "correct": n_correct,
            "wrong": n_wrong,
            "score": score,
            "max_score": max_score,
            "per_q_max": per_q_max,
            "type_exam": type_exam,
            "type_label": dc.get("_type_label") or "",
            "qtype_dist": _qtype_histogram(questions),
            "delete_before": delete_result,
            # ── v20.0: chi tiết "làm bài thật sự" ──
            "submit_path": dom_path or ("http_post" if http_post_status == 200
                                        else "none"),
            "dom_fill": dr.get("fill"),
            "dom_choices": dom_choices,
            "dom_verified": dom_verified,
            "dom_result": dom_result,
            "dom_clicked": dr.get("clicked"),
            "dom_native": dr.get("native"),
            "dom_errors": dr.get("errors") or [],
            "log_tab_blocked": page_blocked_tab,
            "method": ("dom_real_click" if _dom_ok else
                       "dom_fill+page_post+http_post"),
            "page_post_status": page_post_status,
            "http_post_status": http_post_status,
        }

    async def delete_work(self, id_cate: str) -> dict:
        """Xóa bài làm chuẩn Userscript Blob 54 (DELETE /course/teacher-static với body form-urlencoded)."""
        sess = self.sess
        await sess.refresh_csrf(id_cate)
        csrf = sess.csrf()
        referer_v1 = f"{OLM_BASE}/chu-de/{id_cate}{V1_SUFFIX}"
        headers_form = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
            "Referer": referer_v1,
            "Origin": OLM_BASE,
        }
        if csrf:
            headers_form["X-CSRF-TOKEN"] = csrf
        uid = str(sess.user_id or "")
        form_body = f"id_cate={urllib.parse.quote(str(id_cate))}&id_user={urllib.parse.quote(uid)}"
        endpoints = [
            # 1) Chuẩn 100% Userscript Blob 54: DELETE /course/teacher-static kèm body id_cate & id_user
            ("DELETE", "/course/teacher-static", form_body, headers_form),
            # 2) Query string fallback
            ("DELETE",
             f"/course/teacher-static?id_cate={id_cate}&id_user={uid}",
             form_body, headers_form),
            ("DELETE", f"/course/teacher-static?id_cate={id_cate}", None, headers_form),
            ("POST", f"/course/teacher-categories/{id_cate}/delete-record",
             {"id_cate": str(id_cate), "id_user": uid}, headers_form),
            ("POST", "/course/teacher-static/delete",
             {"id_cate": str(id_cate), "id_user": uid}, headers_form),
        ]
        # Xóa cache câu hỏi/record của bài này để lần đọc tiếp theo cập nhật trạng thái mới
        try:
            for k in [k for k in list(self._qcache.keys()) if k == str(id_cate) or k.startswith(f"{id_cate}#")]:
                self._qcache.pop(k, None)
            self._save_qcache()
        except Exception:
            pass
        last_err = "Không xóa được"
        for method, url, data, hdrs in endpoints:
            try:
                r = await sess.request(method, url, headers=hdrs, data=data)
                if 200 <= r.status_code < 300:
                    return {"ok": True, "method": method, "url": url,
                            "status": r.status_code,
                            "body": (r.text or "")[:200]}
                last_err = f"{method} {url} → {r.status_code}"
            except Exception as e:
                last_err = str(e)
                continue
        return {"ok": False, "error": last_err}

    async def download_word(self, id_cate: str) -> dict:
        """Tải file Word chuẩn 100% Userscript (Blobs 27, 32, 39):
        Ưu tiên GET /download-word-for-user?id_cate=...&showAns=1&questionNotApproved=0
        -> nhận JSON {"file": "https://..."} -> tải đúng file .docx gốc của OLM (không sửa).
        """
        try:
            await self.sess.refresh_csrf(id_cate)
        except Exception:
            pass
        csrf = self.sess.csrf()
        referer_v1 = f"{OLM_BASE}/chu-de/{id_cate}{V1_SUFFIX}"
        headers_get = {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer_v1,
        }
        headers_post = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/plain, */*",
            "Referer": referer_v1,
            "Origin": OLM_BASE,
        }
        if csrf:
            headers_get["X-CSRF-TOKEN"] = csrf
            headers_post["X-CSRF-TOKEN"] = csrf

        def _is_docx(b):
            return bool(b) and len(b) >= 4 and b[:2] == b"PK"

        def _looks_word(ct):
            ct = (ct or "").lower()
            return "word" in ct or "officedocument" in ct or "octet-stream" in ct or "zip" in ct

        async def _handle(r, mn):
            ct = (r.headers.get("content-type") or "").lower()
            if r.status_code != 200:
                return None
            if _is_docx(r.content) and (_looks_word(ct) or len(r.content) > 500):
                return {"ok": True, "content": r.content, "ctype": ct,
                        "method": mn}
            j = try_json(r.text or "")
            if isinstance(j, dict):
                inner = j.get("data") if isinstance(j.get("data"), dict) else {}
                fu = (j.get("file") or j.get("url") or j.get("link")
                      or j.get("download_url") or inner.get("file")
                      or inner.get("url") or inner.get("link"))
                if fu and isinstance(fu, str):
                    fu = fu.strip()
                    if fu.startswith("//"):
                        fu = "https:" + fu
                    elif fu.startswith("/"):
                        fu = OLM_BASE.rstrip("/") + fu
                    try:
                        rr = await self.sess.request("GET", fu, headers={"Referer": referer_v1})
                        if rr.status_code == 200 and _is_docx(rr.content):
                            return {"ok": True, "content": rr.content,
                                    "ctype": rr.headers.get("content-type", ""),
                                    "file_url": fu,
                                    "method": mn + "_follow"}
                    except Exception:
                        pass
            txt = (r.text or "").strip()
            if txt and len(txt) > 32:
                try:
                    if txt.startswith('"'):
                        txt = json.loads(txt)
                except Exception:
                    pass
                try:
                    dec = base64.b64decode(txt)
                    if _is_docx(dec):
                        return {"ok": True, "content": dec,
                                "method": mn + "_b64",
                                "ctype": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
                except Exception:
                    pass
            return None

        # 1) Chuẩn Userscript Blob 27/32/39: GET /download-word-for-user?id_cate=...&showAns=1&questionNotApproved=0
        for path, mn in [
            (f"/download-word-for-user?id_cate={id_cate}"
             f"&showAns=1&questionNotApproved=0", "get_query_approved"),
            (f"/download-word-for-user?id_cate={id_cate}"
             f"&showAns=1&questionNotAppproved=0", "get_query"),
            (f"/download-word-for-user?id_cate={id_cate}"
             f"&showAns=1&redo=1", "get_redo"),
            (f"/course/teacher-categories/{id_cate}/get-crt-ans-file"
             f"?t={int(time.time()*1000)}", "get_crt_ans_file"),
        ]:
            try:
                r = await self.sess.request("GET", path, headers=headers_get)
                got = await _handle(r, mn)
                if got:
                    return got
            except Exception:
                pass

        # 2) Fallback POST /download-word-for-user
        for body, mn in [
            ({"id_cate": id_cate, "showAns": "1",
              "questionNotApproved": "0"}, "post_direct_approved"),
            ({"id_cate": id_cate, "showAns": "1",
              "questionNotAppproved": "0"}, "post_direct"),
            ({"id_cate": id_cate, "showAns": "1"}, "post_no_approved"),
            ({"id_cate": id_cate, "showAns": "1", "redo": "1"}, "post_redo"),
        ]:
            try:
                r = await self.sess.request("POST", "/download-word-for-user",
                                            data=body, headers=headers_post)
                got = await _handle(r, mn)
                if got:
                    return got
            except Exception:
                pass

        # 3) Tải qua browser context (hỗ trợ cả JSON {"file": "..."} lẫn binary trực tiếp)
        if HAS_PW:
            try:
                r = await self._word_via_browser(id_cate)
                if r.get("ok"):
                    _log_hook("download_word_browser_evaluate",
                              id_cate=str(id_cate), method=r.get("method"))
                    return r
            except CloudflareBlock:
                return {"ok": False, "cf": True, "error": "Cloudflare chặn."}
            except Exception:
                pass

        # 4) Fallback cuối cùng: render nội bộ bằng _make_docx khi OLM không cấp file Word
        try:
            qs, dc = await self.fetch_questions(id_cate, force=True)
            if qs:
                title = ((dc or {}).get("_title") or (dc or {}).get("title")
                         or (dc or {}).get("name") or f"Bài {id_cate}")
                return {
                    "ok": True,
                    "content": _make_docx(id_cate, qs, title,
                                          sess_cookies=self.sess.cookies),
                    "ctype": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "title": title,
                    "generated": True,
                    "method": "render_internal",
                }
        except Exception:
            pass

        if not _find_chromium_exe():
            return {"ok": False, "chromium_missing": True,
                    "error": "Không lấy được Word và chưa có Chromium."}
        return {"ok": False, "error": "Không lấy được Word qua mọi fallback"}

    async def _word_via_browser(self, id_cate: str) -> dict:
        cleanup, page = await self._page()
        try:
            url_v1 = _url_v1(f"{OLM_BASE}/chu-de/{id_cate}")
            await self._goto(page, url_v1, timeout=60000, wait_cf=45)
            await asyncio.sleep(2)
            try:
                r = await page.evaluate("""async (id) => {
                    const toB64 = (bytes) => {
                        let bin = '';
                        const chunk = 8192;
                        for (let i = 0; i < bytes.length; i += chunk) {
                            bin += String.fromCharCode.apply(
                                null, bytes.subarray(i, i + chunk));
                        }
                        return btoa(bin);
                    };
                    const pageTitle = (() => {
                        try {
                            const d = window.data_cate;
                            if (d && (d.title || d.name || d.title_cate))
                                return String(d.title || d.name || d.title_cate).trim();
                            const h1 = document.querySelector('h1, .title, .page-title');
                            if (h1 && h1.innerText) return h1.innerText.trim();
                        } catch(e) {}
                        return '';
                    })();
                    const urls = [
                        '/download-word-for-user?id_cate=' + id
                        + '&showAns=1&questionNotApproved=0',
                        '/download-word-for-user?id_cate=' + id
                        + '&showAns=1&questionNotAppproved=0',
                        '/download-word-for-user?id_cate=' + id
                        + '&showAns=1&redo=1'
                    ];
                    for (const url of urls) {
                        try {
                            const r = await fetch(url, {
                                credentials: 'include',
                                headers: {'X-Requested-With': 'XMLHttpRequest'}
                            });
                            if (r.status !== 200) continue;
                            const buf = await r.arrayBuffer();
                            const bytes = new Uint8Array(buf);
                            // Trường hợp 1: trả thẳng file .docx (PK\\x03\\x04)
                            if (bytes.length > 4 && bytes[0] === 0x50 && bytes[1] === 0x4B) {
                                return {
                                    status: 200,
                                    b64: toB64(bytes),
                                    ct: r.headers.get('content-type') || '',
                                    title: pageTitle,
                                    via: 'direct_pk'
                                };
                            }
                            // Trường hợp 2 (Chuẩn Userscript Blob 27/32/39): trả JSON {"file": "https://..."}
                            const txt = new TextDecoder('utf-8').decode(bytes);
                            let j = null;
                            try { j = JSON.parse(txt); } catch(e) {}
                            if (j && typeof j === 'object') {
                                const inner = (j.data && typeof j.data === 'object') ? j.data : {};
                                const fileUrl = j.file || j.url || j.link || j.download_url
                                                || inner.file || inner.url || inner.link;
                                if (fileUrl && typeof fileUrl === 'string') {
                                    try {
                                        const rf = await fetch(fileUrl, { credentials: 'include' });
                                        if (rf.ok) {
                                            const fbuf = await rf.arrayBuffer();
                                            const fbytes = new Uint8Array(fbuf);
                                            if (fbytes.length > 4 && fbytes[0] === 0x50 && fbytes[1] === 0x4B) {
                                                return {
                                                    status: 200,
                                                    b64: toB64(fbytes),
                                                    ct: rf.headers.get('content-type') || '',
                                                    file_url: fileUrl,
                                                    title: pageTitle,
                                                    via: 'json_file_follow'
                                                };
                                            }
                                        }
                                    } catch(e2) {}
                                    return {
                                        status: 200,
                                        file_url: fileUrl,
                                        title: pageTitle,
                                        via: 'json_file_url_only'
                                    };
                                }
                            }
                        } catch(e) {}
                    }
                    return { status: 0, title: pageTitle };
                }""", id_cate)
                if r and r.get("status") == 200:
                    if r.get("b64"):
                        content = base64.b64decode(r["b64"])
                        if content[:2] == b"PK":
                            return {
                                "ok": True,
                                "content": content,
                                "title": r.get("title") or "",
                                "ctype": r.get("ct")
                                    or "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                "method": f"browser_{r.get('via') or 'evaluate'}",
                            }
                    if r.get("file_url"):
                        fu = str(r["file_url"]).strip()
                        if fu.startswith("//"):
                            fu = "https:" + fu
                        elif fu.startswith("/"):
                            fu = OLM_BASE.rstrip("/") + fu
                        rr = await self.sess.request("GET", fu)
                        if rr.status_code == 200 and rr.content and rr.content[:2] == b"PK":
                            return {
                                "ok": True,
                                "content": rr.content,
                                "title": r.get("title") or "",
                                "ctype": rr.headers.get("content-type")
                                    or "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                "method": "browser_json_file_http",
                            }
            except Exception:
                pass
            return {"ok": False, "error": "Không tải được qua browser"}
        finally:
            try:
                await cleanup()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
# WORD GENERATOR
# ═══════════════════════════════════════════════════════════════
def _download_image_sync(src: str, cookies: dict, timeout: int = 15):
    try:
        from curl_cffi import requests as curl_requests
        s = _normalize_media_url(src)
        if s.startswith("data:"):
            _, b64 = s.split(",", 1)
            return base64.b64decode(b64)
        for imp in (effective_impersonate(), "chrome124", "chrome110"):
            try:
                r = curl_requests.get(s, cookies=cookies, impersonate=imp,
                                      timeout=timeout, headers={"Referer": OLM_BASE + "/"})
                if r.status_code == 200 and len(r.content) > 100:
                    return r.content
                break
            except Exception as e_imp:
                if "impersonat" in str(e_imp).lower() or "not supported" in str(e_imp).lower():
                    continue
                raise
    except Exception as e:
        _log_warn(f"download_image failed: {src} — {e}")
    return None


def _emu(px: int) -> int:
    return int(px * 9525)


def _make_docx(id_cate, questions, title=None, sess_cookies=None) -> bytes:
    """Sinh file .docx đáp án.

    v19.1 (H) — header/footer & style chuẩn:
      * Calibri 11pt (sz=22 half-points) toàn bộ body.
      * Title 32 half-points (16pt) bold, màu 1F4E79, canh giữa.
      * Header mỗi câu: `Câu N [Type] (Xđ): text`.
      * q_type 13 multi_select: đánh dấu ✓ từng option đúng.
      * q_type 13 single: in `Đúng` (008000) / `Sai` (C00000).
      * q_type 2/3/11: in `→ Đáp án: value, value`.
      * q_type 5/6: note "Sắp xếp theo thứ tự..." / "Nối cặp tương ứng...".
      * Explanation: nghiêng, màu 666666, prefix 💡.
    """
    def esc(s):
        return (str(s or "").replace("&", "&amp;")
                .replace("<", "&lt;").replace(">", "&gt;"))

    header_title = title or f"Bài {id_cate}"
    media_files: Dict[str, bytes] = {}
    rels_entries: List[Tuple[str, str]] = []
    rel_counter = [1]
    img_id = [1]

    def register_image(img_bytes: bytes) -> str:
        rid = f"rIdImg{rel_counter[0]}"
        name = f"image{rel_counter[0]}.png"
        rel_counter[0] += 1
        media_files[name] = img_bytes
        rels_entries.append((rid, f"media/{name}"))
        return rid

    def P(text, *, bold=False, italic=False, size=22,
          color=None, align=None, spacing_after=100):
        rpr = ['<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>',
               f'<w:sz w:val="{size}"/>']
        if bold: rpr.append('<w:b/>')
        if italic: rpr.append('<w:i/>')
        if color: rpr.append(f'<w:color w:val="{color}"/>')
        ppr = [f'<w:spacing w:after="{spacing_after}"/>']
        if align: ppr.append(f'<w:jc w:val="{align}"/>')
        return (f'<w:p><w:pPr>{"".join(ppr)}</w:pPr>'
                f'<w:r><w:rPr>{"".join(rpr)}</w:rPr>'
                f'<w:t xml:space="preserve">{esc(text)}</w:t></w:r></w:p>')

    def IMG_PARA(rid: str, w_px: int = 400, h_px: int = 300):
        cx, cy = _emu(w_px), _emu(h_px)
        img_id[0] += 1
        _id = img_id[0]
        return (
            '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
            '<wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/>'
            f'<wp:docPr id="{_id}" name="Picture{_id}"/>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="{_id}" name="img{_id}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/>'
            '<a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            '<pic:spPr><a:xfrm><a:off x="0" y="0"/>'
            f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
        )

    body = P(f"ĐÁP ÁN {header_title.upper()}", bold=True, size=32,
             color="1F4E79", align="center", spacing_after=200)
    _qt_hist = _qtype_histogram(questions)
    body += P(f"Tổng: {total_problem_count(questions)} câu"
              + (f"  ·  {' · '.join(f'{k}:{v}' for k, v in sorted(_qt_hist.items()))}"
                 if _qt_hist else ""),
              italic=True, size=20, color="666666", align="center",
              spacing_after=300)

    for i, q in enumerate(questions):
        qt = q.get("q_type")
        qtext = q.get("question_text") or q.get("text", "")
        type_lbl = q.get("type_label", "")
        score = q.get("max_score", 1)
        body += P(f"Câu {i+1} [{type_lbl}] ({score}đ): {qtext}",
                  bold=True, size=24, spacing_after=60)

        if sess_cookies is not None:
            for b in (q.get("rich_content") or []):
                if b.get("kind") == "image" and b.get("src"):
                    img_bytes = _download_image_sync(b["src"], sess_cookies)
                    if img_bytes:
                        rid = register_image(img_bytes)
                        body += IMG_PARA(rid, 480, 360)

        opts = q.get("options") or []

        if qt == 13 and q.get("multi_select"):
            ca = q.get("correctAnswers") or []
            idxs = [k for k, v in enumerate(ca) if v == '1']
            for k, o in enumerate(opts):
                is_ok = k in idxs
                body += P(f"    {chr(65+k)}. {o}{'  ✓' if is_ok else ''}",
                          size=22, color="008000" if is_ok else "000000",
                          bold=is_ok, spacing_after=40)
            body += P("    → Đáp án (chọn nhiều): "
                      + ", ".join(chr(65 + k) for k in idxs),
                      bold=True, size=22, color="008000", spacing_after=200)
        elif qt == 13:
            ca = q.get("correctAnswers") or []
            for k, o in enumerate(opts):
                val = ca[k] if k < len(ca) else "0"
                is_ok = val == "1"
                body += P(f"    {k+1}. {o}", size=22, spacing_after=30)
                body += P(f"        → {'Đúng' if val == '1' else 'Sai'}",
                          size=20, color="008000" if is_ok else "C00000",
                          italic=True, spacing_after=60)
            ans_tf = ", ".join(
                ["Đ" if (k < len(ca) and ca[k] == '1') else "S"
                 for k in range(len(opts))])
            body += P(f"    → Đáp án: {ans_tf}", bold=True, size=22,
                      color="008000", spacing_after=200)
        elif qt in (2, 3, 11):
            vals = q.get("correctAnswers") or (
                [q.get("correctAnswer")] if q.get("correctAnswer") else [])
            for k, o in enumerate(opts):
                body += P(f"    {chr(65+k)}. {o}", size=22, spacing_after=40)
            if vals:
                body += P(f"    → Đáp án: {', '.join(map(str, vals))}",
                          bold=True, size=22, color="008000",
                          spacing_after=200)
        elif qt == 5:
            body += P("    → Sắp xếp theo đúng thứ tự 1, 2, 3, ...",
                      size=22, color="008000", spacing_after=200)
        elif qt == 6:
            body += P("    → Nối cặp tương ứng theo thứ tự A-1, B-2, C-3, ...",
                      size=22, color="008000", spacing_after=200)
        elif qt == 9:
            ul = q.get("correctAnswers") or []
            body += P(f"    → Gạch chân: {', '.join(map(str, ul))}",
                      size=22, color="008000", spacing_after=200)
        elif qt == 10:
            body += P(f"    → Nhóm: {q.get('answerIndices') or []}",
                      size=22, color="008000", spacing_after=200)
        elif qt in (21, 22) and q.get("sub_answers"):
            sub_qs = _ensure_group_sub_questions(q)
            for k, sa in enumerate(q["sub_answers"]):
                sq = sub_qs[k] if k < len(sub_qs) else {}
                st = (q.get("sub_types") or [1])[k] if k < len(q.get("sub_types") or []) else 1
                stem_t = (sq.get("text") or "").strip()
                if stem_t and not stem_t.startswith("Câu hỏi phần"):
                    body += P(f"    Câu {k+1}: {stem_t}", bold=True, size=22, spacing_after=40)
                for oi, o_txt in enumerate(sq.get("options") or []):
                    body += P(f"      {chr(65+oi)}. {o_txt}", size=21, spacing_after=30)
                if st == 13:
                    txt = ", ".join(["Đúng" if v == "1" else "Sai" for v in sa])
                elif st == 1 and isinstance(sa, list):
                    txt = ", ".join(chr(65 + int(v)) for v in sa)
                elif st == 11:
                    txt = " | ".join(sa) if isinstance(sa, list) else str(sa)
                else:
                    txt = str(sa)
                body += P(f"    Phần {k+1}: {txt}", bold=True, color="008000", size=22, spacing_after=60)
        else:
            labels = set()
            if q.get("correctLabel"): labels.add(q["correctLabel"])
            for v in (q.get("correctAnswers") or []):
                if isinstance(v, str) and re.match(r'^[A-Z]$', v):
                    labels.add(v)
            for k, o in enumerate(opts):
                lb = chr(65+k)
                is_ok = lb in labels
                body += P(f"    {lb}. {o}{'  ✓' if is_ok else ''}",
                          size=22,
                          color="008000" if is_ok else "000000",
                          bold=is_ok, spacing_after=40)
            if labels:
                body += P(f"    → Đáp án: {', '.join(sorted(labels))}",
                          bold=True, size=22, color="008000",
                          spacing_after=200)

        if q.get("explanation"):
            body += P(f"    💡 {q['explanation']}", italic=True, size=20,
                      color="666666", spacing_after=100)
        body += P("", size=20, spacing_after=200)

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<w:body>{body}'
        '<w:sectPr>'
        '<w:footerReference w:type="default" r:id="rIdFooter1"/>'
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
        'w:header="709" w:footer="709" w:gutter="0"/>'
        '</w:sectPr></w:body></w:document>')

    # Footer chuẩn: tên tool + số trang + ghi chú
    footer_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<w:p><w:pPr><w:jc w:val="center"/><w:spacing w:after="0"/></w:pPr>'
        '<w:r><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
        '<w:sz w:val="16"/><w:color w:val="808080"/></w:rPr>'
        f'<w:t xml:space="preserve">OLM Tool Pro v19.3 · Bài {esc(id_cate)} · Trang </w:t></w:r>'
        '<w:r><w:rPr><w:sz w:val="16"/><w:color w:val="808080"/></w:rPr>'
        '<w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:rPr><w:sz w:val="16"/><w:color w:val="808080"/></w:rPr>'
        '<w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        '<w:r><w:rPr><w:sz w:val="16"/><w:color w:val="808080"/></w:rPr>'
        '<w:fldChar w:fldCharType="end"/></w:r>'
        '</w:p></w:ftr>')

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Default Extension="jpg" ContentType="image/jpeg"/>'
        '<Default Extension="jpeg" ContentType="image/jpeg"/>'
        '<Default Extension="gif" ContentType="image/gif"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/footer1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
        '</Types>')

    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>')

    word_rels_items = [
        '<Relationship Id="rIdFooter1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" '
        'Target="footer1.xml"/>'
    ]
    for rid, target in rels_entries:
        word_rels_items.append(
            f'<Relationship Id="{rid}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="{target}"/>')
    word_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(word_rels_items) + '</Relationships>')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document_xml)
        z.writestr("word/footer1.xml", footer_xml)
        z.writestr("word/_rels/document.xml.rels", word_rels)
        for name, data in media_files.items():
            z.writestr(f"word/media/{name}", data)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════
# ANSWERS HTML — v20.1 (tách từng câu con, liền mạch công thức, không lặp)
# ═══════════════════════════════════════════════════════════════
def _ans_order_index(q: dict, n: int) -> List[int]:
    """Thứ tự hiển thị option theo `order` gốc của OLM (không lệch, không lặp)."""
    order = q.get("order")
    if not isinstance(order, list) or len(order) != n:
        return list(range(n))
    seen, out = set(), []
    for v in order:
        try:
            i = int(v)
        except Exception:
            continue
        if 0 <= i < n and i not in seen:
            seen.add(i)
            out.append(i)
    for i in range(n):
        if i not in seen:
            out.append(i)
    return out


def _ans_dedup_questions(questions: List[dict]) -> List[dict]:
    """Bỏ câu hỏi trùng (theo id) và bỏ câu rỗng hoàn toàn."""
    out, seen = [], set()
    for q in questions or []:
        qid = str(q.get("id") or "")
        key = qid or json.dumps(
            {k: q.get(k) for k in ("q_type", "text", "correctAnswers",
                                   "correctAnswer", "correctIndex",
                                   "answerIndices", "sub_answers")},
            ensure_ascii=False, sort_keys=True)[:400]
        if key in seen:
            continue
        seen.add(key)
        has_text = bool((q.get("text_html") or q.get("text")
                         or q.get("question_text") or "").strip())
        has_ans = any(q.get(k) not in (None, "", [], {}) for k in (
            "correctAnswers", "correctAnswer", "correctIndex", "correctLabel",
            "answerIndices", "sub_answers"))
        has_opts = bool(q.get("options"))
        if not (has_text or has_ans or has_opts):
            continue
        out.append(q)
    return out


def _ans_option_rows(q: dict, opts: List[str]) -> List[Tuple[int, str, bool, bool]]:
    """Trả list (index_gốc, text, is_correct, is_selected) theo thứ tự hiển thị."""
    qt = q.get("q_type")
    n = len(opts)
    order = _ans_order_index(q, n)
    positional = qt in (5, 6, 20)
    correct_idx: set = set()
    labels: set = set()
    ca = q.get("correctAnswers") or []

    if qt == 13 or q.get("multi_select"):
        for k, v in enumerate(ca):
            if str(v) == "1":
                correct_idx.add(k)
        if q.get("multi_select"):
            return [(i, opts[i], i in correct_idx, False) for i in order]
        rows = []
        for i in order:
            val = str(ca[i]) if i < len(ca) else "0"
            rows.append((i, opts[i], val == "1", False))
        return rows

    if not positional:
        if q.get("correctLabel") and re.fullmatch(r'[A-Z]', str(q["correctLabel"])):
            labels.add(str(q["correctLabel"]))
        if q.get("correctIndex") is not None:
            try:
                correct_idx.add(int(q["correctIndex"]))
            except Exception:
                pass
        for v in ca:
            s = str(v)
            if re.fullmatch(r'[A-Z]', s):
                labels.add(s)
            else:
                try:
                    correct_idx.add(int(s))
                except Exception:
                    pass

    return [(i, opts[i],
             (i in correct_idx) or (chr(65 + i) in labels),
             False) for i in order]


def _ans_box(label: str, html_body: str, kind: str = "ok") -> str:
    return (f'<div class="ansbox {kind}">'
            f'<div class="ansbox-h"><span class="anslbl">{label}</span></div>'
            f'<div class="ansbox-b">{html_body}</div></div>')


def _ans_answer_block(q: dict, opts: List[str], sub_card_num: Optional[int] = None) -> str:
    """Khối 'ĐÁP ÁN' nổi bật cho từng q_type — không lặp nội dung, đúng thứ tự."""
    qt = q.get("q_type")
    esc = _html_escape

    def opt_text(i: int) -> str:
        return _format_opt_html(opts[i]) if 0 <= i < len(opts) else f"#{i}"

    # ── Đúng/Sai (13) ──
    if qt == 13:
        ca = [str(v) for v in (q.get("correctAnswers") or [])]
        if q.get("multi_select"):
            idxs = [i for i, v in enumerate(ca) if v == "1"]
            chips = "".join(
                f'<span class="chip ok"><b>{chr(65+i)}.</b> {opt_text(i)}'
                f'<span class="tick">✓</span></span>'
                for i in idxs)
            return _ans_box(
                f'Chọn {len(idxs)} đáp án đúng',
                f'<div class="chips">{chips}</div>')
        # Nếu đã có danh sách mệnh đề ở .opts phía trên, chỉ hiện bảng tóm tắt ngắn gọn (A: Đúng · B: Sai...)
        # để KHÔNG lặp lại nguyên văn cả 4 mệnh đề lần thứ 2/3!
        if opts:
            chips = []
            for i in range(len(ca)):
                ok = ca[i] == "1"
                chips.append(
                    f'<span class="chip {"ok" if ok else "no"}">'
                    f'<span class="chip-i">{chr(65+i)}</span>'
                    f'<b class="{"ok" if ok else "no"}">{"Đúng" if ok else "Sai"}</b>'
                    f'</span>'
                )
            return _ans_box("Kết luận Đúng / Sai từng ý", f'<div class="chips">{"".join(chips)}</div>')
        rows = []
        for i in range(len(ca)):
            ok = ca[i] == "1"
            rows.append(
                f'<div class="tf">'
                f'<span class="tf-n">{i+1}</span>'
                f'<span class="tf-t">{opt_text(i)}</span>'
                f'<span class="tf-v {"ok" if ok else "no"}">'
                f'{"Đúng" if ok else "Sai"}</span></div>')
        return _ans_box("Đúng / Sai", "".join(rows))

    # ── 21/22: câu hỏi nhóm (fallback nếu không tách sub_questions) ──
    if qt in (21, 22) and q.get("sub_answers"):
        parts = []
        for k, sa in enumerate(q["sub_answers"]):
            st = (q.get("sub_types") or [1])[k] if k < len(q.get("sub_types") or []) else 1
            sl = (q.get("sub_labels") or [""])[k] if k < len(q.get("sub_labels") or []) else ""
            if st == 13:
                if isinstance(sa, list):
                    txt = " · ".join(
                        f'Ý {chr(97+j)}: <b class="{"ok" if v == "1" else "no"}">'
                        f'{"Đúng" if v == "1" else "Sai"}</b>'
                        for j, v in enumerate(sa))
                else:
                    txt = esc(sa)
            elif st == 1 and isinstance(sa, list):
                labs = []
                for v in sa:
                    try:
                        labs.append(f'<b class="ok">{chr(65 + int(v))}</b>')
                    except Exception:
                        labs.append(f'<b class="ok">{esc(v)}</b>')
                txt = ", ".join(labs) if labs else f'<b class="ok">{esc(sl or "A")}</b>'
            elif st == 11:
                txt = " · ".join(
                    f'<code>{esc(x)}</code>' for x in sa) if isinstance(sa, list) \
                    else f'<code>{esc(sa)}</code>'
            else:
                txt = esc(sa)
            parts.append(f'<div class="sub"><span class="sub-n">Câu {k+1}</span>'
                         f'<span class="sub-v">{txt}</span></div>')
        return _ans_box("Đáp án từng phần", "".join(parts))

    # ── q_type 2/3/11: điền khuyết ──
    if qt in (2, 3, 11):
        texts = [str(x) for x in (q.get("answer_texts") or []) if str(x).strip()]
        vals = [str(x) for x in texts]
        if not vals:
            if q.get("correctAnswer"):
                vals.append(str(q["correctAnswer"]))
            for v in (q.get("correctAnswers") or []):
                vals.append(str(v))
        seen, uniq = set(), []
        for v in vals:
            if v not in seen:
                seen.add(v)
                uniq.append(v)
        if qt == 2:
            body = (f'<div class="fill"><code class="fill-v">'
                    f'{esc(_normalize_inline_math_str(uniq[0] if uniq else ""))}</code></div>')
            return _ans_box("Đáp án điền vào ô trống", body)
        label = (f'Đáp án {len(uniq)} ô (theo thứ tự ô)'
                 if qt == 3 else f'Đáp án {len(uniq)} ô chọn')
        chips = "".join(
            f'<span class="chip"><span class="chip-i">{i+1}</span>'
            f'<code>{esc(_normalize_inline_math_str(v))}</code></span>' for i, v in enumerate(uniq))
        return _ans_box(label, f'<div class="chips">{chips}</div>')

    # ── q_type 5/20: sắp xếp ──
    if qt in (5, 20):
        n = q.get("optionCount") or len(q.get("correctAnswers") or []) or len(opts)
        seq = " → ".join(f'<span class="ord-i">{i+1}</span>' for i in range(int(n)))
        note = ("Sắp xếp các mục theo đúng thứ tự dưới đây"
                if qt == 5 else "Thứ tự đúng của các đoạn")
        return _ans_box(note, f'<div class="order">{seq}</div>')

    # ── q_type 6: nối cặp ──
    if qt == 6:
        n = q.get("optionCount") or len(opts) or len(q.get("correctAnswers") or [])
        pairs = " · ".join(
            f'<span class="pair"><b>{chr(65+i)}</b>–<b>{i+1}</b></span>'
            for i in range(int(n)))
        return _ans_box("Nối cặp tương ứng", f'<div class="pairs">{pairs}</div>')

    # ── q_type 9: gạch chân ──
    if qt == 9:
        idxs = [int(x) for x in (q.get("correctAnswers") or [])]
        if idxs:
            chips = "".join(
                f'<span class="chip ok"><span class="chip-i">{i+1}</span>'
                f'<span class="tick">✓</span></span>' for i in idxs)
            return _ans_box("Từ/cụm cần gạch chân (vị trí)", f'<div class="chips">{chips}</div>')
        return ""

    # ── q_type 10: kéo nhóm ──
    if qt == 10:
        groups = q.get("answerIndices") or []
        rows = []
        for gi, g in enumerate(groups):
            items = ", ".join(f'<span class="ord-i">{int(i)+1}</span>'
                              for i in (g or []))
            rows.append(f'<div class="sub"><span class="sub-n">Nhóm {gi+1}</span>'
                        f'<span class="sub-v">{items or "—"}</span></div>')
        return _ans_box(f'Nhóm đúng ({len(groups)} nhóm)', "".join(rows))

    # ── fallback: trắc nghiệm 1 đáp án ──
    rows = _ans_option_rows(q, opts)
    ok = [i for i, _t, c, _s in rows if c]
    if not ok:
        if q.get("correctLabel"):
            lbl = _html_escape(str(q["correctLabel"]))
            box_lbl = f"Đáp án câu {sub_card_num}" if sub_card_num else "Đáp án đúng"
            return _ans_box(box_lbl, f'<div class="ansbig"><b class="ok">{lbl}</b></div>')
        return ""
    txts = ", ".join(f'<b class="ok">{chr(65+i)}</b>' for i in ok)
    box_lbl = f"Đáp án câu {sub_card_num}" if sub_card_num else "Đáp án đúng"
    return _ans_box(box_lbl, f'<div class="ansbig">{txts}</div>')


def _render_single_question_card(q: dict, num_label: int, is_correct_this: bool,
                                 wrapper_tag: str = "article",
                                 wrapper_cls: str = "qitem",
                                 sub_card_num: Optional[int] = None) -> str:
    """Render 1 thẻ câu hỏi độc lập (stem + ảnh không lặp + danh sách đáp án + hộp đáp án)."""
    t = q.get("type_label", "")
    qt = q.get("q_type")
    raw_html = q.get("text_html") or ""
    if raw_html:
        text = _normalize_inline_math_str(raw_html)
    else:
        plain_q = _normalize_inline_math_str(q.get("question_text") or q.get("text", ""))
        text = f'<span class="olm-text">{_html_escape(plain_q)}</span>' if plain_q else ""

    opts = list(q.get("options") or [])
    if not opts and qt in (1, 13):
        ta = [x for x in (q.get("text_all") or []) if x and x.strip()]
        stem = (q.get("question_text") or q.get("text") or "").strip()
        opts = [x for x in ta[:8] if x.strip() != stem]

    status = ('<span class="qscore ok-badge">✓ ĐÚNG</span>'
              if is_correct_this else
              '<span class="qscore wrong-badge">✗ SAI</span>')

    opt_html = ""
    if opts:
        rows_i = _ans_option_rows(q, opts)
        items = []
        is_tf_single = (qt == 13 and not q.get("multi_select"))
        for gi, txt, is_ok, _sel in rows_i:
            lb = chr(65 + gi)
            if is_tf_single:
                cls = "opt ok" if is_ok else "opt tf-wrong"
                mark = ('<span class="otick">✓ Đúng</span>'
                        if is_ok else '<span class="otick no">✗ Sai</span>')
            else:
                cls = "opt ok" if is_ok else "opt"
                mark = '<span class="otick">✓</span>' if is_ok else ''
            items.append(
                f'<li class="{cls}"><span class="olb">{lb}.</span>'
                f'<span class="otx">{_format_opt_html(txt)}</span>{mark}</li>')
        opt_html = f'<ul class="opts">{"".join(items)}</ul>'

    ans_block = _ans_answer_block(q, opts, sub_card_num=sub_card_num)

    expl = _normalize_inline_math_str((q.get("explanation") or "").strip())
    exp_html = (f'<div class="exp"><span class="explbl">💡 HƯỚNG DẪN GIẢI</span>'
                f'<div class="exptx">{_html_escape(expl)}</div></div>'
                if expl else "")

    # Chỉ thêm img_html nếu trong `text` (từ _render_rich_to_html) CHƯA có thẻ <img
    img_html = ""
    if "<img" not in text.lower():
        for b in (q.get("rich_content") or []):
            if b.get("kind") == "image" and b.get("src"):
                src = _html_escape(_normalize_media_url(b["src"]))
                img_html += (f'<figure class="olm-figure">'
                             f'<img src="{src}" loading="lazy" alt="hình" '
                             f'onerror="this.parentElement.classList.add('
                             f'\'img-error\')"></figure>')

    wrong_cls = f" {wrapper_cls}-wrong" if not is_correct_this else ""
    return (
        f'<{wrapper_tag} class="{wrapper_cls}{wrong_cls}">'
        f'<header class="qhead">'
        f'<span class="qnum">Câu {num_label}</span>'
        f'<span class="qtype">{_html_escape(t)}</span>'
        f'<span class="qscore-pill">{float(q.get("max_score") or 1):g}Đ</span>'
        f'{status}</header>'
        f'<div class="qtext">{text}{img_html}</div>'
        f'{opt_html}{ans_block}{exp_html}'
        f'</{wrapper_tag}>'
    )


def answers_html(id_skill, questions, exam_type=0, n_correct=None,
                 meta: Optional[dict] = None) -> str:
    """Sinh trang HTML đáp án (webview)."""
    meta = meta or {}
    questions = _ans_dedup_questions(questions or [])

    ans_html_raw = meta.get("ans_html") or ""
    ans_text_raw = meta.get("ans_text") or ""
    reviewed = bool(meta.get("from_ans_file"))

    n_questions = len(questions)
    n_parts = total_problem_count(questions) if questions else 0
    total_count = n_parts if questions else int(meta.get("total") or 0)

    # Nếu toàn bộ bài là câu hỏi nhóm (q_type 21/22, VD bài #2136053455 có 1 group = 9 câu con),
    # thì tổng số câu hiển thị là tổng số câu con (9 câu) và mặc định đúng 9/9 câu!
    all_group = bool(questions and all(q.get("q_type") in (21, 22) and q.get("sub_answers") for q in questions))
    count_display = n_parts if all_group else (n_questions or total_count)
    if n_correct is None:
        n_correct = count_display
    elif all_group and n_correct == n_questions and n_parts > n_questions:
        n_correct = n_parts

    parts_note = (f' <span class="parts-note">(gồm {n_parts} câu hỏi thành phần)</span>'
                  if n_parts and n_parts != count_display else "")

    banner = ""
    if reviewed:
        banner += ('<div class="exam-banner review">[REVIEW] <b>Bài đã nộp</b> — '
                   'đang hiển thị đáp án chuẩn từ server OLM.</div>')
    if exam_type in (4, 10):
        banner += ('<div class="exam-banner pdf">[PDF] <b>Kiểm tra PDF</b></div>')
    elif exam_type == 5 and meta.get("is_flexible"):
        banner += ('<div class="exam-banner flex">[FLEX] <b>Luyện tập linh hoạt</b>'
                   ' — không tính điểm.</div>')
    elif exam_type in (1, 5):
        banner += ('<div class="exam-banner video">[VIDEO] <b>Bài giảng Video tương tác</b></div>')
    elif exam_type == 2:
        banner += ('<div class="exam-banner flex">[THEORY] <b>Lý thuyết tương tác</b></div>')
    elif exam_type in (13, 14, 18, 21, 22):
        banner += ('<div class="exam-banner pdf">[EXAM] <b>Đề kiểm tra / Đánh giá</b></div>')

    if not questions and (ans_html_raw or ans_text_raw):
        if ans_html_raw:
            body_inner = f'<div class="ansfile">{ans_html_raw}</div>'
        else:
            body_inner = (f'<pre class="ansfile-pre">'
                          f'{_html_escape(ans_text_raw)}</pre>')
        return _answers_shell(
            id_skill, banner, f'<h1>ĐÁP ÁN // BÀI {id_skill}</h1>',
            body_inner, total_count, n_correct, review=True,
            max_points=float(meta.get("max_points") or 10.0))

    if not questions:
        return _answers_shell(
            id_skill, banner, f'<h1>ĐÁP ÁN // BÀI {id_skill}</h1>',
            '<div class="empty">[!] Chưa lấy được câu hỏi cho bài này.<br>'
            'Hãy bấm <b>“Đáp án”</b> lại, hoặc mở bài bằng chế độ v1 '
            '(<code>?v=v1</code>).</div>', 0, 0)

    rows = []
    sub_global_idx = 0
    for i, q in enumerate(questions):
        qt = q.get("q_type")
        if qt in (21, 22) and q.get("sub_answers"):
            sub_qs = _ensure_group_sub_questions(q)
            if sub_qs:
                sub_cards = []
                for sq in sub_qs:
                    sub_global_idx += 1
                    card_no = sub_global_idx if all_group else (i + 1)
                    is_sub_ok = (sub_global_idx - 1) < n_correct if all_group else (i < n_correct)
                    sub_cards.append(
                        _render_single_question_card(
                            sq,
                            num_label=card_no,
                            is_correct_this=is_sub_ok,
                            wrapper_tag="div",
                            wrapper_cls="subq-card",
                            sub_card_num=card_no,
                        )
                    )
                is_grp_ok = i < n_correct
                rows.append(
                    f'<article class="qitem qitem-group{" qitem-wrong" if not is_grp_ok else ""}">'
                    f'{"".join(sub_cards)}'
                    f'</article>'
                )
                continue

        is_correct_this = i < n_correct
        rows.append(
            _render_single_question_card(
                q,
                num_label=i + 1,
                is_correct_this=is_correct_this,
                wrapper_tag="article",
                wrapper_cls="qitem",
            )
        )

    return _answers_shell(id_skill, banner,
                          f'<h1>ĐÁP ÁN // BÀI {id_skill} '
                          f'· {count_display} CÂU{parts_note}</h1>',
                          "".join(rows), count_display, n_correct,
                          review=reviewed,
                          max_points=_ans_max_points(questions))


def _ans_max_points(questions: List[dict], fallback: float = 10.0) -> float:
    """Tổng điểm tối đa của bài (chuẩn thang 10 của OLM)."""
    total = 0.0
    for q in questions or []:
        try:
            v = float(q.get("max_score") or 0)
        except Exception:
            v = 0.0
        if v > 0:
            total += v
    if total <= 0 or (len(questions or []) == 1 and questions[0].get("q_type") in (21, 22)):
        return float(fallback)
    return total if total <= 10.0 else float(fallback)


def _answers_shell(id_skill, banner, title_html, body_inner,
                   total_count, n_correct, review=False,
                   max_points: float = 10.0) -> str:
    """Khung HTML + CSS hiện đại, sắc nét cho trang đáp án."""
    if total_count:
        n_ok = min(max(0, int(n_correct or 0)), total_count)
        pts = min(float(max_points), round(n_ok * float(max_points) / total_count, 2))
        score_line = (f'<div class="score-summary">'
                      f'<span class="ss-ic">[ĐÁP ÁN CHUẨN]</span> '
                      f'Đúng <b>{n_ok}/{total_count}</b> câu'
                      f' · <b>{pts:g}/{float(max_points):g}</b> điểm'
                      f'</div>')
    else:
        score_line = ""

    return f"""<!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Đáp án bài {id_skill}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:ital,wght@0,400;0,500;0,600;0,700;1,400&family=JetBrains+Mono:wght@500;600;700&family=Space+Grotesk:wght@600;700&display=swap" rel="stylesheet">
<script>
window.MathJax = {{
  tex: {{ inlineMath: [['$$','$$'], ['$','$'], ['\\\\(','\\\\)']],
          displayMath: [['\\\\[','\\\\]']],
          processEscapes: true, processEnvironments: true }},
  options: {{ skipHtmlTags: ['script','noscript','style','textarea','pre','code'],
              ignoreHtmlClass: 'tex2jax_ignore', processHtmlClass: 'tex2jax_process' }},
  svg: {{ fontCache: 'global', scale: 1.04 }},
  startup: {{ typeset: true,
    ready: () => {{ MathJax.startup.defaultReady();
      window.addEventListener('load', () => MathJax.typesetPromise().catch(()=>{{}})); }} }}
}};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js" async></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#090d14;--panel:#111824;--panel2:#162030;--bd:#233147;--bd2:#30435f;
  --tx:#f8fafc;--tx2:#cbd5e1;--dim:#94a3b8;--ok:#10b981;--ok-bright:#00f59b;
  --err:#f43f5e;--err2:#fb7185;--p1:#00f59b;--p2:#38bdf8;--p3:#22d3ee;
  --font-sans:'Be Vietnam Pro',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  --font-head:'Space Grotesk','Be Vietnam Pro',sans-serif;
  --font-mono:'JetBrains Mono','Cascadia Mono',Consolas,monospace;
}}
body{{background:var(--bg);color:var(--tx);
  font-family:var(--font-sans);
  padding:22px 26px 48px;line-height:1.7;font-size:15px;
  -webkit-font-smoothing:antialiased}}
h1{{font-family:var(--font-head);font-size:19px;font-weight:700;margin-bottom:14px;
  letter-spacing:.02em;color:var(--ok-bright);text-transform:uppercase;
  border-bottom:2px solid var(--bd2);padding-bottom:10px}}
.exam-banner{{padding:10px 14px;border-radius:6px;margin-bottom:12px;
  font-size:13.5px;border:1px solid var(--bd2);border-left:4px solid var(--p2);
  background:var(--panel2);font-family:var(--font-sans)}}
.exam-banner.review{{border-left-color:var(--ok-bright);color:#6ee7b7}}
.exam-banner.pdf{{border-left-color:var(--p2);color:#bae6fd}}
.exam-banner.flex{{border-left-color:var(--p2);color:#7dd3fc}}
.exam-banner.video{{border-left-color:var(--p3);color:#67e8f9}}
.score-summary{{display:flex;align-items:center;gap:10px;
  background:rgba(16,185,129,.1);border:1px solid rgba(0,245,155,.45);border-left:4px solid var(--ok-bright);
  border-radius:6px;padding:11px 16px;margin-bottom:18px;font-size:14.5px;color:#d1fae5}}
.score-summary b{{color:#fff;font-family:var(--font-mono);font-size:15px;font-weight:700}}
.ss-ic{{font-family:var(--font-mono);color:var(--ok-bright);font-weight:700;font-size:13px}}
.parts-note{{font-size:13px;color:var(--dim);font-weight:500}}

.qitem,.subq-card{{background:var(--panel);border:1px solid var(--bd2);border-radius:8px;
  padding:18px 20px;margin-bottom:16px;border-left:4px solid var(--ok-bright);
  box-shadow:0 4px 14px rgba(0,0,0,.35)}}
.qitem.qitem-group{{background:transparent;border:none;padding:0;margin:0;box-shadow:none}}
.qitem.qitem-wrong,.subq-card.subq-card-wrong{{border-left-color:var(--err)}}
.qhead{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px;
  padding-bottom:10px;border-bottom:1px solid var(--bd)}}
.qnum{{font-family:var(--font-head);font-size:13.5px;font-weight:700;letter-spacing:.03em;
  color:#04120b;background:var(--ok-bright);padding:3px 12px;border-radius:5px}}
.qtype{{font-size:12px;color:var(--p2);border:1px solid var(--bd2);
  background:var(--panel2);padding:3px 9px;border-radius:5px;font-weight:600}}
.qscore-pill{{font-family:var(--font-mono);font-size:12px;color:#fbbf24;border:1px solid rgba(251,191,36,.35);
  background:rgba(251,191,36,.1);padding:2px 9px;border-radius:5px;font-weight:700}}
.qscore{{font-family:var(--font-mono);font-size:12px;font-weight:700;padding:3px 10px;
  border-radius:5px;margin-left:auto;letter-spacing:.02em}}
.ok-badge{{color:var(--ok-bright);background:rgba(0,245,155,.12);
  border:1px solid rgba(0,245,155,.4)}}
.wrong-badge{{color:var(--err2);background:rgba(255,77,109,.12);
  border:1px solid rgba(255,77,109,.4)}}

.qtext{{color:var(--tx);font-size:15.5px;line-height:1.75;margin-bottom:14px;word-break:break-word}}
.olm-text{{display:inline}}
.olm-math{{display:inline;vertical-align:middle}}
mjx-container{{display:inline !important;margin:0 2px !important;vertical-align:middle !important;color:var(--tx) !important}}
mjx-container[jax="SVG"]>svg{{display:inline !important;vertical-align:middle !important;fill:currentColor}}
.qtext img,.otx img,.ansfile img{{max-width:min(100%,560px);height:auto;
  display:block;margin:10px auto;background:#fff;border-radius:6px;padding:8px;
  border:1px solid var(--bd2)}}
.otx img{{margin:6px 0;max-height:220px;width:auto}}

.opts{{list-style:none;display:flex;flex-direction:column;gap:8px;margin-bottom:14px}}
.opt{{display:flex;gap:12px;align-items:center;
  background:var(--panel2);border:1px solid var(--bd);border-radius:6px;
  padding:11px 14px;font-size:15px;line-height:1.6}}
.opt.ok{{background:rgba(0,245,155,.11);border:1px solid var(--ok-bright);
  box-shadow:inset 3px 0 0 var(--ok-bright)}}
.opt.tf-wrong{{border-color:rgba(244,63,94,.35);background:rgba(244,63,94,.05)}}
.olb{{font-family:var(--font-mono);font-weight:700;color:var(--dim);min-width:24px;flex-shrink:0;font-size:14.5px}}
.opt.ok .olb{{color:var(--ok-bright)}}
.otx{{flex:1;min-width:0;word-break:break-word;color:var(--tx2)}}
.opt.ok .otx{{color:#ffffff;font-weight:600}}
.otick{{font-family:var(--font-mono);color:var(--ok-bright);font-weight:700;flex-shrink:0;
  font-size:13px;background:rgba(0,245,155,.12);padding:2px 8px;border-radius:4px}}
.otick.no{{color:var(--err2);background:rgba(244,63,94,.12)}}

.ansbox{{border-radius:6px;padding:12px 16px;margin-top:10px;
  background:rgba(0,245,155,.08);border:1px solid rgba(0,245,155,.4);
  border-left:4px solid var(--ok-bright);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}}
.ansbox-h{{margin:0}}
.anslbl{{font-family:var(--font-head);font-size:12.5px;font-weight:700;
  letter-spacing:.05em;text-transform:uppercase;color:var(--ok-bright)}}
.ansbox-b{{font-size:15px;color:#ecfdf5}}
.ansbig{{font-family:var(--font-mono);font-size:18px;letter-spacing:.03em}}
.ansbig b.ok{{color:var(--ok-bright);font-size:20px;background:rgba(0,245,155,.15);padding:2px 12px;border-radius:5px;border:1px solid var(--ok-bright)}}
b.ok{{color:var(--ok-bright)}}
b.no{{color:var(--err2)}}

.chips{{display:flex;flex-wrap:wrap;gap:8px}}
.chip{{display:inline-flex;align-items:center;gap:7px;background:var(--bg);
  border:1px solid var(--bd2);border-radius:5px;padding:5px 11px;font-size:14px}}
.chip.ok{{background:rgba(0,245,155,.12);border-color:var(--ok-bright)}}
.chip.no{{background:rgba(244,63,94,.1);border-color:rgba(244,63,94,.45)}}
.chip-i{{font-family:var(--font-mono);font-size:12px;font-weight:700;color:#04120b;
  background:var(--ok-bright);border-radius:4px;padding:1px 7px}}
.chip.no .chip-i{{background:var(--err2);color:#fff}}
.chip code{{color:#ecfdf5;font-family:var(--font-mono);font-size:14px}}
.tick{{color:var(--ok-bright);font-weight:800}}

.fill{{font-size:15px}}
.fill-v{{background:var(--bg);border:1px solid var(--ok-bright);color:#ecfdf5;
  border-radius:5px;padding:6px 14px;font-family:var(--font-mono);font-size:15px;font-weight:600}}

.tf{{display:flex;align-items:flex-start;gap:10px;padding:6px 0;
  border-bottom:1px dashed var(--bd)}}
.tf:last-child{{border-bottom:none}}
.tf-n{{font-family:var(--font-mono);font-size:12px;font-weight:700;color:#04120b;
  background:var(--tx2);border-radius:4px;padding:2px 7px;flex-shrink:0;margin-top:2px}}
.tf-t{{flex:1;min-width:0;color:var(--tx2);word-break:break-word}}
.tf-v{{font-family:var(--font-mono);font-weight:700;flex-shrink:0;font-size:13px}}
.tf-v.ok{{color:var(--ok-bright)}}
.tf-v.no{{color:var(--err2)}}

.order{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:15px}}
.ord-i{{width:28px;height:28px;display:inline-grid;place-items:center;
  background:rgba(0,245,155,.18);border:1px solid var(--ok-bright);
  border-radius:5px;font-family:var(--font-mono);font-size:13px;font-weight:700;color:#ecfdf5}}
.pairs{{display:flex;flex-wrap:wrap;gap:10px}}
.pair{{background:var(--bg);border:1px solid var(--ok-bright);
  border-radius:5px;padding:4px 11px;font-size:14px;font-family:var(--font-mono)}}
.pair b{{color:var(--ok-bright)}}

.sub{{display:flex;gap:10px;padding:6px 0;align-items:flex-start;
  border-bottom:1px dashed var(--bd);width:100%}}
.sub:last-child{{border-bottom:none}}
.sub-n{{font-family:var(--font-mono);font-size:12.5px;font-weight:700;color:var(--ok-bright);
  flex-shrink:0;min-width:64px;padding-top:2px}}
.sub-v{{flex:1;min-width:0;color:#ecfdf5;word-break:break-word}}
.sub-v code{{background:var(--bg);border:1px solid var(--bd2);border-radius:4px;
  padding:2px 7px;font-family:var(--font-mono);font-size:13px}}

.exp{{margin-top:12px;background:var(--panel2);
  border:1px solid var(--bd2);border-left:3px solid var(--p2);border-radius:6px;padding:12px 15px}}
.explbl{{font-family:var(--font-head);display:block;font-size:12px;font-weight:700;
  color:var(--p2);letter-spacing:.04em;margin-bottom:5px;text-transform:uppercase}}
.exptx{{font-size:14px;color:var(--tx2);line-height:1.65}}

.olm-figure{{margin:10px 0;text-align:center}}
.olm-figure.img-error{{min-height:60px;background:rgba(255,77,109,.08);
  border:1px dashed var(--err);border-radius:6px}}
.olm-figure.img-error::after{{content:'[!] Không tải được hình';color:var(--err2);
  font-size:12.5px;display:block;padding:18px}}

.ansfile{{background:var(--panel);border:1px solid var(--bd2);border-radius:6px;
  padding:16px 18px;color:var(--tx);font-size:14.5px;word-break:break-word}}
.ansfile img{{max-width:100%;height:auto;background:#fff;border-radius:6px;
  padding:5px;margin:8px auto;display:block}}
.ansfile table{{width:100%;border-collapse:collapse;margin:8px 0}}
.ansfile td,.ansfile th{{border:1px solid var(--bd2);padding:6px 9px;
  text-align:left;font-size:13.5px}}
.ansfile-pre{{background:var(--panel);border:1px solid var(--bd2);
  border-radius:6px;padding:16px;white-space:pre-wrap;word-break:break-word;
  font-family:var(--font-mono);font-size:13px;color:#e2e8f0}}
.empty{{background:var(--panel);border:1px dashed var(--bd2);border-radius:6px;
  padding:24px;text-align:center;color:var(--tx2);font-size:14px}}
.empty code{{background:var(--bg);border:1px solid var(--bd2);border-radius:4px;
  padding:2px 7px;font-family:var(--font-mono)}}
::-webkit-scrollbar{{width:10px;height:10px}}
::-webkit-scrollbar-track{{background:var(--bg)}}
::-webkit-scrollbar-thumb{{background:var(--bd2);border-radius:5px;border:2px solid var(--bg)}}
::-webkit-scrollbar-thumb:hover{{background:var(--p2)}}
</style></head><body>
{banner}{title_html}{score_line}
{body_inner}
<script>
setTimeout(() => {{
  document.querySelectorAll('.olm-math').forEach(el => {{
    if (!window.MathJax || !window.MathJax.startup) {{
      el.title = 'MathJax offline — LaTeX thô';
      el.style.background = 'rgba(56,189,248,.1)';
      el.style.padding = '1px 5px'; el.style.borderRadius = '4px';
    }}
  }});
}}, 5000);
</script>
</body></html>"""
# ═══════════════════════════════════════════════════════════════
# OLM APP (BRIDGE)
# ═══════════════════════════════════════════════════════════════
class OLMApp:
    def __init__(self):
        self.sess = OLMSession.load()
        self.cdp = CDPManager()
        self.client = OLMClient(self.sess, self.cdp)
        self.lic = LicenseManager()

    def lic_hwid(self): return self.lic.get_hwid()

    def lic_status(self):
        try:
            r = self.lic.validate()
            r["hwid"] = self.lic.get_hwid()
            # v20.0: cho UI biết link lấy key + đã cấu hình hay chưa
            try:
                u = get_key_url()
                r["key_url"] = u
                r["key_url_set"] = bool(u)
            except Exception:
                r["key_url"] = ""
                r["key_url_set"] = False
            return r
        except Exception as e:
            return {"ok": False, "error": str(e),
                    "hwid": self.lic.get_hwid(),
                    "reason": "Lỗi kiểm tra license"}

    # === v20.0 — LẤY KEY MIỄN PHÍ ===
    def get_key_info(self):
        """Trả link lấy key hiện tại + có trang đi kèm hay không."""
        try:
            u = get_key_url()
            p = self.key_page_path()
            return {
                "ok": True,
                "url": u or "",
                "url_set": bool(u),
                "local_page": bool(p),
                "local_path": str(p) if p else "",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_key_url(self, url):
        """Lưu link lấy key vào settings.json (không cần build lại)."""
        try:
            u = str(url or "").strip()
            if u and not re.match(r"^https?://", u, re.I):
                return {"ok": False,
                        "error": "Link phải bắt đầu bằng http:// hoặc https://"}
            save_settings({"get_key_url": u})
            _log_telemetry("SET_KEY_URL", set=bool(u))
            return {"ok": True, "url": u}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_get_key(self):
        """Mở trang lấy key: ưu tiên link người dùng cấu hình, nếu trống thì
        mở `key_generator.html` đi kèm."""
        try:
            u = get_key_url()
            if u:
                try:
                    if sys.platform == "win32":
                        os.startfile(u)
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", u])
                    else:
                        subprocess.Popen(["xdg-open", u])
                    _log_telemetry("OPEN_GET_KEY", mode="url")
                    return {"ok": True, "mode": "url", "url": u}
                except Exception as e:
                    return {"ok": False,
                            "error": f"Không mở được link: {e}", "url": u}
            r = self.open_key_page()
            if r.get("ok"):
                return {"ok": True, "mode": "local", "path": r.get("path")}
            return {"ok": False,
                    "error": r.get("error")
                    or "Chưa cấu hình link lấy key và không tìm thấy "
                       "key_generator.html"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def lic_activate(self, key):
        try: return self.lic.activate(key)
        except Exception as e: return {"ok": False, "error": str(e)}

    def lic_deactivate(self):
        try: return self.lic.deactivate()
        except Exception as e: return {"ok": False, "error": str(e)}

    def copy_to_clipboard(self, text):
        text = str(text or "")
        try:
            import tkinter
            r = tkinter.Tk(); r.withdraw()
            r.clipboard_clear(); r.clipboard_append(text)
            r.update(); r.destroy()
            return {"ok": True, "method": "tkinter"}
        except Exception:
            pass
        try:
            if sys.platform == "win32":
                p = subprocess.Popen(["clip"], stdin=subprocess.PIPE, shell=True)
                p.communicate(text.encode("utf-16le"))
                return {"ok": True, "method": "clip"}
            elif sys.platform == "darwin":
                p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
                p.communicate(text.encode("utf-8"))
                return {"ok": True, "method": "pbcopy"}
            else:
                p = subprocess.Popen(["xclip", "-selection", "clipboard"],
                                     stdin=subprocess.PIPE)
                p.communicate(text.encode("utf-8"))
                return {"ok": True, "method": "xclip"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def chromium_status(self):
        try: return check_chromium()
        except Exception as e: return {"ok": False, "error": str(e)}

    def chromium_install(self):
        try: return install_chromium()
        except Exception as e: return {"ok": False, "error": str(e)}

    def chrome_open(self):
        try: return _launch_chrome()
        except Exception as e: return {"ok": False, "error": str(e)}

    def chrome_grab_cookies(self):
        try:
            if not self.lic.validate().get("ok"):
                return {"ok": False, "error": "License không hợp lệ"}
            grabber = ChromeCookieGrabber(self.cdp)
            r = run_async(grabber.grab(), timeout=120)
            if not r.get("ok"):
                return r
            self.sess.set_cookie_dict(r["cookies"])
            self.sess.save()
            self.client = OLMClient(self.sess, self.cdp)
            return {"ok": True, "user_id": self.sess.user_id,
                    "username": self.sess.username,
                    "count": len(r["cookies"])}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_cookies(self, ck):
        try:
            if not self.lic.validate().get("ok"):
                return {"ok": False, "error": "License không hợp lệ"}
            self.sess.set_cookies(ck)
            if not self.sess.has_auth():
                return {"ok": False, "error": "Cookie thiếu session/user"}
            self.sess.save()
            self.client = OLMClient(self.sess, self.cdp)
            return {"ok": True, "user_id": self.sess.user_id,
                    "username": self.sess.username}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_status(self):
        try:
            if not self.sess.has_auth():
                return {"ok": False}
            try:
                r = run_async(self.sess.request("GET", "/"), timeout=20)
                if r.status_code == 200:
                    return {"ok": True, "user_id": self.sess.user_id,
                            "username": self.sess.username,
                            "verified": True}
            except Exception:
                pass
            if self.sess.user_id:
                return {"ok": True, "user_id": self.sess.user_id,
                        "username": self.sess.username,
                        "verified": False}
            return {"ok": False}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def logout(self):
        try:
            self.sess.clear()
            self.sess = OLMSession()
            self.client = OLMClient(self.sess, self.cdp)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _handle_err(self, e: Exception) -> dict:
        if isinstance(e, CloudflareBlock) or "CF_BLOCK" in str(e):
            return {"ok": False, "cf": True,
                    "error": "Cloudflare chặn. Bấm '🌐 Chrome' để giải."}
        if isinstance(e, ChromiumMissing) or "CHROMIUM_MISSING" in str(e):
            return {"ok": False, "chromium_missing": True,
                    "error": "Chưa có Chromium. Bấm 'Cài Chromium' để tải."}
        if isinstance(e, V1Required) or "V1_REQUIRED" in str(e):
            return {"ok": False, "v1_required": True,
                    "error": "Giao diện v2 — cần ?v=v1. Thử lại."}
        return {"ok": False, "error": str(e)}

    def scan(self):
        try:
            if not self.lic.validate().get("ok"):
                return {"ok": False, "error": "License không hợp lệ"}
            items = run_async(self.client.scan_assignments(), timeout=240)
            return {"ok": True, "count": len(items), "items": items}
        except Exception as e:
            return self._handle_err(e)

    def add_manual(self, input_value):
        try:
            cid = extract_id_from_input(input_value)
            if not cid:
                return {"ok": False, "error": "Không nhận diện được ID/link."}
            title = f"Bài {cid}"
            type_label = ""
            exam_type = 0
            try:
                if self.lic.validate().get("ok") and self.sess.has_auth():
                    qs, dc = run_async(self.client.fetch_questions(cid), timeout=90)
                    dc = dc or {}
                    t = (dc.get("_title") or dc.get("title") or dc.get("name"))
                    if t: title = t
                    type_label = dc.get("_type_label") or ""
                    exam_type = int(dc.get("type") or 0)
            except Exception:
                pass
            return {"ok": True, "count": 1,
                    "items": [{
                        "id": cid, "title": title, "href": "",
                        "type_label": type_label, "type": exam_type,
                        "done": False, "score": None,
                        "correct": None, "total": None,
                    }]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_questions(self, id_skill):
        try:
            qs, dc = run_async(self.client.fetch_questions(id_skill), timeout=180)
            dc = dc or {}
            qs = qs or []
            tc = {}
            for q in qs:
                k = q.get("type_label") or f"Type {q.get('q_type')}"
                tc[k] = tc.get(k, 0) + 1
            rec = dict(dc.get("_record") or {})
            count_real = total_problem_count(qs)
            if count_real == 0 and rec.get("totalq"):
                count_real = int(rec["totalq"])
            score_val = rec.get("score")
            has_pos_score = False
            try:
                has_pos_score = (score_val is not None and float(score_val) > 0)
            except Exception:
                pass
            done_flag = bool(
                rec.get("done")
                or rec.get("ended") in (1, True, "1")
                or rec.get("messageStatic")
                or has_pos_score
                or (rec.get("correct") is not None and int(rec.get("correct") or 0) > 0)
            )
            if not done_flag:
                rec["done"] = False
                rec["score"] = None
                rec["correct"] = None
            return {
                "ok": True,
                "questions": qs,
                "count": count_real,
                "title": (dc.get("_title") or dc.get("title")
                          or dc.get("name") or ""),
                "type_label": dc.get("_type_label") or "",
                "type": int(dc.get("type") or dc.get("_exam_type") or 0),
                "type_counts": tc,
                "record": rec,
                "done_from_record": done_flag,
            }
        except Exception as e:
            return self._handle_err(e)

    def get_answers_html(self, id_skill, n_correct=None):
        try:
            if not self.lic.validate().get("ok"):
                return {"ok": False, "error": "License không hợp lệ"}
            qs, dc = run_async(self.client.fetch_questions(id_skill, force=True),
                               timeout=180)
            dc = dc or {}
            qs = qs or []

            # ── v19.2: chế độ XEM LẠI BÀI ĐÃ NỘP ──
            # Server không trả câu hỏi nữa -> dùng đáp án từ get-crt-ans-file.
            meta = {
                "from_ans_file": bool(dc.get("_from_ans_file")),
                "ans_html": dc.get("_ans_html") or "",
                "ans_text": dc.get("_ans_text") or "",
                "is_flexible": bool(dc.get("_is_flexible")),
                "total": 0,
            }
            if not qs and not (meta["ans_html"] or meta["ans_text"]):
                return {"ok": False,
                        "error": "Không lấy được câu hỏi lẫn file đáp án "
                                 "của bài này."}

            exam_type = int(dc.get("type") or dc.get("_exam_type") or 0)
            rec = dc.get("_record") or {}
            total = total_problem_count(qs) or len(qs)
            if total == 0 and rec.get("totalq"):
                total = int(rec["totalq"])
            meta["total"] = total
            # Khi mở bảng Đáp án: luôn ưu tiên hiển thị đầy đủ 100% câu đúng (total/total)
            # trừ khi caller truyền n_correct dương hợp lệ.
            if n_correct is not None:
                try:
                    n_correct = int(n_correct)
                except Exception:
                    n_correct = None
            if n_correct is None or n_correct <= 0 or (total and n_correct > total):
                n_correct = total if total > 0 else (len(qs) if qs else 0)

            html = answers_html(id_skill, qs, exam_type=exam_type,
                                 n_correct=n_correct, meta=meta)
            return {
                "ok": True,
                "html": html,
                "questions": total,
                "title": dc.get("_title") or "",
                "type_label": dc.get("_type_label") or "",
                "type": exam_type,
                "n_correct": n_correct,
                "from_ans_file": meta["from_ans_file"],
                "answered": total,
                "json": json.dumps(qs, ensure_ascii=False, indent=2),
            }
        except Exception as e:
            return self._handle_err(e)

    def submit(self, id_skill, mode="minus1", custom=9.0,
               time_spent=300, force_redo=False):
        try:
            if not self.lic.validate().get("ok"):
                return {"ok": False, "error": "License không hợp lệ"}
            return run_async(
                self.client.submit(id_skill, mode, float(custom),
                                    int(time_spent), bool(force_redo)),
                timeout=600)
        except Exception as e:
            return self._handle_err(e)

    def delete_work(self, id_cate):
        try:
            if not self.lic.validate().get("ok"):
                return {"ok": False, "error": "License không hợp lệ"}
            return run_async(self.client.delete_work(id_cate), timeout=120)
        except Exception as e:
            return self._handle_err(e)

    def download_word(self, id_cate):
        try:
            if not self.lic.validate().get("ok"):
                return {"ok": False, "error": "License không hợp lệ"}
            r = run_async(self.client.download_word(id_cate), timeout=180)
            if not r.get("ok"):
                return r
            title = r.get("title") or ""
            type_label = ""
            # Ưu tiên lấy tên bài từ cache sẵn có để không phải mở lại trình duyệt
            # khi đã tải được file Word gốc trực tiếp từ API /download-word-for-user.
            try:
                cached = self.client._qcache.get(str(id_cate))
                if cached and isinstance(cached, tuple) and len(cached) >= 2:
                    dc_cached = cached[1] or {}
                    if not title:
                        title = (dc_cached.get("_title") or dc_cached.get("title")
                                 or dc_cached.get("name") or "")
                    type_label = dc_cached.get("_type_label") or ""
            except Exception:
                pass
            if not title:
                try:
                    qs, dc = run_async(self.client.fetch_questions(id_cate, force=False),
                                       timeout=90)
                    dc = dc or {}
                    if not title:
                        title = (dc_cached.get("_title") if 'dc_cached' in locals() else "") or (
                            dc.get("_title") or dc.get("title") or dc.get("name") or ""
                        )
                    if not type_label:
                        type_label = dc.get("_type_label") or ""
                except Exception:
                    pass
            title = title or f"Bài {id_cate}"
            base = f"Đáp án {title}"
            if type_label:
                base += f" [{type_label}]"
            safe = re.sub(r'[<>:"/\\|?*\n\r\t]+', ' ', base).strip()[:120] \
                   or f"Đáp án Bài {id_cate}"
            fname = f"{safe}.docx"
            out = DOWNLOAD_DIR / fname
            n = 1
            while out.exists():
                out = DOWNLOAD_DIR / f"{safe} ({n}).docx"
                n += 1
            content = r["content"]
            out.write_bytes(content)
            return {
                "ok": True, "path": str(out), "filename": out.name,
                "size": len(content), "method": r.get("method", ""),
                "generated": bool(r.get("generated")),
                "title": title, "type_label": type_label,
            }
        except Exception as e:
            return self._handle_err(e)

    def open_path(self, path):
        try:
            if sys.platform == "win32": os.startfile(path)
            elif sys.platform == "darwin": subprocess.Popen(["open", path])
            else: subprocess.Popen(["xdg-open", path])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # === v19.1 - PORT FROM USERSCRIPT (key viewer page) ===
    def key_page_path(self) -> Optional[Path]:
        """Tìm `key_generator.html` (bundled cạnh exe / trong _MEIPASS / repo)."""
        for base in (RES_DIR, APP_DIR, Path(__file__).resolve().parent):
            try:
                p = Path(base) / "key_generator.html"
                if p.exists():
                    return p
            except Exception:
                continue
        return None

    def open_key_page(self):
        """Mở trang HTML hiện key Supabase (1 key chưa hiện / máy / ngày).

        Trang chỉ gọi REST `/rest/v1/<bảng>` bằng anon key đã cấu hình —
        không thêm secret mới. Trả kèm `path` để UI có thể hiển thị.
        """
        try:
            p = self.key_page_path()
            if not p:
                return {"ok": False,
                        "error": "Không tìm thấy key_generator.html "
                                 "(thiếu trong bản build?)"}
            r = self.open_path(str(p))
            if r.get("ok"):
                _log_hook("open_key_page", path=str(p))
            return {**r, "path": str(p)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # === v20.1 — HỆ THỐNG AUTO-UPDATE NGẦM (ẨN SÂU TRONG CODE, USER KHÔNG THẤY LINK) ===
    # Nguồn cập nhật được mã hoá XOR + Base64 trong `_INTERNAL_UPDATE_BLOB`.
    # Để đổi sang link GitHub Raw / Releases / Cloud riêng, chạy lệnh:
    #   python -c "import olm; print(olm.OLMApp._encode_hidden_update_url('https://...'))"
    # rồi dán chuỗi kết quả vào `_INTERNAL_UPDATE_BLOB` bên dưới.
    _INTERNAL_UPDATE_KEY = b"OLM_PRO_V20_DEEP_SECRET_2026_KEY"
    _INTERNAL_UPDATE_BLOB = (
        "Jzs4NWx/cHZrY2Z4O2Nrfj92d2s6eG5lZno+emZpY3B3N2lrf2ZjP2h6c2t+"
        "ZHl7dj96b2s9dWw/f2s5bmJofH9rOmZrOnhuamJ7aX5rOnhuZmp6eG5/fHZr"
        "Y3F6Zmk="
    )

    @classmethod
    def _encode_hidden_update_url(cls, plain_url: str) -> str:
        """Mã hoá URL cập nhật thành chuỗi XOR+Base64 để nhúng sâu vào `_INTERNAL_UPDATE_BLOB`."""
        raw = str(plain_url or "").strip().encode("utf-8")
        k = cls._INTERNAL_UPDATE_KEY
        xored = bytes(b ^ k[i % len(k)] for i, b in enumerate(raw))
        return base64.b64encode(xored).decode("ascii")

    @classmethod
    def _resolve_hidden_update_url(cls) -> str:
        """Giải mã URL cập nhật ẩn sâu trong code (ưu tiên biến môi trường dev nếu có)."""
        st = load_settings()
        override = str(st.get("_internal_update_ep") or st.get("update_url") or "").strip()
        if override and re.match(r"^https?://", override, re.I):
            return override
        env_u = os.environ.get("OLM_UPDATE_URL", "").strip()
        if env_u and re.match(r"^https?://", env_u, re.I):
            return env_u
        try:
            raw = base64.b64decode(cls._INTERNAL_UPDATE_BLOB.encode("ascii"))
            k = cls._INTERNAL_UPDATE_KEY
            dec = bytes(b ^ k[i % len(k)] for i, b in enumerate(raw)).decode("utf-8")
            if dec.startswith(("http://", "https://")):
                return dec
        except Exception:
            pass
        return f"{SUPABASE_URL}/storage/v1/object/public/updates/version.json"

    @staticmethod
    def _parse_ver_tuple(v_str: str) -> Tuple[int, ...]:
        nums = [int(x) for x in re.findall(r"\d+", str(v_str or "0"))]
        while len(nums) < 3:
            nums.append(0)
        return tuple(nums[:4])

    @staticmethod
    def _current_target_path() -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve()
        return Path(__file__).resolve()

    def _current_sha256(self) -> str:
        try:
            if getattr(sys, "frozen", False) and HOTPATCH_F.exists():
                return hashlib.sha256(HOTPATCH_F.read_bytes()).hexdigest()
            p = self._current_target_path()
            if p.exists():
                return hashlib.sha256(p.read_bytes()).hexdigest()
        except Exception:
            pass
        return ""

    def get_update_config(self) -> dict:
        """Trả thông tin phiên bản hiện tại cho UI (TUYỆT ĐỐI KHÔNG lộ URL cập nhật cho user)."""
        try:
            is_frozen = bool(getattr(sys, "frozen", False))
            sha = self._current_sha256()
            return {
                "ok": True,
                "current_version": APP_VERSION,
                "mode": "exe" if is_frozen else "py",
                "target_path": str(self._current_target_path().name),
                "sha256": sha[:12] if sha else "",
                "channel": "Official Cloud (Encrypted)",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_update_config(self, update_url: str) -> dict:
        """API nội bộ lưu nguồn cập nhật ghi đè (không hiển thị trên UI user)."""
        try:
            u = str(update_url or "").strip()
            if u and not re.match(r"^https?://", u, re.I):
                return {"ok": False, "error": "URL cập nhật phải bắt đầu bằng http:// hoặc https://"}
            save_settings({"_internal_update_ep": u, "update_url": u})
            _log_telemetry("SET_UPDATE_URL", set=bool(u))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _http_fetch_bytes(url: str, timeout: int = 30) -> Tuple[int, bytes, str]:
        """Tải nội dung từ URL (ưu tiên urllib chuẩn + fallback curl_cffi)."""
        import urllib.request
        import ssl
        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": effective_ua(),
                "Accept": "application/json, text/plain, */*",
                "Cache-Control": "no-cache",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                status = getattr(resp, "status", 200)
                ctype = resp.headers.get("Content-Type", "")
                return status, resp.read(), ctype
        except Exception as e1:
            # Fallback qua curl_cffi nếu urllib gặp lỗi TLS/Cloudflare
            try:
                async def _cf_get():
                    async with CurlAsyncSession(impersonate=_supported_chrome_impersonate(124),
                                                timeout=timeout, verify=True) as s:
                        r = await s.get(url, headers={"Cache-Control": "no-cache"})
                        return r.status_code, r.content, r.headers.get("content-type", "")
                return run_async(_cf_get(), timeout=timeout + 5)
            except Exception:
                raise e1

    def check_update(self, custom_url: str = "") -> dict:
        """Kiểm tra phiên bản mới từ nguồn cập nhật ẩn sâu trong code:
        1. Link trực tiếp tới file `olm.py` raw (Cách 1 — tự bóc `APP_VERSION = "..."` + so khớp SHA-256,
           hỗ trợ cả bản `.py` lẫn bản `.exe` qua cơ chế Hot-Patch runtime).
        2. JSON Manifest (`{"version": "20.2", "py_url": "...", "exe_url": "...", "changelog": "..."}`)
        3. GitHub Releases API (`https://api.github.com/repos/<owner>/<repo>/releases/latest`)
        """
        try:
            u = str(custom_url or "").strip() or self._resolve_hidden_update_url()
            if not u:
                return {"ok": False, "error": "Chưa cấu hình kênh cập nhật."}

            status, raw, ctype = self._http_fetch_bytes(u, timeout=25)
            if status != 200 or not raw:
                return {"ok": False, "error": f"Máy chủ cập nhật hiện chưa phản hồi (HTTP {status})."}

            is_frozen = bool(getattr(sys, "frozen", False))
            cur_ver = APP_VERSION
            cur_tuple = self._parse_ver_tuple(cur_ver)
            cur_sha = self._current_sha256()

            text = ""
            try:
                text = raw.decode("utf-8", errors="replace")
            except Exception:
                pass

            # Trường hợp 1 (Cách 1): URL trả về trực tiếp mã nguồn Python (olm.py)
            if ("def main():" in text and "class OLMApp" in text) or u.lower().split("?")[0].endswith(".py"):
                m_ver = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', text)
                remote_ver = m_ver.group(1).strip() if m_ver else cur_ver
                remote_tuple = self._parse_ver_tuple(remote_ver)
                remote_sha = hashlib.sha256(raw).hexdigest()
                has_update = (remote_tuple > cur_tuple) or (
                    remote_tuple == cur_tuple and bool(cur_sha) and remote_sha != cur_sha
                    and (not is_frozen or HOTPATCH_F.exists())
                )
                notes = "Bản cập nhật trực tiếp từ máy chủ phát hành"
                if remote_tuple == cur_tuple and remote_sha != cur_sha:
                    notes = f"Bản vá (hotfix) mới cho v{remote_ver} (SHA: {remote_sha[:8]})"
                self._pending_dl_url = u
                self._pending_sha256 = remote_sha
                return {
                    "ok": True,
                    "has_update": bool(has_update),
                    "current_version": cur_ver,
                    "latest_version": remote_ver,
                    "sha256": remote_sha,
                    "changelog": notes,
                    "mode": "exe" if is_frozen else "py",
                }

            # Trường hợp 2: JSON Manifest hoặc GitHub Releases API
            data = json.loads(text)
            if not isinstance(data, dict):
                return {"ok": False, "error": "Dữ liệu manifest không đúng định dạng JSON object."}

            # Hỗ trợ GitHub Releases API (/releases/latest)
            if "tag_name" in data and "assets" in data:
                remote_ver = str(data.get("tag_name") or "").lstrip("vV").strip() or cur_ver
                changelog = str(data.get("body") or "Bản phát hành mới.").strip()
                dl_url = ""
                assets = data.get("assets") or []
                for asset in assets:
                    if not isinstance(asset, dict):
                        continue
                    aname = str(asset.get("name") or "").lower()
                    burl = str(asset.get("browser_download_url") or "")
                    if is_frozen and aname.endswith(".exe"):
                        dl_url = burl
                        break
                    if not is_frozen and aname.endswith(".py"):
                        dl_url = burl
                        break
                if not dl_url and assets and isinstance(assets[0], dict):
                    dl_url = str(assets[0].get("browser_download_url") or "")
                remote_sha = ""
            else:
                # Standard Manifest JSON
                remote_ver = str(data.get("version") or data.get("latest") or data.get("app_version") or cur_ver).lstrip("vV").strip()
                changelog = str(data.get("changelog") or data.get("notes") or data.get("description") or "").strip()
                if is_frozen:
                    dl_url = str(data.get("exe_url") or data.get("py_url") or data.get("download_url") or data.get("url") or "").strip()
                    remote_sha = str(data.get("exe_sha256") or data.get("py_sha256") or data.get("sha256") or "").strip().lower()
                else:
                    dl_url = str(data.get("py_url") or data.get("script_url") or data.get("download_url") or data.get("url") or "").strip()
                    remote_sha = str(data.get("py_sha256") or data.get("sha256") or "").strip().lower()

            remote_tuple = self._parse_ver_tuple(remote_ver)
            has_update = (remote_tuple > cur_tuple) or bool(remote_sha and cur_sha and remote_sha != cur_sha.lower())
            self._pending_dl_url = dl_url
            self._pending_sha256 = remote_sha
            return {
                "ok": True,
                "has_update": bool(has_update),
                "current_version": cur_ver,
                "latest_version": remote_ver,
                "sha256": remote_sha,
                "changelog": changelog or f"Phiên bản v{remote_ver}",
                "mode": "exe" if is_frozen else "py",
            }
        except Exception as e:
            return {"ok": False, "error": f"Không kiểm tra được cập nhật: {e}"}

    def perform_update(self, download_url: str = "", expected_sha256: str = "") -> dict:
        """Tải và áp dụng bản cập nhật trực tiếp cho `.py` hoặc `.exe` (không lộ URL cho user).
        Hỗ trợ Cách 1 (file `olm.py` raw) cho CẢ bản `.py` lẫn bản `.exe` (qua Runtime Hot-Patch).
        """
        try:
            dl_url = str(download_url or getattr(self, "_pending_dl_url", "") or "").strip()
            if not expected_sha256:
                expected_sha256 = str(getattr(self, "_pending_sha256", "") or "").strip()
            if not dl_url:
                chk = self.check_update()
                if not chk.get("ok"):
                    return chk
                dl_url = str(getattr(self, "_pending_dl_url", "") or chk.get("download_url") or "").strip()
                if not expected_sha256:
                    expected_sha256 = str(getattr(self, "_pending_sha256", "") or chk.get("sha256") or "").strip()
            if not dl_url:
                return {"ok": False, "error": "Không tìm thấy gói cập nhật trên máy chủ."}

            status, raw, _ = self._http_fetch_bytes(dl_url, timeout=90)
            if status != 200 or not raw:
                return {"ok": False, "error": f"Tải gói cập nhật thất bại (HTTP {status})."}

            actual_sha = hashlib.sha256(raw).hexdigest().lower()
            if expected_sha256 and len(expected_sha256) == 64 and actual_sha != expected_sha256.lower():
                return {"ok": False, "error": f"Sai mã kiểm tra SHA-256 (mong đợi {expected_sha256[:10]}…, nhận {actual_sha[:10]}…)."}

            is_frozen = bool(getattr(sys, "frozen", False))
            target = self._current_target_path()

            # Kiểm tra xem gói tải về có phải mã nguồn Python `olm.py` (Cách 1) hay không
            is_py_payload = False
            code_text = ""
            if raw[:2] != b"MZ":
                try:
                    code_text = raw.decode("utf-8")
                    if "class OLMApp" in code_text and "INDEX_HTML" in code_text:
                        is_py_payload = True
                except UnicodeDecodeError:
                    is_py_payload = False

            if not is_frozen or is_py_payload:
                # Cập nhật bằng mã nguồn olm.py (Cách 1):
                # - Nếu đang chạy .py: ghi đè trực tiếp vào olm.py (kèm bản backup).
                # - Nếu đang chạy .exe: lưu vào HOTPATCH_F (~/.olm_tool_pro/runtime_patch/olm_hotpatch.py)
                #   để khi khởi động lại, .exe tự động nạp bản mới nhất mà không cần build lại .exe!
                if not code_text:
                    try:
                        code_text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        return {"ok": False, "error": "Gói tải về không phải mã nguồn UTF-8 hợp lệ."}
                if "class OLMApp" not in code_text or "INDEX_HTML" not in code_text:
                    return {"ok": False, "error": "Gói tải về không phải mã nguồn OLM Tool Pro hợp lệ."}
                try:
                    compile(code_text, "<olm_update>", "exec")
                except SyntaxError as se:
                    return {"ok": False, "error": f"Bản cập nhật có lỗi cú pháp Python: {se}"}

                m_ver = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', code_text)
                new_ver = m_ver.group(1).strip() if m_ver else APP_VERSION

                if not is_frozen:
                    backup_path = DATA_DIR / f"olm_backup_v{APP_VERSION}.py"
                    try:
                        if target.exists():
                            backup_path.write_bytes(target.read_bytes())
                    except Exception:
                        pass
                    tmp_path = target.with_suffix(".py.tmp")
                    tmp_path.write_bytes(raw)
                    os.replace(str(tmp_path), str(target))
                    out_mode = "py"
                    out_name = str(target.name)
                else:
                    HOTPATCH_DIR.mkdir(parents=True, exist_ok=True)
                    tmp_patch = HOTPATCH_F.with_suffix(".py.tmp")
                    tmp_patch.write_bytes(raw)
                    os.replace(str(tmp_patch), str(HOTPATCH_F))
                    out_mode = "exe-hotpatch"
                    out_name = str(HOTPATCH_F.name)

                _log_telemetry("AUTO_UPDATE_SUCCESS", mode=out_mode, version=new_ver)
                return {
                    "ok": True,
                    "mode": out_mode,
                    "new_version": new_ver,
                    "path": out_name,
                    "sha256": actual_sha[:12],
                    "message": f"Đã cập nhật thành công lên v{new_ver}! Bấm 'Khởi động lại' để áp dụng.",
                }
            else:
                # Cập nhật file .exe đang chạy thông qua batch self-replacer (Cách 2/3)
                if len(raw) < 500_000 or not raw[:2] == b"MZ":
                    return {"ok": False, "error": "Gói tải về không phải file thực thi Windows (.exe) hợp lệ."}
                new_exe = target.with_suffix(".new.exe")
                new_exe.write_bytes(raw)
                # Nếu cập nhật toàn bộ file .exe mới, xoá bản hotpatch cũ (nếu có) để tránh xung đột
                try:
                    if HOTPATCH_F.exists():
                        HOTPATCH_F.unlink()
                except Exception:
                    pass
                bat_path = DATA_DIR / "apply_update.bat"
                bat_content = (
                    "@echo off\r\n"
                    "chcp 65001 >nul\r\n"
                    "ping 127.0.0.1 -n 3 >nul\r\n"
                    f'move /Y "{target}" "{target}.bak" >nul 2>&1\r\n'
                    f'move /Y "{new_exe}" "{target}" >nul 2>&1\r\n'
                    f'start "" "{target}"\r\n'
                    'del "%~f0"\r\n'
                )
                bat_path.write_text(bat_content, encoding="utf-8")
                _log_telemetry("AUTO_UPDATE_SUCCESS", mode="exe")
                return {
                    "ok": True,
                    "mode": "exe",
                    "path": str(target.name),
                    "sha256": actual_sha[:12],
                    "message": "Đã tải xong bản .exe mới! Bấm 'Khởi động lại' để tự động thay thế và mở bản mới.",
                }
        except Exception as e:
            return {"ok": False, "error": f"Lỗi khi cập nhật: {e}"}

    def restart_app(self) -> dict:
        """Khởi động lại ứng dụng sau khi cập nhật."""
        try:
            is_frozen = bool(getattr(sys, "frozen", False))
            target = self._current_target_path()
            if is_frozen:
                bat_path = DATA_DIR / "apply_update.bat"
                if bat_path.exists():
                    subprocess.Popen(["cmd.exe", "/c", str(bat_path)],
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                else:
                    env = dict(os.environ)
                    env.pop("OLM_HOTPATCH_ACTIVE", None)
                    subprocess.Popen([str(target)], env=env)
            else:
                subprocess.Popen([sys.executable, str(target)], cwd=str(target.parent))
            threading.Timer(0.45, lambda: os._exit(0)).start()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}



# ═══════════════════════════════════════════════════════════════
# UI HTML
# ═══════════════════════════════════════════════════════════════
INDEX_HTML = r"""<!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no,maximum-scale=1">
<title>OLM Tool Pro v20.2 by Crayz</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400&family=JetBrains+Mono:wght@400;500;600;700;800&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
<style>
/* ══════════════════════════════════════════════════════════════════════
   MODERN TECHNICAL DESIGN SYSTEM — OLM TOOL PRO v20.2
   Sắc nét, dễ nhìn tuyệt đối, font chữ Be Vietnam Pro + Space Grotesk + JetBrains Mono
   ══════════════════════════════════════════════════════════════════════ */
:root{
  /* Bảng màu Dark Technical Terminal */
  --bg:#07090d;--bg2:#0b0f17;--bg3:#101622;
  /* Bề mặt khung vuông sắc nét */
  --glass:rgba(14,19,29,.94);
  --glass2:rgba(19,26,39,.96);
  --glass3:rgba(26,35,52,.98);
  --glass-hi:rgba(255,255,255,.07);
  --glass-lo:rgba(255,255,255,.025);
  --stroke:#222f43;
  --stroke2:#334663;
  /* Tương thích biến hệ thống */
  --panel:var(--glass);--panel2:var(--glass2);--panel3:var(--glass3);
  --panel4:rgba(34,46,68,.98);
  --bd:var(--stroke);--bd2:var(--stroke2);--bd3:#476085;
  /* Chữ độ tương phản cao */
  --tx:#f1f5f9;--tx2:#94a3b8;--dim:#64748b;--mute:#475569;
  /* Màu nhấn kỹ thuật */
  --p1:#00f59b;--p2:#38bdf8;--p3:#22d3ee;--p4:#fbbf24;
  --ok:#10b981;--ok2:#00f59b;--warn:#f59e0b;--err:#ef4444;--err2:#ff5c77;
  --grad:linear-gradient(135deg,#00f59b 0%,#0ea5e9 100%);
  --grad3:linear-gradient(120deg,#00f59b 0%,#38bdf8 52%,#22d3ee 100%);
  --grad-soft:linear-gradient(135deg,rgba(0,245,155,.12),rgba(56,189,248,.12));
  /* Đổ bóng khối vuông sắc nét */
  --shadow1:2px 2px 0 rgba(0,0,0,.65);
  --shadow2:4px 4px 0 rgba(0,0,0,.72);
  --shadow3:6px 6px 0 rgba(0,0,0,.82);
  --glow1:3px 3px 0 rgba(0,245,155,.28);
  --blur:blur(16px) saturate(180%);
  --blur-lg:blur(24px) saturate(190%);
  --r-sm:2px;--r:4px;--r-lg:6px;--r-xl:8px;
  --spring:cubic-bezier(.22,1.2,.36,1);
  --ease:cubic-bezier(.4,0,.2,1);
  /* Bộ Font hiện đại siêu rõ nét (Be Vietnam Pro + Space Grotesk + JetBrains Mono) */
  --font-sans:'Be Vietnam Pro','Segoe UI',system-ui,-apple-system,Roboto,sans-serif;
  --font-head:'Space Grotesk','Be Vietnam Pro','Segoe UI',sans-serif;
  --font-mono:'JetBrains Mono','Cascadia Mono','SF Mono',Consolas,monospace;
  --font-pixel:var(--font-head);
  --font-ui:var(--font-sans);
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{
  height:100%;width:100%;background:var(--bg);color:var(--tx);
  font-family:var(--font-sans);
  font-size:13.5px;overflow:hidden;position:fixed;inset:0;
  user-select:none;-webkit-user-select:none;-webkit-touch-callout:none;
  touch-action:manipulation;letter-spacing:0;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
}
/* ── LƯỚI PIXEL KỸ THUẬT NỀN ── */
body::before{
  content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
  background-image:
    linear-gradient(rgba(56,189,248,.03) 1px,transparent 1px),
    linear-gradient(90deg,rgba(56,189,248,.03) 1px,transparent 1px),
    radial-gradient(60% 50% at 15% 10%,rgba(0,245,155,.06),transparent 70%),
    radial-gradient(60% 50% at 85% 90%,rgba(56,189,248,.06),transparent 70%);
  background-size:24px 24px,24px 24px,100% 100%,100% 100%;
  animation:auroraDrift 60s linear infinite alternate;
}
@keyframes auroraDrift{
  0%{background-position:0 0,0 0,0 0,0 0}
  100%{background-position:24px 24px,24px 24px,0 0,0 0}
}
/* ── TEXTURE MICRO-NOISE ── */
body::after{
  content:'';position:fixed;inset:0;pointer-events:none;z-index:1;opacity:.22;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='140' height='140'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3'/></filter><rect width='140' height='140' filter='url(%23n)' opacity='.35'/></svg>");
  mix-blend-mode:overlay;
}
input,textarea,button,select{font-family:inherit}
input,textarea{-webkit-user-select:text;user-select:text}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:var(--bg2);border-left:1px solid var(--stroke)}
::-webkit-scrollbar-thumb{
  background:var(--stroke2);border-radius:0;border:2px solid var(--bg2)}
::-webkit-scrollbar-thumb:hover{background:var(--p2)}

#app{position:relative;z-index:2;display:flex;flex-direction:column;
     height:100vh;width:100vw;overflow:hidden}

/* ══════════════════════════════════════════════════════════════════════
   KHUNG PANEL VUÔNG DÙNG CHUNG
   ══════════════════════════════════════════════════════════════════════ */
.topbar,.chip,.login-card,.list-panel,.log-panel,.modal-box,.answers-modal,
.card,.lic-info,.progress-bar,.steps,.config-bar,.toolbar,.exam-banner{
  background:var(--glass);
  -webkit-backdrop-filter:var(--blur);backdrop-filter:var(--blur);
  border:1px solid var(--stroke);
  box-shadow:var(--shadow1),0 1px 0 var(--glass-hi) inset;
  position:relative;
}

/* ══════════════════════════════════════════════════════════════════════
   TOPBAR — THANH ĐIỀU KHIỂN ĐẦU TRANG
   ══════════════════════════════════════════════════════════════════════ */
.topbar{
  display:flex;align-items:center;gap:12px;padding:0 16px;
  border-radius:0;border-left:0;border-right:0;border-top:0;
  border-bottom:2px solid var(--stroke2);
  z-index:20;min-height:54px;max-height:54px;flex-shrink:0;
  background:var(--bg2);
  -webkit-app-region:drag;
}
.topbar::after{
  content:'';position:absolute;left:0;right:0;bottom:-2px;height:2px;
  background:linear-gradient(90deg,var(--p1),var(--p2),transparent 85%);
  opacity:.9;
}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:15px;flex-shrink:0}
.brand-icon{
  width:32px;height:32px;border-radius:var(--r-sm);background:var(--p1);
  display:grid;place-items:center;color:#04120b;font-size:16px;font-weight:800;flex-shrink:0;
  border:1px solid #fff;box-shadow:2px 2px 0 var(--p2);
  font-family:var(--font-pixel);position:relative;overflow:hidden;
}
@keyframes iconBreathe{
  0%,100%{transform:translateY(0)}
  50%{transform:translateY(-1px)}
}
.brand-icon::after{
  content:'';position:absolute;inset:0;
  background:linear-gradient(115deg,transparent 30%,rgba(255,255,255,.45) 50%,transparent 70%);
  transform:translateX(-120%);animation:sheen 6s ease-in-out infinite;
}
@keyframes sheen{0%,75%{transform:translateX(-120%)}100%{transform:translateX(120%)}}
.brand-name{
  font-family:var(--font-pixel);font-size:17px;font-weight:700;
  color:var(--tx);letter-spacing:.04em;text-transform:uppercase;white-space:nowrap;
}
@keyframes gradFlow{0%,100%{background-position:0% 50%}50%{background-position:100% 50%}}
.brand-ver{
  font-size:10.5px;color:var(--p1);font-weight:700;padding:3px 8px;
  border:1px solid rgba(0,245,155,.45);border-radius:2px;background:rgba(0,245,155,.08);
  font-family:var(--font-pixel);letter-spacing:.05em;flex-shrink:0;
}
.spacer{flex:1;min-width:0}
.topbar-actions{display:flex;gap:6px;align-items:center;flex-wrap:nowrap;
                -webkit-app-region:no-drag;flex-shrink:0}

/* ══════════════════════════════════════════════════════════════════════
   CHIP TRẠNG THÁI PIXEL
   ══════════════════════════════════════════════════════════════════════ */
.chip{
  display:flex;align-items:center;gap:7px;padding:6px 11px;border-radius:var(--r-sm);
  font-size:11.5px;color:var(--tx2);font-weight:600;white-space:nowrap;
  background:var(--bg3);border:1px solid var(--stroke2);
  font-family:var(--font-mono);transition:all .15s var(--ease);
}
.chip:hover{border-color:var(--p2);color:var(--tx)}
.chip .dot{
  width:8px;height:8px;border-radius:1px;background:var(--err);flex-shrink:0;
}
.chip .dot.on{background:var(--ok2);box-shadow:0 0 8px rgba(0,245,155,.65);
  animation:pulseDot 2.2s steps(2,end) infinite}
@keyframes pulseDot{0%,100%{opacity:1}50%{opacity:.55}}
.chip.warn{border-color:rgba(245,158,11,.55)}
.chip.warn .dot{background:var(--warn)}

/* ══════════════════════════════════════════════════════════════════════
   NÚT BẤM VUÔNG PIXEL (CRISP SQUARE BUTTONS)
   ══════════════════════════════════════════════════════════════════════ */
.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:6px;
  padding:8px 13px;border-radius:var(--r-sm);border:1px solid var(--stroke2);
  background:var(--glass2);color:var(--tx);font-size:12px;font-weight:700;
  font-family:var(--font-pixel);letter-spacing:.03em;
  cursor:pointer;white-space:nowrap;outline:none;position:relative;
  overflow:hidden;isolation:isolate;
  box-shadow:2px 2px 0 rgba(0,0,0,.75);
  transition:transform .1s var(--ease),box-shadow .1s var(--ease),
             border-color .15s var(--ease),background .15s var(--ease);
}
.btn:hover:not(:disabled){
  transform:translate(-1px,-1px);border-color:var(--p2);
  background:var(--glass3);
  box-shadow:3px 3px 0 rgba(0,0,0,.85);
}
.btn:active:not(:disabled){
  transform:translate(1px,1px);
  box-shadow:1px 1px 0 rgba(0,0,0,.75);
}
.btn:focus-visible{outline:2px solid var(--p1);outline-offset:2px}
.btn:disabled{opacity:.42;cursor:not-allowed;transform:none!important}
.btn::after{
  content:'';position:absolute;inset:0;z-index:-1;opacity:0;
  background:linear-gradient(115deg,transparent 32%,rgba(255,255,255,.18) 50%,transparent 68%);
  transform:translateX(-110%);transition:opacity .15s;
}
.btn:hover:not(:disabled)::after{opacity:1;animation:btnSheen .65s var(--ease)}
@keyframes btnSheen{to{transform:translateX(110%)}}
.btn-primary{
  background:var(--p1);border-color:#00ffaa;color:#04120b;font-weight:700;
  box-shadow:2px 2px 0 #059669;
}
.btn-primary:hover:not(:disabled){
  background:#22ffad;border-color:#fff;color:#020a06;
  box-shadow:3px 3px 0 #059669;
}
.btn-danger{
  background:#e11d48;border-color:#fb7185;color:#fff;font-weight:700;
  box-shadow:2px 2px 0 #881337;
}
.btn-danger:hover:not(:disabled){
  background:#f43f5e;border-color:#fff;box-shadow:3px 3px 0 #881337;
}
.btn-warning{
  background:#d97706;border-color:#fbbf24;color:#fff;font-weight:700;
  box-shadow:2px 2px 0 #78350f;
}
.btn-warning:hover:not(:disabled){
  background:#f59e0b;border-color:#fff;color:#090601;box-shadow:3px 3px 0 #78350f;
}
.btn-ghost{
  background:var(--bg3);border:1px solid var(--stroke2);color:var(--tx2);
}
.btn-ghost:hover:not(:disabled){color:var(--tx);border-color:var(--p2);background:var(--glass2)}
.btn-sm{padding:6px 11px;font-size:11.5px;border-radius:var(--r-sm);gap:5px}
.btn-lg{padding:12px 22px;font-size:13.5px;border-radius:var(--r);gap:8px}

/* ══════════════════════════════════════════════════════════════════════
   BỐ CỤC ĐĂNG NHẬP
   ══════════════════════════════════════════════════════════════════════ */
.login-wrap{
  flex:1;display:flex;align-items:flex-start;justify-content:center;
  padding:28px 20px 80px;overflow-y:auto;overflow-x:hidden;
  position:relative;min-height:0;
}
.blob{display:none}
.blob.b1,.blob.b2{display:none}
@keyframes blobFloat{0%,100%{transform:none}}

/* ══════════════════════════════════════════════════════════════════════
   THẺ ĐĂNG NHẬP VUÔNG (LOGIN CARD)
   ══════════════════════════════════════════════════════════════════════ */
.login-card{
  width:100%;max-width:520px;border-radius:var(--r);padding:26px 26px 24px;
  background:var(--bg2);border:2px solid var(--stroke2);
  box-shadow:var(--shadow3);
  position:relative;z-index:2;overflow:hidden;
  animation:cardIn .25s var(--ease) both;
}
@keyframes cardIn{
  from{opacity:0;transform:translateY(8px)}
  to{opacity:1;transform:none}
}
.login-card::before{
  content:'';position:absolute;top:0;left:0;right:0;height:3px;
  background:linear-gradient(90deg,var(--p1),var(--p2));
}
.login-card h1,.login-card h2,.login-card .title{
  font-family:var(--font-pixel);font-size:20px;font-weight:700;
  letter-spacing:.03em;margin-bottom:6px;color:var(--tx);
  text-transform:uppercase;display:flex;align-items:center;gap:9px;
}
.sub,.login-sub{color:var(--tx2);font-size:12.5px;line-height:1.65;margin-bottom:18px}

/* ══════════════════════════════════════════════════════════════════════
   Ô NHẬP LIỆU KỸ THUẬT
   ══════════════════════════════════════════════════════════════════════ */
.field{margin-bottom:14px}
.field .lbl,.lbl,label{
  font-family:var(--font-pixel);font-size:11.5px;font-weight:600;color:var(--tx2);
  letter-spacing:.04em;
}
.field label{display:block;margin-bottom:6px;text-transform:uppercase;color:var(--p2)}
input[type=text],input[type=number],input[type=password],textarea,.modal-input,
.num-input,.field input,.field textarea,.field select,select{
  width:100%;padding:9px 12px;border-radius:var(--r-sm);
  background:var(--bg);border:1px solid var(--stroke2);color:var(--tx);
  font-family:var(--font-mono);font-size:12.5px;outline:none;
  box-shadow:inset 2px 2px 0 rgba(0,0,0,.5);
  transition:border-color .15s var(--ease),box-shadow .15s var(--ease);
}
input::placeholder,textarea::placeholder{color:var(--mute)}
input:focus,textarea:focus,select:focus{
  border-color:var(--p1);background:#05080c;
  box-shadow:0 0 0 2px rgba(0,245,155,.18),inset 2px 2px 0 rgba(0,0,0,.5);
}
select{cursor:pointer;appearance:none;
  background-image:linear-gradient(45deg,transparent 50%,var(--p1) 50%),
                   linear-gradient(135deg,var(--p1) 50%,transparent 50%);
  background-position:calc(100% - 16px) 50%,calc(100% - 11px) 50%;
  background-size:5px 5px,5px 5px;background-repeat:no-repeat;padding-right:30px}

/* ══════════════════════════════════════════════════════════════════════
   KHỐI THÔNG TIN LICENSE
   ══════════════════════════════════════════════════════════════════════ */
.lic-info{border-radius:var(--r-sm);padding:13px 15px;margin-bottom:14px;
  background:var(--bg3);border:1px solid var(--stroke2)}
.lic-info-row{
  display:flex;justify-content:space-between;gap:12px;align-items:center;
  padding:6px 0;font-size:12px;border-bottom:1px dashed var(--stroke);
}
.lic-info-row:last-child{border-bottom:none}
.lic-info-row span:first-child{color:var(--dim);font-weight:600;font-family:var(--font-pixel)}
.lic-info-row span:last-child{color:var(--tx);font-weight:700;text-align:right;
  word-break:break-all;font-family:var(--font-mono);font-size:11.5px}
.lic-live{border-radius:var(--r-sm);padding:12px 15px;margin-bottom:14px;
  background:rgba(0,245,155,.07);border:1px solid rgba(0,245,155,.4);
  border-left:4px solid var(--p1);display:flex;flex-direction:column;gap:3px}
.lic-live-lbl{font-family:var(--font-pixel);font-size:11px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--p1);font-weight:700}
.lic-live-key{font-family:var(--font-mono);font-size:14.5px;
  font-weight:800;color:#ecfdf5;letter-spacing:.04em;word-break:break-all}
.lic-live-timer{font-family:var(--font-mono);font-size:11.5px;color:var(--tx2)}

/* ══════════════════════════════════════════════════════════════════════
   BƯỚC HƯỚNG DẪN
   ══════════════════════════════════════════════════════════════════════ */
.steps{border-radius:var(--r-sm);padding:6px 14px;margin-bottom:14px;
  background:var(--bg3);border:1px solid var(--stroke2)}
.step{display:flex;gap:11px;align-items:flex-start;padding:10px 0;
  border-bottom:1px dashed var(--stroke);font-size:12.5px}
.step:last-child{border-bottom:none}
.step-num{
  flex-shrink:0;width:22px;height:22px;border-radius:2px;display:grid;
  place-items:center;font-family:var(--font-pixel);font-size:11.5px;font-weight:700;
  color:#04120b;background:var(--p1);
}
.step-text{flex:1;color:var(--tx2);line-height:1.6}
.step-text b{color:var(--tx)}
.spin{
  width:14px;height:14px;border-radius:2px;flex-shrink:0;display:inline-block;
  border:2px solid rgba(255,255,255,.2);border-top-color:var(--p1);
  animation:spin .65s steps(8,end) infinite;
}
.spinner-lg{width:32px;height:32px;border-width:3px;border-radius:3px;
  border-top-color:var(--p1);border-right-color:var(--p2)}
@keyframes spin{to{transform:rotate(360deg)}}

/* ══════════════════════════════════════════════════════════════════════
   DASHBOARD: TOOLBAR + FILTER BAR + SCROLLABLE LIST + FIXED LOG PANEL
   ══════════════════════════════════════════════════════════════════════ */
.content{
  flex:1 1 0%;min-height:0;display:flex;flex-direction:column;overflow:hidden;
}
.config-bar,.toolbar{
  display:flex;gap:10px;align-items:center;flex-wrap:wrap;
  padding:9px 16px;border-radius:0;
  border-left:0;border-right:0;border-top:0;
  border-bottom:1px solid var(--stroke2);flex-shrink:0;
  background:var(--bg2);
}
.config-bar label{
  display:inline-flex;align-items:center;gap:6px;
  font-family:var(--font-pixel);font-size:12px;color:var(--tx2);white-space:nowrap;
}
.config-bar select{width:auto;min-width:165px;padding:6px 28px 6px 10px;font-size:12px}
.config-bar input[type=number]{width:74px;padding:6px 8px;font-size:12px;text-align:center}

/* Thanh tìm kiếm & bộ lọc bài tập */
.filter-bar{
  display:flex;gap:10px;align-items:center;flex-wrap:wrap;
  padding:8px 16px;background:var(--bg3);
  border-bottom:1px solid var(--stroke2);flex-shrink:0;
}
.search-box{
  position:relative;flex:1 1 240px;max-width:420px;display:flex;align-items:center;
}
.search-box input{
  width:100%;padding:6px 11px 6px 28px;font-size:12px;border-radius:var(--r-sm);
  background:var(--bg);border:1px solid var(--stroke2);color:var(--tx);
}
.search-box::before{
  content:'⌕';position:absolute;left:9px;color:var(--p2);font-size:13px;pointer-events:none;
}
.filter-tabs{display:flex;gap:4px;align-items:center}
.filter-tab{
  padding:5px 11px;font-family:var(--font-pixel);font-size:11.5px;font-weight:700;
  border:1px solid var(--stroke2);background:var(--bg2);color:var(--tx2);
  border-radius:2px;cursor:pointer;letter-spacing:.03em;transition:all .12s;
}
.filter-tab:hover{color:var(--tx);border-color:var(--p2)}
.filter-tab.active{
  background:rgba(0,245,155,.14);color:var(--p1);border-color:var(--p1);
  box-shadow:2px 2px 0 rgba(0,245,155,.22);
}
.stat-pills{display:flex;gap:8px;align-items:center;margin-left:auto;
  font-family:var(--font-pixel);font-size:11.5px}
.stat-pill{
  padding:4px 9px;border:1px solid var(--stroke2);background:var(--bg2);
  border-radius:2px;color:var(--tx2);
}
.stat-pill b{color:var(--tx);margin-left:3px}
.stat-pill.todo b{color:var(--p4)}
.stat-pill.done b{color:var(--p1)}

/* KHỐI CHÍNH: đảm bảo .list-panel cuộn mượt khi có nhiều bài và .log-panel luôn hiển thị */
.main{
  flex:1 1 0%;min-height:0;display:flex;flex-direction:row;
  overflow:hidden;position:relative;
}
.list-panel{
  flex:1 1 0%;min-width:0;min-height:0;height:100%;
  overflow-y:auto;overflow-x:hidden;
  padding:14px 16px 32px;
  border-radius:0;border:none;box-shadow:none;background:transparent;
  -webkit-backdrop-filter:none;backdrop-filter:none;
  display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));
  grid-auto-rows:max-content;
  gap:12px;align-content:start;
}

/* THẺ BÀI TẬP PIXEL VUÔNG */
.card{
  border-radius:var(--r);padding:13px 14px;
  background:var(--bg2);border:1px solid var(--stroke2);
  border-left:4px solid var(--p2);
  box-shadow:var(--shadow2);
  display:flex;flex-direction:column;gap:9px;
  min-height:132px;flex-shrink:0;
  transition:transform .12s var(--ease),border-color .15s var(--ease),box-shadow .15s var(--ease);
  animation:cardIn .2s var(--ease) both;
}
.card:hover{
  transform:translate(-1px,-1px);border-color:var(--p2);
  box-shadow:5px 5px 0 rgba(0,0,0,.85);
}
.card.done{
  border-color:rgba(0,245,155,.45);border-left:4px solid var(--p1);
  background:linear-gradient(180deg,rgba(0,245,155,.05),var(--bg2));
}
.card.working{
  border-color:var(--p4);border-left:4px solid var(--p4);
}
.card.fail{
  border-color:var(--err);border-left:4px solid var(--err);
}
.card-head{display:flex;align-items:flex-start;gap:8px;flex-wrap:wrap}
.card-idx{
  min-width:26px;height:24px;padding:0 6px;border-radius:2px;
  display:inline-grid;place-items:center;
  font-family:var(--font-pixel);font-size:11.5px;font-weight:700;
  color:#04120b;background:var(--p2);flex-shrink:0;
}
.card.done .card-idx{background:var(--p1)}
.card-id{
  font-family:var(--font-mono);font-size:10.5px;color:var(--dim);
  background:var(--bg);border:1px solid var(--stroke);padding:2px 6px;
  border-radius:2px;margin-left:auto;
}
.card-title{
  font-family:var(--font-ui);font-size:13.5px;font-weight:700;line-height:1.45;
  color:var(--tx);word-break:break-word;flex:1 1 180px;
}
.card-status{
  font-family:var(--font-mono);font-size:11.5px;color:var(--tx2);
  min-height:16px;padding-top:2px;border-top:1px dashed var(--stroke);
}
.card-status:empty{display:none}
.card-status.ok{color:var(--p1)}
.card-status.err{color:var(--err2)}
.card-status.warn{color:var(--p4)}
.card-status.working{color:var(--p2)}
.badge-done,.type-badge{
  font-family:var(--font-pixel);font-size:10.5px;font-weight:700;
  padding:2px 7px;border-radius:2px;letter-spacing:.03em;
  border:1px solid var(--stroke2);background:var(--bg3);color:var(--tx2);
}
.type-badge.pdf{color:#fda4af;border-color:rgba(244,63,94,.45);background:rgba(244,63,94,.1)}
.type-badge.flex{color:#7dd3fc;border-color:rgba(56,189,248,.45);background:rgba(56,189,248,.1)}
.type-badge.good{color:var(--p1);border-color:rgba(0,245,155,.45);background:rgba(0,245,155,.1)}
.badge-done{
  color:#04120b;background:var(--p1);border-color:var(--p1);
}
.card-actions{
  display:flex;gap:5px;align-items:center;flex-wrap:wrap;
  padding-top:6px;border-top:1px solid var(--stroke);margin-top:auto;
}
.card-actions label{font-size:11px;color:var(--dim);margin-right:1px}
.card-actions .num-input{width:56px;padding:4px 6px;font-size:11.5px;text-align:center}
.card-types{
  font-family:var(--font-mono);font-size:11px;color:var(--tx2);
  display:flex;gap:5px;align-items:center;flex-wrap:wrap;
}
.empty-state{
  grid-column:1/-1;text-align:center;padding:54px 24px;color:var(--tx2);
  font-size:13.5px;line-height:1.9;background:var(--bg2);
  border:1px dashed var(--stroke2);border-radius:var(--r);
}
.empty-state .icon{font-size:28px;margin-bottom:8px;font-family:var(--font-pixel);color:var(--p2)}
.empty-state b{color:var(--p1)}

/* ══════════════════════════════════════════════════════════════════════
   LOG PANEL — KHUNG NHẬT KÝ CỐ ĐỊNH BÊN PHẢI (LUÔN HIỂN THỊ RÕ RÀNG)
   ══════════════════════════════════════════════════════════════════════ */
.log-panel{
  border-radius:0;border-top:0;border-right:0;border-bottom:0;
  border-left:2px solid var(--stroke2);
  flex:0 0 390px;width:390px;min-width:320px;max-width:440px;
  height:100%;min-height:0;
  display:flex;flex-direction:column;flex-shrink:0;
  background:#05070b;z-index:10;
}
.log-header{
  display:flex;align-items:center;gap:8px;padding:6px 16px;
  border-bottom:1px solid var(--stroke);font-size:11.5px;color:var(--p1);
  font-family:var(--font-pixel);font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;flex-shrink:0;background:var(--bg2);
}
.log-header .dot{
  width:8px;height:8px;background:var(--p1);border-radius:1px;
  box-shadow:0 0 6px var(--p1);
}
.log-header .clear{
  margin-left:auto;background:var(--bg3);border:1px solid var(--stroke2);
  color:var(--tx2);font-family:var(--font-pixel);font-size:10.5px;
  padding:3px 10px;border-radius:2px;cursor:pointer;font-weight:700;
}
.log-header .clear:hover{color:var(--tx);border-color:var(--p1)}
.log-body{
  flex:1 1 0%;min-height:0;overflow-y:auto;padding:8px 16px;
  font-size:12px;line-height:1.75;
  font-family:var(--font-mono);color:var(--tx2);
}
.log-line{
  display:flex;gap:10px;padding:2px 0;border-bottom:1px dotted rgba(255,255,255,.03);
  animation:logIn .15s var(--ease) both;
}
@keyframes logIn{from{opacity:0;transform:translateX(-4px)}to{opacity:1;transform:none}}
.log-line .ts{color:var(--dim);flex-shrink:0;font-size:11px;font-weight:600}
.log-line .msg{margin:0;min-height:0;font-size:12px;line-height:1.6;word-break:break-word}
.log-line.ok,.log-line.ok .msg{color:var(--ok2);font-weight:600}
.log-line.err,.log-line.err .msg{color:var(--err2);font-weight:600}
.log-line.warn,.log-line.warn .msg{color:var(--p4)}
.log-line.work,.log-line.work .msg{color:var(--p2);font-weight:600}
.log-line.info,.log-line.info .msg{color:var(--tx)}

/* ══════════════════════════════════════════════════════════════════════
   THANH TIẾN TRÌNH
   ══════════════════════════════════════════════════════════════════════ */
.progress-bar{
  height:5px;border-radius:0;overflow:hidden;margin:0;border:none;
  box-shadow:none;background:var(--bg3);flex-shrink:0;
}
.progress-fill{
  height:100%;width:0;border-radius:0;background:var(--grad3);
  transition:width .25s var(--ease);
}

/* ══════════════════════════════════════════════════════════════════════
   MODAL & VIEWER ĐÁP ÁN
   ══════════════════════════════════════════════════════════════════════ */
.modal,.overlay{
  position:fixed;inset:0;z-index:900;display:none;
  align-items:center;justify-content:center;padding:24px;
  background:rgba(3,5,9,.78);
  -webkit-backdrop-filter:blur(8px) saturate(140%);
  backdrop-filter:blur(8px) saturate(140%);
}
.modal.show,.overlay.show{display:flex;animation:fadeIn .15s var(--ease)}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
.modal-box{
  width:100%;max-width:520px;border-radius:var(--r);padding:22px;
  background:var(--bg2);border:2px solid var(--stroke2);
  box-shadow:var(--shadow3);
  animation:modalIn .2s var(--ease) both;position:relative;overflow:hidden;
}
@keyframes modalIn{
  from{opacity:0;transform:translateY(12px)}
  to{opacity:1;transform:none}
}
.modal-box::before{
  content:'';position:absolute;top:0;left:0;right:0;height:3px;
  background:linear-gradient(90deg,var(--p1),var(--p2));
}
.modal-box h3,.modal-box .title{
  font-family:var(--font-pixel);font-size:16.5px;font-weight:700;
  margin-bottom:8px;display:flex;align-items:center;gap:8px;text-transform:uppercase;
}
.modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}
.modal-input{margin-bottom:4px}

.answers-modal{
  position:fixed;inset:3vh 2vw;z-index:950;display:none;
  width:96vw;max-width:1220px;height:94vh;margin:auto;
  border-radius:var(--r);padding:0;flex-direction:column;overflow:hidden;
  background:var(--bg2);border:2px solid var(--stroke2);
  box-shadow:var(--shadow3);
}
.answers-modal.show{display:flex;animation:modalIn .2s var(--ease) both}
.answers-modal::before{
  content:'';position:absolute;top:0;left:0;right:0;height:3px;
  background:linear-gradient(90deg,var(--p1),var(--p2));
}
.answers-modal-head{
  display:flex;align-items:center;gap:11px;padding:12px 18px;
  border-bottom:2px solid var(--stroke2);flex-shrink:0;flex-wrap:wrap;
  background:var(--bg3);
}
.answers-modal-head .title{
  font-family:var(--font-pixel);font-size:15px;font-weight:700;
  color:var(--p1);display:flex;align-items:center;gap:9px;min-width:0;
}
.answers-modal-head .sub{margin:0;font-size:12px;color:var(--tx2)}
.answers-modal-body{flex:1;min-height:0;padding:0}
.answers-modal-body iframe{width:100%;height:100%;border:none;background:#07090d}

/* ══════════════════════════════════════════════════════════════════════
   TOAST NOTIFICATION
   ══════════════════════════════════════════════════════════════════════ */
.toast-wrap{position:fixed;bottom:20px;right:20px;z-index:1000;display:flex;
  flex-direction:column;gap:8px;pointer-events:none}
.toast{
  pointer-events:auto;min-width:250px;max-width:390px;padding:11px 15px;
  border-radius:var(--r-sm);font-family:var(--font-mono);font-size:12px;
  font-weight:600;color:var(--tx);background:var(--bg2);
  border:1px solid var(--stroke2);box-shadow:var(--shadow2);
  animation:toastIn .22s var(--ease) both;
}
@keyframes toastIn{
  from{opacity:0;transform:translateX(20px)}
  to{opacity:1;transform:none}
}
.toast.ok{border-left:4px solid var(--ok2)}
.toast.err{border-left:4px solid var(--err)}
.toast.warn{border-left:4px solid var(--warn)}
.toast.info{border-left:4px solid var(--p2)}

/* ══════════════════════════════════════════════════════════════════════
   NHÃN TRẠNG THÁI
   ══════════════════════════════════════════════════════════════════════ */
.hidden{display:none!important}
.highlight{background:rgba(0,245,155,.15);padding:1px 6px;border-radius:2px;color:var(--p1)}
.ok,.good{color:var(--ok2)}
.err,.bad{color:var(--err2)}
.warn{color:var(--p4)}
.info{color:var(--p2)}
.msg{font-size:12px;margin-top:8px;min-height:17px;line-height:1.6}
.msg.err{color:var(--err2)}
.msg.ok{color:var(--ok2)}
.val{font-family:var(--font-mono);color:var(--tx);word-break:break-all;font-size:12px}
.icon{display:inline-flex;align-items:center;justify-content:center}

/* ══════════════════════════════════════════════════════════════════════
   MÀN KHOÁ LICENSE (v20.0)
   ══════════════════════════════════════════════════════════════════════ */
#licGate.lg-gate{
  position:fixed;inset:0;z-index:2000;display:none;
  align-items:center;justify-content:center;padding:24px;
  background:rgba(4,6,10,.92);
  -webkit-backdrop-filter:blur(14px) saturate(150%);
  backdrop-filter:blur(14px) saturate(150%);
}
#licGate.lg-gate.show{display:flex;animation:fadeIn .2s var(--ease)}
.lg-gate-card{
  width:100%;max-width:460px;border-radius:var(--r);padding:28px 26px 24px;
  text-align:center;position:relative;overflow:hidden;
  background:var(--bg2);border:2px solid var(--stroke2);
  -webkit-backdrop-filter:var(--blur-lg);backdrop-filter:var(--blur-lg);
  box-shadow:var(--shadow3);animation:gateIn .25s var(--ease) both;
}
@keyframes gateIn{
  from{opacity:0;transform:translateY(14px)}
  to{opacity:1;transform:none}
}
.lg-gate-card::before{
  content:'';position:absolute;top:0;left:0;right:0;height:3px;
  background:linear-gradient(90deg,var(--p1),var(--p2));
}
.lg-lock{
  width:64px;height:64px;margin:0 auto 14px;border-radius:var(--r);
  display:grid;place-items:center;font-size:28px;position:relative;
  background:var(--bg3);border:2px solid var(--p1);
  box-shadow:3px 3px 0 rgba(0,245,155,.25);
  font-family:var(--font-pixel);color:var(--p1);
}
@keyframes lockFloat{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}
.lg-gate-card h2{
  font-family:var(--font-pixel);font-size:20px;font-weight:700;
  letter-spacing:.04em;margin-bottom:8px;color:var(--p1);text-transform:uppercase;
}
.lg-gate-card p{font-size:12.5px;color:var(--tx2);line-height:1.7;margin-bottom:18px}
.lg-hwid{
  display:flex;align-items:center;gap:9px;justify-content:center;
  padding:9px 12px;border-radius:var(--r-sm);margin-bottom:14px;
  background:var(--bg);border:1px dashed var(--stroke2);
  font-family:var(--font-mono);font-size:11.5px;color:var(--tx2);
}
.lg-hwid b{color:var(--p2);letter-spacing:.04em}
.lg-actions{display:flex;flex-direction:column;gap:9px;margin-top:4px}
.lg-row{display:flex;gap:9px}
.lg-row > *{flex:1}
.lg-free{
  background:var(--p2)!important;border-color:#7dd3fc!important;
  color:#04121d!important;font-weight:800!important;
  box-shadow:2px 2px 0 #0284c7!important;
}
.lg-free:hover:not(:disabled){
  background:#7dd3fc!important;box-shadow:3px 3px 0 #0284c7!important;
}
.lg-or{
  display:flex;align-items:center;gap:12px;margin:14px 0 10px;
  font-family:var(--font-pixel);font-size:11px;color:var(--dim);
  letter-spacing:.12em;font-weight:700;
}
.lg-or::before,.lg-or::after{
  content:'';flex:1;height:1px;background:var(--stroke2);
}
.lg-msg{font-size:11.5px;min-height:17px;margin-top:10px;line-height:1.6;
  color:var(--err2);font-weight:600}
.lg-msg.ok{color:var(--ok2)}
.lg-foot{font-size:11px;color:var(--dim);margin-top:14px;line-height:1.75}
.lg-foot a{color:var(--p2);text-decoration:none;font-weight:700}
.lg-foot a:hover{text-decoration:underline}
.lg-shake{animation:shake .35s var(--ease)}
@keyframes shake{
  0%,100%{transform:translateX(0)}20%{transform:translateX(-8px)}
  40%{transform:translateX(8px)}60%{transform:translateX(-5px)}
  80%{transform:translateX(5px)}
}

@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{
    animation-duration:.01ms!important;animation-iteration-count:1!important;
    transition-duration:.01ms!important;
  }
}
@media (max-width:1080px){
  .list-panel{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
}
</style></head><body>

<div id="app">
  <div class="topbar">
    <div class="brand">
      <div class="brand-icon">▣</div>
      <span class="brand-name">OLM Tool Pro</span>
      <span class="brand-ver">v20.2 by Crayz</span>
    </div>
    <div class="spacer"></div>
    <div class="topbar-actions">
      <div class="chip" id="licChip" style="display:none" title="Nhấn để mở License">
        <span class="dot on"></span>
        <span id="licChipText">License</span>
      </div>
      <div class="chip">
        <span class="dot" id="statusDot"></span>
        <span id="statusText">Chưa kết nối</span>
      </div>
      <button class="btn btn-ghost btn-sm hidden" id="btnInstallChromium">[+] CHROMIUM</button>
      <button class="btn btn-ghost btn-sm hidden" id="btnOpenChrome">[🌐] CHROME</button>
      <button class="btn btn-ghost btn-sm hidden" id="btnReGrab">[🍪] COOKIES</button>
      <button class="btn btn-ghost btn-sm" data-update-btn title="Kiểm tra & Cập nhật tự động">[⬆] UPDATE</button>
      <button class="btn btn-ghost btn-sm" id="btnLicense" title="Quản lý License">[KEY]</button>
      <button class="btn btn-ghost btn-sm hidden" id="btnLogout" title="Đăng xuất">[EXIT]</button>
    </div>
  </div>

  <div class="login-wrap" id="loginView">
    <div class="blob b1"></div><div class="blob b2"></div>
    <div class="login-card">
      <h1 id="loginTitle">CHÀO MỪNG</h1>
      <p class="sub" id="loginSub">Đang kiểm tra License...</p>

      <div id="licLiveBox" class="lic-live hidden">
        <div class="lic-live-lbl">// LICENSE ĐANG HOẠT ĐỘNG</div>
        <span class="lic-live-key" id="licLiveKey">—</span>
        <span class="lic-live-timer" id="licLiveTimer">--:--:--</span>
      </div>

      <div id="licRequireBox" class="hidden">
        <div class="lic-info" id="licRequireInfo"></div>
        <div class="field">
          <label>Nhập License Key</label>
          <input type="text" class="modal-input" id="licQuickKey"
            placeholder="OLM-XXXX-XXXX-XXXX" autofocus>
        </div>
        <div id="licQuickMsg"></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
          <button class="btn btn-primary btn-lg" id="btnQuickActivate" style="flex:2;min-width:190px">[🔑] KÍCH HOẠT</button>
          <button class="btn btn-ghost btn-lg" id="btnQuickCopyHwid" style="flex:1;min-width:130px">[📋] COPY HWID</button>
          <button class="btn btn-lg lg-free" id="btnGetKeyFree" style="flex:1 1 100%">[🎁] GET KEY FREE</button>
        </div>
      </div>

      <div id="licenseBlock" class="hidden"><div class="lic-info" id="licInfo"></div></div>
      <div class="steps hidden" id="stepsBlock">
        <div class="step"><div class="step-num">1</div><div class="step-text">Bấm <b>[LẤY TỪ CHROME]</b> — tool tự khởi chạy trình duyệt Chrome.</div></div>
        <div class="step"><div class="step-num">2</div><div class="step-text">Đăng nhập <b>olm.vn</b> trong cửa sổ Chrome, đợi trang tải xong.</div></div>
        <div class="step"><div class="step-num">3</div><div class="step-text">Bấm <b>[LẤY COOKIES]</b> — tool tự động bắt phiên và lưu session.</div></div>
      </div>
      <div class="field hidden" id="manualCookieField">
        <label>Hoặc dán Cookies thủ công</label>
        <textarea id="ck" rows="3" placeholder="onlinemath_session=...; user=...; XSRF-TOKEN=..."></textarea>
      </div>
      <div id="loginBtnsRow" class="hidden" style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn btn-primary btn-lg" id="btnChrome" style="flex:2;min-width:200px">[🌐] LẤY TỪ CHROME</button>
        <button class="btn btn-lg" id="btnCookie" style="flex:1;min-width:130px">[🔗] KẾT NỐI</button>
      </div>
      <div id="chromeHelp" class="hidden"></div>
      <div id="loginMsg"></div>
    </div>
  </div>

  <div class="content hidden" id="dashView">
    <div class="config-bar">
      <label>CHẾ ĐỘ:
        <select id="modeSel">
          <option value="minus1">Sai 1 câu (~9/10đ)</option>
          <option value="full">Full điểm (10/10đ)</option>
          <option value="custom">Tùy chỉnh điểm</option>
        </select>
      </label>
      <label id="customWrap" class="hidden">ĐIỂM:
        <input type="number" id="customScore" min="0" max="10" step="0.1" value="9">
      </label>
      <label>THỜI GIAN (GIÂY):
        <input type="number" id="timeSpent" min="10" step="10" value="300">
      </label>
      <button class="btn btn-ghost btn-sm" id="btnScan">[🔍] QUÉT BÀI</button>
      <button class="btn btn-ghost btn-sm" id="btnAddId">[+] THÊM ID/LINK</button>
      <div class="spacer"></div>
      <button class="btn btn-primary btn-sm" id="btnAllSubmit">[⚡] LÀM TẤT CẢ</button>
      <button class="btn btn-warning btn-sm" id="btnAllRedo">[🔄] LÀM LẠI TẤT CẢ</button>
      <button class="btn btn-danger btn-sm" id="btnAllDelete">[🗑] XÓA TẤT CẢ</button>
    </div>
    <div class="filter-bar">
      <div class="search-box">
        <input type="text" data-search-input placeholder="Tìm nhanh theo tên bài tập hoặc mã ID...">
      </div>
      <div class="filter-tabs">
        <button type="button" class="filter-tab active" data-filter-tab="all">TẤT CẢ</button>
        <button type="button" class="filter-tab" data-filter-tab="todo">CHƯA LÀM</button>
        <button type="button" class="filter-tab" data-filter-tab="done">ĐÃ LÀM</button>
      </div>
      <div class="stat-pills">
        <span class="stat-pill">TỔNG:<b data-stat-total>0</b></span>
        <span class="stat-pill todo">CHƯA LÀM:<b data-stat-todo>0</b></span>
        <span class="stat-pill done">ĐÃ LÀM:<b data-stat-done>0</b></span>
      </div>
    </div>
    <div class="main">
      <div class="list-panel" id="listPanel">
        <div class="empty-state">
          <div class="icon">[ EMPTY ]</div>
          <p>Chưa có bài tập nào trong danh sách.<br>Bấm <b>[🔍 QUÉT BÀI]</b> để tải danh sách bài tập được giao từ OLM.</p>
        </div>
      </div>
      <div class="log-panel" id="logPanel">
        <div class="log-header">
          <span class="dot"></span><span>SYSTEM LOG // NHẬT KÝ HOẠT ĐỘNG</span>
          <button class="clear" id="btnClearLog">XÓA LOG</button>
        </div>
        <div class="progress-bar hidden" id="bulkProgress">
          <div class="progress-fill" id="bulkFill"></div>
        </div>
        <div class="log-body" id="logBody"></div>
      </div>
    </div>
  </div>
</div>

<div class="modal" id="addIdModal">
  <div class="modal-box">
    <h3>[+] THÊM BÀI TẬP THỦ CÔNG</h3>
    <p class="sub" style="margin-bottom:12px">Dán <b>ID bài tập</b> hoặc <b>đường dẫn OLM</b> (hỗ trợ cả link slug và link số).</p>
    <input class="modal-input" id="addIdInput" placeholder="VD: 2131570928 hoặc https://olm.vn/chu-de/..." autofocus>
    <div class="modal-actions">
      <button class="btn btn-ghost" id="addIdCancel">HỦY</button>
      <button class="btn btn-primary" id="addIdOk">THÊM BÀI</button>
    </div>
  </div>
</div>

<div class="modal" id="licModal">
  <div class="modal-box" style="max-width:520px">
    <h3>[KEY] THÔNG TIN LICENSE</h3>
    <div id="licModalBody"></div>
  </div>
</div>

<div class="modal" data-update-modal>
  <div class="modal-box" style="max-width:520px">
    <h3>[⬆] AUTO UPDATE // CẬP NHẬT HỆ THỐNG</h3>
    <p class="sub" style="margin-bottom:12px">Kiểm tra và tự động cập nhật trực tiếp lên phiên bản mới nhất từ máy chủ phát hành chính thức mà không cần tải lại thủ công.</p>
    <div class="lic-info" data-update-info></div>
    <div data-update-msg style="margin-top:10px;min-height:22px"></div>
    <div class="modal-actions" style="margin-top:14px;flex-wrap:wrap">
      <button class="btn btn-ghost" data-update-close>ĐÓNG</button>
      <button class="btn btn-ghost" data-update-check>[🔍] KIỂM TRA BẢN MỚI</button>
      <button class="btn btn-primary hidden" data-update-apply>[⚡] CẬP NHẬT NGAY</button>
      <button class="btn btn-warning hidden" data-update-restart>[🔄] KHỞI ĐỘNG LẠI</button>
    </div>
  </div>
</div>

<div class="answers-modal" id="answersModal">
  <div class="answers-modal-head">
    <span class="title" id="ansTitle">ĐÁP ÁN</span>
    <span class="sub" id="ansSub"></span>
    <div class="spacer"></div>
    <button class="btn btn-ghost btn-sm" id="btnCopyAns">[📋] COPY JSON</button>
    <button class="btn btn-primary btn-sm" id="btnCloseAns">[✖] ĐÓNG</button>
  </div>
  <div class="answers-modal-body">
    <iframe id="ansFrame" sandbox="allow-scripts allow-same-origin"></iframe>
  </div>
</div>

<div class="overlay" id="overlay">
  <div class="spinner-lg"></div>
  <div class="msg" id="overlayMsg">Đang xử lý...</div>
</div>

<div class="lg-gate" id="licGate">
  <div class="lg-gate-card">
    <div class="lg-lock">[🔒]</div>
    <h2>YÊU CẦU KÍCH HOẠT LICENSE</h2>
    <p>Nhập License Key để mở khoá toàn bộ tính năng.<br>
       Key sẽ <b>gắn cố định với mã phần cứng (HWID)</b> của máy này.</p>

    <div class="lg-hwid">HWID MÁY: <b id="lgHwid">…</b></div>

    <div class="field" style="text-align:left">
      <label>License Key</label>
      <input type="text" id="lgKey" placeholder="OLM-XXXX-XXXX-XXXX"
             autocomplete="off" spellcheck="false">
    </div>
    <div class="lg-msg" id="lgMsg"></div>

    <div class="lg-actions">
      <button class="btn btn-primary btn-lg" id="lgActivate">[🔑] KÍCH HOẠT NGAY</button>
      <div class="lg-row">
        <button class="btn btn-ghost" id="lgCopyHwid">[📋] COPY HWID</button>
        <button class="btn btn-ghost" id="lgRetry">[↻] KIỂM TRA LẠI</button>
      </div>
    </div>

    <div class="lg-or">HOẶC</div>
    <button class="btn btn-lg lg-free" id="lgGetKey">[🎁] GET KEY FREE</button>

    <div class="lg-foot">
      Chưa có key? Bấm <b>Get Key Free</b> để nhận key miễn phí.<br>
      <span id="lgFreeHint"></span>
    </div>
  </div>
</div>

<div class="toast-wrap" id="toastWrap"></div>

<script>
document.addEventListener('wheel', e => { if (e.ctrlKey) e.preventDefault(); }, { passive: false });
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && (e.key === '+' || e.key === '-' || e.key === '=' || e.key === '0')) {
    e.preventDefault();
  }
});

const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const api = () => window.pywebview.api;

let ASSIGN = [];
let BULK_RUNNING = false;
let ansJSONCache = "";
const MODE = { m: 'minus1', c: 9, t: 300 };

let LIC_STATE = {
  ok: false, key: '', key_type: '', duration_days: null,
  expires_at: null, hwid: '', seconds_left: 0, _lastSync: 0
};
let LIC_TICKER = null;

function esc(s){
  return String(s||'').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[c]);
}
function toast(msg, kind='info', duration=3500){
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.innerHTML = `<span style="flex:1">${esc(msg)}</span>`;
  $('#toastWrap').appendChild(el);
  setTimeout(() => {
    el.style.transition = 'all .3s';
    el.style.opacity = '0';
    el.style.transform = 'translateX(120%)';
    setTimeout(() => el.remove(), 320);
  }, duration);
}
function log(msg, kind='info'){
  const body = $('#logBody');
  if (!body) return;
  const t = new Date().toLocaleTimeString('vi-VN');
  const line = document.createElement('div');
  line.className = 'log-line ' + kind;
  line.innerHTML = `<span class="ts">${t}</span><span class="msg">${esc(msg)}</span>`;
  body.appendChild(line);
  body.scrollTop = body.scrollHeight;
}
function setStatus(ok, text){
  const d = $('#statusDot');
  d.className = 'dot' + (ok ? ' on' : '');
  $('#statusText').textContent = text || (ok ? 'Đã kết nối' : 'Chưa kết nối');
}
function loginMsg(html, kind='info'){
  $('#loginMsg').innerHTML = html ? `<div class="msg ${kind}">${html}</div>` : '';
}
function setStat(id, txt, kind){
  const el = document.querySelector(`[data-stat="${id}"]`);
  if (el) { el.textContent = txt; el.className = 'card-status ' + (kind||''); }
}
function setCardState(id, state){
  const c = document.querySelector(`[data-card="${id}"]`);
  if (!c) return;
  c.classList.remove('done','fail','working');
  if (state) c.classList.add(state);
}
function formatLeft(sec){
  sec = Math.max(0, sec|0);
  const d = Math.floor(sec/86400);
  const h = Math.floor((sec%86400)/3600);
  const m = Math.floor((sec%3600)/60);
  const s = sec%60;
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}
function formatHMS(sec){
  sec = Math.max(0, sec|0);
  const d = Math.floor(sec/86400);
  const h = Math.floor((sec%86400)/3600);
  const m = Math.floor((sec%3600)/60);
  const s = sec%60;
  const hh = String(h).padStart(2,'0');
  const mm = String(m).padStart(2,'0');
  const ss = String(s).padStart(2,'0');
  if (d > 0) return `${d}d ${hh}:${mm}:${ss}`;
  return `${hh}:${mm}:${ss}`;
}
function showOverlay(msg){ $('#overlayMsg').textContent = msg || 'Đang xử lý...'; $('#overlay').classList.add('show'); }
function hideOverlay(){ $('#overlay').classList.remove('show'); }

function startLicTicker(){
  if (LIC_TICKER) return;
  LIC_TICKER = setInterval(() => {
    if (!LIC_STATE.ok || !LIC_STATE.expires_at) {
      renderLicChip(); renderLicLive(); return;
    }
    try {
      const exp = new Date(LIC_STATE.expires_at.replace('Z','+00:00')).getTime();
      LIC_STATE.seconds_left = Math.max(0, Math.floor((exp - Date.now())/1000));
    } catch(e){}
    renderLicChip();
    renderLicLive();
    if (LIC_STATE.seconds_left <= 0 && LIC_STATE.ok){
      LIC_STATE.ok = false;
      clearInterval(LIC_TICKER); LIC_TICKER = null;
      toast('License đã hết hạn. Vui lòng nhập key mới.', 'err', 8000);
      setTimeout(() => resetUI(), 800);
    }
  }, 1000);
}

function renderLicChip(){
  const chip = $('#licChip');
  const txt = $('#licChipText');
  if (!chip) return;
  // v20.0: phòng thủ — nếu markup đổi/thiếu node con thì không làm sập app
  const dot = chip.querySelector('.dot') || document.getElementById('statusDot');
  chip.style.display = '';
  chip.classList.remove('warn');
  if (!LIC_STATE.ok){
    if (dot) dot.className = 'dot';
    if (txt) txt.textContent = 'Chưa kích hoạt';
    return;
  }
  if (dot) dot.className = 'dot on';
  const left = LIC_STATE.seconds_left || 0;
  if (left < 3600) chip.classList.add('warn');
  const key = LIC_STATE.key || '?';
  if (txt) txt.textContent = `${key} · ${formatHMS(left)}`;
}

function renderLicLive(){
  const box = $('#licLiveBox');
  const reqBox = $('#licRequireBox');
  const keyEl = $('#licLiveKey');
  const timerEl = $('#licLiveTimer');
  if (!LIC_STATE.ok){
    if (box) box.classList.add('hidden');
    if (reqBox) reqBox.classList.remove('hidden');
    return;
  }
  if (box) box.classList.remove('hidden');
  if (reqBox) reqBox.classList.add('hidden');
  if (keyEl) keyEl.textContent = LIC_STATE.key || '—';
  const left = LIC_STATE.seconds_left || 0;
  if (timerEl){
    timerEl.textContent = formatHMS(left);
    timerEl.classList.remove('warn','err');
    if (left <= 0) timerEl.classList.add('err');
    else if (left < 3600) timerEl.classList.add('warn');
  }
}

function updateLicState(info){
  if (!info) return;
  LIC_STATE.ok = !!info.ok;
  LIC_STATE.key = info.key || LIC_STATE.key || '';
  LIC_STATE.key_type = info.key_type || LIC_STATE.key_type || '';
  LIC_STATE.duration_days = info.duration_days || LIC_STATE.duration_days;
  LIC_STATE.expires_at = info.expires_at || LIC_STATE.expires_at;
  LIC_STATE.hwid = info.hwid || LIC_STATE.hwid || '';
  if (typeof info.seconds_left === 'number'){
    LIC_STATE.seconds_left = info.seconds_left;
  } else if (LIC_STATE.expires_at){
    try {
      const exp = new Date(LIC_STATE.expires_at.replace('Z','+00:00')).getTime();
      LIC_STATE.seconds_left = Math.max(0, Math.floor((exp - Date.now())/1000));
    } catch(e){}
  }
  LIC_STATE._lastSync = Date.now();
  renderLicChip();
  renderLicLive();
}

async function copyTextRobust(text){
  if (!text) return false;
  try { const r = await api().copy_to_clipboard(text); if (r && r.ok) return true; } catch(e){}
  try {
    if (navigator.clipboard && navigator.clipboard.writeText){
      await navigator.clipboard.writeText(text); return true;
    }
  } catch(e){}
  try {
    const ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly','');
    ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0;';
    document.body.appendChild(ta);
    ta.focus(); ta.select(); ta.setSelectionRange(0, ta.value.length);
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch(e){}
  return false;
}

function resetUI(){
  ASSIGN = []; BULK_RUNNING = false; ansJSONCache = '';
  $('#answersModal').classList.remove('show');
  $('#licModal').classList.remove('show');
  $('#addIdModal').classList.remove('show');
  hideOverlay();
  $('#dashView').classList.add('hidden');
  $('#loginView').style.display = 'flex';
  $('#loginView').classList.remove('hidden');
  $('#btnLogout').classList.add('hidden');
  $('#btnReGrab').classList.add('hidden');
  $('#btnOpenChrome').classList.add('hidden');
  $('#btnInstallChromium').classList.add('hidden');
  setStatus(false, 'Chưa kết nối');
  $('#ck').value = '';
  $('#loginMsg').innerHTML = '';
  $('#chromeHelp').classList.add('hidden');
  $('#chromeHelp').innerHTML = '';
  $('#loginTitle').textContent = 'Chào mừng';
  $('#loginSub').textContent = 'Đang kiểm tra License...';
  $('#licenseBlock').classList.add('hidden');
  $('#stepsBlock').classList.add('hidden');
  $('#manualCookieField').classList.add('hidden');
  $('#loginBtnsRow').classList.add('hidden');
  $('#btnCookie').disabled = false;
  $('#btnChrome').disabled = false;



  bootstrap();
}

async function bootstrap(){
  try {
    const lic = await api().lic_status();
    updateLicState(lic);
    startLicTicker();

    if (!lic.ok){
      showLicenseRequired(lic);
      checkChromiumAndUpdateUI();
      return;
    }

    hideGate();
    $('#licenseBlock').classList.remove('hidden');
    $('#licRequireBox').classList.add('hidden');
    $('#licLiveBox').classList.remove('hidden');
    renderLicInfo(lic);

    const me = await api().get_status();
    if (me && me.ok){
      onLoggedIn({ user_id: me.user_id, username: me.username });
      checkChromiumAndUpdateUI();
      return;
    }

    $('#stepsBlock').classList.remove('hidden');
    $('#manualCookieField').classList.remove('hidden');
    $('#loginBtnsRow').classList.remove('hidden');
    $('#loginTitle').textContent = 'Chào mừng trở lại';
    $('#loginSub').innerHTML =
      `License OK — còn <b>${formatLeft(lic.seconds_left||0)}</b>.<br>` +
      `Session OLM đã hết hiệu lực, vui lòng đăng nhập lại.`;
    loginMsg(`License OK — còn <b>${formatLeft(lic.seconds_left||0)}</b>.`, 'ok');
    checkChromiumAndUpdateUI();
  } catch(e){
    loginMsg('Lỗi khởi tạo: ' + esc(e.message||String(e)), 'err');
  }
}

async function checkChromiumAndUpdateUI(){
  try {
    const r = await api().chromium_status();
    if (r.ok && !r.installed) $('#btnInstallChromium').classList.remove('hidden');
  } catch(e){}
}

function renderLicInfo(lic){
  $('#licInfo').innerHTML = `
    <div class="lic-info-row"><span class="lbl">Key</span>
      <span class="val" style="color:var(--p1);font-weight:700">${esc(lic.key||'-')}</span></div>
    <div class="lic-info-row"><span class="lbl">Loại key</span>
      <span class="val">${esc(lic.key_type||'-')} (${lic.duration_days||'?'} ngày)</span></div>
    <div class="lic-info-row"><span class="lbl">Còn lại</span>
      <span class="val ok">${formatLeft(lic.seconds_left||0)}</span></div>
    <div class="lic-info-row"><span class="lbl">Hết hạn</span>
      <span class="val">${esc((lic.expires_at||'').replace('T',' ').slice(0,19))}</span></div>
    <div class="lic-info-row"><span class="lbl">HWID</span>
      <span class="val">${esc(lic.hwid||'-')}</span></div>
  `;
}

function showLicenseRequired(lic){
  $('#stepsBlock').classList.add('hidden');
  $('#manualCookieField').classList.add('hidden');
  $('#loginBtnsRow').classList.add('hidden');
  $('#licenseBlock').classList.add('hidden');
  $('#licLiveBox').classList.add('hidden');
  $('#chromeHelp').classList.add('hidden');
  $('#loginMsg').innerHTML = '';
  $('#loginTitle').textContent = '🔒 Cần kích hoạt License';
  $('#loginSub').textContent = 'Nhập key để sử dụng tool. Key sẽ khóa vĩnh viễn với máy này.';

  $('#licRequireInfo').innerHTML = `
    <div class="lic-info-row"><span class="lbl">Trạng thái</span>
      <span class="val" style="color:var(--warn)">● Chưa kích hoạt</span></div>
    <div class="lic-info-row"><span class="lbl">HWID máy</span>
      <span class="val">${esc(lic.hwid||'-')}</span></div>
    <div class="lic-info-row"><span class="lbl">Lý do</span>
      <span class="val">${esc(lic.reason||lic.error||'Chưa nhập key')}</span></div>
  `;
  $('#licQuickMsg').innerHTML = '';
  $('#licQuickKey').value = '';
  $('#licRequireBox').classList.remove('hidden');
  $('#btnCookie').disabled = true;
  $('#btnChrome').disabled = true;
  // v20.0: mở màn khoá toàn màn hình
  showGate(lic);
  setTimeout(() => { const i = $('#licQuickKey'); if (i) i.focus(); }, 200);
}

function onLoggedIn(info){
  setStatus(true, `UID #${info.user_id}`);
  $('#btnLogout').classList.remove('hidden');
  $('#btnReGrab').classList.remove('hidden');
  $('#btnOpenChrome').classList.remove('hidden');
  $('#loginView').style.display = 'none';
  $('#dashView').classList.remove('hidden');
  log(`Đăng nhập OLM OK — UID ${info.user_id} (${info.username||''})`, 'ok');
  scanAssignments();
}

async function openLicenseModal(){
  const st = await api().lic_status();
  const hwid = await api().lic_hwid();
  let body = '';
  if (st.ok){
    body = `
      <div class="lic-info">
        <div class="lic-info-row"><span class="lbl">Trạng thái</span>
          <span class="val ok">● Đang hoạt động</span></div>
        <div class="lic-info-row"><span class="lbl">Key</span>
          <span class="val" style="color:var(--p1);font-weight:700">${esc(st.key||'-')}</span></div>
        <div class="lic-info-row"><span class="lbl">Loại</span>
          <span class="val">${esc(st.key_type||'-')} (${st.duration_days||'?'} ngày)</span></div>
        <div class="lic-info-row"><span class="lbl">Còn lại</span>
          <span class="val highlight">${formatLeft(st.seconds_left||0)}</span></div>
        <div class="lic-info-row"><span class="lbl">HWID</span>
          <span class="val">${esc(hwid)}</span></div>
      </div>
      <p style="color:var(--dim);font-size:12px">Hủy key sẽ giải phóng máy, key chết vĩnh viễn.</p>
      <div class="modal-actions">
        <button class="btn btn-ghost" onclick="closeLicModal()">Đóng</button>
        <button class="btn btn-danger" onclick="doDeactivate()">🔓 Hủy key</button>
      </div>`;
  } else {
    body = `
      <p>Nhập key để kích hoạt. Key sẽ <b>khóa vĩnh viễn</b> với máy này.</p>
      <div class="lic-info">
        <div class="lic-info-row"><span class="lbl">HWID máy bạn</span>
          <span class="val">${esc(hwid)}</span></div>
      </div>
      <input class="modal-input" id="licKeyInput"
        placeholder="OLM-XXXX-XXXX-XXXX" autofocus>
      <div id="licModalMsg"></div>
      <div class="modal-actions" style="margin-top:16px">
        <button class="btn btn-ghost" onclick="closeLicModal()">Đóng</button>
        <button class="btn btn-primary" onclick="doActivate()">🔑 Kích hoạt</button>
      </div>`;
  }
  $('#licModalBody').innerHTML = body;
  $('#licModal').classList.add('show');
  setTimeout(() => { const i = $('#licKeyInput'); if (i) i.focus(); }, 80);
}
window.openLicenseModal = openLicenseModal;
function closeLicModal(){ $('#licModal').classList.remove('show'); }
window.closeLicModal = closeLicModal;

async function doActivate(){
  const key = ($('#licKeyInput').value || '').trim();
  const msg = $('#licModalMsg');
  if (!key){ msg.innerHTML = '<div class="msg err">Nhập key</div>'; return; }
  msg.innerHTML = '<div class="msg info"><span class="spin"></span> Đang kích hoạt…</div>';
  const r = await api().lic_activate(key);
  if (r.ok){
    msg.innerHTML = `<div class="msg ok">Kích hoạt thành công! Hết hạn sau ${r.days||1} ngày.</div>`;
    setTimeout(() => { closeLicModal(); resetUI(); }, 1200);
  } else msg.innerHTML = `<div class="msg err">${esc(r.error||'Lỗi')}</div>`;
}
window.doActivate = doActivate;

async function doDeactivate(){
  if (!confirm('Hủy key? Sau khi hủy, key sẽ hết hiệu lực VĨNH VIỄN.')) return;
  closeLicModal();
  const r = await api().lic_deactivate();
  if (r.ok){ toast('Đã hủy key thành công', 'ok'); setTimeout(() => resetUI(), 400); }
  else toast(r.error || 'Lỗi hủy', 'err');
}
window.doDeactivate = doDeactivate;

$('#btnLicense').onclick = openLicenseModal;
$('#licChip').onclick = openLicenseModal;

$('#btnQuickActivate').onclick = async () => {
  const key = ($('#licQuickKey').value || '').trim();
  const msg = $('#licQuickMsg');
  if (!key){ msg.innerHTML = '<div class="msg err">Nhập key trước.</div>'; return; }
  msg.innerHTML = '<div class="msg info"><span class="spin"></span> Đang kích hoạt…</div>';
  $('#btnQuickActivate').disabled = true;
  try {
    const r = await api().lic_activate(key);
    if (r.ok){
      msg.innerHTML = `<div class="msg ok">Kích hoạt thành công! Hết hạn sau ${r.days||1} ngày.</div>`;
      setTimeout(() => resetUI(), 1200);
    } else {
      msg.innerHTML = `<div class="msg err">${esc(r.error||'Lỗi')}</div>`;
    }
  } finally { $('#btnQuickActivate').disabled = false; }
};

$('#btnQuickCopyHwid').onclick = async () => {
  const hwid = await api().lic_hwid();
  const ok = await copyTextRobust(hwid);
  if (ok) toast('Đã copy HWID: ' + hwid, 'ok', 4000);
  else toast('Không copy được', 'err');
};

$('#licQuickKey').addEventListener('keydown', e => {
  if (e.key === 'Enter') $('#btnQuickActivate').click();
});

$('#btnInstallChromium').onclick = async () => {
  if (!confirm('Tải Chromium (~150MB)? Quá trình mất 2-5 phút.')) return;
  $('#btnInstallChromium').disabled = true;
  showOverlay('Đang tải Chromium…');
  try {
    const r = await api().chromium_install();
    hideOverlay();
    if (r.ok){
      toast('Đã cài Chromium thành công!', 'ok', 4000);
      $('#btnInstallChromium').classList.add('hidden');
    } else toast('Lỗi cài Chromium: ' + (r.error||''), 'err', 6000);
  } catch(e){ hideOverlay(); toast('Lỗi: ' + (e.message||e), 'err'); }
  finally { $('#btnInstallChromium').disabled = false; }
};

$('#btnChrome').onclick = async () => {
  const help = $('#chromeHelp');
  help.classList.remove('hidden');
  $('#btnChrome').disabled = true;
  const renderWait = (extra='') => {
    help.innerHTML =
      `<div class="msg info" style="margin-top:10px">
        <div>
          <b>Chrome đang mở.</b> Login OLM, đợi load xong rồi bấm <b>Lấy cookies</b>.${extra}
          <div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">
            <button class="btn btn-primary btn-sm" id="btnDoGrab">📥 Lấy cookies</button>
            <button class="btn btn-ghost btn-sm" id="btnCancelGrab">Hủy</button>
          </div>
        </div>
      </div>`;
    $('#btnDoGrab').onclick = doGrab;
    $('#btnCancelGrab').onclick = () => {
      help.classList.add('hidden'); help.innerHTML = '';
      $('#btnChrome').disabled = false;
    };
  };
  const doGrab = async () => {
    help.innerHTML = `<div class="msg info"><span class="spin"></span> Đang đọc cookies…</div>`;
    const r = await api().chrome_grab_cookies();
    if (r.ok){
      help.innerHTML =
        `<div class="msg ok">
          ✓ Đã lấy <b>${r.count}</b> cookies. User: <b>${esc(r.username||'')}</b>
        </div>`;
      setTimeout(() => {
        help.classList.add('hidden');
        onLoggedIn({ user_id: r.user_id, username: r.username });
      }, 900);
    } else if (r.waiting_login){
      renderWait('<br><b style="color:var(--warn)">⚠ Chrome chưa login OLM.</b>');
    } else {
      help.innerHTML =
        `<div class="msg err">✖ ${esc(r.error||'Lỗi')}
          <div style="margin-top:10px">
            <button class="btn btn-primary btn-sm" id="btnRetryGrab">Thử lại</button>
          </div>
        </div>`;
      $('#btnRetryGrab').onclick = doGrab;
    }
  };
  try {
    help.innerHTML = `<div class="msg info" style="margin-top:10px"><span class="spin"></span> Đang mở Chrome…</div>`;
    const open = await api().chrome_open();
    if (!open.ok && !open.already){
      help.innerHTML = `<div class="msg err">✖ ${esc(open.error||'Không mở được')}</div>`;
      $('#btnChrome').disabled = false;
      return;
    }
    renderWait();
  } catch(e){ help.innerHTML = `<div class="msg err">✖ ${esc(e.message||String(e))}</div>`; }
  finally { $('#btnChrome').disabled = false; }
};

$('#btnReGrab').onclick = async () => {
  if (!confirm('Lấy cookies mới từ Chrome?')) return;
  const r = await api().chrome_open();
  if (!r.ok && !r.already){ toast(r.error || 'Không mở được Chrome', 'err'); return; }
  toast('Login OLM, rồi bấm lại nút Cookies.', 'info', 6000);
  $('#btnReGrab').onclick = async () => {
    const g = await api().chrome_grab_cookies();
    if (g.ok){
      toast(`Đã cập nhật cookies (${g.count}).`, 'ok');
      setTimeout(() => {
        resetUI();
        setTimeout(() => onLoggedIn({ user_id: g.user_id, username: g.username }), 100);
      }, 800);
    } else if (g.waiting_login) toast('Chưa login OLM.', 'warn', 5000);
    else toast(g.error || 'Lỗi', 'err');
  };
};

$('#btnOpenChrome').onclick = async () => {
  const r = await api().chrome_open();
  if (r.ok || r.already) toast('Chrome đã mở.', 'ok', 5000);
  else toast(r.error || 'Không mở được Chrome', 'err');
};

$('#btnCookie').onclick = async () => {
  const ck = $('#ck').value.trim();
  if (!ck){ loginMsg('Chưa nhập cookies', 'warn'); return; }
  loginMsg('<span class="spin"></span> Đang xác thực…', 'info');
  $('#btnCookie').disabled = true;
  try {
    const r = await api().set_cookies(ck);
    if (r.ok){
      loginMsg('Kết nối thành công!', 'ok');
      const me = await api().get_status();
      setTimeout(() => onLoggedIn({ user_id: me.user_id, username: me.username }), 300);
    } else loginMsg(esc(r.error||'Không kết nối được'), 'err');
  } catch(e){ loginMsg('Lỗi: ' + esc(e.message||String(e)), 'err'); }
  finally { $('#btnCookie').disabled = false; }
};

$('#btnLogout').onclick = async () => {
  if (!confirm('Đăng xuất khỏi OLM (giữ license)?')) return;
  try {
    await api().logout();
    toast('Đã đăng xuất', 'ok');
    setTimeout(() => resetUI(), 400);
  } catch(e){ toast('Lỗi: ' + (e.message||e), 'err'); }
};

$('#modeSel').onchange = e => {
  MODE.m = e.target.value;
  if (MODE.m === 'custom') $('#customWrap').classList.remove('hidden');
  else $('#customWrap').classList.add('hidden');
  renderList();
};
$('#customScore').oninput = e => MODE.c = Math.max(0, Math.min(10, parseFloat(e.target.value)||0));
$('#timeSpent').oninput = e => {
  MODE.t = Math.max(10, parseInt(e.target.value)||300);
  $$('input[data-time]').forEach(el => el.value = MODE.t);
};

let FILTER_MODE = 'all';
let SEARCH_QUERY = '';

function updateStats(){
  const total = ASSIGN.length;
  const done = ASSIGN.filter(a => !!a.done).length;
  const todo = total - done;
  const elTotal = $('[data-stat-total]');
  const elTodo = $('[data-stat-todo]');
  const elDone = $('[data-stat-done]');
  if (elTotal) elTotal.textContent = String(total);
  if (elTodo) elTodo.textContent = String(todo);
  if (elDone) elDone.textContent = String(done);
}

const searchInputEl = $('[data-search-input]');
if (searchInputEl){
  searchInputEl.addEventListener('input', e => {
    SEARCH_QUERY = (e.target.value || '').trim().toLowerCase();
    renderList();
  });
}
$$('[data-filter-tab]').forEach(tab => {
  tab.addEventListener('click', () => {
    $$('[data-filter-tab]').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    FILTER_MODE = tab.getAttribute('data-filter-tab') || 'all';
    renderList();
  });
});

async function scanAssignments(){
  log('[SCAN] Đang quét danh sách bài tập được giao từ OLM…', 'work');
  $('#listPanel').innerHTML = '<div class="empty-state"><div class="icon">[ SCANNING ]</div><p>Đang quét danh sách bài tập từ OLM…</p></div>';
  try {
    const r = await api().scan();
    if (!r.ok){
      if (r.cf){
        log('Cloudflare đang chặn kết nối.', 'warn');
        $('#listPanel').innerHTML =
          `<div class="empty-state"><div class="icon">[ CF BLOCKED ]</div>
            <p>Cloudflare đang chặn.<br>Bấm <b>[🌐 CHROME]</b> để giải xác thực Cloudflare.</p>
            <button class="btn btn-ghost" onclick="scanAssignments()">Thử lại</button>
          </div>`;
        return;
      }
      if (r.chromium_missing){
        log('Chưa cài đặt Chromium.', 'warn');
        $('#btnInstallChromium').classList.remove('hidden');
        $('#listPanel').innerHTML =
          `<div class="empty-state"><div class="icon">[ CHROMIUM ]</div>
            <p>Cần cài đặt <b>Chromium</b> (~150MB) để quét bài.</p>
          </div>`;
        return;
      }
      throw new Error(r.error || 'Scan failed');
    }
    ASSIGN = (r.items || []).map(it => ({
      id: String(it.id),
      title: it.title,
      href: it.href || '',
      type: it.type || null,
      type_label: it.type_label || '',
      done: !!it.done,
      score: it.score,
      correct: it.correct,
      total: it.total,
      _types: null,
      _count: it.total || null,
    }));
    renderList();
    if (!ASSIGN.length){
      log('[SCAN] Không tìm thấy bài tập nào được giao.', 'warn');
      log('  ↳ Kiểm tra lại tài khoản OLM hoặc thêm bài bằng ID/Link.', 'warn');
      return;
    }
    const initDone = ASSIGN.filter(a => a.done).length;
    const initTodo = ASSIGN.length - initDone;
    log(`[SCAN] Tìm thấy ${ASSIGN.length} bài GV giao (${initTodo} chưa làm · ${initDone} đã làm). Đang nạp cấu trúc câu hỏi…`, 'ok');

    const normTitle = s => String(s || '').trim().replace(/\s+/g, ' ').toLowerCase();
    let loadedCount = 0;
    for (let i = 0; i < ASSIGN.length; i++){
      const a = ASSIGN[i];
      try {
        const qr = await api().get_questions(a.id);
        if (!qr.ok){
          const isFakeOrDuplicate = (!qr.cf && !qr.chromium_missing && !qr.v1_required) && (
            String(a.id).length >= 11 ||
            ASSIGN.some((other, idx) => idx !== i && normTitle(other.title) && normTitle(other.title) === normTitle(a.title))
          );
          if (isFakeOrDuplicate){
            log(`[LỌC BÀI GIẢ] Đã loại bỏ mục trùng/wrapper #${a.id} (${a.title})`, 'warn');
            ASSIGN.splice(i, 1);
            i--;
            renderList();
            continue;
          }
          log(`[${i+1}/${ASSIGN.length}] Bài #${a.id}: không tải được chi tiết (${qr.error || 'bỏ qua'})`, 'warn');
          continue;
        }
        if (qr.title && (a.title === `Bài ${a.id}` || !a.title)){
          a.title = qr.title;
        }
        if (qr.type_label){
          a.type_label = qr.type_label;
        }
        if (qr.type !== undefined && qr.type !== null){
          a.type = qr.type;
        }
        if (qr.count !== undefined){
          const t = qr.type_counts || {};
          a._types = t;
          a._count = qr.count;
        }
        const rec = qr.record || {};
        const recDone = !!(
          qr.done_from_record ||
          rec.done ||
          rec.ended === 1 ||
          rec.ended === true ||
          rec.messageStatic ||
          (rec.score !== undefined && rec.score !== null && Number(rec.score) > 0) ||
          (rec.correct !== undefined && rec.correct !== null && Number(rec.correct) > 0)
        );
        if (recDone){
          a.done = true;
          if (rec.score !== undefined && rec.score !== null && (a.score === null || a.score === undefined)) a.score = rec.score;
          if (rec.correct !== undefined && rec.correct !== null && (a.correct === null || a.correct === undefined)) a.correct = rec.correct;
        } else if (!(a.score !== null && a.score !== undefined && Number(a.score) > 0)) {
          a.done = false;
          a.score = null;
          a.correct = null;
        }
        if (rec.totalq !== undefined && rec.totalq !== null && (a.total === null || a.total === undefined)) a.total = rec.totalq;

        loadedCount++;
        const qCountStr = (a._count !== null && a._count !== undefined) ? `${a._count} câu` : '0 câu';
        const typeStr = a.type_label ? ` · ${a.type_label}` : '';
        const statusStr = a.done ? ` · ĐÃ LÀM (${a.score !== null && a.score !== undefined ? a.score + 'đ' : 'OK'})` : ' · CHƯA LÀM';
        log(`[${i+1}/${ASSIGN.length}] ${a.title} (#${a.id}) → ${qCountStr}${typeStr}${statusStr}`, 'info');
        renderList();
      } catch(e){
        log(`[${i+1}/${ASSIGN.length}] Lỗi nạp bài #${a.id}: ${e.message||e}`, 'warn');
      }
    }
    const finalDone = ASSIGN.filter(a => a.done).length;
    const finalTodo = ASSIGN.length - finalDone;
    log(`═══ QUÉT HOÀN TẤT: ${ASSIGN.length} bài (${finalTodo} chưa làm · ${finalDone} đã làm · đã nạp ${loadedCount}/${ASSIGN.length}) ═══`, 'ok');
    toast(`Đã quét xong ${ASSIGN.length} bài (${finalTodo} chưa làm)`, 'ok', 4000);
  } catch(e){
    log('Lỗi quét: ' + (e.message||e), 'err');
    $('#listPanel').innerHTML =
      `<div class="empty-state"><div class="icon">[ ERROR ]</div>
        <p>${esc(e.message||String(e))}</p>
        <button class="btn btn-ghost" onclick="scanAssignments()">Thử lại</button>
      </div>`;
  }
}
window.scanAssignments = scanAssignments;
$('#btnScan').onclick = scanAssignments;

function _typeBadgeClass(type){
  if (type === 4 || type === 10 || type === 13 || type === 14 || type === 18 || type === 21 || type === 22) return 'type-badge pdf';
  if (type === 1 || type === 2 || type === 5) return 'type-badge flex';
  return 'type-badge';
}

function renderList(){
  updateStats();
  const panel = $('#listPanel');
  if (!panel) return;
  const prevScroll = panel.scrollTop;
  if (!ASSIGN.length){
    panel.innerHTML =
      '<div class="empty-state"><div class="icon">[ EMPTY ]</div>' +
      '<p>Không có bài tập nào. Bấm <b>[🔍 QUÉT BÀI]</b> hoặc <b>[+ THÊM ID/LINK]</b>.</p></div>';
    return;
  }
  const filtered = ASSIGN.map((a, idx) => ({ a, idx })).filter(({ a }) => {
    if (FILTER_MODE === 'todo' && a.done) return false;
    if (FILTER_MODE === 'done' && !a.done) return false;
    if (SEARCH_QUERY){
      const hay = `${a.title || ''} ${a.id || ''} ${a.type_label || ''}`.toLowerCase();
      if (!hay.includes(SEARCH_QUERY)) return false;
    }
    return true;
  });
  if (!filtered.length){
    panel.innerHTML =
      '<div class="empty-state"><div class="icon">[ NO MATCH ]</div>' +
      '<p>Không có bài tập nào khớp bộ lọc hiện tại.</p></div>';
    return;
  }
  panel.innerHTML = filtered.map(({ a, idx }) => {
    const initScore = MODE.m === 'custom' ? MODE.c : '';
    const doneBadge = a.done
      ? `<span class="badge-done">✓ ĐÃ LÀM${a.score !== null && a.score !== undefined ? ' · ' + a.score + 'đ' : ''}</span>`
      : '';
    const typeBadge = a.type_label
      ? `<span class="${_typeBadgeClass(a.type)}">${esc(a.type_label)}</span>`
      : '';
    const correctInfo = (a._correct_count !== undefined && a._correct_count !== null)
      ? ` · <span class="type-badge good">✔ ${a._correct_count}/${a._count||0} đúng · ${a._score||0}đ</span>`
      : (a.correct !== null && a.correct !== undefined && a.total
          ? ` · <span class="type-badge good">✔ ${a.correct}/${a.total} đúng</span>`
          : '');
    const isVideo = (a.type === 1 || a.type === 5);
    const typesHtml = a._types
      ? (isVideo && (!a._count || a._count === 0)
          ? `<b style="color:var(--p1)">Bài giảng Video / Lý thuyết</b>` + correctInfo
          : `<b style="color:var(--p1)">${a._count||0} câu</b> · ` +
            Object.entries(a._types).map(([k,v])=>
              `<span class="type-badge">${esc(k)} × ${v}</span>`
            ).join(' ') + correctInfo)
      : '⏳ Đang tải thông tin bài…';
    return `<div class="card${a.done ? ' done' : ''}" data-card="${a.id}">
      <div class="card-head">
        <span class="card-idx">${idx+1}</span>
        <span class="card-title" title="${esc(a.title)}">${esc(a.title)}</span>
        ${doneBadge}
        ${typeBadge}
        <span class="card-id">#${esc(a.id)}</span>
      </div>
      <div class="card-types" data-types="${a.id}">
        ${typesHtml}
      </div>
      <div class="card-actions">
        <label>Điểm</label>
        <input type="number" class="num-input" data-score="${a.id}"
          min="0" max="10" step="0.1" value="${initScore}" placeholder="auto">
        <label>Giây</label>
        <input type="number" class="num-input" data-time="${a.id}"
          min="10" step="10" value="${MODE.t}">
        <button class="btn btn-primary btn-sm" data-act="do" data-id="${a.id}">⚡ Làm</button>
        <button class="btn btn-warning btn-sm" data-act="redo" data-id="${a.id}">🔄 Làm lại</button>
        <button class="btn btn-danger btn-sm" data-act="del" data-id="${a.id}">🗑</button>
        <button class="btn btn-ghost btn-sm" data-act="ans" data-id="${a.id}">📋 Đáp án</button>
        <button class="btn btn-ghost btn-sm" data-act="word" data-id="${a.id}">📥 Word</button>
      </div>
      <div class="card-status" data-stat="${a.id}"></div>
    </div>`;
  }).join('');
  panel.querySelectorAll('button[data-act]').forEach(b => b.onclick = onCardAction);
  panel.scrollTop = prevScroll;
}

async function onCardAction(e){
  const btn = e.currentTarget;
  const act = btn.dataset.act;
  const id = btn.dataset.id;

  if (act === 'ans'){
    setStat(id, '⏳ Đang tải đáp án…', 'working');
    setCardState(id, 'working');
    const r = await api().get_answers_html(id, null);
    setCardState(id, '');
    if (r.ok){
      const ncInfo = r.n_correct ? ` · ${r.n_correct}/${r.questions} câu đúng` : '';
      setStat(id, `✔ Đã tải ${r.questions} câu đáp án${ncInfo}`, 'ok');
      ansJSONCache = r.json || '';
      const tl = r.type_label ? ` · ${r.type_label}` : '';
      $('#ansTitle').textContent = `Đáp án — Bài ${id}`;
      $('#ansSub').textContent = `${r.questions} câu${r.title ? ' · ' + r.title : ''}${tl}${ncInfo}`;
      $('#ansFrame').srcdoc = r.html;
      $('#answersModal').classList.add('show');
      log(`Mở đáp án ${id} (${r.questions} câu)${ncInfo}`, 'ok');
    } else if (r.cf){ setStat(id, '🛡 CF chặn', 'warn'); }
    else if (r.chromium_missing){
      setStat(id, '📦 Chưa có Chromium', 'warn');
      $('#btnInstallChromium').classList.remove('hidden');
    } else if (r.v1_required){ setStat(id, '🔀 Cần v1', 'warn'); }
    else { setStat(id, '✖ ' + (r.error||''), 'err'); toast(r.error || 'Lỗi', 'err'); }
    return;
  }

  if (act === 'word'){
    setStat(id, '⏳ Đang tải Word…', 'working');
    setCardState(id, 'working');
    const r = await api().download_word(id);
    setCardState(id, '');
    if (r.ok){
      const suffix = r.generated ? ' (render)' : '';
      setStat(id, `✔ Đã lưu: ${r.filename}${suffix}`, 'ok');
      log(`Word ${id} → ${r.filename}${suffix}`, 'ok');
      toast('Đã lưu: ' + r.filename, 'ok');
      api().open_path(r.path);
    } else {
      setStat(id, '✖ ' + (r.error||''), 'err');
      toast(r.error || 'Lỗi Word', 'err');
    }
    return;
  }

  if (act === 'del'){
    if (!confirm(`Xóa bài làm của "${id}"?`)) return;
    setStat(id, '⏳ Đang xóa…', 'working');
    const r = await api().delete_work(id);
    if (r.ok){
      setStat(id, '✔ Đã xóa bài làm', 'ok');
      toast('Đã xóa bài làm', 'ok');
      const a = ASSIGN.find(x => x.id === id);
      if (a){
        a.done = false;
        a._correct_count = null;
        a._score = null;
        a.score = null;
        a.correct = null;
        renderList();
      }
    } else {
      setStat(id, '✖ ' + (r.error||'Lỗi'), 'err');
      toast(r.error || 'Lỗi', 'err');
    }
    return;
  }

  if (act === 'do'){ await doSubmit(id, btn, false); return; }
  if (act === 'redo'){ await doSubmit(id, btn, true); return; }
}

async function doSubmit(id, btn, forceRedo){
  const scoreEl = document.querySelector(`input[data-score="${id}"]`);
  const timeEl = document.querySelector(`input[data-time="${id}"]`);
  const custom = scoreEl && scoreEl.value ? parseFloat(scoreEl.value) : MODE.c;
  const t = timeEl && timeEl.value ? parseInt(timeEl.value) : MODE.t;
  const mode = MODE.m;
  const eff_mode = (scoreEl && scoreEl.value !== '') ? 'custom'
                   : (mode === 'custom' ? 'custom' : mode);

  if (btn) btn.disabled = true;
  const label = forceRedo ? 'Làm lại' : 'Làm';
  setStat(id, `⏳ Đang ${label.toLowerCase()}…`, 'working');
  setCardState(id, 'working');
  log(`${label} ${id} (mode=${eff_mode}, t=${t}s, redo=${forceRedo})`, 'work');

  try {
    const r = await api().submit(id, eff_mode, custom, t, !!forceRedo);
    if (r.ok){
      const ok = r.status === 200;
      const mtd = r.method ? ` [${r.method}]` : '';
      const dl = (r.delete_before && r.delete_before.ok) ? ' ✓del' : '';
      const fillInfo = r.dom_fill
        ? ` · fill:${r.dom_fill.text||0}/${r.dom_fill.radio||0}/${r.dom_fill.checkbox||0}`
        : '';
      const postInfo = (r.page_post_status || r.http_post_status)
        ? ` · page:${r.page_post_status||'-'} http:${r.http_post_status||'-'}`
        : '';
      setStat(id, `✔ ${r.questions} câu · đúng ${r.correct} · ${r.score}đ${dl}${postInfo}${mtd}${fillInfo}`,
              ok ? 'ok' : 'warn');
      log(`Xong ${id}: ${r.questions} câu, đúng ${r.correct}, ${r.score}đ${dl}${postInfo}${mtd}`,
          ok ? 'ok' : 'warn');

      const a = ASSIGN.find(x => x.id === id);
      if (a){
        a.done = true;
        a._correct_count = r.correct;
        a._score = r.score;
        a._count = r.questions;
        a.correct = r.correct;
        a.score = r.score;
      }
      setCardState(id, ok ? 'done' : 'fail');
      renderList();
      return true;
    }
    if (r.cf){ setStat(id, '🛡 CF chặn', 'warn'); setCardState(id, 'fail'); return false; }
    if (r.chromium_missing){
      setStat(id, '📦 Chưa có Chromium', 'warn');
      $('#btnInstallChromium').classList.remove('hidden');
      setCardState(id, 'fail');
      return false;
    }
    if (r.v1_required){ setStat(id, '🔀 Cần v1', 'warn'); setCardState(id, 'fail'); return false; }
    setStat(id, '✖ ' + (r.error||''), 'err');
    log(`Lỗi ${id}: ${r.error||''}`, 'err');
    setCardState(id, 'fail');
    return false;
  } catch(e){
    setStat(id, '✖ ' + String(e.message||e).slice(0,100), 'err');
    log(`Lỗi ${id}: ${e.message||e}`, 'err');
    setCardState(id, 'fail');
    return false;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function bulkRun(kind){
  if (BULK_RUNNING){ toast('Đang chạy tác vụ khác', 'warn'); return; }
  if (!ASSIGN.length){ toast('Chưa có bài nào', 'warn'); return; }

  let targets = ASSIGN;
  if (kind === 'submit'){
    targets = ASSIGN.filter(a => !a.done);
    if (!targets.length){
      toast('Tất cả bài đã làm rồi. Bấm "Làm lại tất cả" nếu muốn làm lại.', 'warn', 5000);
      return;
    }
  }

  const labels = {
    submit: `Làm ${targets.length} bài CHƯA làm (bỏ qua ${ASSIGN.length - targets.length} bài đã làm)?`,
    redo: `Làm LẠI TẤT CẢ ${ASSIGN.length} bài? (xóa bài cũ + làm mới)`,
    delete: `Xóa bài làm của TẤT CẢ ${ASSIGN.length} bài?`,
  };
  if (!confirm(labels[kind])) return;

  BULK_RUNNING = true;
  const btns = { submit: $('#btnAllSubmit'), redo: $('#btnAllRedo'), delete: $('#btnAllDelete') };
  const orig = btns[kind].innerHTML;
  Object.values(btns).forEach(b => b.disabled = true);
  btns[kind].innerHTML = '<span class="spin"></span> Đang chạy';
  $('#bulkProgress').classList.remove('hidden');
  $('#bulkFill').style.width = '0%';

  let ok = 0, fail = 0, skipped = 0;
  const titles = { submit: 'BẮT ĐẦU LÀM', redo: 'BẮT ĐẦU LÀM LẠI', delete: 'XÓA TẤT CẢ' };
  log(`═══ ${titles[kind]} (${targets.length}/${ASSIGN.length} bài) ═══`, 'work');
  if (kind === 'submit' && targets.length < ASSIGN.length){
    log(`  ↳ Bỏ qua ${ASSIGN.length - targets.length} bài đã làm`, 'warn');
  }

  for (let i = 0; i < ASSIGN.length; i++){
    const a = ASSIGN[i];
    if (kind === 'submit' && a.done){
      skipped++;
      log(`[${i+1}/${ASSIGN.length}] Bài ${a.id} — BỎ QUA (đã làm)`, 'warn');
      continue;
    }
    log(`[${i+1}/${ASSIGN.length}] Bài ${a.id}`, 'work');
    $('#bulkFill').style.width = `${Math.round((i / ASSIGN.length) * 100)}%`;
    try {
      if (kind === 'delete'){
        const r = await api().delete_work(a.id);
        if (r.ok){ ok++; a.done = false; a._correct_count = null; a._score = null;
                    a.score = null; a.correct = null; } else fail++;
      } else {
        const success = await doSubmit(a.id, null, kind === 'redo');
        if (success) ok++; else fail++;
      }
    } catch(e){
      fail++;
      log(`  Lỗi: ${e.message||e}`, 'err');
    }
    if (i < ASSIGN.length - 1) await sleep(kind === 'delete' ? 350 : 700);
  }

  $('#bulkFill').style.width = '100%';
  setTimeout(() => $('#bulkProgress').classList.add('hidden'), 1500);
  log(`═══ HOÀN TẤT: ${ok} OK · ${fail} lỗi · ${skipped} bỏ qua ═══`,
      fail===0 ? 'ok' : 'warn');
  toast(`Xong: ${ok} OK · ${fail} lỗi · ${skipped} bỏ qua`,
        fail===0 ? 'ok' : 'warn', 5000);

  Object.values(btns).forEach(b => b.disabled = false);
  btns[kind].innerHTML = orig;
  BULK_RUNNING = false;
  renderList();
}

$('#btnAllSubmit').onclick = () => bulkRun('submit');
$('#btnAllRedo').onclick = () => bulkRun('redo');
$('#btnAllDelete').onclick = () => bulkRun('delete');

$('#btnClearLog').onclick = () => { $('#logBody').innerHTML = ''; };

$('#btnAddId').onclick = () => {
  $('#addIdInput').value = '';
  $('#addIdModal').classList.add('show');
  setTimeout(() => $('#addIdInput').focus(), 80);
};
$('#addIdCancel').onclick = () => $('#addIdModal').classList.remove('show');
$('#addIdOk').onclick = async () => {
  const v = $('#addIdInput').value.trim();
  if (!v){ toast('Nhập ID hoặc link', 'warn'); return; }
  const btn = $('#addIdOk');
  const orig = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>';
  try {
    const r = await api().add_manual(v);
    if (r.ok){
      const item = r.items[0];
      if (!ASSIGN.find(a => a.id === item.id)){
        ASSIGN.push({
          id: item.id, title: item.title, href: item.href || '',
          type: item.type || null, type_label: item.type_label || '',
          done: false, score: null, correct: null, total: null,
          _types: null, _count: null,
        });
        renderList();
        try {
          const qr = await api().get_questions(item.id);
          if (qr.ok){
            const t = qr.type_counts || {};
            const a = ASSIGN.find(x => x.id === item.id);
            if (a){
              a._types = t;
              a._count = qr.count;
              a.title = qr.title || a.title;
              a.type_label = qr.type_label || a.type_label;
              a.type = qr.type;
              log(`[+BÀI] Đã nạp: ${a.title} (#${a.id}) — ${a._count||0} câu`, 'ok');
            }
          }
        } catch(e){}
        renderList();
      }
      $('#addIdModal').classList.remove('show');
      toast(`Đã thêm: ${item.title}`, 'ok', 4000);
    } else toast(r.error || 'Không thêm được', 'err');
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
};
$('#addIdInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') $('#addIdOk').click();
  if (e.key === 'Escape') $('#addIdCancel').click();
});

$('#btnCloseAns').onclick = () => $('#answersModal').classList.remove('show');
$('#btnCopyAns').onclick = async () => {
  if (!ansJSONCache){ toast('Không có dữ liệu', 'warn'); return; }
  const ok = await copyTextRobust(ansJSONCache);
  if (ok) toast(`Đã copy JSON (${(ansJSONCache.length/1024).toFixed(1)} KB)`, 'ok');
  else toast('Không copy được.', 'err', 5000);
};

document.addEventListener('keydown', e => {
  if (e.key === 'Escape'){
    $('#answersModal').classList.remove('show');
    $('#licModal').classList.remove('show');
    $('#addIdModal').classList.remove('show');
  }
  if (e.ctrlKey && e.shiftKey){
    if (e.key === 'R' || e.key === 'r'){ e.preventDefault(); scanAssignments(); }
    if (e.key === 'L' || e.key === 'l'){ e.preventDefault(); bulkRun('submit'); }
  }
});

window.addEventListener('pywebviewready', () => {
  if (!document.__booted){ document.__booted = true; bootstrap(); }
});
setTimeout(() => {
  if (window.pywebview && window.pywebview.api && !document.__booted){
    document.__booted = true; bootstrap();
  }
}, 800);

setInterval(async () => {
  try {
    const r = await api().lic_status();
    if (r){
      const wasOk = LIC_STATE.ok;
      updateLicState(r);
      if (!wasOk && r.ok){ toast('License đã được kích hoạt', 'ok'); }
      if (wasOk && !r.ok){
        toast('License đã hết hạn / bị hủy', 'err', 6000);
        setTimeout(() => resetUI(), 800);
      }
    }
  } catch(e){}
}, 30000);

/* ══════════════════════════════════════════════════════════════════════
   v20.0 — LICENSE GATE (màn khoá toàn màn hình)
   Hiện khi chưa có license hợp lệ. Chặn mọi thao tác cho tới khi
   kích hoạt thành công. Có nút "Get Key Free" mở link lấy key.
   ══════════════════════════════════════════════════════════════════════ */
let GATE_BUSY = false;
let KEY_URL_CACHE = '';

function gateMsg(html, kind){
  const el = $('#lgMsg');
  if (!el) return;
  el.className = 'lg-msg' + (kind === 'ok' ? ' ok' : '');
  el.innerHTML = html || '';
}
function gateShake(){
  const c = document.querySelector('.lg-gate-card');
  if (!c) return;
  c.classList.remove('lg-shake');
  void c.offsetWidth;
  c.classList.add('lg-shake');
}
function showGate(info){
  const g = $('#licGate');
  if (!g) return;
  g.classList.add('show');
  if (info){
    const h = $('#lgHwid');
    if (h) h.textContent = info.hwid || '—';
  }
  setTimeout(() => { const i = $('#lgKey'); if (i) i.focus(); }, 220);
}
function hideGate(){
  const g = $('#licGate');
  if (g) g.classList.remove('show');
}
window.showGate = showGate;
window.hideGate = hideGate;

async function gateActivate(){
  if (GATE_BUSY) return;
  const key = ($('#lgKey').value || '').trim();
  if (!key){
    gateMsg('Nhập key trước đã.');
    gateShake();
    return;
  }
  GATE_BUSY = true;
  const btn = $('#lgActivate');
  btn.disabled = true;
  gateMsg('<span class="spin"></span> Đang kích hoạt…');
  try {
    const r = await api().lic_activate(key);
    if (r.ok){
      gateMsg(`✓ Kích hoạt thành công! Hết hạn sau ${r.days || 1} ngày.`, 'ok');
      toast('Kích hoạt License thành công!', 'ok', 4000);
      setTimeout(() => { hideGate(); resetUI(); }, 900);
    } else {
      gateMsg('✖ ' + esc(r.error || 'Key không hợp lệ'));
      gateShake();
    }
  } catch(e){
    gateMsg('✖ ' + esc(e.message || String(e)));
    gateShake();
  } finally {
    GATE_BUSY = false;
    btn.disabled = false;
  }
}

async function openGetKey(){
  try {
    if (!KEY_URL_CACHE){
      const r = await api().get_key_info();
      KEY_URL_CACHE = (r && r.url) || '';
      const hint = $('#lgFreeHint');
      if (hint && r && r.local_page){
        hint.innerHTML = 'Trang lấy key đi kèm sẽ được mở. ' +
          'Chưa cấu hình link riêng — đặt <b>OLM_GET_KEY_URL</b> hoặc sửa ' +
          'trong Cài đặt.';
      }
    }
    const r = await api().open_get_key();
    if (r && r.ok){
      toast('Đã mở trang lấy key' + (r.mode === 'url' ? '' : ' (file đi kèm)'),
            'ok', 4000);
    } else {
      toast('Không mở được trang lấy key: ' + esc((r && r.error) || ''), 'err', 5000);
    }
  } catch(e){
    toast('Lỗi: ' + esc(e.message || String(e)), 'err');
  }
}

if ($('#lgActivate')) $('#lgActivate').onclick = gateActivate;
if ($('#lgGetKey')) $('#lgGetKey').onclick = openGetKey;
if ($('#btnGetKeyFree')) $('#btnGetKeyFree').onclick = openGetKey;
if ($('#lgCopyHwid')) $('#lgCopyHwid').onclick = async () => {
  const hwid = await api().lic_hwid();
  const ok = await copyTextRobust(hwid);
  gateMsg(ok ? '✓ Đã copy HWID: ' + esc(hwid) : '✖ Không copy được',
          ok ? 'ok' : '');
};
if ($('#lgRetry')) $('#lgRetry').onclick = async () => {
  gateMsg('<span class="spin"></span> Đang kiểm tra lại…');
  try {
    const lic = await api().lic_status();
    updateLicState(lic);
    if (lic.ok){
      gateMsg('✓ License hợp lệ!', 'ok');
      setTimeout(() => { hideGate(); resetUI(); }, 600);
    } else {
      gateMsg('Vẫn chưa có license hợp lệ.');
    }
  } catch(e){ gateMsg('✖ ' + esc(e.message || String(e))); }
};
if ($('#lgKey')) $('#lgKey').addEventListener('keydown', e => {
  if (e.key === 'Enter') gateActivate();
});

/* ══════════════════════════════════════════════════════════════════════
   v20.1 — HỆ THỐNG AUTO-UPDATE NGẦM TRỰC TIẾP TRONG TOOL (ẨN NGUỒN SÂU TRONG CODE)
   ══════════════════════════════════════════════════════════════════════ */
let PENDING_UPDATE = null;

async function openUpdateModal(){
  const modal = $('[data-update-modal]');
  if (!modal) return;
  const infoEl = $('[data-update-info]');
  const msgEl = $('[data-update-msg]');
  const applyBtn = $('[data-update-apply]');
  const restartBtn = $('[data-update-restart]');
  if (msgEl) msgEl.innerHTML = '';
  if (applyBtn) applyBtn.classList.add('hidden');
  if (restartBtn) restartBtn.classList.add('hidden');
  modal.classList.add('show');
  try {
    const cfg = await api().get_update_config();
    if (cfg && cfg.ok){
      if (infoEl){
        infoEl.innerHTML = `
          <div class="lic-info-row"><span class="lbl">Phiên bản hiện tại</span>
            <span class="val" style="color:var(--p1);font-weight:700">v${esc(cfg.current_version||'20.1')}</span></div>
          <div class="lic-info-row"><span class="lbl">Chế độ chạy</span>
            <span class="val">${cfg.mode === 'exe' ? 'Bản đóng gói (.EXE)' : 'Mã nguồn trực tiếp (.PY)'}</span></div>
          <div class="lic-info-row"><span class="lbl">Kênh phát hành</span>
            <span class="val" style="color:var(--p2)">● Máy chủ Cloud chính thức (Bảo mật)</span></div>
          <div class="lic-info-row"><span class="lbl">Mã kiểm tra SHA-256</span>
            <span class="val">${esc(cfg.sha256||'—')}</span></div>
        `;
      }
    }
  } catch(e){}
}
window.openUpdateModal = openUpdateModal;

function closeUpdateModal(){
  const modal = $('[data-update-modal]');
  if (modal) modal.classList.remove('show');
}
window.closeUpdateModal = closeUpdateModal;

async function checkUpdateNow(){
  const msgEl = $('[data-update-msg]');
  const checkBtn = $('[data-update-check]');
  const applyBtn = $('[data-update-apply]');
  if (msgEl) msgEl.innerHTML = '<div class="msg info"><span class="spin"></span> Đang kiểm tra bản cập nhật mới từ máy chủ phát hành…</div>';
  if (checkBtn) checkBtn.disabled = true;
  if (applyBtn) applyBtn.classList.add('hidden');
  try {
    const r = await api().check_update('');
    if (!r || !r.ok){
      if (msgEl) msgEl.innerHTML = `<div class="msg err">✖ ${esc((r && r.error) || 'Không thể kết nối máy chủ cập nhật')}</div>`;
      return;
    }
    PENDING_UPDATE = r;
    if (r.has_update){
      if (msgEl){
        msgEl.innerHTML = `
          <div class="msg ok">
            ✓ Phát hiện bản cập nhật mới: <b>v${esc(r.latest_version)}</b>!<br>
            <span style="color:var(--tx2);font-size:12px">${esc(r.changelog || '')}</span>
          </div>`;
      }
      if (applyBtn) applyBtn.classList.remove('hidden');
      const topBtn = $('[data-update-btn]');
      if (topBtn){
        topBtn.innerHTML = `[⬆] v${esc(r.latest_version)}`;
        topBtn.style.borderColor = 'var(--p1)';
        topBtn.style.color = 'var(--p1)';
      }
    } else {
      if (msgEl){
        msgEl.innerHTML = `<div class="msg ok">✓ Bạn đang sử dụng phiên bản mới nhất (<b>v${esc(r.current_version)}</b>).</div>`;
      }
    }
  } catch(e){
    if (msgEl) msgEl.innerHTML = `<div class="msg err">✖ Lỗi: ${esc(e.message||String(e))}</div>`;
  } finally {
    if (checkBtn) checkBtn.disabled = false;
  }
}

async function applyUpdateNow(){
  const msgEl = $('[data-update-msg]');
  const applyBtn = $('[data-update-apply]');
  const restartBtn = $('[data-update-restart]');
  const sha = (PENDING_UPDATE && PENDING_UPDATE.sha256) || '';
  if (applyBtn) applyBtn.disabled = true;
  if (msgEl) msgEl.innerHTML = '<div class="msg info"><span class="spin"></span> Đang tải và cài đặt bản cập nhật…</div>';
  try {
    const r = await api().perform_update('', sha);
    if (r && r.ok){
      if (msgEl) msgEl.innerHTML = `<div class="msg ok">✓ ${esc(r.message || 'Cập nhật hoàn tất!')}</div>`;
      if (applyBtn) applyBtn.classList.add('hidden');
      if (restartBtn) restartBtn.classList.remove('hidden');
      toast('Đã cập nhật thành công! Bấm Khởi động lại để áp dụng.', 'ok', 5000);
      log(`[UPDATE] Đã cập nhật thành công lên v${r.new_version || ''}`, 'ok');
    } else {
      if (msgEl) msgEl.innerHTML = `<div class="msg err">✖ ${esc((r && r.error) || 'Cập nhật thất bại')}</div>`;
    }
  } catch(e){
    if (msgEl) msgEl.innerHTML = `<div class="msg err">✖ Lỗi: ${esc(e.message||String(e))}</div>`;
  } finally {
    if (applyBtn) applyBtn.disabled = false;
  }
}

if ($('[data-update-btn]')) $('[data-update-btn]').onclick = openUpdateModal;
if ($('[data-update-close]')) $('[data-update-close]').onclick = closeUpdateModal;
if ($('[data-update-check]')) $('[data-update-check]').onclick = checkUpdateNow;
if ($('[data-update-apply]')) $('[data-update-apply]').onclick = applyUpdateNow;
if ($('[data-update-restart]')) $('[data-update-restart]').onclick = async () => {
  toast('Đang khởi động lại OLM Tool Pro…', 'info', 3000);
  await api().restart_app();
};

setTimeout(async () => {
  try {
    if (!window.pywebview || !window.pywebview.api) return;
    const r = await api().check_update('');
    if (r && r.ok && r.has_update){
      PENDING_UPDATE = r;
      const topBtn = $('[data-update-btn]');
      if (topBtn){
        topBtn.innerHTML = `[⬆] v${esc(r.latest_version)}`;
        topBtn.style.borderColor = 'var(--p1)';
        topBtn.style.color = 'var(--p1)';
      }
      toast(`Có bản cập nhật mới v${r.latest_version}! Bấm [⬆ UPDATE] để cập nhật ngay.`, 'info', 6000);
    }
  } catch(e){}
}, 3500);

</script>
</body></html>
"""


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    # ── v20.2: Nạp Hot-Patch (Cách 1: file olm.py raw) khi chạy dưới dạng .exe ──
    if getattr(sys, "frozen", False) and not os.environ.get("OLM_HOTPATCH_ACTIVE"):
        try:
            if HOTPATCH_F.exists():
                patch_code = HOTPATCH_F.read_text(encoding="utf-8")
                m_ver = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', patch_code)
                patch_ver = m_ver.group(1).strip() if m_ver else "0"
                if OLMApp._parse_ver_tuple(patch_ver) >= OLMApp._parse_ver_tuple(APP_VERSION):
                    os.environ["OLM_HOTPATCH_ACTIVE"] = "1"
                    patch_ns = {"__name__": "__hotpatch__", "__file__": str(HOTPATCH_F)}
                    exec(compile(patch_code, str(HOTPATCH_F), "exec"), patch_ns)
                    if callable(patch_ns.get("main")):
                        patch_ns["main"]()
                        return
        except Exception as e:
            _log_telemetry("HOTPATCH_LOAD_FAIL", error=str(e)[:200])

    api = OLMApp()
    # ── v20.2: cửa sổ kiểu app native ──
    # - Kích thước vàng cho dashboard 3 cột, căn giữa màn hình
    # - Giữ title bar HỆ ĐIỀU HÀNH (nút minimize/maximize/close chuẩn Windows)
    #   => không rủi ro người dùng mất nút đóng như chế độ frameless.
    # - text_select=False ngoài ô nhập -> cảm giác app native, không bôi đen UI
    _win_kwargs = dict(
        title=f"OLM Tool Pro v{APP_VERSION} by Crayz",
        html=INDEX_HTML, js_api=api,
        width=1460, height=940, min_size=(1020, 700),
        text_select=False, confirm_close=False, easy_drag=False,
    )
    try:
        webview.create_window(**_win_kwargs)
    except TypeError:
        # phiên bản pywebview cũ không hỗ trợ easy_drag
        _win_kwargs.pop("easy_drag", None)
        webview.create_window(**_win_kwargs)
    icon_path = None
    if sys.platform == "win32":
        if ICON_ICO.exists(): icon_path = ICON_ICO
        elif ICON_PNG.exists(): icon_path = ICON_PNG
    else:
        if ICON_PNG.exists(): icon_path = ICON_PNG
        elif ICON_ICO.exists(): icon_path = ICON_ICO

    def _start(with_icon):
        try:
            kwargs = {"debug": False, "private_mode": False}
            if with_icon and icon_path is not None:
                try: webview.start(icon=str(icon_path), **kwargs)
                except Exception as e:
                    msg = str(e)
                    if ("icon" in msg.lower() or "picture" in msg.lower()
                            or "ArgumentException" in msg):
                        print(f"[warn] Icon load failed: {msg}")
                        webview.start(**kwargs)
                    else: raise
            else:
                webview.start(**kwargs)
            return True
        except SystemExit: raise
        except Exception as e:
            print(f"[ERROR] webview.start failed: {e}")
            return False

    try:
        if not _start(with_icon=True):
            print("[ERROR] Không mở được cửa sổ")
    except SystemExit: pass
    except Exception as e:
        tb = traceback.format_exc()
        _log_crash(tb)
        _msgbox("OLM Tool Pro — Crash",
                f"Ứng dụng gặp lỗi:\n\n{str(e)[:500]}\n\nLog: {CRASH_F}")


if __name__ == "__main__":
    try: main()
    except SystemExit: pass
    except Exception:
        tb = traceback.format_exc()
        _log_crash(tb)
        _msgbox("OLM Tool Pro — Fatal",
                f"Lỗi nghiêm trọng:\n\n{tb[-800:]}")


# ═══════════════════════════════════════════════════════════════════════════════
# CHANGELOG v19.0 -> v19.1
# ═══════════════════════════════════════════════════════════════════════════════
# Port từ userscript "OLM Hack Pro - OpiCrayzz" (đã reverse-engineer tầng VM
# `vmg_668921`, blob 18 chứa nguyên bản source của extractQuestionData + 11 tầng
# anti-detect). TẤT CẢ thay đổi nằm trong section `# === v19.1 - PORT FROM
# USERSCRIPT ===` hoặc được comment `v19.1`.
#
# A. extract_questions() — port 1:1 `extractQuestionData`
#    A1. Thêm helper `_find_all_nodes_js()` — port chính xác `findAllNodes`:
#        CHỈ đệ quy vào mảng `children` khi node có `children`, ngược lại mới
#        duyệt các key khác (bỏ `type`/`children`). Bản cũ `find_nodes` luôn
#        duyệt mọi key -> sai thứ tự/số lượng node ở một số đề.
#    A2. q_type 9 (underline): `correctAnswers` = index int tăng dần của node
#        có `correct === True`; chỉ push khi có >= 1 index (khớp bản gốc).
#    A3. q_type 21/22 (group): port đầy đủ `processGroupRecursive`:
#        - đệ quy cả `paragraph`/`extended-text` (find olm-input-text +
#          fillme-input, selecttext -> "0", còn lại -> content.split('||')[0])
#        - `sub_labels` = chr(65 + int(correctIdx[0])) (fallback "0")
#        - `sub_orders` = node.order hoặc list(range(numOptions))
#        - `sub_option_counts`, `list_idq` (regex `<hr id="([^"]+)"` từ q.content)
#    A4. q_type 10 (group drag): `answerIndices` giữ **int** tăng dần toàn cục
#        (bản cũ trả list of list of str) -> khớp `data_log`.
#    A5. q_type 3/11 (fill pool): `optionCount = len(inputs)` cho q_type 11
#        (KHÔNG phải poolSize) — khớp `finalSize = (qType===11)?inputs.length:poolSize`.
#    A6. q_type 5/6/20: `correctAnswers` giữ nguyên **int** 0..n-1 (bản gốc
#        `Array.from(Array(n).keys())`), không stringify.
#    A7. q_type 6: bổ sung `optionCount` (bản cũ thiếu).
#    A8. q_type 2: giữ `max_score = 1` đúng bản gốc.
#    A9. Fallback q_type 1/13: bổ sung nhánh `multi_select` — true-false có
#        n_true >= 2 && numOptions >= 3 -> `correctAnswers` là mảng "0"/"1".
#    A10. Union base (options/rich_content/text_html/explanation) giữ nguyên
#         để KHÔNG phá vỡ UI answers_html + DOCX.
#
# B. _build_choose_label()
#    B1. Port đúng thứ tự nhánh + regex `^[A-Z]$` cho label chữ.
#    B2. q_type 13 multi -> list index int; q_type 5/6/3/11 -> list int.
#    B3. Fallback `"A"` (tương đương `String.fromCharCode(65 + NaN)` của JS).
#
# C. _build_data_log() / _build_choose_log() / _build_answer_strings()
#    C1. Thêm helper `_label_to_str()` — nhãn nhiều giá trị nối bằng ','.
#    C2. `data_log` đủ 8 key: _id, nx, _score, iframe, count_redo, correct,
#        user_answer, choose. `user_answer = None` khi câu bị tính sai.
#    C3. `choose_log` = [[{ind, t, l}, ...]] — mảng lồng (nested array).
#    C4. `_build_answer_strings` -> `ans`/`user_ans` nối bằng '|', nhãn con
#        bằng ',' (khớp `'ans':'...|...'` + `'user_ans'` của userscript).
#
# D. _submit_via_dom_fill()
#    D1. Pass 1: gọi `window.__olm_fill_special(questions)` (fill q3/11 pool,
#        q13 multi checkbox, q5/6/9/10/20 ordering/matching/underline/drag).
#    D2. Pass 2: JS nội bộ — normalize `toLowerCase().replace(/\s+/g,' ')` +
#        so khớp **40 ký tự đầu** của q.text với innerText parent.
#    D3. fireInput dispatch CẢ `input` VÀ `change` với `bubbles: true`.
#    D4. q_type 3/11: gán pool index tuần tự theo index input trong câu.
#    D5. q_type 13 multi: tick `checked = true` cho từng index trong label.
#    D6. Radio: so khớp `r.value.toUpperCase() === letter` trước, fallback index.
#    D7. Trả `special` + `fill` gộp; gọi `_log_dom_fill()` để log telemetry.
#
# J. AUTOFILL_JS — bổ sung DOM-fill cho q_type đặc biệt
#    J1. `window.__olm_fill_special(questions)`:
#        - q5/20: sắp xếp lại DOM children theo `correctAnswers`
#        - q6: dispatch mousedown/mouseup/click cho link-list
#        - q9: click `.under-line` tại index `correctAnswers`
#        - q10: gán DOM `.position-column` theo `answerIndices`
#        - q13 multi: set `checked = true` từng checkbox đúng
#        - q3/11: gán pool index vào từng input text theo index
#    J2. Thêm helper `fireMouse()` + `normalize()`.
#
# E. ANTI_DETECT_JS — đủ 11 tầng (verify + chỉnh khớp nguyên bản)
#    E1. L2 JSON.parse forceOrder: đã có đủ `link-list` + `group-list`.
#    E2. L3 detectQuestion: wrap giữ `p.config.mix=false`, `p.shuffle=0`,
#        `p.random=0`, push `__OLM_CAPTURED_QUESTIONS`, wrap `getMany`.
#    E3. L5 EXAM_UI setter: `config.not_shuffle=1` + `config.shuffle=0` +
#        `config.mix=false`.
#    E4. L6/L7 patchCateUI + patchAuth: có `_fakeVipApplied`, `initVipLimit`
#        no-op, `showFooterToolbarPractice()`, defineProperty `auth` setter.
#    E5. L7b Storage.setItem: **bỏ** chặn `olm_intro_shown` để khớp nguyên bản
#        (chỉ chặn `prcc` + tiền tố `tmp_count_q_categories`).
#    E6. L8 block events: giữ cả 3 lớp (Document.prototype, window,
#        EventTarget.prototype) + nullFunc cho oncontextmenu/onselectstart/
#        oncopy/oncut/onpaste.
#    E7. L9 hotkeys: F12, Ctrl+U, Ctrl+Shift+I, Ctrl+S (stopImmediatePropagation
#        + preventDefault) + contextmenu.
#    E8. L10 fullscreen: chặn requestFullscreen + webkit + moz + ms;
#        `document.exitFullscreen` no-op; override visibilityState/hidden.
#    E9. L11 VIP events: chặn `onVipLimit` + `onGuestLimit` (capture).
#    E10. Giữ `window.__olmOlmDecode` (XOR base64, key '1047823200').
#
# F. scan_assignments()
#    F1. `parseInfo` (isDone) chặt hơn: regex class nhận `_`, `-`, space và
#        tiếng Việt có dấu (`đã làm`/`đã-làm`/`da_lam`), attr `data-done=""`,
#        thêm querySelector `.done/.completed/.finished/[data-done="1"]`,
#        text `đã làm|hoàn thành|xem lại|done|completed|finished`.
#    F2. Giữ v1 selector `tr.my-given-courseware-item` + v2 selector
#        `[class*="assignment-item"]`/`[data-assignment-id]`.
#    F3. Fallback quét `a[href*="/chu-de/"]`, `/bai-tap/`, `/lam-bai/` với
#        loại trừ `khoa-hoc|google|facebook|youtube|cloudflare|/bg/|/lop/`.
#
# G. download_word() — đủ 8 fallback chains (verify, không đổi hành vi)
#    1) POST /download-word-for-user {id_cate, showAns:1, questionNotAppproved:0}
#    2) POST variant không có questionNotAppproved
#    3) POST variant có redo:1
#    4) GET /download-word-for-user?id_cate&showAns=1&questionNotAppproved=0
#    5) GET /course/teacher-categories/{id}/get-crt-ans-file
#    6) GET variant &redo=1
#    7) Browser evaluate (Playwright, đọc arrayBuffer -> b64)
#    8) Render nội bộ bằng `_make_docx`
#    + [v19.1] log `HOOK_download_word_browser_evaluate` khi dùng chain 7.
#
# H. _make_docx()
#    H1. Title 32 half-pt (16pt) bold, màu `1F4E79`, canh giữa; body Calibri.
#    H2. Dòng phụ đề thêm phân bố q_type (từ `_qtype_histogram`).
#    H3. Thêm `word/footer1.xml` + content-type + relationship + `sectPr`
#        `footerReference`: "OLM Tool Pro v19.3 · Bài <id> · Trang PAGE".
#    H4. `wp:docPr id` / `pic:cNvPr id` tăng dần (trước đây luôn id=1 -> Word
#        có thể cảnh báo trùng id khi có nhiều ảnh).
#    H5. Footer/sectPr page size A4 (11906x16838 twips) + margin chuẩn.
#
# I. submit() — payload khớp userscript
#    I1. Thêm `tl_score`, `missed`, `count_redo`, `time_stored`, `_id`, `nx`.
#    I2. `choose_log` thêm dạng flatten `choose_log[i][ind|t|l]` song song JSON.
#    I3. `type_vip` mặc định "1"; `name_user` = "0" (khớp biến thể A).
#    I4. `date_end = now + 1000` (khớp `Math.floor(Date.now()/1000)+1000`).
#    I5. Trả thêm `qtype_dist` + `log_tab_blocked` trong kết quả.
#
# K. Chặn log=tab
#    K1. `page.route("**/course/teacher-static*")` -> `route.fulfill(200,
#        {"blocked":true})` mọi POST có `log=tab` / `log%3Dtab`.
#    K2. Đếm số request bị chặn -> `log_tab_blocked` + log `LOG_TAB_BLOCKED`.
#    K3. `route.continue_()` với fallback `route.fallback()` nếu lỗi.
#
# L. Cache
#    L1. Khoá cache `id_cate` + biến thể `id_cate#q_count` (tránh stale khi OLM
#        cập nhật số câu của cùng một bài).
#    L2. `_qcache_get()` / `_qcache_put()` — TTL 1h, auto-clean > 24h mỗi lần ghi.
#    L3. `clear_qcache(id_cate)` — force refresh ("Làm lại").
#    L4. `fetch_questions(force=True)` bỏ qua cache (đã hỗ trợ sẵn).
#
# M. Log/telemetry
#    M1. `_qtype_histogram()` + `_log_qtype_distribution()` — log phân bố q_type
#        mỗi bài (QTYPE_DIST).
#    M2. `_log_hook()` — log hook detect (cache_hit, download_word_browser...).
#    M3. `_log_dom_fill()` — log DOM_FILL {text, radio, checkbox, select,
#        order, match, errors}.
#
# N. Phụ trợ ngoài olm.py  [ĐÃ THAY Ở v19.2 — xem mục O]
#    N1. `key_generator.html` — trang HTML tĩnh hiện key từ Supabase:
#        - mỗi máy/IP (fingerprint canvas+WebGL+UA+screen+tz+hw) chỉ hiện
#          **1 key chưa từng xuất hiện trong 1 ngày** (localStorage theo ngày);
#        - key đã hiện KHÔNG bao giờ lặp lại (danh sách "đã hiện" vĩnh viễn);
#        - nút "ĐỔI KEY KHÁC (reset)" ghi key cũ vào lịch sử rồi lấy key kế;
#        - panel ⚙ cấu hình url/anon/bảng/cột + luật chọn key
#          (unused / any / unused-only);
#        - fallback bỏ `order=created_at` khi bảng không có cột đó;
#        - chỉ gọi REST `/rest/v1/<bảng>`, KHÔNG thêm secret mới.
#    N2. `tests/test_extract_v191.py` + `tests/_stubs.py` — test 19 nhóm
#        (q_type 1/2/3/5/6/9/10/11/13/13-multi/20/21-22, build helpers, DOCX,
#        cache key, 11 tầng anti-detect, syntax JS qua `node --check`).
#
# ⚠ GHI CHÚ KỸ THUẬT
#    - `[CẦN THÊM CONTEXT]`: biến thể B của submit (blob 82) truyền `ans`/
#      `user_ans` cùng `time_stored`/`date_end`/`ended`/`save_star`/`choose_log`
#      qua jQuery `$.ajax` trong một `<div>` tạm; phần build `choose_log`
#      flatten (`choose_log[0][ind|t|l]`) chỉ thấy được 1 phần qua chuỗi
#      `' |(function(){ ... choose_log:'` nên đã dựng theo đúng thứ tự tham số
#      quan sát được. Nếu OLM trả lỗi validate, thử tắt flatten bằng cách bỏ
#      khối `choose_log[{_i}][...]` trong `submit()`.
#    - `[CẦN THÊM CONTEXT]`: q_type 9/10 cần DOM OLM thật để xác nhận class
#      `.under-line` / `.position-column`; nhánh parse đã khớp 100% tree JSON,
#      còn phần DOM-fill dùng heuristic (đã ghi trong AUTOFILL_JS).
#    - Rủi ro pháp lý/ToS: việc dùng các hook này có thể vi phạm điều khoản
#      OLM và dẫn tới khoá tài khoản. Tự chịu trách nhiệm khi sử dụng.
# ═══════════════════════════════════════════════════════════════════════════════
# CHANGELOG v19.1 -> v19.2 (đã có ở trên)
# ═══════════════════════════════════════════════════════════════════════════════
# O. FIX "11/10 điểm" (submit)
#    O1. NGUYÊN NHÂN: payload gửi `tl_score=1` (tự luận) + `tn_score` = điểm
#        trắc nghiệm -> OLM CỘNG DỒN 2 thành phần thành 11/10.
#    O2. `tl_score` = "0". Chỉ còn 1 thành phần điểm.
#    O3. `max_score` = "10" (hằng số MAX_SCORE), không phụ thuộc số câu; trước
#        đây `per_q_max = 10/N` rồi cộng dồn có thể lệch do làm tròn.
#    O4. `score = min(max_score, max(0, n_correct * per_q_max))` — KẸP TRẦN
#        đúng 10.0 cho MỌI mode (full/minus1/custom) và mọi số câu.
#    O5. `mode=minus1` trừ theo điểm/câu (`10/N`) thay vì trừ cứng 1 điểm.
#    O6. Trả thêm `max_score`, `per_q_max` trong kết quả submit.
#    O7. Log `SCORE_CALC` + `SCORE_CLAMPED` để soi lại nếu OLM báo lệch.
#    O8. Test: `tests/test_review_v192.py::TEST 1/2` — quét 8 giá trị N × 7 tổ
#        hợp mode/custom, xác nhận `tl_score + tn_score <= 10` luôn đúng.
#
# P. XEM LẠI BÀI ĐÃ NỘP (đáp án trống khi xem lại)
#    P1. NGUYÊN NHÂN: khi bài đã nộp, `get-question-of-ids` KHÔNG còn trả
#        `json_content` -> `extract_questions()` ra rỗng -> trang đáp án chỉ có
#        điểm, không có đáp án. Userscript tránh bằng `/get-crt-ans-file`.
#    P2. Thêm `OLMClient._fetch_ans_file(page, id_cate)` — port đúng luồng
#        userscript: ưu tiên `_apiGet`, fallback `fetch`, rồi curl_cffi; decode
#        bằng chính `CATE_UI.OlmEncode.decode` của OLM (hàm này KHÔNG có trong
#        userscript nên không thể port sang Python — phải gọi trong page).
#    P3. Thêm `_parse_ans_file(decoded)` — nhận diện 6 shape:
#        string JSON / double-encoded / HTML thô / {data|result|questions|list|
#        list_quiz|items} / {record:{list_quiz}} / list id thuần.
#        Khi chỉ có list id -> KHÔNG bịa câu hỏi (trả rỗng).
#    P4. Thêm `_sanitize_ans_html()` — bỏ `<script>/<style>/<iframe>/<form>`,
#        mọi thuộc tính `on*`, `javascript:` trong href/src trước khi nhúng.
#    P5. `fetch_questions()`: khi `qs` rỗng thì thử `_fetch_ans_file`; lưu
#        `_ans_html` / `_ans_text` / `_from_ans_file` vào `data_cate`.
#    P6. `get_answers_html()`: bỏ điều kiện "phải có qs"; chấp nhận chế độ
#        review, trả `from_ans_file` + `answered`; mẫu số điểm lấy từ
#        `_record.totalq` khi không có câu hỏi.
#    P7. Test: `tests/test_review_v192.py::TEST 6/7` (sanitize + 6 shape).
#
# Q. RENDER LẠI TRANG ĐÁP ÁN (dễ nhìn, đúng thứ tự, không lặp)
#    Q1. `_ans_dedup_questions()` — bỏ câu trùng `id`, bỏ câu rỗng hoàn toàn.
#    Q2. `_ans_order_index()` — hiển thị option theo ĐÚNG `order` gốc của OLM
#        (trước đây luôn 0..n-1 -> nhãn A/B/C lệch khi OLM xáo).
#    Q3. `_ans_option_rows()` — tính đáp án đúng theo index GỐC, không theo vị
#        trí hiển thị; q_type 5/6/20 KHÔNG tick ✓ vào từng phương án (thứ tự
#        đúng đã thể hiện riêng) — trước đây tick hết gây hiểu sai.
#    Q4. `_ans_answer_block()` — khối "ĐÁP ÁN" riêng cho từng q_type:
#        q13-multi (chip ✓), q13 đơn (Đúng/Sai từng mệnh đề, màu), q21/22
#        (đáp án từng phần), q2/3/11 (giá trị thật), q5/20 (dãy thứ tự),
#        q6 (cặp A–1), q9 (vị trí ✓), q10 (nhóm), fallback (nhãn chữ).
#    Q5. q_type 3/11: thêm `answer_texts` trong `extract_questions` để hiện
#        CHỮ đáp án thay vì chỉ số pool ("1 0 2 2" vô nghĩa).
#    Q6. Sửa lỗi "A. <cả câu hỏi>": chỉ dùng `text_all` làm phương án cho
#        q_type 1/13, và loại bỏ chính câu hỏi khỏi danh sách đó.
#    Q7. `_ans_max_points()` — tổng `max_score` từng câu; `_answers_shell()`
#        nhận `max_points` nên hiển thị "Đúng 7/11 câu · 7/11 điểm" thay vì
#        "7/13 câu · 5.38/10 điểm" (mẫu số lệch do cộng phần của q21/22).
#    Q8. Số câu hiển thị theo CÂU (khớp điểm đã nộp) + ghi chú "(gồm N phần)".
#    Q9. Thêm banner "Bài đã nộp" khi render từ file đáp án.
#    Q10. Viết lại CSS: palette đồng bộ UI tool, khối đáp án nổi bật, option
#         đúng có tick + nền xanh, bảng Đúng/Sai, chip đáp án, thanh cuộn,
#         `min-width:0` chống tràn, word-break cho nội dung dài.
#    Q11. Test: `tests/test_review_v192.py::TEST 3/4/5/9`.
#
# R. TRANG LẤY KEY MỚI — `key_generator.html` (thay toàn bộ bản v19.1)
#    R1. KHÔNG lộ Supabase: bỏ hẳn panel cấu hình, bỏ anon key, bỏ tên
#        bảng/cột. Trang chỉ biết MỘT URL endpoint trong `claim-config.js`.
#    R2. Server quyết định tất cả: `sql/01_claim_key.sql` tạo bảng
#        `key_claims` (RLS bật, `REVOKE ALL FROM anon`), hàm
#        `public.claim_key(p_device)` `SECURITY DEFINER`:
#          - đọc IP thật từ `cf-connecting-ip` / `x-forwarded-for` / `x-real-ip`
#          - BĂM IP (SHA-256 + salt) — KHÔNG lưu IP thô
#          - khoá `claim_key = ip_hash || ':' || device_hash`, UNIQUE
#          - đã lấy trong 24h -> trả `already_claimed` + `seconds_left`
#          - chọn key còn trống `FOR UPDATE SKIP LOCKED` (chống 2 request cùng
#            lúc), `not exists` loại key đã phát, vòng retry 5 lần khi
#            `unique_violation`
#          - chỉ `GRANT EXECUTE` cho anon; bảng vẫn không đọc được
#    R3. KHÔNG reset: không có nút đổi key; bản ghi `key_claims` là vĩnh viễn,
#        `key_value` cũ không bao giờ quay lại pool. Muốn mở khoá phải chạy SQL
#        bằng service_role.
#    R4. KHÔNG localStorage: key chỉ nằm trong `sessionStorage` (mất khi đóng
#        tab); `purgeLegacy()` xoá mọi khoá `oltp_*` của bản v19.1 khi tải trang
#        (không đụng dữ liệu khác của người dùng).
#    R5. KHÔNG lộ thông tin: payload chỉ `{device: <sha256>}`; không log key;
#        UI che key mặc định (blur), có nút con mắt + copy; thiết bị chỉ hiện
#        dạng rút gọn.
#    R6. Sau khi lấy -> KHOÁ: lần 2 server trả `already_claimed`, UI ẩn nút và
#        hiện đồng hồ đếm ngược tới lúc mở lại.
#    R7. UI ĐỒNG BỘ TOOL (không theo userscript): cùng palette
#        `--bg/--panel/--bd/--p1/--p2/--p3`, cùng `--grad`, `brand-icon` ⚡,
#        `brand-name` gradient động, `brand-ver`, glassmorphism
#        `backdrop-filter: blur(22px)`, bo góc 14px, nền radial gradient
#        chuyển động, nút gradient có hiệu ứng shine, skeleton/spinner,
#        `prefers-reduced-motion`, responsive <=420px.
#    R8. `sql/edge-function-claim-key.ts` — Edge Function giữ `service_role` ở
#        server, CORS giới hạn origin, rate-limit 20 req/phút/IP, validate
#        input, ẩn chi tiết lỗi DB.
#    R9. `sql/README.md` — hướng dẫn từng bước + 2 cách dựng endpoint + checklist
#        bảo mật + cách mở khoá thủ công.
#    R10. Test: `tests/test_key_page_node.js` chạy LOGIC THẬT của trang trong
#         Node (DOM/session/localStorage/fetch stub) — 11 nhóm, gồm cả kiểm tra
#         tĩnh "không có Supabase/anon key/nút reset/localStorage.setItem".
#
# S. Phụ trợ khác
#    S1. `OLMApp.key_page_path()` + `OLMApp.open_key_page()` — mở trang key từ
#        tool (tìm trong RES_DIR / APP_DIR / thư mục script).
#    S2. `OLMToolPro.spec` + `OLMToolPro_Debug.spec` — bundle thêm
#        `key_generator.html`.
#    S3. `tests/test_sql_structure.py` — kiểm tra cấu trúc SQL không cần
#        PostgreSQL: dollar-quote, ngoặc, hàm chưa định nghĩa, cột NOT NULL,
#        19 thành phần bảo mật bắt buộc.
#    S4. `tests/make_ans_preview.py` — sinh `_work/ans_page_v192.html` +
#        `_work/ans_review_v192.html` để QA giao diện.
# ═══════════════════════════════════════════════════════════════════════════════
# CHANGELOG v19.2 -> v19.3
# ═══════════════════════════════════════════════════════════════════════════════
# T. FIX LỖI SQL 42883 — `function pg_catalog.coalesce(text, unknown) does not exist`
#    T1. NGUYÊN NHÂN: COALESCE / NULLIF / GREATEST / LEAST / EXTRACT / MD5 / TRIM
#        là CÚ PHÁP đặc biệt của SQL, KHÔNG phải hàm nằm trong `pg_catalog`.
#        Viết `pg_catalog.coalesce(...)` khiến PostgreSQL đi tìm hàm tên
#        `coalesce` trong schema `pg_catalog` -> không có -> 42883.
#    T2. Bỏ MỌI tiền tố `pg_catalog.` khỏi code SQL (giữ `set search_path` để
#        vẫn chống bị shadow). Comment giải thích lỗi vẫn giữ nguyên chữ
#        `pg_catalog.coalesce` nhưng validator đã bỏ comment trước khi kiểm tra.
#    T3. `md5(a, b, c)` (3 tham số) cũng là lỗi 42883 vì MD5 chỉ nhận 1 tham số
#        -> bỏ luôn MD5 fallback trong `claim_key` (pgcrypto đã được tạo ở ▸ 0).
#    T4. LỖI CÙNG HỌ (sẽ nổ ngay sau khi sửa T1): `oltp_hash_ip` khai
#        `immutable` nhưng gọi `current_setting()` (là `stable`) -> PostgreSQL
#        từ chối tạo hàm. Sửa: `oltp_hash_ip(p_ip text, p_salt text)` nhận salt
#        qua THAM SỐ; `claim_key` (stable) đọc `current_setting('oltp.salt')`
#        rồi truyền vào.
#    T5. Cập nhật GRANT/REVOKE theo CHỮ KÝ MỚI `oltp_hash_ip(text, text)`.
#    T6. Thêm khối hướng dẫn `drop function if exists public.oltp_hash_ip(text);`
#        + `drop function ... claim_key(text);` ở cuối file — vì
#        `create or replace` KHÔNG đổi được danh sách tham số.
#    T7. `tests/test_sql_structure.py` thêm mục 8 (regression): bắt đúng
#        pg_catalog trên hàm cú pháp đặc biệt, md5() sai số tham số, hàm
#        IMMUTABLE gọi current_setting()/now(), GRANT dùng chữ ký cũ.
#
# U. CHỈ PHÁT KEY CÒN HẠN >= 1 NGÀY (yêu cầu mới)
#    U1. `oltp_config()` thêm `'min_valid_hours': 24` và `'allow_no_expiry': true`.
#    U2. Điều kiện chọn key trong `claim_key` đổi từ
#          `expires_at is null or expires_at > now()`
#        thành
#          `((expires_at is null and <allow_no_expiry>)
#            or expires_at > now() + make_interval(hours => <min_valid_hours>))`
#        -> key còn hạn < 24 giờ (kể cả đúng 24 giờ) KHÔNG được phát.
#    U3. `allow_no_expiry = true` -> key `expires_at IS NULL` coi là vĩnh viễn
#        và vẫn phát. Đặt `false` nếu muốn bắt buộc mọi key phải có hạn.
#    U4. Thông báo `out_of_stock` đổi thành "hết key còn hạn sử dụng" và trả
#        thêm `min_valid_hours` để dễ chẩn đoán.
#    U5. `tests/test_sql_structure.py` thêm mục 9: xác nhận có
#        `min_valid_hours`, `allow_no_expiry`, và điều kiện
#        `now() + make_interval(hours => %s)`.
#
# V. CẬP NHẬT TÀI LIỆU
#    V1. `sql/00_preflight.sql`: khối (3) tách riêng
#        `sap_het_han_bi_loai` (key bị loại vì còn < 24h) và `khong_co_han`;
#        thêm khối (3b) liệt kê key sắp bị loại.
#    V2. `sql/02_verify_and_test.sql`: thêm khối (7) kiểm tra MỌI key đã phát
#        đều còn hạn >= 24h (query trả `so_key_vi_pham`, kỳ vọng 0).
#    V3. `sql/HUONG-DAN-SETUP.md`:
#        - §0 mới: giải thích 2 luật (hạn >= 1 ngày; 1 key/máy/24h, không reset)
#          kèm bảng `expires_at` -> có được phát hay không
#        - §2.4: mẫu `oltp_config()` có `min_valid_hours` + `allow_no_expiry`
#        - §2.7 mới: chẩn đoán + cách sửa lỗi 42883 (kèm bảng 3 lỗi cùng họ)
#        - §7.1: bảng nguyên nhân "hết key" + câu lệnh gia hạn
#        - §8.3b mới: bảng 8 mã lỗi SQL (42883/0A000/42P13/42723/42710/42501/3F000)
#        - §9.1: thêm câu lệnh gia hạn + thêm key vĩnh viễn/có hạn
#    V4. `sql/README.md` + `key_generator.html` + `olm.py`: bump v19.3.
# ═══════════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════
# CHANGELOG v19.3 -> v20.0
# ═══════════════════════════════════════════════════════════════════════════════
# W. LÀM BÀI THẬT SỰ TRONG DOM (thay vì chỉ POST dữ liệu)
#    W1. NGUYÊN NHÂN CŨ: `_submit_via_dom_fill` chủ yếu gán `input.value` /
#        `.checked` rồi POST payload thô. Gán thuộc tính KHÔNG đủ để OLM ghi
#        nhận câu trả lời: radio không được "chọn" thật, sortable không đổi
#        thứ tự, underline không được đánh dấu -> bài nộp nhưng không có tick.
#    W2. Phân tích lại userscript (blob 18 + 88 + 126):
#          blob 126: "li.correctAnswer", "$input[data-accept]",
#                    ".selecttext, .dragtext", ".under-line"
#          blob 88:  "ol:not(.true-false)", "ol.true-false", "li", "data-accept"
#        => OLM render <ol>/<li> cho quiz-list/true-false, input[data-accept]
#           cho điền khuyết, .selecttext/.dragtext cho chọn từ, .under-line
#           cho gạch chân.
#    W3. Thêm hằng `REAL_SUBMIT_JS` (~21 KB) — lớp "làm bài thật":
#        • `realClick()`: pointerdown → mousedown → focus → pointerup →
#          mouseup → click(). Đủ để jQuery/OLM nhận là thao tác người dùng.
#        • `realDrag()`: pointerdown/mousedown/dragstart + 6 bước mousemove +
#          dragover/drop (DragEvent có DataTransfer) + mouseup/pointerup/
#          dragend → kích hoạt SortableJS / jQuery UI sortable / HTML5 DnD.
#        • `setNativeValue()`: dùng property descriptor của `value` rồi phát
#          input/change/keyup/blur như người dùng gõ.
#        • `__olm_fill_real(questions)` xử lý 10 nhóm:
#            1 input/textarea  2 radio (khớp value → data-accept →
#            data-answer → index)  3 checkbox (q13 multi)  4 select
#            5 .selecttext  6 pool điền + kéo-thả từ kho  7 sắp xếp q5/q20
#            (kéo từng item về đúng vị trí + đồng bộ DOM + trigger
#            `sortupdate`)  8 nối cặp q6  9 gạch chân q9  10 kéo nhóm q10.
#          Trả báo cáo chi tiết từng nhóm + `matched`/`unmatched`.
#        • `__olm_read_choices()`: ĐỌC LẠI DOM (radio/checkbox đang checked,
#          text đang có giá trị, underline đang active) để KIỂM CHỨNG việc
#          tick có hiệu lực thật.
#        • `__olm_find_submit()`: chấm điểm mọi button/a/[role=button] theo
#          nhãn ("nộp bài"/"hoàn thành"/"kết thúc"/"submit"...) + class/id
#          (`submit|nop-bai|finish|hoanthanh`) → trả ứng viên tốt nhất.
#        • `__olm_native_submit()`: thử submitQuiz / submit_quiz / submitExam /
#          submitExercise / submitAnswer / finishQuiz / endExam / nopBai /
#          EXAM_UI.submit / quiz.finish / quiz.submit.
#        • `__olm_read_result()`: đọc "Điểm: x/y", "x/y câu", "Đúng x/y" và
#          dấu hiệu `submitted` (thông báo nộp thành công, có .correctAnswer).
#    W4. `_submit_via_dom_fill()` viết lại thành luồng 5 bước:
#          0. gọi lại autofill cũ  1. bảng câu hỏi + pool text
#          2. `__olm_fill_real` (điền/tick/kéo thả THẬT)
#          3. `__olm_read_choices` (kiểm chứng)
#          4. bấm nút Nộp của OLM (hoặc native submit)
#          5. chờ tối đa `wait_seconds` để OLM chấm & render kết quả
#        Trả `{ok, path, fill, choices, native, clicked, result, errors}`.
#    W5. `submit()` đổi thành **DOM-FIRST**:
#          • Nếu DOM đã nộp (`dom_submitted`) → **KHÔNG** POST thêm, tránh ghi
#            đè kết quả thật của OLM. Log `SUBMIT_PATH ... native_post=skipped`.
#          • Chỉ khi DOM thất bại mới POST dự phòng (`_PAGE_POST_JS`, tách ra
#            thành hằng riêng, bỏ đoạn probe `log=tab` gây nhiễu).
#          • HTTP POST qua curl_cffi là lớp dự phòng CUỐI, chỉ chạy khi chưa
#            nộp được bằng cách nào.
#          • Trả thêm: `submit_path`, `dom_choices`, `dom_verified`,
#            `dom_result`, `dom_clicked`, `dom_native`, `dom_errors`.
#    W6. `REAL_SUBMIT_JS` được inject ở CẢ 3 đường: `add_init_script` cho
#        context headless, `add_init_script` + `evaluate` cho CDP.
#    W7. `tests/test_real_submit_js.py` — kiểm cú pháp JS bằng `node --check`
#        và sự hiện diện của 17 API then chốt.
#
# X. MÀN KHOÁ LICENSE TRONG TOOL + NÚT "GET KEY FREE"
#    X1. Thêm `#licGate` — overlay toàn màn hình hiện khi chưa có license:
#        ổ khoá động, tiêu đề gradient, HWID máy, ô nhập key, nút Kích hoạt,
#        Copy HWID, Thử lại, và nút **🎁 Get Key Free**.
#    X2. Thêm nút **Get Key Free** thứ hai trong thẻ License ở màn đăng nhập
#        (`#btnGetKeyFree`, dùng chung logic).
#    X3. `showLicenseRequired()` gọi `showGate(lic)`; `bootstrap()` gọi
#        `hideGate()` khi license hợp lệ.
#    X4. Thêm hằng `GET_KEY_URL` + biến môi trường `OLM_GET_KEY_URL` +
#        `SETTINGS_F` (`~/.olm_tool_pro/settings.json`) + `load_settings()` /
#        `save_settings()` / `get_key_url()` (thứ tự ưu tiên 4 tầng; tầng cuối
#        mở `key_generator.html` đi kèm).
#    X5. API mới trong `OLMApp`: `get_key_info()`, `set_key_url(url)`,
#        `open_get_key()`; `lic_status()` trả thêm `key_url`/`key_url_set`.
#    X6. `main()`: cửa sổ 1460x940, `min_size=(1020,700)`, `easy_drag=False`,
#        có fallback khi pywebview cũ không hỗ trợ.
#    X7. `tests/test_tool_ui_v20.js` — chạy JS THẬT của tool trong Node:
#        61 id, xác nhận gate hiện khi chưa license, kích hoạt key sai/đúng,
#        nút Get Key Free, copy HWID, Enter, và không truy cập id không tồn tại.
#
# Y. LIQUID GLASS — THIẾT KẾ LẠI TOÀN BỘ UI
#    Y1. Hằng `LIQUID_GLASS_CSS` (~30 KB, 213 rule) thay `INDEX_HTML`'s CSS cũ
#        nhưng GIỮ NGUYÊN 30 biến CSS và 91 class -> JS không phải sửa gì.
#    Y2. Ngôn ngữ thiết kế:
#        • Aurora: 4 radial-gradient trôi 34s + lớp noise SVG (feTurbulence)
#        • Kính: `backdrop-filter: blur(22–34px) saturate(180–190%)`, viền
#          `rgba(255,255,255,.10–.16)`, highlight đỉnh `inset`, bóng 3 lớp
#        • Chuyển động spring `cubic-bezier(.22,1.2,.36,1)`, hover nâng 2px,
#          sheen quét qua nút, chip pulse, card/ modal/ toast vào có nhịp
#        • Bán kính 10/14/20/26px, typography Inter với letter-spacing âm nhẹ
#        • Thanh cuộn trong suốt, `prefers-reduced-motion`, responsive 1080px
#    Y3. `key_generator.html` viết lại cùng ngôn ngữ thiết kế (aurora, noise,
#        kính, spring) + hero đổi trạng thái theo kết quả (🔑 → 🎉/🔒).
#    Y4. `tests/test_liquid_css.py` — kiểm ngoặc/khai báo, phủ hết class cũ,
#        đủ biến CSS cũ, và 8 dấu hiệu liquid glass.
#
# Z. TRANG KEY — CÁCH B MẶC ĐỊNH
#    Z1. `claim-config.js` có `OLTP_RPC_URL` + `OLTP_RPC_ANON` (Cách B) và
#        `OLTP_ENDPOINT` (Cách A), kèm `OLTP_SITE_NAME` / `OLTP_SUPPORT_URL`.
#    Z2. `postClaim()` tự chọn chế độ: có `OLTP_ENDPOINT` → Cách A (body
#        `{device}`); ngược lại dùng `OLTP_RPC_URL` → Cách B (header `apikey` +
#        `Authorization: Bearer`, body `{p_device}`).
#    Z3. `normResp()` đọc được mảng 1 phần tử mà PostgREST trả cho hàm scalar.
#    Z4. Không cấu hình → vô hiệu hoá nút và in hướng dẫn cụ thể.
#    Z5. `sql/HUONG-DAN-SETUP.md` + `sql/README.md` viết lại: Cách B là mặc
#        định (TL;DR 3 bước), thêm mục tích hợp tool + nút Get Key Free.
#    Z6. `tests/test_key_page_node.js` mở rộng lên 13 nhóm, chạy cả Cách A và
#        Cách B, kiểm payload/header/chế độ chưa cấu hình.
#
# AA. SỬA LỖI PHÁT HIỆN KHI TEST
#    AA1. `renderLicChip()` / `renderLicLive()` gọi `.className`/`.textContent`
#         trên node có thể null -> thêm guard, không làm sập bootstrap.
#    AA2. Khối GATE_JS ban đầu bị chèn vào GIỮA script (ngay trước lần gọi
#         `bootstrap()` đầu tiên), cắt cụt mọi thứ định nghĩa sau đó — kể cả
#         listener `pywebviewready` và `setTimeout(bootstrap, 800)` khiến
#         bootstrap không bao giờ chạy. Đã chuyển xuống CUỐI khối <script>.
#    AA3. `_log_telemetry()` dùng mức warn thay vì error để không làm nhiễu
#         crash.log (đã có ở v19.3, giữ nguyên hành vi).
# ═══════════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════
# CHANGELOG v20.0 -> v20.1
# ═══════════════════════════════════════════════════════════════════════════════
# BB. LẤY COOKIE THẬT  (sửa "không lấy đúng cookie thật")
#     BB1. BUG: `ctx.cookies()` gọi KHÔNG tham số -> chỉ trả cookie của các URL
#          đang mở trong context. Khi tab là about:blank (hoặc chưa có tab OLM)
#          thì trả RỖNG -> tool báo "Chrome chưa login OLM" dù người dùng đã
#          đăng nhập.  FIX: `ctx.cookies(OLM_BASE)` -> cookie đúng origin OLM,
#          bao gồm cookie HttpOnly.
#     BB2. BUG: lọc `"olm.vn" in domain` -> BỎ SÓT cookie đặt ở domain cha
#          (`.olm.vn`) và ở subdomain khác.  FIX: hàm `_cookie_belongs_to_olm()`
#          nhận mọi domain chứa olm.vn / onlinemath.vn / olm.com.vn, và cookie
#          không gắn domain (gửi cho mọi host) cũng được nhận.
#     BB3. BUG: không kiểm chứng cookie -> báo "thành công" dù cookie đã hết
#          hạn, rồi mọi thao tác sau (quét bài) đều rỗng.  FIX: thêm
#          `verify_olm_cookies()` gọi request THẬT tới 5 endpoint
#          (/user/get-info, /user/info, /api/user/me, /course/teacher-categories,
#          /) và chỉ báo ok khi server thực sự chấp nhận phiên.
#     BB4. Thêm `_collect_cookies_from_context()` thu cookie từ nhiều nguồn
#          (origin OLM, mọi tab OLM đang mở, document.cookie) để không bỏ sót.
#     BB5. Thông báo lỗi nêu RÕ nguyên nhân: thiếu cookie phiên / cookie không
#          dùng được (kèm HTTP status) / chưa đăng nhập — thay vì chuỗi chung.
#
# CC. KHỚP PHIÊN BẢN TRÌNH DUYỆT  (nguyên nhân gốc của "cookie sai")
#     CC1. BUG: tool LUÔN gửi `User-Agent: Chrome/124` và
#          `impersonate="chrome124"` bất kể trình duyệt thật là bản nào. Chrome
#          131+ tạo cookie gắn với fingerprint TLS/UA mới; gửi UA 124 cho cùng
#          cookie đó làm Cloudflare/OLM thấy lệch -> coi như chưa đăng nhập.
#     CC2. Thêm `detect_browser_version()`: đọc phiên bản THẬT theo 3 đường —
#          (a) `_win_file_version()` đọc FileVersion của chrome.exe/msedge.exe
#          qua Win32 API (không cần thư viện ngoài), (b) registry BLBeacon,
#          (c) file `Local State` của profile. Có cache.
#     CC3. `effective_ua()` sinh UA khớp major; `effective_impersonate()` ánh
#          xạ major -> target curl_cffi gần nhất (chrome99..chrome131) để không
#          lỗi khi Chrome quá mới; `effective_sec_ch_ua()` sinh Client Hints
#          khớp — lệch UA vs sec-ch-ua là dấu hiệu bot rõ nhất.
#     CC4. `OLMSession.client()` dùng cả 3 hàm trên + Accept-Encoding +
#          Upgrade-Insecure-Requests.
#
# DD. QUÉT BÀI CHO GIAO DIỆN OLM MỚI  (sửa "không quét được bài")
#     DD1. BUG: chỉ quét DOM theo selector giao diện CŨ
#          (`tr.my-given-courseware-item`, `table.table-striped tbody tr`...).
#          OLM v2 render bằng JS/SPA, không dùng class đó -> quét ra RỖNG.
#     DD2. Không có API OLM nào được công bố, nên ĐOÁN selector/endpoint là vô
#          ích. Cách chắc chắn: BẮT response XHR/fetch mà chính trang gọi.
#          Thêm `_scan_via_network()` gắn listener `page.on("response")`, thu
#          mọi body JSON (lọc theo content-type + tiền tố URL, bỏ asset/CDN/
#          analytics), rồi bóc bằng thuật toán tổng quát.
#     DD3. Thêm `_walk_json_for_assignments()` — duyệt ĐỆ QUY mọi shape JSON
#          ({data:[...]}, {data:{items:[...]}}, {results:...}, mảng trần, lồng
#          3+ tầng) và nhặt object trông như bài tập dựa trên 8 khoá id
#          (id_category, id_cate, id_courseware, id_homework, id_assignment,
#          id_exercise, cate_id, courseware_id) + tên + trạng thái + điểm.
#          `id` trần chỉ được nhận khi object có thêm khoá tên (tránh nhặt nhầm
#          object người dùng/cấu hình).
#     DD4. Thêm `_scan_via_page_state()` — bóc dữ liệu đã có trong biến JS của
#          trang (data_cate, INITIAL_STATE, __NEXT_DATA__, __NUXT__, script
#          application/json, và mọi window.X khớp /cate|assign|courseware/).
#     DD5. `scan_assignments()` viết lại thành 4 TẦNG, dừng ngay khi có kết quả:
#            T1 bắt response JSON   T2 state JS   T3 DOM   T4 GET HTML thô.
#          Thêm log chẩn đoán `[SCAN_EMPTY] T1_net:0@url | ...` để biết tầng nào
#          chạy và ra bao nhiêu kết quả.
#     DD6. BUG nhỏ: slug OLM có dạng `/chu-de/kiem-tra-15-phut-2131570928`
#          (chữ + gạch + số). Regex cũ neo số ngay sau dấu `/` nên BỎ SÓT toàn
#          bộ link dạng này. Đã sửa ở `extractId` trong JS_SCAN, ở
#          `_parse_assignment_html`, ở bộ lọc `<a href>` tổng quát, và ở phần
#          dựng `href` (kèm sửa lỗi ghép ra `//`).
#
# EE. LUẬT HẠN KEY ĐẢO THÀNH "<= 1 NGÀY"  (yêu cầu mới)
#     EE1. `oltp_config()`: bỏ `'min_valid_hours': 24` (nghĩa là "phát key còn
#          hạn >= 24h"), thay bằng `'max_valid_hours': 24` ("phát key còn hạn
#          <= 24h"). `'allow_no_expiry'` đổi mặc định true -> false vì key vĩnh
#          viễn không thoả luật <= 1 ngày.
#     EE2. `claim_key()`: điều kiện đổi từ
#            expires_at > now() + interval '24 hours'
#          thành
#            expires_at > now() AND expires_at <= now() + interval '24 hours'
#          Đặt `max_valid_hours = 0` để TẮT giới hạn trên (phát mọi key còn hạn).
#     EE3. Thông báo `out_of_stock` nói rõ "hết key còn hạn trong vòng N giờ"
#          và trả thêm `max_valid_hours`.
#     EE4. `00_preflight.sql`: đổi cột chẩn đoán thành `qua_1_ngay_bi_loai`,
#          `khong_co_han_bi_loai`, `DU_DIEU_KIEN_PHAT`; thêm khối (3c) liệt kê
#          key ĐỦ điều kiện.
#     EE5. `02_verify_and_test.sql`: kiểm tra key đã phát phải nằm trong
#          (now(), now()+24h].
#     EE6. `03_gen_30_keys_1day.sql`: hạn mặc định 25h -> **20 giờ** (nằm trong
#          (0, 24h]), thêm `raise warning` nếu đặt hạn > 24h, thêm query đếm key
#          bị loại, và cập nhật bảng chọn hạn.
#     EE7. `test_sql_structure.py` + `test_sql_gen_keys.py` cập nhật để bắt
#          đúng luật mới và bắt lỗi nếu ai đó quay lại luật cũ.
#
# FF. TEST MỚI
#     FF1. `tests/test_cookie_scan_v201.py` — 13 nhóm: bóc JSON 6 shape khác
#          nhau, bỏ object không phải bài tập, khử trùng lặp, nhận trạng thái
#          đã làm/điểm qua 8 kiểu dữ liệu, dựng href, chống payload hỏng và
#          lồng 30 tầng, nhận diện domain cookie, tổng hợp jar, bóc uid/uname
#          từ cookie `user` URL-encoded, phiên bản trình duyệt (UA + impersonate
#          + Client Hints cùng major, cache), `_win_file_version` an toàn,
#          `verify_olm_cookies` xử lý jar rỗng và mạng lỗi.
# ═══════════════════════════════════════════════════════════════════════════════

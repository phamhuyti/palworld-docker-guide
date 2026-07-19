#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
admin-tool.py — Bảng điều khiển quản trị Palworld + PWA cài được lên Android.

Backend nhỏ bằng Python (chỉ thư viện chuẩn) làm gateway an toàn:
  trình duyệt / PWA  ──HTTP(S)──>  admin-tool.py  ──REST/RCON──>  Palworld server
Nhờ vậy tránh CORS/mixed-content, và AdminPassword của server KHÔNG bao giờ ra
tới trình duyệt (chỉ nằm trong tiến trình Python này).

DÙNG QUA INTERNET: gateway này KHÔNG nên expose thẳng ra Internet công khai.
Cách an toàn: vào LAN qua VPN (OpenVPN/WireGuard/Tailscale) rồi mở gateway bằng
IP LAN. Khi bind ra ngoài 127.0.0.1, tool BẮT BUỘC đặt mật khẩu đăng nhập app.

BẢO MẬT TẦNG ỨNG DỤNG (đã tích hợp):
  - Đăng nhập bằng PAL_APP_PASSWORD (khác AdminPassword), phiên cookie HttpOnly.
  - Khóa tạm khi thử sai nhiều lần (chống dò mật khẩu).
  - Header bảo mật; cookie Secure khi bật TLS.

CÀI PWA LÊN ANDROID:
  1. (Khuyến nghị) chạy có TLS để thành "app thật" cài được: xem PAL_TLS_* bên dưới.
  2. Vào VPN, mở https://<IP-LAN>:<port> bằng Chrome trên điện thoại, đăng nhập.
  3. Menu Chrome ⋮ → "Thêm vào Màn hình chính" / "Cài đặt ứng dụng".

Chạy (Windows PowerShell), bind ra LAN cho điện thoại (qua VPN) truy cập:
  $env:PAL_HOST="192.168.1.160"; $env:PAL_ADMIN_PASSWORD="mk-admin";
  $env:PAL_APP_PASSWORD="mk-dang-nhap-app"; $env:PAL_BIND="0.0.0.0"; python admin-tool.py

Biến môi trường:
  PAL_HOST            IP server Palworld            (mặc định 127.0.0.1)
  PAL_ADMIN_PASSWORD  = AdminPassword của server    (BẮT BUỘC)
  PAL_APP_PASSWORD    mật khẩu ĐĂNG NHẬP app        (bắt buộc khi bind != 127.0.0.1)
  PAL_BIND            địa chỉ lắng nghe web UI      (mặc định 127.0.0.1; đặt 0.0.0.0 cho LAN)
  PAL_WEB_PORT        cổng web UI                   (mặc định 8080)
  PAL_REST_PORT       cổng REST API server          (mặc định 8212)
  PAL_RCON_PORT       cổng RCON server              (mặc định 25575)
  PAL_TLS_CERT        đường dẫn file cert PEM        (bật HTTPS nếu có cả cert+key)
  PAL_TLS_KEY         đường dẫn file private key PEM
  PAL_SESSION_HOURS   thời hạn phiên đăng nhập (giờ) (mặc định 12)
"""
import os, sys, json, base64, socket, struct, time, hmac, secrets, threading, zlib, gzip, ssl, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlreq, error as urlerr

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HOST      = os.environ.get("PAL_HOST", "127.0.0.1")
REST_PORT = int(os.environ.get("PAL_REST_PORT", "8212"))
RCON_PORT = int(os.environ.get("PAL_RCON_PORT", "25575"))
WEB_PORT  = int(os.environ.get("PAL_WEB_PORT", "8080"))
BIND      = os.environ.get("PAL_BIND", "127.0.0.1")
PASSWORD  = os.environ.get("PAL_ADMIN_PASSWORD", "")
APP_PW    = os.environ.get("PAL_APP_PASSWORD", "")
TLS_CERT  = os.environ.get("PAL_TLS_CERT", "")
TLS_KEY   = os.environ.get("PAL_TLS_KEY", "")
SESSION_SECONDS = int(float(os.environ.get("PAL_SESSION_HOURS", "12")) * 3600)
CONFIG_PATH = os.environ.get("PAL_CONFIG_PATH", "/pal/Config/LinuxServer/PalWorldSettings.ini")
REST_BASE = "http://%s:%d/v1/api" % (HOST, REST_PORT)
REST_AUTH = "Basic " + base64.b64encode(("admin:%s" % PASSWORD).encode("utf-8")).decode("ascii")
USE_TLS   = bool(TLS_CERT and TLS_KEY)

# Thư mục Config/LinuxServer được cả admin-tool và palworld-server cùng mount
# (xem compose.yaml), nên dùng làm "hộp thư" báo cho helper.sh biết lần tắt
# server sắp tới là DO ADMIN-TOOL CHỦ ĐỘNG yêu cầu (không phải crash thật).
# helper.sh sẽ đọc file này để quyết định có nên báo "thoát bất thường" cho
# Docker hay không — xem comment trong helper.sh.
INTENT_MARKER = os.path.join(os.path.dirname(CONFIG_PATH), ".pal_intentional_exit")

def mark_intentional_exit():
    try:
        with open(INTENT_MARKER, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass

# Auth bắt buộc nếu: có đặt mật khẩu app, HOẶC bind ra ngoài localhost.
ENFORCE_AUTH = bool(APP_PW) or (BIND not in ("127.0.0.1", "localhost"))

# ---- session + rate-limit state (ThreadingHTTPServer -> cần lock) ----
_LOCK = threading.Lock()
SESSIONS = {}          # token -> expiry_ts
FAILS = {}             # ip -> [count, locked_until_ts]
MAX_FAILS = 5
LOCK_SECONDS = 300

def _new_session():
    tok = secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        # dọn session hết hạn (login là sự kiện hiếm nên prune ở đây là đủ)
        for t in [t for t, exp in SESSIONS.items() if exp <= now]:
            del SESSIONS[t]
        SESSIONS[tok] = now + SESSION_SECONDS
    return tok

def _valid_session(tok):
    if not tok:
        return False
    with _LOCK:
        exp = SESSIONS.get(tok)
        if exp and exp > time.time():
            return True
        if tok in SESSIONS:
            del SESSIONS[tok]
    return False

def _check_login(ip, password):
    now = time.time()
    with _LOCK:
        cnt, until = FAILS.get(ip, [0, 0])
        if until > now:
            return False, int(until - now)      # đang bị khóa
        if until:                               # khóa đã hết hạn -> đếm lại từ đầu
            cnt = 0
    ok = bool(APP_PW) and hmac.compare_digest(password or "", APP_PW)
    with _LOCK:
        if ok:
            FAILS.pop(ip, None)
            return True, 0
        cnt += 1
        until = now + LOCK_SECONDS if cnt >= MAX_FAILS else 0
        FAILS[ip] = [cnt, until]
        return False, (LOCK_SECONDS if until else 0)

# ---------------------------------------------------------------- REST helper
def rest(method, path, body=None):
    url = REST_BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urlreq.Request(url, data=data, method=method)
    req.add_header("Authorization", REST_AUTH)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urlreq.urlopen(req, timeout=8) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urlerr.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)

# ---------------------------------------------------------------- config file helper
def read_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return True, f.read()
    except FileNotFoundError:
        return False, "không tìm thấy file (%s)" % CONFIG_PATH
    except Exception as e:
        return False, str(e)

def _prune_backups(keep=10):
    d = os.path.dirname(CONFIG_PATH) or "."
    prefix = os.path.basename(CONFIG_PATH) + ".bak-"
    try:
        baks = sorted(f for f in os.listdir(d) if f.startswith(prefix))
        for f in baks[:-keep]:
            os.remove(os.path.join(d, f))
    except OSError:
        pass

def write_config(text):
    if "OptionSettings" not in text:
        return False, "nội dung không hợp lệ (thiếu OptionSettings) — từ chối ghi"
    try:
        if os.path.exists(CONFIG_PATH):
            backup = CONFIG_PATH + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(CONFIG_PATH, backup)
            _prune_backups()
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, CONFIG_PATH)
        return True, None
    except Exception as e:
        return False, str(e)

# ---------------------------------------------------------------- RCON helper
def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("kết nối RCON bị đóng giữa chừng")
        buf += chunk
    return buf

def _rcon_packet(pid, ptype, body):
    payload = struct.pack("<ii", pid, ptype) + body.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload

def _rcon_read(sock):
    (length,) = struct.unpack("<i", _recv_exact(sock, 4))
    data = _recv_exact(sock, length)
    pid, ptype = struct.unpack("<ii", data[:8])
    return pid, ptype, data[8:-2].decode("utf-8", "replace")

def rcon(command):
    if not command.strip():
        return 400, "Lệnh rỗng"
    try:
        with socket.create_connection((HOST, RCON_PORT), timeout=6) as s:
            s.sendall(_rcon_packet(1, 3, PASSWORD))
            pid, _, _ = _rcon_read(s)
            if pid == -1:
                return 401, "RCON auth thất bại — sai AdminPassword."
            s.sendall(_rcon_packet(2, 2, command))
            _, _, body = _rcon_read(s)
            return 200, (body.strip() or "(OK — server không trả nội dung)")
    except Exception as e:
        return 0, "RCON lỗi: %s" % e

# ---------------------------------------------------------------- đếm ngược + spam thông báo
_CD_LOCK = threading.Lock()
_CD = {"active": False, "mode": None, "ends": 0.0, "cancel": None}

def _fmt_remain(sec):
    sec = int(round(sec))
    if sec >= 60:
        m, s = divmod(sec, 60)
        return "%d phut%s" % (m, (" %d giay" % s if s else ""))
    return "%d giay" % sec

def countdown_status():
    with _CD_LOCK:
        if not _CD["active"]:
            return {"active": False}
        return {"active": True, "mode": _CD["mode"],
                "remaining": max(0, int(round(_CD["ends"] - time.time())))}

def cancel_countdown():
    with _CD_LOCK:
        if not _CD["active"] or not _CD["cancel"]:
            return False, "Không có đếm ngược nào đang chạy"
        _CD["cancel"].set()
    return True, "Đã yêu cầu hủy đếm ngược"

def start_countdown(minutes, mode, message):
    total = int(round(float(minutes) * 60))
    if total < 5:
        total = 5
    if total > 3600:
        total = 3600
    mode = "shutdown" if mode == "shutdown" else "restart"
    with _CD_LOCK:
        if _CD["active"]:
            return False, "Đang có đếm ngược khác chạy — hủy trước đã"
        cancel = threading.Event()
        _CD.update(active=True, mode=mode, ends=time.time() + total, cancel=cancel)
    t = threading.Thread(target=_countdown_worker,
                         args=(total, mode, (message or "").strip(), cancel), daemon=True)
    t.start()
    return True, "Bắt đầu đếm ngược %s, spam thông báo tới người chơi" % _fmt_remain(total)

def _countdown_done():
    with _CD_LOCK:
        _CD.update(active=False, mode=None, ends=0.0, cancel=None)

def _countdown_worker(total, mode, custom, cancel):
    verb = "khoi dong lai" if mode == "restart" else "tat"
    ends = time.time() + total
    # các mốc còn lại (giây) — dày dần về cuối để "spam"
    points = set(m * 60 for m in range(1, total // 60 + 1))
    points.update(p for p in (30, 10, 5, 4, 3, 2, 1) if p <= total)
    for p in sorted(points, reverse=True):
        while True:
            if cancel.is_set():
                rest("POST", "/announce", {"message": "[SERVER] Da HUY lenh %s. Server tiep tuc binh thuong." % verb})
                _countdown_done(); return
            remain = ends - time.time()
            if remain <= p:
                break
            time.sleep(min(1.0, max(0.05, remain - p)))
        if custom:
            msg = "[SERVER] %s (con %s)" % (custom, _fmt_remain(p))
        else:
            msg = "[SERVER] Server se %s sau %s! Hay ket thuc viec dang lam va tim noi an toan." % (verb, _fmt_remain(p))
        rest("POST", "/announce", {"message": msg})
    # chờ về 0
    while ends - time.time() > 0.1:
        if cancel.is_set():
            rest("POST", "/announce", {"message": "[SERVER] Da HUY lenh %s." % verb})
            _countdown_done(); return
        time.sleep(0.1)
    if cancel.is_set():
        _countdown_done(); return
    # thực thi: save rồi shutdown (docker restart:unless-stopped -> server tu bat lai)
    rest("POST", "/announce", {"message": "[SERVER] Server %s ngay bay gio!" % verb})
    rest("POST", "/save")
    time.sleep(1)
    mark_intentional_exit()
    st, _ = rest("POST", "/shutdown", {"waittime": 1, "message": "Server %s" % verb})
    log_activity("Đếm ngược kết thúc — đã save và %s server" % verb,
                 "ok" if 200 <= st < 300 else "err")
    _countdown_done()

# ---------------------------------------------------------------- nhật ký hoạt động
# Lưu ở server (không phải DOM client) để: đóng tab không mất, PC và điện thoại
# thấy chung một nhật ký, restart admin-tool vẫn còn (persist ra file JSON trong
# thư mục config đã mount — cùng chỗ với marker/backup).
ACT_PATH = os.path.join(os.path.dirname(CONFIG_PATH), ".pal_admin_activity.json")
ACT_MAX  = 200
_ACT_LOCK = threading.Lock()
_ACT = {"next_id": 1, "items": []}    # items: [{i, ts, msg, cls}]

def _act_save_locked():
    try:
        tmp = ACT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_ACT, f, ensure_ascii=False)
        os.replace(tmp, ACT_PATH)
    except Exception:
        pass    # không ghi được file (vd chạy local không có thư mục) -> vẫn giữ trong RAM

def _act_load():
    try:
        with open(ACT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            _ACT["items"] = data["items"][-ACT_MAX:]
            _ACT["next_id"] = int(data.get("next_id", len(_ACT["items"]) + 1))
    except Exception:
        pass

def log_activity(msg, cls="ok"):
    with _ACT_LOCK:
        _ACT["items"].append({"i": _ACT["next_id"], "ts": time.time(),
                              "msg": str(msg)[:500], "cls": cls})
        _ACT["next_id"] += 1
        if len(_ACT["items"]) > ACT_MAX:
            _ACT["items"] = _ACT["items"][-ACT_MAX:]
        _act_save_locked()

def activity_since(since):
    with _ACT_LOCK:
        return [it for it in _ACT["items"] if it["i"] > since]

def activity_clear():
    with _ACT_LOCK:
        _ACT["items"] = []
        _act_save_locked()

_act_load()

# mô tả ngắn cho từng action POST để ghi nhật ký tập trung (xem do_POST)
_ACTION_LABELS = {
    "/api/announce":         lambda p: "Thông báo: %s" % (p.get("message", "")[:80]),
    "/api/save":             lambda p: "Lưu game",
    "/api/shutdown":         lambda p: "Shutdown server (sau %ss)" % p.get("waittime", 30),
    "/api/stop":             lambda p: "Stop server ngay",
    "/api/kick":             lambda p: "Kick %s" % p.get("userid", ""),
    "/api/ban":              lambda p: "Ban %s" % p.get("userid", ""),
    "/api/unban":            lambda p: "Gỡ ban %s" % p.get("userid", ""),
    "/api/rcon":             lambda p: "RCON » %s" % p.get("command", "")[:120],
    "/api/countdown":        lambda p: "Bắt đầu đếm ngược %s phút (%s)" % (p.get("minutes", 5), p.get("mode", "restart")),
    "/api/countdown_cancel": lambda p: "Hủy đếm ngược",
    "/api/config":           lambda p: "Ghi PalWorldSettings.ini",
    "/api/activity_clear":   lambda p: "Xóa nhật ký",
}

def _log_action(path, payload, body):
    fn = _ACTION_LABELS.get(path)
    if not fn:
        return
    try:
        j = json.loads(body)
        ok = bool(j.get("ok"))
        extra = ""
        if path == "/api/rcon" and ok and j.get("text"):
            extra = "\n" + str(j.get("text"))[:300]
        elif not ok:
            extra = " — " + str(j.get("text") or j.get("error") or ("HTTP %s" % j.get("status")))[:200]
        log_activity(fn(payload or {}) + extra, "ok" if ok else "err")
    except Exception:
        pass

# ---------------------------------------------------------------- routes
def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def _wrap(status, raw):
    ok = 200 <= status < 300
    try:
        parsed = json.loads(raw) if raw.strip() else None
        return 200, json.dumps({"ok": ok, "status": status, "data": parsed,
                                "text": None if parsed is not None else raw})
    except Exception:
        return 200, json.dumps({"ok": ok, "status": status, "data": None, "text": raw})

def route(method, path, payload):
    if method == "GET":
        if path == "/api/info":     return _wrap(*rest("GET", "/info"))
        if path == "/api/players":  return _wrap(*rest("GET", "/players"))
        if path == "/api/metrics":  return _wrap(*rest("GET", "/metrics"))
        if path == "/api/settings": return _wrap(*rest("GET", "/settings"))
        if path == "/api/countdown_status":
            return 200, json.dumps({"ok": True, "status": 200, "data": countdown_status(), "text": None})
        if path == "/api/activity":
            since = int(_num((payload or {}).get("since", 0), 0))
            return 200, json.dumps({"ok": True, "status": 200,
                                    "data": {"items": activity_since(since)}, "text": None}, ensure_ascii=False)
        if path == "/api/config":
            ok, data = read_config()
            return 200, json.dumps({"ok": ok, "data": {"text": data} if ok else None,
                                     "error": None if ok else data})
    if method == "POST":
        p = payload or {}
        if path == "/api/announce":
            return _wrap(*rest("POST", "/announce", {"message": p.get("message", "")}))
        if path == "/api/save":
            return _wrap(*rest("POST", "/save"))
        if path == "/api/shutdown":
            rest("POST", "/save")   # lưu trước cho chắc (server cũng autosave khi tắt êm)
            mark_intentional_exit()
            return _wrap(*rest("POST", "/shutdown",
                   {"waittime": int(_num(p.get("waittime", 30), 30)), "message": p.get("message", "")}))
        if path == "/api/stop":
            rest("POST", "/save")   # Stop tắt gấp -> bắt buộc lưu trước
            mark_intentional_exit()
            return _wrap(*rest("POST", "/stop"))
        if path == "/api/kick":
            return _wrap(*rest("POST", "/kick",
                   {"userid": p.get("userid", ""), "message": p.get("message", "Ban da bi kick")}))
        if path == "/api/ban":
            return _wrap(*rest("POST", "/ban",
                   {"userid": p.get("userid", ""), "message": p.get("message", "Ban da bi ban")}))
        if path == "/api/unban":
            return _wrap(*rest("POST", "/unban", {"userid": p.get("userid", "")}))
        if path == "/api/rcon":
            cmd = (p.get("command", "") or "").strip()
            # Shutdown/DoExit qua RCON cũng là tắt chủ động -> báo helper.sh như REST
            if cmd.lstrip("/").split(" ", 1)[0].lower() in ("shutdown", "doexit"):
                mark_intentional_exit()
            return _wrap(*rcon(cmd))
        if path == "/api/countdown":
            ok, msg = start_countdown(_num(p.get("minutes", 5), 5), p.get("mode", "restart"), p.get("message", ""))
            return 200, json.dumps({"ok": ok, "status": 200 if ok else 409, "data": None, "text": msg})
        if path == "/api/countdown_cancel":
            ok, msg = cancel_countdown()
            return 200, json.dumps({"ok": ok, "status": 200 if ok else 409, "data": None, "text": msg})
        if path == "/api/activity_clear":
            activity_clear()
            return 200, json.dumps({"ok": True})
        if path == "/api/config":
            ok, err = write_config(p.get("text", ""))
            return (200 if ok else 400), json.dumps({"ok": ok, "error": err})
    return 404, json.dumps({"ok": False, "error": "route không tồn tại"})

# ---------------------------------------------------------------- PWA icon (PNG tự vẽ chữ P)
def _png(width, height, rgba):
    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data +
                struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))

_ICON_CACHE = {}
def make_icon(size):
    if size in _ICON_CACHE:
        return _ICON_CACHE[size]
    a = (0x1f, 0x9c, 0x78); b = (0x0e, 0x5c, 0x46)
    buf = bytearray(size * size * 4)
    m = size * 0.27
    bx0, by0, bx1, by1 = m, m, size - m, size - m
    bw, bh = bx1 - bx0, by1 - by0
    sw = bw * 0.24
    right = bx0 + bw * 0.86
    for y in range(size):
        t = y / (size - 1)
        r = int(a[0] + (b[0] - a[0]) * t); g = int(a[1] + (b[1] - a[1]) * t); bl = int(a[2] + (b[2] - a[2]) * t)
        for x in range(size):
            white = False
            if bx0 <= x <= bx1 and by0 <= y <= by1:
                if bx0 <= x < bx0 + sw:                                   white = True   # thân
                elif by0 <= y < by0 + sw and x < right:                   white = True   # ngang trên
                elif by0 + bh * 0.5 - sw * 0.5 <= y < by0 + bh * 0.5 + sw * 0.5 and x < right: white = True  # ngang giữa
                elif right - sw <= x < right and by0 <= y <= by0 + bh * 0.5: white = True # cạnh phải bụng
            idx = (y * size + x) * 4
            if white:
                buf[idx] = buf[idx+1] = buf[idx+2] = 255
            else:
                buf[idx] = r; buf[idx+1] = g; buf[idx+2] = bl
            buf[idx+3] = 255
    png = _png(size, size, bytes(buf))
    _ICON_CACHE[size] = png
    return png

MANIFEST = json.dumps({
    "name": "Palworld Admin", "short_name": "Pal Admin",
    "description": "Quản trị Palworld Dedicated Server",
    "start_url": "/", "scope": "/", "display": "standalone",
    "orientation": "portrait", "background_color": "#12171a", "theme_color": "#1f9c78",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}, ensure_ascii=False)

SW_JS = r"""
const C='pal-admin-v6';
self.addEventListener('install',e=>{self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(Promise.all([
  clients.claim(),
  caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==C).map(k=>caches.delete(k))))
]));});
self.addEventListener('fetch',e=>{
  const u=new URL(e.request.url);
  if(e.request.method!=='GET'||u.pathname.startsWith('/api/')||u.pathname==='/login'||u.pathname==='/logout') return;
  if(e.request.mode==='navigate'){
    // HTML: ưu tiên mạng để luôn có UI mới nhất, offline mới rơi về cache
    e.respondWith(fetch(e.request).then(resp=>{
      if(resp && resp.ok){const copy=resp.clone();caches.open(C).then(c=>c.put(e.request,copy));}
      return resp;
    }).catch(()=>caches.open(C).then(c=>c.match(e.request).then(r=>r||c.match('/')))));
    return;
  }
  // tài nguyên tĩnh (icon, manifest): cache-first
  e.respondWith(caches.open(C).then(c=>c.match(e.request).then(r=>r||fetch(e.request).then(resp=>{
    if(resp && resp.ok) c.put(e.request, resp.clone()); return resp;
  }))));
});
"""

# ---------------------------------------------------------------- HTTP server
class Handler(BaseHTTPRequestHandler):
    server_version = "PalAdmin"
    protocol_version = "HTTP/1.1"   # keep-alive: UI poll liên tục, đỡ bắt tay TCP/TLS lại mỗi request
    timeout = 75                    # đóng kết nối keep-alive bỏ không, tránh giữ thread mãi
    def log_message(self, *a):
        pass

    def _cookies(self):
        out = {}
        for part in (self.headers.get("Cookie", "") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out[k] = v
        return out

    def _authed(self):
        if not ENFORCE_AUTH:
            return True
        return _valid_session(self._cookies().get("sid", ""))

    def _send(self, code, body, ctype="application/json; charset=utf-8", cookie=None, cache="no-store"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        # gzip cho nội dung text đủ lớn (trang HTML ~30–50KB -> còn ~5–10KB qua VPN)
        if (len(b) >= 512 and not ctype.startswith("image/")
                and "gzip" in (self.headers.get("Accept-Encoding") or "")):
            b = gzip.compress(b, 6)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        try:
            self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _route(self, method, path, payload):
        try:
            return route(method, path, payload)
        except Exception as e:
            return 500, json.dumps({"ok": False, "error": "lỗi nội bộ: %s" % e})

    def do_GET(self):
        path = self.path.split("?")[0]
        # tài nguyên công khai (cần cho cả trang đăng nhập + cài PWA)
        if path == "/manifest.webmanifest":
            return self._send(200, MANIFEST, "application/manifest+json; charset=utf-8", cache="public, max-age=3600")
        if path == "/sw.js":
            return self._send(200, SW_JS, "application/javascript; charset=utf-8")
        if path == "/icon-192.png":
            return self._send(200, make_icon(192), "image/png", cache="public, max-age=86400")
        if path == "/icon-512.png":
            return self._send(200, make_icon(512), "image/png", cache="public, max-age=86400")
        if path == "/login":
            if self._authed():
                return self._redirect("/")
            return self._send(200, LOGIN_PAGE, "text/html; charset=utf-8")
        if path == "/logout":
            tok = self._cookies().get("sid", "")
            with _LOCK:
                SESSIONS.pop(tok, None)
            return self._redirect("/login", clear=True)
        # từ đây cần đăng nhập
        if not self._authed():
            if path.startswith("/api/"):
                return self._send(401, json.dumps({"ok": False, "error": "auth"}))
            return self._redirect("/login")
        if path == "/" or path == "":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/config":
            return self._send(200, CONFIG_PAGE, "text/html; charset=utf-8")
        if path.startswith("/api/"):
            q = {}
            if "?" in self.path:
                for part in self.path.split("?", 1)[1].split("&"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        q[k] = v
            code, body = self._route("GET", path, q)
            return self._send(code, body)
        return self._send(404, json.dumps({"ok": False, "error": "not found"}))

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        raw = raw.lstrip("﻿")   # bỏ BOM nếu client gửi kèm
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {}

        if path == "/login":
            ip = self.client_address[0]
            ok, wait = _check_login(ip, payload.get("password", ""))
            if ok:
                tok = _new_session()
                flags = "HttpOnly; SameSite=Strict; Path=/"
                if USE_TLS:
                    flags += "; Secure"
                cookie = "sid=%s; %s; Max-Age=%d" % (tok, flags, SESSION_SECONDS)
                log_activity("Đăng nhập thành công (IP %s)" % ip)
                return self._send(200, json.dumps({"ok": True}), cookie=cookie)
            if wait:
                log_activity("Đăng nhập sai nhiều lần — khóa IP %s %ds" % (ip, wait), "err")
                return self._send(429, json.dumps({"ok": False, "error": "locked", "wait": wait}))
            log_activity("Đăng nhập sai (IP %s)" % ip, "err")
            return self._send(401, json.dumps({"ok": False, "error": "wrong"}))

        if not self._authed():
            return self._send(401, json.dumps({"ok": False, "error": "auth"}))
        if path.startswith("/api/"):
            code, body = self._route("POST", path, payload)
            _log_action(path, payload, body)
            return self._send(code, body)
        return self._send(404, json.dumps({"ok": False, "error": "not found"}))

    def _redirect(self, to, clear=False):
        self.send_response(303)
        self.send_header("Location", to)
        if clear:
            self.send_header("Set-Cookie", "sid=; Max-Age=0; Path=/")
        self.send_header("Content-Length", "0")
        self.end_headers()

# ---------------------------------------------------------------- login page
LOGIN_PAGE = r"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Đăng nhập · Palworld Admin</title>
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#1f9c78">
<link rel="apple-touch-icon" href="/icon-192.png">
<style>
:root{--bg:#12171a;--surface:#1a2126;--surface-2:#212a30;--text:#e8ece8;--muted:#9aa8a2;
--border:#2b353b;--accent:#1f9c78;--accent-ink:#0e5c46;--danger:#e07a60;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
*{box-sizing:border-box}
body{margin:0;min-height:100dvh;display:grid;place-items:center;background:
radial-gradient(120% 80% at 50% -10%,#1c2a26 0%,var(--bg) 60%);color:var(--text);
font-family:var(--sans);padding:24px}
.box{width:min(360px,100%);background:var(--surface);border:1px solid var(--border);
border-radius:16px;padding:26px 24px;box-shadow:0 20px 50px -20px rgba(0,0,0,.7)}
.logo{width:52px;height:52px;border-radius:14px;background:linear-gradient(150deg,var(--accent),var(--accent-ink));
display:grid;place-items:center;color:#fff;font-weight:800;font-size:26px;margin:0 auto 16px}
h1{font-size:18px;text-align:center;margin:0 0 4px}
p.sub{text-align:center;color:var(--muted);font-size:12.5px;margin:0 0 20px}
label{font-size:12px;color:var(--muted);font-weight:600;display:block;margin:0 0 6px}
input{width:100%;padding:12px 13px;border:1px solid var(--border);background:var(--surface-2);
color:var(--text);border-radius:10px;font-size:15px}
input:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
button{width:100%;margin-top:16px;padding:12px;border:none;border-radius:10px;background:var(--accent);
color:#fff;font-size:15px;font-weight:700;cursor:pointer}
button:hover{filter:brightness(1.06)}button:disabled{opacity:.6}
.err{color:var(--danger);font-size:13px;font-weight:600;text-align:center;margin-top:14px;min-height:18px}
</style></head><body>
<form class="box" id="f">
  <div class="logo">P</div>
  <h1>Palworld Admin</h1>
  <p class="sub">Đăng nhập để quản trị server</p>
  <label for="pw">Mật khẩu ứng dụng</label>
  <input id="pw" type="password" autocomplete="current-password" autofocus>
  <button id="b" type="submit">Đăng nhập</button>
  <div class="err" id="e"></div>
</form>
<script>
if('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(()=>{});
var f=document.getElementById('f'),e=document.getElementById('e'),b=document.getElementById('b');
f.addEventListener('submit',async function(ev){
  ev.preventDefault();e.textContent='';b.disabled=true;
  try{
    var r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({password:document.getElementById('pw').value})});
    var j=await r.json();
    if(j.ok){ location.href='/'; return; }
    if(j.error==='locked') e.textContent='Sai quá nhiều lần — thử lại sau '+Math.ceil(j.wait/60)+' phút.';
    else e.textContent='Sai mật khẩu.';
  }catch(ex){ e.textContent='Lỗi kết nối.'; }
  b.disabled=false;
});
</script></body></html>"""

# ---------------------------------------------------------------- app page
PAGE = r"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Palworld Admin</title>
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#1f9c78">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="apple-touch-icon" href="/icon-192.png">
<style>
:root{
  --bg:#f4f6f3;--surface:#fff;--surface-2:#eef1ec;--surface-3:#e7ebe4;
  --text:#1a2129;--muted:#5c6a72;--faint:#8a978f;--border:#dde3db;
  --accent:#1f9c78;--accent-weak:#e2f1eb;--accent-ink:#0e5c46;
  --danger:#c0533b;--danger-weak:#f6e2dc;--warn:#b7770c;--ok:#1f9c78;
  --mono:ui-monospace,"SFMono-Regular",Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#12171a;--surface:#1a2126;--surface-2:#212a30;--surface-3:#28333a;
  --text:#e8ece8;--muted:#9aa8a2;--faint:#6f7d77;--border:#2b353b;
  --accent:#3fca9e;--accent-weak:#123028;--accent-ink:#8fe6cb;
  --danger:#e07a60;--danger-weak:#3a201a;--warn:#e0a53a;--ok:#3fca9e;
}}
*{box-sizing:border-box}[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);font-size:15px;line-height:1.5;
  padding-top:env(safe-area-inset-top);-webkit-tap-highlight-color:transparent}
button{font-family:inherit;cursor:pointer}input,textarea{font-family:inherit}
.top{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--surface) 92%,transparent);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--border)}
.top-in{max-width:1080px;margin:0 auto;padding:11px 16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.glyph{width:32px;height:32px;border-radius:9px;background:linear-gradient(150deg,var(--accent),var(--accent-ink));
  display:grid;place-items:center;color:#fff;font-weight:700}
.brand h1{margin:0;font-size:15px;letter-spacing:-.01em}
.brand p{margin:0;font-size:11px;color:var(--muted)}
.stat{margin-left:auto;display:flex;gap:16px;flex-wrap:wrap;align-items:center}
.conn{display:flex;align-items:center;gap:7px;font-size:12.5px;font-weight:600}
.dot{width:9px;height:9px;border-radius:50%;background:var(--faint)}
.dot.on{background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 25%,transparent)}
.dot.off{background:var(--danger);box-shadow:0 0 0 3px color-mix(in srgb,var(--danger) 25%,transparent)}
.kv{display:flex;flex-direction:column;line-height:1.15}
.kv b{font-size:15px;font-variant-numeric:tabular-nums}
.kv span{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.logout{border:1px solid var(--border);background:var(--surface);color:var(--muted);
  border-radius:8px;padding:6px 10px;font-size:12px;font-weight:600}
.logout:hover{color:var(--danger);border-color:var(--danger)}
main{max-width:1080px;margin:0 auto;padding:16px;display:grid;grid-template-columns:1fr 1fr;gap:14px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:15px 16px}
.card.wide{grid-column:1/-1}
.card h2{margin:0 0 12px;font-size:12.5px;letter-spacing:.03em;text-transform:uppercase;color:var(--muted);
  display:flex;align-items:center;gap:8px}
.card h2 .c{font-size:11px;color:var(--faint);text-transform:none}
label{font-size:12px;color:var(--muted);font-weight:600;display:block;margin:0 0 5px}
input[type=text],input[type=number],textarea{width:100%;padding:11px;border:1px solid var(--border);
  background:var(--surface-2);color:var(--text);border-radius:9px;font-size:15px}
textarea{resize:vertical;min-height:56px}
select{width:100%;padding:11px;border:1px solid var(--border);background:var(--surface-2);color:var(--text);border-radius:9px;font-size:14px;cursor:pointer}
input:focus,textarea:focus,select:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
.cd-banner{margin-top:12px;padding:11px 13px;border-radius:10px;border:1px solid var(--warn);
  background:var(--warn-weak);display:flex;align-items:center;gap:10px;font-size:13.5px;font-weight:600;color:var(--warn)}
.cd-banner b{font-variant-numeric:tabular-nums}
.cd-banner .btn{margin-left:auto}
.divider{border-top:1px solid var(--border);margin:14px 0 0;padding-top:12px}
.row{display:flex;gap:9px;flex-wrap:wrap;align-items:flex-end}
.row>div{flex:1;min-width:90px}
.btn{border:1px solid var(--border);background:var(--surface);color:var(--text);padding:11px 15px;
  border-radius:9px;font-size:14px;font-weight:600;display:inline-flex;align-items:center;gap:7px;transition:.13s}
.btn:hover{background:var(--surface-2);border-color:var(--faint)}
.btn:active{transform:scale(.98)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.danger{background:var(--danger-weak);border-color:var(--danger);color:var(--danger)}
.btn.danger.armed{background:var(--danger);color:#fff}
.btn.sm{padding:7px 11px;font-size:12.5px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:9px;border-bottom:1px solid var(--border)}
th{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
td.mono{font-family:var(--mono);font-size:11px;color:var(--muted)}
td.num{font-variant-numeric:tabular-nums;text-align:right}
.empty{color:var(--faint);font-size:13px;padding:14px 4px;text-align:center}
.log{font-family:var(--mono);font-size:12px;line-height:1.55;max-height:220px;overflow:auto;
  background:var(--surface-2);border:1px solid var(--border);border-radius:9px;padding:10px}
.log .l{padding:2px 0;border-bottom:1px dashed var(--border);white-space:pre-wrap;word-break:break-word}
.log .t{color:var(--faint)}.log .ok{color:var(--ok)}.log .err{color:var(--danger)}
.hint{font-size:11.5px;color:var(--faint);margin-top:8px}
.toast{position:fixed;left:50%;bottom:calc(20px + env(safe-area-inset-bottom));transform:translateX(-50%) translateY(16px);
  background:var(--text);color:var(--bg);padding:10px 17px;border-radius:20px;font-size:13px;font-weight:600;
  opacity:0;pointer-events:none;transition:.2s;z-index:9}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
@media (max-width:760px){main{grid-template-columns:1fr}.stat{gap:12px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body>
<div class="top"><div class="top-in">
  <div class="glyph">P</div>
  <div class="brand"><h1 id="srvName">Palworld Admin</h1><p id="srvSub">đang kết nối…</p></div>
  <div class="stat">
    <span class="conn"><span class="dot" id="dot"></span><span id="connText">…</span></span>
    <div class="kv"><b id="mPlayers">–</b><span>người</span></div>
    <div class="kv"><b id="mFps">–</b><span>fps</span></div>
    <div class="kv"><b id="mUptime">–</b><span>uptime</span></div>
    <a class="logout" href="/config" style="text-decoration:none">Cấu hình</a>
    <button class="logout" id="btnLogout" hidden>Đăng xuất</button>
  </div>
</div></div>
<main>
  <section class="card wide">
    <h2>Người chơi online <span class="c" id="pcount"></span>
      <button class="btn sm" id="btnRefresh" style="margin-left:auto">Làm mới</button></h2>
    <div id="playersWrap"><div class="empty">Chưa có dữ liệu.</div></div>
  </section>
  <section class="card">
    <h2>Thông báo toàn server</h2>
    <label for="annMsg">Nội dung</label>
    <textarea id="annMsg" placeholder="Ví dụ: Server se restart sau 10 phut!"></textarea>
    <div class="row" style="margin-top:9px"><button class="btn primary" id="btnAnnounce">Gửi thông báo</button></div>
    <p class="hint">Dùng REST API — hỗ trợ tiếng Việt/dấu cách tốt hơn RCON.</p>
  </section>
  <section class="card">
    <h2>Thế giới</h2>
    <div class="row"><div style="flex:0 0 auto"><button class="btn" id="btnSave">Lưu game</button></div></div>
    <label for="sdWait" style="margin-top:12px">Tắt server êm sau (giây)</label>
    <div class="row">
      <div style="flex:0 0 110px"><input type="number" id="sdWait" value="30" min="0" max="3600"></div>
      <div><input type="text" id="sdMsg" placeholder="Lời nhắn khi tắt"></div>
    </div>
    <div class="row" style="margin-top:9px">
      <button class="btn danger" id="btnShutdown">Shutdown</button>
      <button class="btn danger" id="btnStop">Stop (ngay)</button>
    </div>
    <p class="hint">Tự động Save trước khi tắt. Shutdown có đếm giờ + báo trước; Stop tắt ngay. Nút đỏ bấm 2 lần để xác nhận.</p>
    <div class="divider">
      <label>Đếm ngược có spam thông báo</label>
      <div class="row">
        <div style="flex:0 0 110px"><input type="number" id="cdMin" value="5" min="1" max="60" title="Số phút"></div>
        <div><select id="cdMode">
          <option value="restart">Khởi động lại</option>
          <option value="shutdown">Tắt server</option>
        </select></div>
      </div>
      <input type="text" id="cdMsg" placeholder="(tùy chọn) lời nhắn riêng thay mặc định" style="margin-top:9px">
      <div class="row" style="margin-top:9px">
        <button class="btn danger" id="btnCountdown">Bắt đầu đếm ngược</button>
      </div>
      <div class="cd-banner" id="cdBanner" hidden>
        <span>⏳ Đang đếm ngược: <b id="cdRemain">–</b></span>
        <button class="btn sm" id="btnCdCancel">Hủy</button>
      </div>
      <p class="hint">Spam thông báo cho người chơi mỗi phút → 30s → 10s → 5..1s rồi tự save & tắt.
        Với <span style="font-family:var(--mono)">restart: unless-stopped</span>, server tự bật lại (= restart).</p>
    </div>
  </section>
  <section class="card">
    <h2>Gỡ ban</h2>
    <label for="unbanId">User ID</label>
    <div class="row"><div><input type="text" id="unbanId" placeholder="steam_0123456789"></div>
      <div style="flex:0 0 auto"><button class="btn" id="btnUnban">Gỡ ban</button></div></div>
    <p class="hint">Kick/Ban thao tác ở bảng người chơi phía trên.</p>
  </section>
  <section class="card">
    <h2>RCON console</h2>
    <label for="rconSel">Lệnh</label>
    <select id="rconSel"></select>
    <div id="rconArgs"></div>
    <div class="row" style="margin-top:9px">
      <button class="btn" id="btnRcon">Chạy</button>
    </div>
    <p class="hint" id="rconHint">Kết quả hiện ở Nhật ký bên dưới.</p>
  </section>
  <section class="card wide">
    <h2>Nhật ký <button class="btn sm" id="btnClear" style="margin-left:auto">Xóa</button></h2>
    <div class="log" id="log"><div class="empty">Chưa có hoạt động.</div></div>
  </section>
</main>
<div class="toast" id="toast"></div>
<script>
if('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(()=>{});
var $=function(s){return document.querySelector(s)};
function toast(m){var t=$("#toast");t.textContent=m;t.classList.add("show");
  clearTimeout(t._t);t._t=setTimeout(function(){t.classList.remove("show")},1800);}
function pad(n){return n<10?"0"+n:""+n}
function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}
// Nhật ký lưu ở server: đóng tab / mở trên thiết bị khác vẫn thấy chung một lịch sử.
var lastActId=0;
function fmtTs(ts){var d=new Date(ts*1000);return pad(d.getHours())+":"+pad(d.getMinutes())+":"+pad(d.getSeconds());}
async function pollActivity(){
  try{
    var r=await call("GET","/api/activity?since="+lastActId);
    var items=(r.data&&r.data.items)||[];
    if(!items.length)return;
    var box=$("#log");if(box.querySelector(".empty"))box.innerHTML="";
    items.forEach(function(it){
      var d=document.createElement("div");d.className="l";
      d.innerHTML='<span class="t">['+fmtTs(it.ts)+']</span> <span class="'+(it.cls==="err"?"err":"ok")+'">'+esc(it.msg)+'</span>';
      box.appendChild(d);lastActId=it.i;
    });
    while(box.children.length>200)box.removeChild(box.firstChild);
    box.scrollTop=box.scrollHeight;
  }catch(e){}
}
async function call(method,path,body){
  var opt={method:method,headers:{}};
  if(body){opt.headers["Content-Type"]="application/json";opt.body=JSON.stringify(body);}
  var r=await fetch(path,opt);
  if(r.status===401){ location.href="/login"; throw new Error("auth"); }
  return await r.json();
}
function fmtUptime(sec){sec=parseInt(sec||0,10);if(!sec||sec<0)return "–";
  var h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60);return (h>0?h+"h":"")+m+"m";}
function setConn(ok,err){$("#dot").className="dot "+(ok?"on":"off");
  $("#connText").textContent=ok?"Đã kết nối":"Mất kết nối";
  if(!ok)$("#srvSub").textContent="REST API: "+(err||"lỗi");}
async function refreshStatus(){
  try{
    var res=await Promise.all([call("GET","/api/info"),call("GET","/api/metrics")]);
    var info=res[0],m=res[1];
    if(info.ok&&info.data){
      $("#srvName").textContent=info.data.servername||info.data.name||"Palworld Server";
      $("#srvSub").textContent=(info.data.version?("v"+info.data.version+" · "):"")+"REST API";
      setConn(true);
    }else{ setConn(false, info.status===401?"sai AdminPassword":(info.status===0?"không kết nối được (server tắt/REST chưa bật?)":("HTTP "+info.status))); return; }
    if(m.ok&&m.data){
      $("#mPlayers").textContent=(m.data.currentplayernum!=null?m.data.currentplayernum:"–")+" / "+(m.data.maxplayernum!=null?m.data.maxplayernum:"–");
      $("#mFps").textContent=m.data.serverfps!=null?m.data.serverfps:"–";
      $("#mUptime").textContent=fmtUptime(m.data.uptime);
    }
  }catch(e){}
}
async function refreshPlayers(){
  var wrap=$("#playersWrap");
  if(wrap.querySelector(".btn.armed"))return; // đang chờ xác nhận Kick/Ban — đừng vẽ lại mất trạng thái
  try{
    var r=await call("GET","/api/players");
    if(!r.ok){wrap.innerHTML='<div class="empty">Không lấy được danh sách (HTTP '+r.status+'). '+esc(r.text||"")+'</div>';$("#pcount").textContent="";return;}
    var list=(r.data&&r.data.players)||[];
    $("#pcount").textContent=list.length?("· "+list.length):"";
    if(!list.length){wrap.innerHTML='<div class="empty">Không có ai online.</div>';return;}
    var html='<div style="overflow-x:auto"><table><thead><tr><th>Tên</th><th>Lv</th><th>Ping</th><th>User ID</th><th style="text-align:right">Thao tác</th></tr></thead><tbody>';
    list.forEach(function(p){
      var uid=p.userId||p.userid||p.playerId||"";
      html+='<tr><td>'+esc(p.name||"?")+'</td><td class="num">'+esc(p.level||"–")+'</td><td class="num">'+esc(p.ping!=null?Math.round(p.ping):"–")+
        '</td><td class="mono">'+esc(uid)+'</td><td style="text-align:right;white-space:nowrap">'+
        '<button class="btn sm danger" data-kick="'+esc(uid)+'" data-name="'+esc(p.name||"")+'">Kick</button> '+
        '<button class="btn sm danger" data-ban="'+esc(uid)+'" data-name="'+esc(p.name||"")+'">Ban</button></td></tr>';
    });
    wrap.innerHTML=html+'</tbody></table></div>';
    document.querySelectorAll("[data-kick]").forEach(function(b){b.onclick=function(){arm(b,function(){mod("/api/kick",b.dataset.kick,b.dataset.name,"kick");});};});
    document.querySelectorAll("[data-ban]").forEach(function(b){b.onclick=function(){arm(b,function(){mod("/api/ban",b.dataset.ban,b.dataset.name,"ban");});};});
  }catch(e){}
}
function arm(btn,action){
  if(btn._armed){clearTimeout(btn._t);btn._armed=false;btn.classList.remove("armed");btn.textContent=btn._label;action();return;}
  btn._label=btn.textContent;btn._armed=true;btn.classList.add("armed");btn.textContent="Chắc chắn?";
  btn._t=setTimeout(function(){btn._armed=false;btn.classList.remove("armed");btn.textContent=btn._label;},3000);
}
async function mod(path,uid,name,verb){
  if(!uid){toast("Thiếu User ID");return;}
  report(verb+" "+(name||uid), await call("POST",path,{userid:uid,message:"Ban da bi "+verb}));refreshPlayers();
}
function report(action,r){
  toast(r.ok?("✓ "+action):("Lỗi: "+action));
  pollActivity();   // server đã ghi nhật ký — kéo về hiển thị ngay
}
$("#btnAnnounce").onclick=async function(){var m=$("#annMsg").value.trim();if(!m){toast("Nhập nội dung");return;}
  report("announce",await call("POST","/api/announce",{message:m}));$("#annMsg").value="";};
$("#btnSave").onclick=async function(){report("save",await call("POST","/api/save",{}));};
$("#btnUnban").onclick=async function(){var id=$("#unbanId").value.trim();if(!id){toast("Nhập User ID");return;}
  report("unban "+id,await call("POST","/api/unban",{userid:id}));$("#unbanId").value="";};
// ---- RCON dạng UI: chọn lệnh -> hiện ô tham số tương ứng ----
// us:1 = thay dấu cách bằng "_" (RCON Palworld cắt chuỗi ở dấu cách), num:1 = ô số,
// raw:1 = gửi nguyên văn (lệnh tùy ý), danger:1 = bấm 2 lần xác nhận.
var RCON_CMDS=[
 {c:"Info",d:"Thông tin server",h:"Xem tên và phiên bản server."},
 {c:"ShowPlayers",d:"Danh sách người chơi",h:"Trả về CSV: name,playeruid,steamid."},
 {c:"Save",d:"Lưu world",h:"Lưu game ngay lập tức."},
 {c:"Broadcast",d:"Thông báo toàn server",h:"RCON không nhận dấu cách/tiếng Việt tốt — dấu cách tự thay bằng \"_\". Muốn gửi có dấu, dùng thẻ Thông báo (REST).",
   args:[{n:"Nội dung",ph:"vd: server_restart_10p",req:1,us:1}]},
 {c:"KickPlayer",d:"Kick người chơi",h:"User ID lấy ở bảng người chơi phía trên.",danger:1,
   args:[{n:"User ID",ph:"steam_0123456789",req:1}]},
 {c:"BanPlayer",d:"Ban người chơi",h:"Ban theo User ID (gỡ bằng UnBanPlayer).",danger:1,
   args:[{n:"User ID",ph:"steam_0123456789",req:1}]},
 {c:"UnBanPlayer",d:"Gỡ ban",h:"Gỡ ban theo User ID.",
   args:[{n:"User ID",ph:"steam_0123456789",req:1}]},
 {c:"Shutdown",d:"Tắt server sau N giây",h:"Tắt êm có đếm giờ + lời nhắn (không dấu cách). Docker sẽ tự bật lại server.",danger:1,
   args:[{n:"Giây",ph:"30",def:"30",num:1,req:1},{n:"Lời nhắn",ph:"server_se_tat",us:1}]},
 {c:"DoExit",d:"Tắt NGAY lập tức",h:"⚠️ Dừng tiến trình ngay, không đếm giờ — nên bấm Lưu game trước.",danger:1},
 {c:"",d:"✎ Lệnh tùy ý (gõ tay)",h:"Gõ nguyên văn lệnh RCON bất kỳ.",
   args:[{n:"Lệnh",ph:"vd: Broadcast xin_chao",req:1,raw:1}]},
];
function rconSelected(){return RCON_CMDS[parseInt($("#rconSel").value,10)]||RCON_CMDS[0];}
function renderRconArgs(){
  var cmd=rconSelected(),box=$("#rconArgs");box.innerHTML="";
  (cmd.args||[]).forEach(function(a,i){
    var lab=document.createElement("label");lab.textContent=a.n;lab.style.marginTop="9px";
    var inp=document.createElement("input");inp.id="rconArg"+i;
    inp.type=a.num?"number":"text";if(a.num)inp.min=0;
    inp.placeholder=a.ph||"";if(a.def)inp.value=a.def;
    inp.addEventListener("keydown",function(e){if(e.key==="Enter")$("#btnRcon").click();});
    box.appendChild(lab);box.appendChild(inp);
  });
  $("#rconHint").textContent=cmd.h||"";
  var b=$("#btnRcon");
  b.classList.toggle("danger",!!cmd.danger);
  b.textContent=cmd.danger?"Chạy (bấm 2 lần)":"Chạy";
}
function buildRconCmd(){
  var cmd=rconSelected(),parts=[],missing=false;
  (cmd.args||[]).forEach(function(a,i){
    var v=($("#rconArg"+i).value||"").trim();
    if(a.req&&!v){missing=true;return;}
    if(a.us)v=v.replace(/\s+/g,"_");
    if(v)parts.push(v);
  });
  if(missing)return null;
  return cmd.c?(cmd.c+(parts.length?" "+parts.join(" "):"")):parts.join(" ");
}
async function runRcon(){
  var c=buildRconCmd();
  if(!c){toast("Điền tham số bắt buộc");return;}
  var r=await call("POST","/api/rcon",{command:c});
  toast(r.ok?"RCON OK":"RCON lỗi");
  pollActivity();   // lệnh + kết quả đã nằm trong nhật ký server
}
(function(){
  var sel=$("#rconSel");
  RCON_CMDS.forEach(function(c,i){var o=document.createElement("option");o.value=i;
    o.textContent=c.c?(c.c+" — "+c.d):c.d;sel.appendChild(o);});
  sel.addEventListener("change",renderRconArgs);
  renderRconArgs();
})();
$("#btnRcon").onclick=function(){if(rconSelected().danger)arm(this,runRcon);else runRcon();};
$("#btnShutdown").onclick=function(){arm(this,async function(){
  report("shutdown",await call("POST","/api/shutdown",{waittime:parseInt($("#sdWait").value||"30",10),message:$("#sdMsg").value||"Server se tat"}));});};
$("#btnStop").onclick=function(){arm(this,async function(){report("stop",await call("POST","/api/stop",{}));});};
$("#btnRefresh").onclick=refreshPlayers;
$("#btnClear").onclick=async function(){
  await call("POST","/api/activity_clear",{});
  $("#log").innerHTML='<div class="empty">Chưa có hoạt động.</div>';
  pollActivity();
};
$("#btnLogout").onclick=function(){location.href="/logout";};
if(__ENFORCE__)$("#btnLogout").hidden=false;
function fmtRemain(s){s=parseInt(s||0,10);if(s>=60){var m=Math.floor(s/60),ss=s%60;return m+" phút"+(ss?(" "+ss+"s"):"");}return s+" giây";}
var cdActive=false;
async function pollCountdown(){
  try{var r=await call("GET","/api/countdown_status");var s=(r&&r.data)||{};var b=$("#cdBanner");
    cdActive=!!s.active;
    if(s.active){b.hidden=false;$("#cdRemain").textContent=fmtRemain(s.remaining)+" · "+(s.mode==="restart"?"khởi động lại":"tắt");}
    else b.hidden=true;
  }catch(e){}
}
$("#btnCountdown").onclick=function(){arm(this,async function(){
  var r=await call("POST","/api/countdown",{minutes:parseInt($("#cdMin").value||"5",10),mode:$("#cdMode").value,message:$("#cdMsg").value.trim()});
  report("đếm ngược "+($("#cdMode").value==="restart"?"restart":"shutdown"),r);pollCountdown();
});};
$("#btnCdCancel").onclick=async function(){report("hủy đếm ngược",await call("POST","/api/countdown_cancel",{}));pollCountdown();};
function refreshAll(){refreshStatus();refreshPlayers();pollCountdown();pollActivity();}
refreshAll();
// tạm dừng poll khi app chạy nền (đỡ tốn pin/mạng trên điện thoại), poll lại ngay khi mở lên
setInterval(function(){if(!document.hidden){refreshStatus();pollActivity();}},6000);
setInterval(function(){if(!document.hidden)refreshPlayers();},10000);
var cdTick=0;
setInterval(function(){if(document.hidden)return;cdTick++;if(cdActive||cdTick%5===0)pollCountdown();},2000);
document.addEventListener("visibilitychange",function(){if(!document.hidden)refreshAll();});
</script></body></html>"""
PAGE = PAGE.replace("__ENFORCE__", "true" if ENFORCE_AUTH else "false")

# ---------------------------------------------------------------- config editor page
CONFIG_PAGE = r"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Palworld Config Editor — PalWorldSettings.ini</title>
<meta name="description" content="Trình chỉnh cấu hình Palworld Dedicated Server trực quan: 119 tham số PalWorldSettings.ini có giải thích tiếng Việt, nạp/lưu trực tiếp vào server qua admin-tool, xuất ra file ini chuẩn.">
<style>html,body{margin:0}img{max-width:100%}</style>
</head>
<body>
<style>
  :root{
    --bg:#f4f6f3; --surface:#ffffff; --surface-2:#eef1ec; --surface-3:#e7ebe4;
    --text:#1a2129; --muted:#5c6a72; --faint:#8a978f; --border:#dde3db;
    --accent:#1f9c78; --accent-weak:#e2f1eb; --accent-ink:#0e5c46;
    --warn:#b7770c; --warn-weak:#f6ecd6; --warn-line:#d99a2b;
    --danger:#c0533b; --danger-weak:#f6e2dc;
    --mono:ui-monospace,"SFMono-Regular","JetBrains Mono",Consolas,"Liberation Mono",monospace;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
    --radius:12px; --radius-sm:8px;
    --shadow:0 1px 2px rgba(20,30,25,.05),0 8px 24px -12px rgba(20,30,25,.18);
  }
  @media (prefers-color-scheme:dark){
    :root{
      --bg:#12171a; --surface:#1a2126; --surface-2:#212a30; --surface-3:#28333a;
      --text:#e8ece8; --muted:#9aa8a2; --faint:#6f7d77; --border:#2b353b;
      --accent:#3fca9e; --accent-weak:#123028; --accent-ink:#8fe6cb;
      --warn:#e0a53a; --warn-weak:#332a15; --warn-line:#a9781f;
      --danger:#e07a60; --danger-weak:#3a201a;
      --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px -14px rgba(0,0,0,.6);
    }
  }
  *{box-sizing:border-box}
  [hidden]{display:none!important}
  body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);
    font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;}
  button{font-family:inherit;cursor:pointer}
  input,select{font-family:inherit}
  a{color:var(--accent);text-underline-offset:2px}

  /* ---- header ---- */
  .topbar{position:sticky;top:0;z-index:40;background:color-mix(in srgb,var(--surface) 88%,transparent);
    backdrop-filter:blur(10px);border-bottom:1px solid var(--border);}
  .topbar-in{max-width:1220px;margin:0 auto;padding:12px 20px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
  .brand{display:flex;align-items:center;gap:11px;margin-right:auto}
  .glyph{width:34px;height:34px;border-radius:9px;background:linear-gradient(150deg,var(--accent),var(--accent-ink));
    display:grid;place-items:center;color:#fff;font-weight:700;font-size:17px;flex:none;
    box-shadow:0 2px 8px -2px color-mix(in srgb,var(--accent) 60%,transparent)}
  .brand h1{font-size:16px;margin:0;letter-spacing:-.01em;font-weight:650}
  .brand p{margin:0;font-size:11.5px;color:var(--muted);letter-spacing:.02em}
  .changed-pill{font-size:12px;color:var(--warn);background:var(--warn-weak);
    border:1px solid var(--warn-line);padding:3px 10px;border-radius:20px;font-weight:600;
    display:inline-flex;gap:6px;align-items:center;white-space:nowrap}
  .changed-pill::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--warn-line)}
  .actions{display:flex;gap:8px;flex-wrap:wrap}
  .btn{border:1px solid var(--border);background:var(--surface);color:var(--text);
    padding:8px 13px;border-radius:var(--radius-sm);font-size:13px;font-weight:550;
    display:inline-flex;align-items:center;gap:7px;transition:.14s;white-space:nowrap}
  .btn:hover{background:var(--surface-2);border-color:var(--faint)}
  .btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  .btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
  .btn.primary:hover{filter:brightness(1.06)}
  .btn.ghost{background:transparent}
  .btn svg{width:15px;height:15px}

  /* ---- shell ---- */
  .shell{max-width:1220px;margin:0 auto;padding:22px 20px 120px;display:grid;
    grid-template-columns:236px 1fr;gap:26px;align-items:start}
  .rail{position:sticky;top:74px;display:flex;flex-direction:column;gap:14px;
    max-height:calc(100vh - 94px);overflow:auto;padding-right:2px}
  .search{position:relative}
  .search input{width:100%;padding:9px 12px 9px 34px;border:1px solid var(--border);
    background:var(--surface);color:var(--text);border-radius:var(--radius-sm);font-size:13.5px}
  .search input:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
  .search svg{position:absolute;left:11px;top:50%;transform:translateY(-50%);width:15px;height:15px;color:var(--faint)}
  .navlist{display:flex;flex-direction:column;gap:1px}
  .navlist a{display:flex;align-items:center;gap:9px;padding:7px 10px;border-radius:7px;
    color:var(--muted);text-decoration:none;font-size:13px;font-weight:520;transition:.12s}
  .navlist a:hover{background:var(--surface-2);color:var(--text)}
  .navlist a.on{background:var(--accent-weak);color:var(--accent-ink);font-weight:620}
  .navlist a .nc{margin-left:auto;font-size:10.5px;font-weight:700;color:var(--warn);
    background:var(--warn-weak);border-radius:20px;padding:1px 7px;min-width:20px;text-align:center;
    font-variant-numeric:tabular-nums}
  .navlist a .nc.zero{display:none}
  .rail-note{font-size:11.5px;color:var(--faint);line-height:1.55;padding:2px 4px}

  /* ---- content ---- */
  .content{min-width:0;display:flex;flex-direction:column;gap:30px}
  .intro{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
    padding:16px 18px;box-shadow:var(--shadow)}
  .intro h2{margin:0 0 6px;font-size:15px}
  .intro p{margin:0;font-size:13px;color:var(--muted)}
  .presets{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
  .preset{border:1px solid var(--border);background:var(--surface-2);border-radius:20px;
    padding:6px 13px;font-size:12.5px;font-weight:550;color:var(--text)}
  .preset:hover{border-color:var(--accent);color:var(--accent-ink);background:var(--accent-weak)}

  .group{scroll-margin-top:80px}
  .group-head{display:flex;align-items:baseline;gap:11px;margin-bottom:12px;
    padding-bottom:8px;border-bottom:1px solid var(--border)}
  .group-head h3{margin:0;font-size:16px;letter-spacing:-.01em;font-weight:640}
  .group-head .gnum{font-family:var(--mono);font-size:11px;color:var(--accent);font-weight:700;
    background:var(--accent-weak);border-radius:6px;padding:2px 7px}
  .group-head .gdesc{font-size:12px;color:var(--muted);margin-left:auto;text-align:right;max-width:44ch}

  .rows{display:flex;flex-direction:column;gap:9px}
  .row{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
    padding:13px 15px;display:grid;grid-template-columns:1fr 260px;gap:14px 18px;align-items:center;
    position:relative;transition:border-color .14s}
  .row.changed{border-color:var(--warn-line)}
  .row.changed::before{content:"";position:absolute;left:0;top:10px;bottom:10px;width:3px;
    border-radius:3px;background:var(--warn-line)}
  .row.hidden{display:none}
  .rmeta{min-width:0}
  .rkey{font-family:var(--mono);font-size:13.5px;font-weight:600;color:var(--text);
    display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .rkey .tag{font-family:var(--sans);font-size:9.5px;font-weight:700;letter-spacing:.05em;
    text-transform:uppercase;padding:2px 6px;border-radius:5px;background:var(--surface-3);color:var(--muted)}
  .rkey .tag.pvp{background:var(--danger-weak);color:var(--danger)}
  .rnote{font-size:12.5px;color:var(--muted);margin-top:4px;line-height:1.5}
  .rdefault{font-size:11px;color:var(--faint);margin-top:5px;font-family:var(--mono);
    display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .reset-field{border:none;background:none;color:var(--warn);font-size:11px;font-weight:600;
    padding:0;text-decoration:underline;text-underline-offset:2px;display:none}
  .row.changed .reset-field{display:inline}
  .warn-range{color:var(--danger);font-weight:600}

  .rctl{display:flex;flex-direction:column;gap:7px;align-items:stretch}
  .num-wrap{display:flex;align-items:center;gap:9px}
  input[type=number],input[type=text],input[type=password],select{
    width:100%;padding:8px 10px;border:1px solid var(--border);background:var(--surface-2);
    color:var(--text);border-radius:var(--radius-sm);font-size:13.5px;font-family:var(--mono)}
  input[type=text],input[type=password]{font-family:var(--sans)}
  input:focus,select:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
  .num-wrap input[type=number]{width:96px;flex:none;text-align:right;font-variant-numeric:tabular-nums}
  input[type=range]{flex:1;accent-color:var(--accent);height:4px}
  select{cursor:pointer}

  /* toggle */
  .toggle{display:inline-flex;align-items:center;gap:10px;cursor:pointer;user-select:none;
    justify-content:flex-end}
  .toggle input{position:absolute;opacity:0;width:0;height:0}
  .track{width:42px;height:24px;border-radius:20px;background:var(--surface-3);
    border:1px solid var(--border);position:relative;transition:.16s;flex:none}
  .track::after{content:"";position:absolute;top:2px;left:2px;width:18px;height:18px;border-radius:50%;
    background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.3);transition:.16s}
  .toggle input:checked + .track{background:var(--accent);border-color:var(--accent)}
  .toggle input:checked + .track::after{transform:translateX(18px)}
  .toggle input:focus-visible + .track{outline:2px solid var(--accent);outline-offset:2px}
  .tstate{font-size:12.5px;font-weight:600;color:var(--muted);width:32px;text-align:left}
  .toggle input:checked ~ .tstate{color:var(--accent-ink)}

  /* platforms / checks */
  .checks{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}
  .chk{font-size:12px;font-weight:560;padding:6px 11px;border-radius:20px;border:1px solid var(--border);
    background:var(--surface-2);color:var(--muted);cursor:pointer;user-select:none}
  .chk.on{background:var(--accent-weak);border-color:var(--accent);color:var(--accent-ink)}
  .chk input{display:none}

  /* ---- output dock ---- */
  .dock{position:fixed;left:0;right:0;bottom:0;z-index:50;background:var(--surface);
    border-top:1px solid var(--border);box-shadow:0 -8px 30px -18px rgba(0,0,0,.5)}
  .dock-in{max-width:1220px;margin:0 auto;padding:11px 20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  .dock .lbl{font-size:12.5px;color:var(--muted);display:flex;align-items:center;gap:9px}
  .dock .lbl b{color:var(--text);font-variant-numeric:tabular-nums}
  .dock .grow{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}
  .open-name{font-family:var(--mono);font-size:11.5px;color:var(--accent-ink);
    background:var(--accent-weak);border:1px solid var(--accent);border-radius:6px;padding:2px 8px;
    display:inline-flex;align-items:center;gap:5px}
  .open-name::before{content:"●";font-size:8px;color:var(--accent)}
  .btn.save{background:var(--accent);border-color:var(--accent);color:#fff}
  .btn.save:hover{filter:brightness(1.06)}

  /* ---- modal ---- */
  .overlay{position:fixed;inset:0;z-index:60;background:rgba(10,15,12,.5);
    display:none;align-items:center;justify-content:center;padding:20px}
  .overlay.show{display:flex}
  .modal{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
    width:min(720px,100%);max-height:86vh;display:flex;flex-direction:column;box-shadow:var(--shadow)}
  .modal-head{padding:15px 18px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px}
  .modal-head h3{margin:0;font-size:15px}
  .modal-head .x{margin-left:auto;border:none;background:var(--surface-2);width:30px;height:30px;
    border-radius:7px;color:var(--muted);font-size:17px;line-height:1}
  .modal-head .x:hover{background:var(--surface-3);color:var(--text)}
  .modal-body{padding:16px 18px;overflow:auto}
  .modal-body p{margin:0 0 11px;font-size:13px;color:var(--muted)}
  .modal-body textarea{width:100%;min-height:150px;padding:12px;border:1px solid var(--border);
    background:var(--surface-2);color:var(--text);border-radius:var(--radius-sm);
    font-family:var(--mono);font-size:12px;line-height:1.55;resize:vertical}
  .modal-body textarea:focus{outline:2px solid var(--accent);border-color:var(--accent)}
  .code-out{background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius-sm);
    padding:13px;font-family:var(--mono);font-size:12px;line-height:1.6;white-space:pre-wrap;
    word-break:break-all;max-height:38vh;overflow:auto;color:var(--text)}
  .code-out .k{color:var(--accent-ink)}
  .code-out .hl{background:var(--warn-weak);border-radius:3px;padding:0 2px;
    box-shadow:inset 0 -1px 0 var(--warn-line)}
  .modal-foot{padding:13px 18px;border-top:1px solid var(--border);display:flex;gap:9px;justify-content:flex-end;flex-wrap:wrap}
  .import-err{color:var(--danger);font-size:12.5px;font-weight:600;margin-top:9px;display:none}

  .toast{position:fixed;bottom:78px;left:50%;transform:translateX(-50%) translateY(20px);
    background:var(--text);color:var(--bg);padding:10px 18px;border-radius:22px;font-size:13px;
    font-weight:600;opacity:0;pointer-events:none;transition:.22s;z-index:80;box-shadow:var(--shadow)}
  .toast.show{opacity:1;transform:translateX(-50%) translateY(0)}

  @media (max-width:900px){
    .shell{grid-template-columns:1fr;gap:16px}
    .rail{position:static;max-height:none;flex-direction:column}
    .navlist{flex-direction:row;flex-wrap:wrap;gap:6px}
    .navlist a{border:1px solid var(--border)}
    .row{grid-template-columns:1fr}
    .rctl{align-items:stretch}
    .toggle,.checks{justify-content:flex-start}
    .group-head{flex-wrap:wrap}
    .group-head .gdesc{margin-left:0;text-align:left;max-width:none}
  }
  @media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>

<div class="topbar">
  <div class="topbar-in">
    <div class="brand">
      <div class="glyph">P</div>
      <div>
        <h1>Palworld Config Editor</h1>
        <p>PalWorldSettings.ini · bản 1.0 · 119 tham số</p>
      </div>
    </div>
    <span class="changed-pill" id="changedPill" hidden><span id="changedCount">0</span> đã đổi</span>
    <div class="actions">
      <a class="btn ghost" href="/" style="text-decoration:none" title="Quay về bảng điều khiển admin">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
        Admin
      </a>
      <button class="btn ghost" id="btnLoadServer" title="Nạp cấu hình hiện tại đang chạy trên server">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h5l2 3h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/></svg>
        Nạp từ server
      </button>
      <button class="btn ghost" id="btnImport" title="Dán nội dung file hiện tại để nạp vào">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        Nhập (dán)
      </button>
      <button class="btn ghost" id="btnReset" title="Đưa tất cả về mặc định game">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9 9 0 0 0-6.4 2.6L3 8"/><path d="M3 3v5h5"/></svg>
        Đặt lại
      </button>
      <button class="btn primary" id="btnExport">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        Xuất cấu hình
      </button>
    </div>
  </div>
</div>

<div class="shell">
  <aside class="rail">
    <div class="search">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      <input id="searchBox" type="text" placeholder="Tìm tham số / giải thích…" autocomplete="off">
    </div>
    <nav class="navlist" id="navlist"></nav>
    <p class="rail-note">Bấm <b>Nạp từ server</b> để lấy cấu hình đang chạy → chỉnh → <b>Lưu vào server</b> (ghi thẳng vào file trên NAS, tự backup bản cũ). Hoặc <b>Nhập (dán)</b> / <b>Xuất cấu hình</b> thủ công. Sửa xong nhớ <b>restart container palworld-server</b> để áp dụng.</p>
  </aside>

  <main class="content" id="content">
    <div class="intro">
      <h2>Chỉnh cấu hình server trực quan</h2>
      <p>Mỗi tham số có control phù hợp và note giải thích. Ô viền hổ phách = đang khác mặc định game. Bấm một preset để áp nhanh, hoặc nhập file hiện tại của bạn để chỉnh tiếp — mọi thứ xử lý ngay trong trình duyệt, không có gì được gửi đi.</p>
      <div class="presets" id="presets"></div>
    </div>
  </main>
</div>

<div class="dock">
  <div class="dock-in">
    <div class="lbl">
      <span>Tham số đã đổi khỏi mặc định: <b id="dockChanged">0</b> / <b>119</b></span>
      <span id="openName" class="open-name" hidden></span>
    </div>
    <div class="grow">
      <button class="btn ghost" id="btnPreview">Xem kết quả</button>
      <button class="btn save" id="btnSaveServer" hidden>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
        Lưu vào server
      </button>
      <button class="btn" id="btnCopyQuick">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        Sao chép ini
      </button>
      <button class="btn primary" id="btnDownload">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        Tải .ini
      </button>
    </div>
  </div>
</div>

<!-- import modal -->
<div class="overlay" id="importOverlay">
  <div class="modal">
    <div class="modal-head"><h3>Nhập file cấu hình hiện tại</h3><button class="x" data-close>&times;</button></div>
    <div class="modal-body">
      <p>Mở <span style="font-family:var(--mono);font-size:12px">Saved/Config/LinuxServer/PalWorldSettings.ini</span>, copy toàn bộ và dán vào đây. Có thể dán cả 2 dòng hoặc chỉ dòng <span style="font-family:var(--mono);font-size:12px">OptionSettings=(…)</span>.</p>
      <textarea id="importText" placeholder="[/Script/Pal.PalGameWorldSettings]&#10;OptionSettings=(Difficulty=None,...)"></textarea>
      <div class="import-err" id="importErr"></div>
    </div>
    <div class="modal-foot">
      <button class="btn ghost" data-close>Hủy</button>
      <button class="btn primary" id="btnDoImport">Nạp vào trình chỉnh</button>
    </div>
  </div>
</div>

<!-- export modal -->
<div class="overlay" id="exportOverlay">
  <div class="modal">
    <div class="modal-head"><h3>Cấu hình xuất ra</h3><button class="x" data-close>&times;</button></div>
    <div class="modal-body">
      <p>Định dạng chuẩn 2 dòng — <b style="color:var(--warn)">tô vàng</b> là các giá trị khác mặc định. Dán đè toàn bộ file ini rồi restart container.</p>
      <div class="code-out" id="codeOut"></div>
    </div>
    <div class="modal-foot">
      <button class="btn ghost" data-close>Đóng</button>
      <button class="btn" id="btnCopyModal">Sao chép</button>
      <button class="btn primary" id="btnDownloadModal">Tải file .ini</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
(function(){
"use strict";

// ---- parameter schema (t: b=bool i=int f=float e=enum s=string p=password pf=platforms raw=raw list) ----
var GROUPS = [
 {id:"server", n:"1", label:"Server & kết nối", desc:"Tên, mật khẩu, số slot, crossplay", items:[
   {k:"ServerName",t:"s",def:"Default Palworld Server",note:"Tên server hiển thị trên danh sách."},
   {k:"ServerDescription",t:"s",def:"",note:"Mô tả server."},
   {k:"ServerPassword",t:"p",def:"",note:"Mật khẩu vào server (rỗng = ai cũng vào được)."},
   {k:"AdminPassword",t:"p",def:"",note:"Mật khẩu admin — dùng cho RCON, REST API, lệnh /AdminPassword. Bắt buộc đặt nếu server công khai."},
   {k:"ServerPlayerMaxNum",t:"i",def:32,min:1,max:512,note:"Số người chơi tối đa."},
   {k:"CoopPlayerMaxNum",t:"i",def:4,min:1,max:4,note:"Số người tối đa trong phiên co-op (không áp dụng cho dedicated server)."},
   {k:"PublicIP",t:"s",def:"",note:"IP công khai khai báo lên server list (để trống = tự phát hiện)."},
   {k:"PublicPort",t:"i",def:8211,min:1024,max:65535,note:"Port công khai khai báo lên server list (không đổi port thực)."},
   {k:"Region",t:"s",def:"",note:"Nhãn khu vực hiển thị trên server list."},
   {k:"bUseAuth",t:"b",def:true,note:"Xác thực tài khoản khi kết nối. Luôn để bật (tắt cho phép giả mạo danh tính)."},
   {k:"BanListURL",t:"s",def:"https://api.palworldgame.com/api/banlist.txt",note:"Nguồn danh sách ban toàn cục; có thể trỏ tới file riêng của bạn."},
   {k:"bIsMultiplay",t:"b",def:false,note:"Bật chế độ multiplayer cho world (dedicated server tự xử lý — thường không cần đổi)."},
   {k:"CrossplayPlatforms",t:"pf",def:["Steam","Xbox","PS5","Mac"],note:"Các nền tảng được phép kết nối. Bỏ bớt để giới hạn (ví dụ chỉ Steam)."},
   {k:"bShowPlayerList",t:"b",def:false,note:"Hiện danh sách người chơi trên màn hình ESC của client."},
   {k:"bIsShowJoinLeftMessage",t:"b",def:true,note:'Hiện thông báo "đã vào/rời server" trong chat.'},
   {k:"bAllowClientMod",t:"b",def:true,note:"Cho phép client dùng mod. Tắt nếu muốn hạn chế gian lận."},
 ]},
 {id:"difficulty", n:"2", label:"Độ khó & Randomizer", desc:"Preset độ khó, xáo trộn spawn", items:[
   {k:"Difficulty",t:"e",def:"None",opts:["None","Casual","Normal","Hard"],note:"Preset độ khó. None = tùy chỉnh bằng các thông số bên dưới. Đặt preset sẽ ghi đè nhiều rate."},
   {k:"RandomizerType",t:"e",def:"None",opts:["None","Region","All"],note:"Xáo trộn vị trí spawn Pal: Region trộn trong vùng, All trộn toàn map."},
   {k:"RandomizerSeed",t:"s",def:"",note:"Seed cho randomizer (cùng seed = cùng kết quả trộn)."},
   {k:"bIsRandomizerPalLevelRandom",t:"b",def:false,note:"Random luôn cấp độ Pal khi bật randomizer."},
 ]},
 {id:"world", n:"3", label:"Thời gian, EXP & tài nguyên", desc:"Nhịp thế giới, tỉ lệ rơi & thu thập", items:[
   {k:"DayTimeSpeedRate",t:"f",def:1,min:0.1,max:5,note:"Tốc độ trôi thời gian ban ngày (2 = ngày ngắn gấp đôi)."},
   {k:"NightTimeSpeedRate",t:"f",def:1,min:0.1,max:5,note:"Tốc độ trôi thời gian ban đêm."},
   {k:"ExpRate",t:"f",def:1,min:0,max:20,note:"Hệ số kinh nghiệm nhận được (người chơi + Pal)."},
   {k:"WorkSpeedRate",t:"f",def:1,min:0.1,max:5,note:"Tốc độ làm việc tại base (craft, xây…)."},
   {k:"PalEggDefaultHatchingTime",t:"f",def:72,min:0,max:240,step:1,note:"Số giờ thực để ấp trứng lớn nhất (0 = nở ngay). Bản 1.0 mặc định là 1."},
   {k:"SupplyDropSpan",t:"i",def:180,min:0,max:1000,step:10,note:"Chu kỳ (phút) xuất hiện supply drop. 0 = tắt."},
   {k:"EnemyDropItemRate",t:"f",def:1,min:0.5,max:5,note:"Hệ số vật phẩm rơi từ quái."},
   {k:"CollectionDropRate",t:"f",def:1,min:0.5,max:5,note:"Hệ số vật phẩm thu thập (chặt cây, đập đá…)."},
   {k:"CollectionObjectHpRate",t:"f",def:1,min:0.5,max:3,note:"Máu của vật thể thu thập. Giảm để chặt/đập nhanh hơn."},
   {k:"CollectionObjectRespawnSpeedRate",t:"f",def:1,min:0.5,max:5,note:"Tốc độ hồi sinh vật thể thu thập (cao = mọc lại nhanh)."},
   {k:"ItemWeightRate",t:"f",def:1,min:0.1,max:5,note:"Hệ số cân nặng vật phẩm (giảm để vác được nhiều hơn)."},
   {k:"ItemCorruptionMultiplier",t:"f",def:1,min:0.1,max:10,note:"Hệ số tốc độ hỏng/ôi thiu của thực phẩm."},
 ]},
 {id:"pal", n:"4", label:"Pal & chiến đấu", desc:"Bắt, spawn, sát thương, raid boss", items:[
   {k:"PalCaptureRate",t:"f",def:1,min:0.5,max:5,note:"Tỉ lệ bắt Pal thành công."},
   {k:"PalSpawnNumRate",t:"f",def:1,min:0.5,max:5,note:"Mật độ Pal spawn ngoài map (tăng = đông hơn, tốn CPU hơn)."},
   {k:"PalDamageRateAttack",t:"f",def:1,min:0.1,max:5,note:"Sát thương Pal GÂY RA."},
   {k:"PalDamageRateDefense",t:"f",def:1,min:0.1,max:5,note:'Sát thương Pal NHẬN VÀO (tăng = Pal "giòn" hơn, giảm = Pal bền hơn).'},
   {k:"PalStomachDecreaceRate",t:"f",def:1,min:0.1,max:5,note:"Tốc độ đói của Pal."},
   {k:"PalStaminaDecreaceRate",t:"f",def:1,min:0.1,max:5,note:"Tốc độ tụt stamina của Pal."},
   {k:"PalAutoHPRegeneRate",t:"f",def:1,min:0.1,max:5,note:"Tốc độ hồi máu tự nhiên của Pal."},
   {k:"PalAutoHpRegeneRateInSleep",t:"f",def:1,min:0.1,max:5,note:"Tốc độ hồi máu của Pal khi ngủ (trong Palbox)."},
   {k:"bEnableInvaderEnemy",t:"b",def:true,note:"Bật/tắt các đợt raid tấn công base."},
   {k:"EnablePredatorBossPal",t:"b",def:true,note:"Bật/tắt Predator Pal (boss hung dữ ngoài map)."},
   {k:"bActiveUNKO",t:"b",def:false,note:"Bật UNKO (vật phẩm 💩 joke item)."},
   {k:"MonsterFarmActionSpeedRate",t:"f",def:1,min:0.1,max:5,note:"Tốc độ làm việc của Pal ở trang trại/ranch."},
 ]},
 {id:"player", n:"5", label:"Người chơi & sinh tồn", desc:"Sát thương, hồi phục, hình phạt chết, fast-travel", items:[
   {k:"PlayerDamageRateAttack",t:"f",def:1,min:0.1,max:5,note:"Sát thương người chơi gây ra."},
   {k:"PlayerDamageRateDefense",t:"f",def:1,min:0.1,max:5,note:"Sát thương người chơi nhận vào."},
   {k:"PlayerStomachDecreaceRate",t:"f",def:1,min:0.1,max:5,note:"Tốc độ đói của người chơi."},
   {k:"PlayerStaminaDecreaceRate",t:"f",def:1,min:0.1,max:5,note:"Tốc độ tụt stamina người chơi."},
   {k:"PlayerAutoHPRegeneRate",t:"f",def:1,min:0.1,max:5,note:"Tốc độ hồi máu tự nhiên."},
   {k:"PlayerAutoHpRegeneRateInSleep",t:"f",def:1,min:0.1,max:5,note:"Tốc độ hồi máu khi ngủ."},
   {k:"DeathPenalty",t:"e",def:"All",opts:["None","Item","ItemAndEquipment","All"],note:"Hình phạt khi chết: None không rơi gì; Item rơi đồ trong túi (trừ trang bị); ItemAndEquipment rơi cả trang bị; All rơi cả Pal. Bản 1.0 mặc định là Item."},
   {k:"bEnableNonLoginPenalty",t:"b",def:true,note:"Phạt người không đăng nhập lâu ngày (độ bền/thối đồ vẫn tính)."},
   {k:"bEnableFastTravel",t:"b",def:true,note:"Cho phép dịch chuyển nhanh qua Great Eagle Statue."},
   {k:"bEnableFastTravelOnlyBaseCamp",t:"b",def:false,note:"Chỉ cho fast-travel tới base camp (tăng độ khó)."},
   {k:"bIsStartLocationSelectByMap",t:"b",def:true,note:"Cho người mới chọn điểm xuất phát trên map (tắt = spawn cố định). Bản 1.0 mặc định tắt."},
   {k:"bExistPlayerAfterLogout",t:"b",def:false,note:"Nhân vật vẫn đứng trong world sau khi thoát game (có thể bị giết/cướp đồ — chỉ nên bật server hardcore PvP)."},
   {k:"bEnableAimAssistPad",t:"b",def:true,note:"Hỗ trợ ngắm cho tay cầm."},
   {k:"bEnableAimAssistKeyboard",t:"b",def:false,note:"Hỗ trợ ngắm cho chuột + bàn phím."},
   {k:"EquipmentDurabilityDamageRate",t:"f",def:1,min:0.1,max:5,note:"Tốc độ hao độ bền trang bị."},
   {k:"BlockRespawnTime",t:"f",def:5,min:0,max:60,step:1,note:"Thời gian (giây) chặn respawn sau khi chết."},
   {k:"RespawnPenaltyDurationThreshold",t:"f",def:0,min:0,max:3600,step:10,note:"Ngưỡng (giây) kích hoạt phạt respawn khi chết liên tục (0 = tắt)."},
   {k:"RespawnPenaltyTimeScale",t:"f",def:2,min:0,max:10,note:"Hệ số nhân thời gian chờ respawn mỗi lần chết liên tiếp."},
   {k:"bAllowEnhanceStat_Health",t:"b",def:true,note:"Cho phép cộng điểm chỉ số HP khi lên cấp."},
   {k:"bAllowEnhanceStat_Attack",t:"b",def:true,note:"Cho phép cộng điểm Attack."},
   {k:"bAllowEnhanceStat_Stamina",t:"b",def:true,note:"Cho phép cộng điểm Stamina."},
   {k:"bAllowEnhanceStat_Weight",t:"b",def:true,note:"Cho phép cộng điểm Weight (sức vác)."},
   {k:"bAllowEnhanceStat_WorkSpeed",t:"b",def:true,note:"Cho phép cộng điểm Work Speed."},
 ]},
 {id:"build", n:"6", label:"Xây dựng & vật phẩm rơi", desc:"Máu công trình, giới hạn build, drop trên đất", items:[
   {k:"BuildObjectHpRate",t:"f",def:1,min:0.5,max:5,note:"Máu công trình xây dựng."},
   {k:"BuildObjectDamageRate",t:"f",def:1,min:0.5,max:3,note:"Sát thương công trình nhận vào (tăng = dễ phá)."},
   {k:"BuildObjectDeteriorationDamageRate",t:"f",def:1,min:0,max:10,note:"Tốc độ mục nát tự nhiên của công trình ngoài vùng base (0 = không mục)."},
   {k:"bBuildAreaLimit",t:"b",def:false,note:"Cấm xây sát các vị trí quan trọng (fast-travel, boss tower…)."},
   {k:"MaxBuildingLimitNum",t:"i",def:0,min:0,max:8,note:"Giới hạn số công trình mỗi người chơi (0 = không giới hạn)."},
   {k:"DropItemMaxNum",t:"i",def:3000,min:0,max:10000,step:100,note:"Số vật phẩm rơi tối đa tồn tại trong world (quá nhiều gây lag)."},
   {k:"DropItemMaxNum_UNKO",t:"i",def:100,min:0,max:5000,step:10,note:"Giới hạn riêng cho UNKO."},
   {k:"DropItemAliveMaxHours",t:"f",def:1,min:0,max:240,step:0.5,note:"Số giờ vật phẩm rơi tồn tại trước khi biến mất."},
   {k:"PhysicsActiveDropItemMaxNum",t:"i",def:-1,min:-1,max:10000,step:50,note:"Số vật phẩm rơi có mô phỏng vật lý cùng lúc (-1 = không giới hạn; đặt thấp để giảm lag)."},
   {k:"bEnableBuildingPlayerUIdDisplay",t:"b",def:false,note:"Hiện tên người xây trên công trình (tiện quản trị server đông)."},
   {k:"BuildingNameDisplayCacheTTLSeconds",t:"i",def:60,min:1,max:3600,step:10,note:"TTL (giây) cache tên người xây hiển thị."},
 ]},
 {id:"guild", n:"7", label:"Base camp & Guild", desc:"Số base, worker, thành viên, chuyển chủ guild", items:[
   {k:"BaseCampMaxNum",t:"i",def:128,min:0,max:10240,step:8,note:"Tổng số base camp tối đa toàn server."},
   {k:"BaseCampMaxNumInGuild",t:"i",def:3,min:1,max:50,note:"Số base camp tối đa mỗi guild (tăng cẩn thận — ảnh hưởng hiệu năng mạnh)."},
   {k:"BaseCampWorkerMaxNum",t:"i",def:15,min:1,max:50,note:"Số Pal làm việc tối đa mỗi base (tăng = nặng server đáng kể)."},
   {k:"GuildPlayerMaxNum",t:"i",def:20,min:1,max:100,note:"Số thành viên tối đa mỗi guild."},
   {k:"bAutoResetGuildNoOnlinePlayers",t:"b",def:false,note:"Tự xóa guild khi không ai online quá thời gian dưới đây (⚠️ mất base + đồ của guild)."},
   {k:"AutoResetGuildTimeNoOnlinePlayers",t:"f",def:72,min:0,max:240,step:1,note:"Số giờ không online trước khi guild bị reset."},
   {k:"GuildRejoinCooldownMinutes",t:"i",def:0,min:0,max:1440,step:5,note:"Thời gian chờ (phút) trước khi được gia nhập lại guild vừa rời (chống lách luật PvP)."},
   {k:"AutoTransferMasterCheckIntervalSeconds",t:"f",def:3600,min:60,max:86400,step:60,note:"Chu kỳ (giây) kiểm tra guild master vắng mặt."},
   {k:"AutoTransferMasterThresholdDays",t:"i",def:14,min:1,max:365,note:"Số ngày guild master không online thì tự chuyển quyền cho thành viên khác."},
   {k:"bInvisibleOtherGuildBaseCampAreaFX",t:"b",def:false,note:"Ẩn hiệu ứng vòng sáng vùng base của guild khác."},
   {k:"bEnableDefenseOtherGuildPlayer",t:"b",def:false,note:"Pal ở base tấn công người chơi guild khác đi vào vùng base."},
   {k:"MaxGuildsPerFrame",t:"i",def:10,min:1,max:100,note:"Số guild xử lý mỗi frame (thông số hiệu năng — giữ mặc định)."},
 ]},
 {id:"pvp", n:"8", label:"PvP & Hardcore", desc:"Đối kháng người chơi, chết mất nhân vật/Pal", items:[
   {k:"bIsPvP",t:"b",def:false,tag:"pvp",note:"Bật chế độ PvP toàn server."},
   {k:"bEnablePlayerToPlayerDamage",t:"b",def:false,tag:"pvp",note:"Người chơi gây sát thương lẫn nhau (kể cả ngoài chế độ PvP)."},
   {k:"bEnableFriendlyFire",t:"b",def:false,tag:"pvp",note:"Sát thương đồng đội (cùng guild)."},
   {k:"bCanPickupOtherGuildDeathPenaltyDrop",t:"b",def:false,tag:"pvp",note:"Cho phép nhặt đồ rơi khi chết của người thuộc guild khác."},
   {k:"bHardcore",t:"b",def:false,tag:"pvp",note:"Chế độ hardcore: chết là mất nhân vật."},
   {k:"bPalLost",t:"b",def:false,tag:"pvp",note:"Pal chết là mất vĩnh viễn (permadeath cho Pal)."},
   {k:"bCharacterRecreateInHardcore",t:"b",def:false,tag:"pvp",note:"Cho phép tạo lại nhân vật mới sau khi chết ở hardcore."},
   {k:"bDisplayPvPItemNumOnWorldMap_BaseCamp",t:"b",def:false,tag:"pvp",note:"(PvP) Hiện số vật phẩm tại base camp lên world map."},
   {k:"bDisplayPvPItemNumOnWorldMap_Player",t:"b",def:false,tag:"pvp",note:"(PvP) Hiện số vật phẩm người chơi mang theo lên world map."},
   {k:"bAdditionalDropItemWhenPlayerKillingInPvPMode",t:"b",def:false,tag:"pvp",note:"(PvP) Rơi thêm vật phẩm đặc biệt khi giết người chơi."},
   {k:"AdditionalDropItemWhenPlayerKillingInPvPMode",t:"s",def:"PlayerDropItem",tag:"pvp",note:"(PvP) Loại vật phẩm (ID) rơi thêm khi giết người."},
   {k:"AdditionalDropItemNumWhenPlayerKillingInPvPMode",t:"i",def:1,min:0,max:100,tag:"pvp",note:"(PvP) Số lượng vật phẩm rơi thêm."},
 ]},
 {id:"chat", n:"9", label:"Chat & Voice", desc:"Giới hạn chat, proximity voice chat", items:[
   {k:"ChatPostLimitPerMinute",t:"i",def:10,min:0,max:100,note:"Giới hạn số tin chat mỗi phút mỗi người (chống spam)."},
   {k:"bEnableVoiceChat",t:"b",def:false,note:"Bật proximity voice chat tích hợp."},
   {k:"VoiceChatMaxVolumeDistance",t:"f",def:3000,min:100,max:50000,step:100,note:"Khoảng cách (cm) còn nghe rõ 100% âm lượng."},
   {k:"VoiceChatZeroVolumeDistance",t:"f",def:15000,min:100,max:50000,step:100,note:"Khoảng cách (cm) âm lượng về 0 (ngoài tầm không nghe)."},
 ]},
 {id:"rcon", n:"10", label:"RCON & REST API", desc:"Cổng quản trị từ xa", items:[
   {k:"RCONEnabled",t:"b",def:false,note:"Bật RCON (điều khiển từ xa qua giao thức Source RCON)."},
   {k:"RCONPort",t:"i",def:25575,min:1,max:65535,note:"Port TCP của RCON."},
   {k:"RESTAPIEnabled",t:"b",def:false,note:"Bật REST API quản trị (khuyên dùng thay RCON)."},
   {k:"RESTAPIPort",t:"i",def:8212,min:1,max:65535,note:"Port TCP của REST API."},
 ]},
 {id:"system", n:"11", label:"Lưu trữ, log & hiệu năng", desc:"Auto-save, backup, log, đồng bộ mạng", items:[
   {k:"AutoSaveSpan",t:"f",def:30,min:30,max:3600,step:5,note:"Chu kỳ auto-save (giây)."},
   {k:"bIsUseBackupSaveData",t:"b",def:true,note:"Tự tạo bản backup save (thư mục Saved/SaveGames/.../backup)."},
   {k:"LogFormatType",t:"e",def:"Text",opts:["Text","Json"],note:"Định dạng log server (Json tiện cho log collector)."},
   {k:"ServerReplicatePawnCullDistance",t:"f",def:15000,min:5000,max:15000,step:500,note:"Khoảng cách (cm) server đồng bộ pawn tới client. Giảm để nhẹ mạng/CPU, đổi lại Pal hiện muộn khi lại gần."},
   {k:"ItemContainerForceMarkDirtyInterval",t:"f",def:1,min:0.1,max:10,note:"Chu kỳ (giây) đánh dấu đồng bộ container đồ — giữ mặc định."},
   {k:"PlayerDataPalStorageUpdateCheckTickInterval",t:"f",def:1,min:0.1,max:60,step:0.5,note:"Chu kỳ (giây) kiểm tra cập nhật Pal storage của người chơi."},
   {k:"bAllowGlobalPalboxExport",t:"b",def:true,note:"Cho phép xuất Pal lên Global Palbox (chuyển Pal giữa các server/world)."},
   {k:"bAllowGlobalPalboxImport",t:"b",def:false,note:"Cho phép nhập Pal từ Global Palbox vào server (tắt để tránh tuồn Pal ngoài luồng)."},
   {k:"DenyTechnologyList",t:"raw",def:"",note:'Danh sách công nghệ bị cấm mở khóa, phân cách phẩy (ví dụ cấm lồng bắt người: HumanCage).'},
 ]},
];

// canonical order for the OptionSettings line (must match game's file order)
var CANON = ("Difficulty,RandomizerType,RandomizerSeed,bIsRandomizerPalLevelRandom,DayTimeSpeedRate,NightTimeSpeedRate,ExpRate,PalCaptureRate,PalSpawnNumRate,PalDamageRateAttack,PalDamageRateDefense,PlayerDamageRateAttack,PlayerDamageRateDefense,PlayerStomachDecreaceRate,PlayerStaminaDecreaceRate,PlayerAutoHPRegeneRate,PlayerAutoHpRegeneRateInSleep,PalStomachDecreaceRate,PalStaminaDecreaceRate,PalAutoHPRegeneRate,PalAutoHpRegeneRateInSleep,BuildObjectHpRate,BuildObjectDamageRate,BuildObjectDeteriorationDamageRate,CollectionDropRate,CollectionObjectHpRate,CollectionObjectRespawnSpeedRate,EnemyDropItemRate,DeathPenalty,bEnablePlayerToPlayerDamage,bEnableFriendlyFire,bEnableInvaderEnemy,bActiveUNKO,bEnableAimAssistPad,bEnableAimAssistKeyboard,DropItemMaxNum,PhysicsActiveDropItemMaxNum,DropItemMaxNum_UNKO,BaseCampMaxNum,BaseCampWorkerMaxNum,DropItemAliveMaxHours,bAutoResetGuildNoOnlinePlayers,AutoResetGuildTimeNoOnlinePlayers,GuildPlayerMaxNum,BaseCampMaxNumInGuild,PalEggDefaultHatchingTime,WorkSpeedRate,AutoSaveSpan,bIsMultiplay,bIsPvP,bHardcore,bPalLost,bCharacterRecreateInHardcore,bCanPickupOtherGuildDeathPenaltyDrop,bEnableNonLoginPenalty,bEnableFastTravel,bEnableFastTravelOnlyBaseCamp,bIsStartLocationSelectByMap,bExistPlayerAfterLogout,bEnableDefenseOtherGuildPlayer,bInvisibleOtherGuildBaseCampAreaFX,bBuildAreaLimit,ItemWeightRate,CoopPlayerMaxNum,ServerPlayerMaxNum,ServerName,ServerDescription,AdminPassword,ServerPassword,bAllowClientMod,PublicPort,PublicIP,RCONEnabled,RCONPort,Region,bUseAuth,BanListURL,RESTAPIEnabled,RESTAPIPort,bShowPlayerList,ChatPostLimitPerMinute,CrossplayPlatforms,bIsUseBackupSaveData,LogFormatType,bIsShowJoinLeftMessage,SupplyDropSpan,EnablePredatorBossPal,MaxBuildingLimitNum,ServerReplicatePawnCullDistance,bAllowGlobalPalboxExport,bAllowGlobalPalboxImport,EquipmentDurabilityDamageRate,ItemContainerForceMarkDirtyInterval,PlayerDataPalStorageUpdateCheckTickInterval,ItemCorruptionMultiplier,MonsterFarmActionSpeedRate,DenyTechnologyList,GuildRejoinCooldownMinutes,AutoTransferMasterCheckIntervalSeconds,AutoTransferMasterThresholdDays,MaxGuildsPerFrame,BlockRespawnTime,RespawnPenaltyDurationThreshold,RespawnPenaltyTimeScale,bDisplayPvPItemNumOnWorldMap_BaseCamp,bDisplayPvPItemNumOnWorldMap_Player,AdditionalDropItemWhenPlayerKillingInPvPMode,AdditionalDropItemNumWhenPlayerKillingInPvPMode,bAdditionalDropItemWhenPlayerKillingInPvPMode,bEnableVoiceChat,VoiceChatMaxVolumeDistance,VoiceChatZeroVolumeDistance,bAllowEnhanceStat_Health,bAllowEnhanceStat_Attack,bAllowEnhanceStat_Stamina,bAllowEnhanceStat_Weight,bAllowEnhanceStat_WorkSpeed,bEnableBuildingPlayerUIdDisplay,BuildingNameDisplayCacheTTLSeconds").split(",");

var PRESETS = [
  {name:"↺ Mặc định game", d:{}},
  {name:"👥 Co-op bạn bè", d:{ExpRate:2,PalCaptureRate:1.5,WorkSpeedRate:1.5,PalEggDefaultHatchingTime:1,DeathPenalty:"None",CollectionDropRate:2,EnemyDropItemRate:2,ServerPlayerMaxNum:8,bEnableInvaderEnemy:true}},
  {name:"🌐 Public PvE", d:{ServerPlayerMaxNum:32,RESTAPIEnabled:true,bShowPlayerList:true,ChatPostLimitPerMinute:10,bAutoResetGuildNoOnlinePlayers:true,AutoResetGuildTimeNoOnlinePlayers:240,bBuildAreaLimit:true,bIsUseBackupSaveData:true}},
  {name:"⚔️ PvP / Hardcore", d:{bIsPvP:true,bEnablePlayerToPlayerDamage:true,bCanPickupOtherGuildDeathPenaltyDrop:true,DeathPenalty:"All",bHardcore:true,bCharacterRecreateInHardcore:true,bEnableDefenseOtherGuildPlayer:true,GuildRejoinCooldownMinutes:60,bDisplayPvPItemNumOnWorldMap_Player:true,bAdditionalDropItemWhenPlayerKillingInPvPMode:true}},
  {name:"🪶 Tối ưu server yếu", d:{DropItemMaxNum:1000,PhysicsActiveDropItemMaxNum:200,DropItemAliveMaxHours:0.5,ServerReplicatePawnCullDistance:10000,BaseCampMaxNumInGuild:3,AutoSaveSpan:60,bInvisibleOtherGuildBaseCampAreaFX:true}},
];

// ---- build lookup + state ----
var DEF = {}, META = {};
GROUPS.forEach(function(g){ g.items.forEach(function(it){ it.group=g.id; META[it.k]=it;
  DEF[it.k] = (it.t==="pf") ? it.def.slice() : it.def; }); });
var state = clone(DEF);

function clone(o){ var r={}; for(var k in o){ r[k]=Array.isArray(o[k])?o[k].slice():o[k]; } return r; }
function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

function isChanged(k){
  var m=META[k], a=state[k], b=DEF[k];
  if(m.t==="pf") return a.slice().sort().join(",")!==b.slice().sort().join(",");
  if(m.t==="f") return Math.abs(Number(a)-Number(b))>1e-9;
  return a!==b;
}
function fmtVal(k){
  var m=META[k], v=state[k];
  switch(m.t){
    case "b": return v?"True":"False";
    case "i": return String(Math.round(Number(v)));
    case "f": return Number(v).toFixed(6);
    case "e": return String(v);
    case "s": case "p": return '"'+String(v)+'"';
    case "pf": return "("+v.join(",")+")";
    case "raw": return String(v);
  }
  return String(v);
}

// ---- render ----
var content=document.getElementById("content");
var navlist=document.getElementById("navlist");
var rowEls={};

function buildNav(){
  navlist.innerHTML="";
  GROUPS.forEach(function(g){
    var a=document.createElement("a"); a.href="#g-"+g.id; a.dataset.g=g.id;
    a.innerHTML='<span>'+esc(g.label)+'</span><span class="nc zero" data-nc="'+g.id+'">0</span>';
    a.addEventListener("click",function(e){ e.preventDefault();
      document.getElementById("g-"+g.id).scrollIntoView({behavior:"smooth",block:"start"}); });
    navlist.appendChild(a);
  });
}
function buildPresets(){
  var wrap=document.getElementById("presets");
  PRESETS.forEach(function(p){
    var b=document.createElement("button"); b.className="preset"; b.textContent=p.name;
    b.addEventListener("click",function(){ applyPreset(p); }); wrap.appendChild(b);
  });
}
function applyPreset(p){
  state=clone(DEF);
  for(var k in p.d){ if(META[k]) state[k]= (META[k].t==="pf")? p.d[k].slice(): p.d[k]; }
  syncAll(); toast(p.d && Object.keys(p.d).length? "Đã áp preset: "+p.name : "Đã đặt lại mặc định");
}

function buildGroups(){
  GROUPS.forEach(function(g){
    var sec=document.createElement("section"); sec.className="group"; sec.id="g-"+g.id;
    var head=document.createElement("div"); head.className="group-head";
    head.innerHTML='<span class="gnum">'+g.n+'</span><h3>'+esc(g.label)+'</h3><span class="gdesc">'+esc(g.desc)+'</span>';
    sec.appendChild(head);
    var rows=document.createElement("div"); rows.className="rows";
    g.items.forEach(function(it){ rows.appendChild(buildRow(it)); });
    sec.appendChild(rows); content.appendChild(sec);
  });
}

function buildRow(it){
  var row=document.createElement("div"); row.className="row"; row.dataset.k=it.k;
  row.dataset.search=(it.k+" "+it.note).toLowerCase();
  var meta=document.createElement("div"); meta.className="rmeta";
  var tag = it.tag==="pvp" ? '<span class="tag pvp">PvP</span>' : "";
  var defStr = defaultLabel(it);
  meta.innerHTML='<div class="rkey"><span>'+esc(it.k)+'</span>'+tag+'</div>'+
    '<div class="rnote">'+esc(it.note)+'</div>'+
    '<div class="rdefault"><span>Mặc định: <b>'+esc(defStr)+'</b></span>'+
    '<button class="reset-field" data-reset="'+it.k+'">đặt lại</button>'+
    '<span class="warn-range" data-warn="'+it.k+'" style="display:none"></span></div>';
  var ctl=document.createElement("div"); ctl.className="rctl";
  ctl.appendChild(buildControl(it));
  row.appendChild(meta); row.appendChild(ctl);
  meta.querySelector('[data-reset]').addEventListener("click",function(){
    state[it.k]= (it.t==="pf")? DEF[it.k].slice(): DEF[it.k]; syncRow(it.k); syncSummary();
  });
  rowEls[it.k]=row; return row;
}
function defaultLabel(it){
  if(it.t==="b") return it.def?"Bật":"Tắt";
  if(it.t==="f") return Number(it.def).toString();
  if(it.t==="pf") return "("+it.def.join(",")+")";
  if(it.t==="s"||it.t==="p") return it.def===""?"(trống)":it.def;
  if(it.t==="raw") return it.def===""?"(trống)":it.def;
  return String(it.def);
}

function buildControl(it){
  var k=it.k, wrap=document.createElement("div"); wrap.style.width="100%";
  if(it.t==="b"){
    var lab=document.createElement("label"); lab.className="toggle";
    var inp=document.createElement("input"); inp.type="checkbox"; inp.checked=!!state[k];
    var tr=document.createElement("span"); tr.className="track";
    var st=document.createElement("span"); st.className="tstate"; st.textContent=inp.checked?"Bật":"Tắt";
    inp.addEventListener("change",function(){ state[k]=inp.checked; st.textContent=inp.checked?"Bật":"Tắt"; syncRow(k); syncSummary(); });
    lab.appendChild(inp); lab.appendChild(tr); lab.appendChild(st); wrap.appendChild(lab);
  } else if(it.t==="e"){
    var sel=document.createElement("select");
    it.opts.forEach(function(o){ var op=document.createElement("option"); op.value=o; op.textContent=o; if(o===state[k])op.selected=true; sel.appendChild(op); });
    sel.addEventListener("change",function(){ state[k]=sel.value; syncRow(k); syncSummary(); });
    wrap.appendChild(sel);
  } else if(it.t==="pf"){
    var box=document.createElement("div"); box.className="checks";
    ["Steam","Xbox","PS5","Mac"].forEach(function(pl){
      var lab=document.createElement("label"); lab.className="chk"; lab.textContent=pl;
      var inp=document.createElement("input"); inp.type="checkbox"; inp.checked=state[k].indexOf(pl)>=0;
      if(inp.checked) lab.classList.add("on");
      inp.addEventListener("change",function(){
        var arr=state[k].filter(function(x){return x!==pl;});
        if(inp.checked) arr.push(pl);
        // preserve canonical platform order
        state[k]=["Steam","Xbox","PS5","Mac"].filter(function(x){return arr.indexOf(x)>=0;});
        lab.classList.toggle("on",inp.checked); syncRow(k); syncSummary();
      });
      lab.insertBefore(inp,lab.firstChild); box.appendChild(lab);
    });
    wrap.appendChild(box);
  } else if(it.t==="s"||it.t==="p"||it.t==="raw"){
    var inp2=document.createElement("input"); inp2.type= it.t==="p"?"password":"text"; inp2.value=state[k];
    if(it.t==="p"){ inp2.autocomplete="new-password"; inp2.placeholder="(để trống nếu không đặt)"; }
    if(it.t==="raw"){ inp2.placeholder="ID1,ID2,… (trống = không cấm gì)"; inp2.style.fontFamily="var(--mono)"; }
    inp2.addEventListener("input",function(){ state[k]=inp2.value; syncRow(k); syncSummary(); });
    wrap.appendChild(inp2);
  } else { // i / f
    var box2=document.createElement("div"); box2.className="num-wrap";
    var slider=null, hasSlider = it.t==="f" && typeof it.max==="number" && it.max<=20 && it.min>=0;
    var step = it.step!=null ? it.step : (it.t==="f"?0.1:1);
    if(hasSlider){
      slider=document.createElement("input"); slider.type="range";
      slider.min=it.min; slider.max=it.max; slider.step=step; slider.value=state[k];
    }
    var num=document.createElement("input"); num.type="number"; num.value=state[k];
    if(it.min!=null)num.min=it.min; if(it.max!=null)num.max=it.max; num.step=step;
    function commit(v,fromSlider){
      var n=parseFloat(v); if(isNaN(n)){ return; }
      if(it.t==="i") n=Math.round(n);
      state[k]=n;
      if(slider && n>=it.min && n<=it.max) slider.value=n;
      if(fromSlider) num.value=n;
      syncRow(k); syncSummary();
    }
    num.addEventListener("input",function(){ commit(num.value,false); });
    if(slider){ slider.addEventListener("input",function(){ commit(slider.value,true); }); box2.appendChild(slider); }
    box2.appendChild(num);
    wrap.appendChild(box2);
  }
  return wrap;
}

// ---- sync ----
function syncRow(k){
  var row=rowEls[k]; if(!row) return;
  var ch=isChanged(k); row.classList.toggle("changed",ch);
  // range warn
  var m=META[k], warn=row.querySelector('[data-warn]');
  if((m.t==="i"||m.t==="f") && (m.min!=null||m.max!=null)){
    var v=Number(state[k]), bad=(m.min!=null&&v<m.min)||(m.max!=null&&v>m.max);
    if(bad){ warn.style.display="inline"; warn.textContent="⚠ ngoài khoảng khuyến nghị "+m.min+"–"+m.max; }
    else warn.style.display="none";
  }
}
function syncAll(){
  // rebuild controls to reflect state fully (used after preset/import)
  content.querySelectorAll(".group").forEach(function(n){n.remove()});
  rowEls={}; buildGroups();
  Object.keys(META).forEach(syncRow); syncSummary();
}
function syncSummary(){
  var n=0; Object.keys(META).forEach(function(k){ if(isChanged(k))n++; });
  document.getElementById("changedCount").textContent=n;
  document.getElementById("changedPill").hidden = n===0;
  document.getElementById("dockChanged").textContent=n;
  // per-group counts
  var per={}; GROUPS.forEach(function(g){per[g.id]=0;});
  Object.keys(META).forEach(function(k){ if(isChanged(k)) per[META[k].group]++; });
  navlist.querySelectorAll("[data-nc]").forEach(function(el){
    var c=per[el.dataset.nc]||0; el.textContent=c; el.classList.toggle("zero",c===0);
  });
  save();
}

// ---- output ----
function buildLine(){ return "OptionSettings=("+CANON.map(function(k){return k+"="+fmtVal(k);}).join(",")+")"; }
function buildFile(){ return "[/Script/Pal.PalGameWorldSettings]\n"+buildLine(); }
function buildHighlighted(){
  var parts=CANON.map(function(k){
    var seg=esc(k)+"="+esc(fmtVal(k));
    return isChanged(k)? '<span class="hl">'+seg+'</span>' : seg;
  });
  return esc("[/Script/Pal.PalGameWorldSettings]")+"\n"+'<span class="k">OptionSettings</span>=('+parts.join(",")+")";
}

// ---- import ----
function parseIni(text){
  var i=text.indexOf("OptionSettings");
  var open = i>=0 ? text.indexOf("(", i) : -1;
  var inner;
  if(open>=0){
    var depth=0,end=-1;
    for(var p=open;p<text.length;p++){ var c=text[p]; if(c==="(")depth++; else if(c===")"){depth--; if(depth===0){end=p;break;}} }
    if(end<0) throw new Error("Thiếu dấu ) đóng của OptionSettings.");
    inner=text.slice(open+1,end);
  } else {
    inner=text.trim(); // maybe they pasted just the inner list
  }
  // split by comma at depth 0
  var out={},depth=0,cur="";
  for(var q=0;q<inner.length;q++){
    var ch=inner[q];
    if(ch==="(")depth++; if(ch===")")depth--;
    if(ch===","&&depth===0){ pushKV(out,cur); cur=""; } else cur+=ch;
  }
  if(cur.trim()) pushKV(out,cur);
  if(Object.keys(out).length===0) throw new Error("Không tìm thấy tham số nào. Kiểm tra lại nội dung đã dán.");
  return out;
}
function pushKV(out,seg){
  var e=seg.indexOf("="); if(e<0) return;
  var key=seg.slice(0,e).trim(), val=seg.slice(e+1).trim();
  if(key) out[key]=val;
}
function applyImport(raw){
  var applied=0;
  Object.keys(raw).forEach(function(k){
    if(!META[k]) return; var m=META[k], v=raw[k];
    try{
      if(m.t==="b") state[k]=/^true$/i.test(v);
      else if(m.t==="i"||m.t==="f"){
        var n=(m.t==="i")?parseInt(v,10):parseFloat(v);
        if(isNaN(n)) return;            // giá trị rác -> giữ nguyên, đừng nạp NaN vào state
        state[k]=n;
      }
      else if(m.t==="pf"){
        var s=v.replace(/^\(|\)$/g,"");
        state[k]= s.length? s.split(",").map(function(x){return x.trim();}).filter(Boolean) : [];
      }
      else state[k]=stripQ(v);          // e / s / p / raw
      applied++;
    }catch(e){}
  });
  return applied;
}
function stripQ(v){ v=v.trim(); if(v.length>=2&&v[0]==='"'&&v[v.length-1]==='"') return v.slice(1,-1); return v; }

// ---- persistence ----
function save(){ try{ localStorage.setItem("palcfg", JSON.stringify(state)); }catch(e){} }
function load(){ try{ var s=localStorage.getItem("palcfg"); if(s){ var o=JSON.parse(s);
  Object.keys(o).forEach(function(k){ if(META[k]!=null) state[k]=o[k]; }); } }catch(e){} }

// ---- misc ui ----
var toastEl=document.getElementById("toast"),toastT;
function toast(msg){ toastEl.textContent=msg; toastEl.classList.add("show");
  clearTimeout(toastT); toastT=setTimeout(function(){toastEl.classList.remove("show");},1900); }
function copyText(t,msg){
  if(navigator.clipboard&&navigator.clipboard.writeText){ navigator.clipboard.writeText(t).then(function(){toast(msg||"Đã sao chép");},fallback); }
  else fallback();
  function fallback(){ var ta=document.createElement("textarea"); ta.value=t; document.body.appendChild(ta); ta.select();
    try{document.execCommand("copy");toast(msg||"Đã sao chép");}catch(e){toast("Không sao chép được");} document.body.removeChild(ta); }
}
function download(){
  var blob=new Blob([buildFile()],{type:"text/plain"}), url=URL.createObjectURL(blob);
  var a=document.createElement("a"); a.href=url; a.download="PalWorldSettings.ini"; document.body.appendChild(a); a.click();
  document.body.removeChild(a); URL.revokeObjectURL(url); toast("Đã tải PalWorldSettings.ini");
}

// scrollspy
function initSpy(){
  var links=Array.prototype.slice.call(navlist.querySelectorAll("a"));
  var obs=new IntersectionObserver(function(ents){
    ents.forEach(function(en){ if(en.isIntersecting){
      var id=en.target.id.replace("g-","");
      links.forEach(function(l){ l.classList.toggle("on", l.dataset.g===id); });
    }});
  },{rootMargin:"-40% 0px -55% 0px"});
  GROUPS.forEach(function(g){ var el=document.getElementById("g-"+g.id); if(el)obs.observe(el); });
}

// search
document.getElementById("searchBox").addEventListener("input",function(e){
  var q=e.target.value.trim().toLowerCase();
  GROUPS.forEach(function(g){
    var sec=document.getElementById("g-"+g.id), any=false;
    sec.querySelectorAll(".row").forEach(function(r){
      var hit = !q || r.dataset.search.indexOf(q)>=0; r.classList.toggle("hidden",!hit); if(hit)any=true;
    });
    sec.style.display = any?"":"none";
  });
});

// modal helpers
function openM(id){ document.getElementById(id).classList.add("show"); }
function closeM(el){ el.classList.remove("show"); }
document.querySelectorAll("[data-close]").forEach(function(b){
  b.addEventListener("click",function(){ closeM(b.closest(".overlay")); });
});
document.querySelectorAll(".overlay").forEach(function(o){
  o.addEventListener("click",function(e){ if(e.target===o) closeM(o); });
});
document.addEventListener("keydown",function(e){ if(e.key==="Escape") document.querySelectorAll(".overlay.show").forEach(closeM); });

// buttons
document.getElementById("btnImport").addEventListener("click",function(){ openM("importOverlay"); document.getElementById("importErr").style.display="none"; document.getElementById("importText").focus(); });
document.getElementById("btnDoImport").addEventListener("click",function(){
  var t=document.getElementById("importText").value; var err=document.getElementById("importErr");
  try{ var raw=parseIni(t); var n=applyImport(raw); syncAll(); closeM(document.getElementById("importOverlay"));
    document.getElementById("importText").value=""; toast("Đã nạp "+n+" tham số từ file");
  }catch(ex){ err.textContent=ex.message; err.style.display="block"; }
});
document.getElementById("btnReset").addEventListener("click",function(){
  if(confirm("Đặt lại TẤT CẢ tham số về mặc định game?")){ state=clone(DEF); syncAll(); toast("Đã đặt lại mặc định"); }
});
function openExport(){
  document.getElementById("codeOut").innerHTML=buildHighlighted(); openM("exportOverlay");
}
document.getElementById("btnExport").addEventListener("click",openExport);
document.getElementById("btnPreview").addEventListener("click",openExport);
document.getElementById("btnCopyModal").addEventListener("click",function(){ copyText(buildFile(),"Đã sao chép cấu hình"); });
document.getElementById("btnCopyQuick").addEventListener("click",function(){ copyText(buildFile(),"Đã sao chép cấu hình"); });
document.getElementById("btnDownload").addEventListener("click",download);
document.getElementById("btnDownloadModal").addEventListener("click",download);

// ---- server-backed load/save ----
var originalText = null;
var btnLoadServer = document.getElementById("btnLoadServer");
var btnSaveServer = document.getElementById("btnSaveServer");
var openName = document.getElementById("openName");

function mergeIntoOriginal(orig){
  var line=buildLine();
  if(!orig || !orig.trim()) return buildFile();
  var idx=orig.indexOf("OptionSettings");
  if(idx<0){
    var m=orig.match(/\[\/Script\/Pal\.PalGameWorldSettings\][^\n]*\n?/);
    if(m) return orig.replace(m[0], m[0].replace(/\n?$/,"\n")+line+"\n");
    return orig.replace(/\s*$/,"")+"\n"+line+"\n";
  }
  var open=orig.indexOf("(", idx);
  if(open<0) return orig.slice(0,idx)+line;
  var depth=0,end=-1;
  for(var p=open;p<orig.length;p++){ var c=orig[p]; if(c==="(")depth++; else if(c===")"){depth--; if(depth===0){end=p;break;}} }
  if(end<0) return orig.slice(0,idx)+line;
  return orig.slice(0,idx)+line+orig.slice(end+1);
}

btnLoadServer && btnLoadServer.addEventListener("click", function(){
  (async function(){
    try{
      var r = await fetch("/api/config");
      if(r.status===401){ location.href="/login"; return; }
      var j = await r.json();
      if(!j.ok){ toast("Không nạp được: "+(j.error||"lỗi")); return; }
      originalText = j.data.text;
      var raw = parseIni(originalText);
      var n = applyImport(raw); syncAll();
      btnSaveServer.hidden=false; openName.hidden=false; openName.textContent="PalWorldSettings.ini (server)";
      toast("Đã nạp "+n+" tham số từ server");
    }catch(ex){ toast("Lỗi kết nối: "+(ex && ex.message ? ex.message : ex)); }
  })();
});

btnSaveServer && btnSaveServer.addEventListener("click", function(){
  (async function(){
    try{
      var text = mergeIntoOriginal(originalText);
      var r = await fetch("/api/config", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({text: text})});
      if(r.status===401){ location.href="/login"; return; }
      var j = await r.json();
      if(!j.ok){ toast("Lưu thất bại: "+(j.error||"lỗi")); return; }
      originalText = text;
      toast("✓ Đã lưu vào server — cần restart container palworld-server để áp dụng");
    }catch(ex){ toast("Lưu thất bại: "+(ex && ex.message ? ex.message : ex)); }
  })();
});

// init
buildNav(); buildPresets(); load(); buildGroups();
Object.keys(META).forEach(syncRow); syncSummary(); initSpy();
})();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------- main
def main():
    if not PASSWORD:
        print("‼  Chưa đặt PAL_ADMIN_PASSWORD (AdminPassword của server).")
        print('   PowerShell: $env:PAL_ADMIN_PASSWORD="mk-admin"; python admin-tool.py')
        sys.exit(1)
    if BIND not in ("127.0.0.1", "localhost") and not APP_PW:
        print("‼  Bind ra %s nhưng CHƯA đặt PAL_APP_PASSWORD — từ chối chạy để không lộ panel." % BIND)
        print('   Đặt mật khẩu đăng nhập app: $env:PAL_APP_PASSWORD="mk-dang-nhap"')
        sys.exit(1)

    srv = ThreadingHTTPServer((BIND, WEB_PORT), Handler)
    scheme = "https" if USE_TLS else "http"
    if USE_TLS:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(TLS_CERT, TLS_KEY)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)

    print("Palworld Admin Tool (PWA)")
    print("  Server đích  : %s (REST %d / RCON %d)" % (HOST, REST_PORT, RCON_PORT))
    print("  Lắng nghe    : %s://%s:%d" % (scheme, BIND, WEB_PORT))
    if BIND in ("127.0.0.1", "localhost"):
        print("  Truy cập     : %s://localhost:%d" % (scheme, WEB_PORT))
    else:
        print("  Truy cập     : %s://<IP-LAN-cua-may-nay>:%d  (điện thoại vào VPN rồi mở)" % (scheme, WEB_PORT))
    print("  Đăng nhập    : %s" % ("BẬT (PAL_APP_PASSWORD)" if ENFORCE_AUTH else "TẮT (chỉ localhost)"))
    print("  TLS/HTTPS    : %s" % ("BẬT" if USE_TLS else "TẮT — nên bật khi cài PWA (xem PAL_TLS_*)"))
    print("  Ctrl+C để dừng.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng.")

if __name__ == "__main__":
    main()

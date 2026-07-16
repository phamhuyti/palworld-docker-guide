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
import os, sys, json, base64, socket, struct, time, hmac, secrets, threading, zlib, ssl
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
REST_BASE = "http://%s:%d/v1/api" % (HOST, REST_PORT)
USE_TLS   = bool(TLS_CERT and TLS_KEY)

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
    with _LOCK:
        SESSIONS[tok] = time.time() + SESSION_SECONDS
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
    token = base64.b64encode(("admin:%s" % PASSWORD).encode("utf-8")).decode("ascii")
    req.add_header("Authorization", "Basic " + token)
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
    rest("POST", "/shutdown", {"waittime": 1, "message": "Server %s" % verb})
    _countdown_done()

# ---------------------------------------------------------------- routes
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
    if method == "POST":
        p = payload or {}
        if path == "/api/announce":
            return _wrap(*rest("POST", "/announce", {"message": p.get("message", "")}))
        if path == "/api/save":
            return _wrap(*rest("POST", "/save"))
        if path == "/api/shutdown":
            rest("POST", "/save")   # lưu trước cho chắc (server cũng autosave khi tắt êm)
            return _wrap(*rest("POST", "/shutdown",
                   {"waittime": int(p.get("waittime", 30)), "message": p.get("message", "")}))
        if path == "/api/stop":
            rest("POST", "/save")   # Stop tắt gấp -> bắt buộc lưu trước
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
            return _wrap(*rcon(p.get("command", "")))
        if path == "/api/countdown":
            ok, msg = start_countdown(p.get("minutes", 5), p.get("mode", "restart"), p.get("message", ""))
            return 200, json.dumps({"ok": ok, "status": 200 if ok else 409, "data": None, "text": msg})
        if path == "/api/countdown_cancel":
            ok, msg = cancel_countdown()
            return 200, json.dumps({"ok": ok, "status": 200 if ok else 409, "data": None, "text": msg})
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
const C='pal-admin-v3';
self.addEventListener('install',e=>{self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(clients.claim());});
self.addEventListener('fetch',e=>{
  const u=new URL(e.request.url);
  if(e.request.method!=='GET'||u.pathname.startsWith('/api/')||u.pathname==='/login'||u.pathname==='/logout') return;
  e.respondWith(caches.open(C).then(c=>c.match(e.request).then(r=>r||fetch(e.request).then(resp=>{
    if(resp && resp.ok) c.put(e.request, resp.clone()); return resp;
  }).catch(()=>c.match('/')))));
});
"""

# ---------------------------------------------------------------- HTTP server
class Handler(BaseHTTPRequestHandler):
    server_version = "PalAdmin"
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

    def _send(self, code, body, ctype="application/json; charset=utf-8", cookie=None):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
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

    def do_GET(self):
        path = self.path.split("?")[0]
        # tài nguyên công khai (cần cho cả trang đăng nhập + cài PWA)
        if path == "/manifest.webmanifest":
            return self._send(200, MANIFEST, "application/manifest+json; charset=utf-8")
        if path == "/sw.js":
            return self._send(200, SW_JS, "application/javascript; charset=utf-8")
        if path == "/icon-192.png":
            return self._send(200, make_icon(192), "image/png")
        if path == "/icon-512.png":
            return self._send(200, make_icon(512), "image/png")
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
        if path.startswith("/api/"):
            code, body = route("GET", path, None)
            return self._send(code, body)
        return self._send(404, json.dumps({"ok": False, "error": "not found"}))

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
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
                return self._send(200, json.dumps({"ok": True}), cookie=cookie)
            if wait:
                return self._send(429, json.dumps({"ok": False, "error": "locked", "wait": wait}))
            return self._send(401, json.dumps({"ok": False, "error": "wrong"}))

        if not self._authed():
            return self._send(401, json.dumps({"ok": False, "error": "auth"}))
        if path.startswith("/api/"):
            code, body = route("POST", path, payload)
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
    <div class="row"><div><input type="text" id="rconCmd" placeholder="Info / ShowPlayers / Broadcast xin_chao"></div>
      <div style="flex:0 0 auto"><button class="btn" id="btnRcon">Chạy</button></div></div>
    <p class="hint">Broadcast qua RCON không nhận dấu cách/unicode tốt.</p>
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
function now(){var d=new Date();return pad(d.getHours())+":"+pad(d.getMinutes())+":"+pad(d.getSeconds());}
function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}
function logLine(msg,cls){var box=$("#log");if(box.querySelector(".empty"))box.innerHTML="";
  var d=document.createElement("div");d.className="l";
  d.innerHTML='<span class="t">['+now()+']</span> <span class="'+(cls||"")+'">'+msg+'</span>';
  box.appendChild(d);box.scrollTop=box.scrollHeight;}
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
    var info=await call("GET","/api/info");
    if(info.ok&&info.data){
      $("#srvName").textContent=info.data.servername||info.data.name||"Palworld Server";
      $("#srvSub").textContent=(info.data.version?("v"+info.data.version+" · "):"")+"REST API";
      setConn(true);
    }else{ setConn(false, info.status===401?"sai AdminPassword":(info.status===0?"không kết nối được (server tắt/REST chưa bật?)":("HTTP "+info.status))); return; }
    var m=await call("GET","/api/metrics");
    if(m.ok&&m.data){
      $("#mPlayers").textContent=(m.data.currentplayernum!=null?m.data.currentplayernum:"–")+" / "+(m.data.maxplayernum!=null?m.data.maxplayernum:"–");
      $("#mFps").textContent=m.data.serverfps!=null?m.data.serverfps:"–";
      $("#mUptime").textContent=fmtUptime(m.data.uptime);
    }
  }catch(e){}
}
async function refreshPlayers(){
  var wrap=$("#playersWrap");
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
  if(r.ok){logLine("✓ "+esc(action)+" — OK"+(r.text?(" · "+esc(r.text)):""),"ok");toast("✓ "+action);}
  else{logLine("✗ "+esc(action)+" — HTTP "+r.status+" "+esc(r.text||""),"err");toast("Lỗi: "+action);}
}
$("#btnAnnounce").onclick=async function(){var m=$("#annMsg").value.trim();if(!m){toast("Nhập nội dung");return;}
  report("announce",await call("POST","/api/announce",{message:m}));$("#annMsg").value="";};
$("#btnSave").onclick=async function(){report("save",await call("POST","/api/save",{}));};
$("#btnUnban").onclick=async function(){var id=$("#unbanId").value.trim();if(!id){toast("Nhập User ID");return;}
  report("unban "+id,await call("POST","/api/unban",{userid:id}));$("#unbanId").value="";};
$("#btnRcon").onclick=async function(){var c=$("#rconCmd").value.trim();if(!c){toast("Nhập lệnh");return;}
  var r=await call("POST","/api/rcon",{command:c});
  if(r.ok)logLine("» "+esc(c)+"\n"+esc(r.text||r.data||""),"ok");else logLine("» "+esc(c)+" ✗ "+esc(r.text||""),"err");
  toast(r.ok?"RCON OK":"RCON lỗi");};
$("#rconCmd").addEventListener("keydown",function(e){if(e.key==="Enter")$("#btnRcon").click();});
$("#btnShutdown").onclick=function(){arm(this,async function(){
  report("shutdown",await call("POST","/api/shutdown",{waittime:parseInt($("#sdWait").value||"30",10),message:$("#sdMsg").value||"Server se tat"}));});};
$("#btnStop").onclick=function(){arm(this,async function(){report("stop",await call("POST","/api/stop",{}));});};
$("#btnRefresh").onclick=refreshPlayers;
$("#btnClear").onclick=function(){$("#log").innerHTML='<div class="empty">Chưa có hoạt động.</div>';};
$("#btnLogout").onclick=function(){location.href="/logout";};
if(__ENFORCE__)$("#btnLogout").hidden=false;
function fmtRemain(s){s=parseInt(s||0,10);if(s>=60){var m=Math.floor(s/60),ss=s%60;return m+" phút"+(ss?(" "+ss+"s"):"");}return s+" giây";}
async function pollCountdown(){
  try{var r=await call("GET","/api/countdown_status");var s=(r&&r.data)||{};var b=$("#cdBanner");
    if(s.active){b.hidden=false;$("#cdRemain").textContent=fmtRemain(s.remaining)+" · "+(s.mode==="restart"?"khởi động lại":"tắt");}
    else b.hidden=true;
  }catch(e){}
}
$("#btnCountdown").onclick=function(){arm(this,async function(){
  var r=await call("POST","/api/countdown",{minutes:parseInt($("#cdMin").value||"5",10),mode:$("#cdMode").value,message:$("#cdMsg").value.trim()});
  report("đếm ngược "+($("#cdMode").value==="restart"?"restart":"shutdown"),r);pollCountdown();
});};
$("#btnCdCancel").onclick=async function(){report("hủy đếm ngược",await call("POST","/api/countdown_cancel",{}));pollCountdown();};
refreshStatus();refreshPlayers();pollCountdown();
setInterval(refreshStatus,6000);setInterval(refreshPlayers,10000);setInterval(pollCountdown,2000);
</script></body></html>"""
PAGE = PAGE.replace("__ENFORCE__", "true" if ENFORCE_AUTH else "false")

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

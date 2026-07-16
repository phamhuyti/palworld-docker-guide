#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
admin-tool.py — Bảng điều khiển quản trị Palworld Dedicated Server.

Chạy backend nhỏ bằng Python (chỉ dùng thư viện chuẩn), phục vụ giao diện web
trên localhost và làm cầu nối tới REST API + RCON của server. Nhờ đó tránh được
giới hạn CORS/mixed-content của trình duyệt, và mật khẩu admin KHÔNG bị lộ ra
trang web (chỉ nằm trong tiến trình Python này).

Cách chạy (Windows PowerShell):
    $env:PAL_HOST="192.168.1.160"; $env:PAL_ADMIN_PASSWORD="mat-khau-admin"; python admin-tool.py
Cách chạy (Linux/macOS/Git-Bash):
    PAL_HOST=192.168.1.160 PAL_ADMIN_PASSWORD=mat-khau-admin python3 admin-tool.py

Sau đó mở trình duyệt tới:  http://localhost:8080

Yêu cầu trên server: RESTAPIEnabled=True (và RCONEnabled=True nếu dùng RCON console),
AdminPassword đã đặt, port 8212/25575 mở cho máy chạy tool (LAN). Xem HUONG-DAN-QUAN-TRI.md.

Biến môi trường:
    PAL_HOST            IP/hostname server        (mặc định 127.0.0.1)
    PAL_ADMIN_PASSWORD  = AdminPassword           (BẮT BUỘC)
    PAL_REST_PORT       cổng REST API             (mặc định 8212)
    PAL_RCON_PORT       cổng RCON                 (mặc định 25575)
    PAL_WEB_PORT        cổng giao diện web cục bộ (mặc định 8080)
"""
import os, sys, json, base64, socket, struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlreq, error as urlerr

# Windows console mặc định cp1252 không in được tiếng Việt -> ép UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HOST      = os.environ.get("PAL_HOST", "127.0.0.1")
REST_PORT = int(os.environ.get("PAL_REST_PORT", "8212"))
RCON_PORT = int(os.environ.get("PAL_RCON_PORT", "25575"))
WEB_PORT  = int(os.environ.get("PAL_WEB_PORT", "8080"))
PASSWORD  = os.environ.get("PAL_ADMIN_PASSWORD", "")
REST_BASE = "http://%s:%d/v1/api" % (HOST, REST_PORT)

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
            raw = r.read().decode("utf-8", "replace")
            return r.status, raw
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
    body = data[8:-2].decode("utf-8", "replace")
    return pid, ptype, body

def rcon(command):
    if not command.strip():
        return 400, "Lệnh rỗng"
    try:
        with socket.create_connection((HOST, RCON_PORT), timeout=6) as s:
            s.sendall(_rcon_packet(1, 3, PASSWORD))          # SERVERDATA_AUTH
            pid, _, _ = _rcon_read(s)
            if pid == -1:
                return 401, "RCON auth thất bại — sai AdminPassword."
            s.sendall(_rcon_packet(2, 2, command))           # EXECCOMMAND
            _, _, body = _rcon_read(s)
            return 200, (body.strip() or "(OK — server không trả nội dung)")
    except Exception as e:
        return 0, "RCON lỗi: %s" % e

# ---------------------------------------------------------------- routes
def route(method, path, payload):
    # REST GET
    if method == "GET":
        if path == "/api/info":     return _wrap(*rest("GET", "/info"))
        if path == "/api/players":  return _wrap(*rest("GET", "/players"))
        if path == "/api/metrics":  return _wrap(*rest("GET", "/metrics"))
        if path == "/api/settings": return _wrap(*rest("GET", "/settings"))
    if method == "POST":
        p = payload or {}
        if path == "/api/announce":
            return _wrap(*rest("POST", "/announce", {"message": p.get("message", "")}))
        if path == "/api/save":
            return _wrap(*rest("POST", "/save"))
        if path == "/api/shutdown":
            return _wrap(*rest("POST", "/shutdown",
                   {"waittime": int(p.get("waittime", 30)), "message": p.get("message", "")}))
        if path == "/api/stop":
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
    return 404, json.dumps({"ok": False, "error": "route không tồn tại"})

def _wrap(status, raw):
    # normalize upstream response into {ok, status, data|text}
    ok = 200 <= status < 300
    try:
        parsed = json.loads(raw) if raw.strip() else None
        return 200, json.dumps({"ok": ok, "status": status, "data": parsed, "text": None if parsed is not None else raw})
    except Exception:
        return 200, json.dumps({"ok": ok, "status": status, "data": None, "text": raw})

# ---------------------------------------------------------------- HTTP server
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # bớt spam log
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path.startswith("/api/"):
            code, body = route("GET", self.path.split("?")[0], None)
            return self._send(code, body)
        return self._send(404, json.dumps({"ok": False, "error": "not found"}))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {}
        code, body = route("POST", self.path.split("?")[0], payload)
        return self._send(code, body)

# ---------------------------------------------------------------- frontend
PAGE = r"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Palworld Admin</title>
<style>
:root{
  --bg:#f4f6f3;--surface:#fff;--surface-2:#eef1ec;--surface-3:#e7ebe4;
  --text:#1a2129;--muted:#5c6a72;--faint:#8a978f;--border:#dde3db;
  --accent:#1f9c78;--accent-weak:#e2f1eb;--accent-ink:#0e5c46;
  --danger:#c0533b;--danger-weak:#f6e2dc;--warn:#b7770c;--warn-weak:#f6ecd6;
  --ok:#1f9c78;--mono:ui-monospace,"SFMono-Regular",Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#12171a;--surface:#1a2126;--surface-2:#212a30;--surface-3:#28333a;
  --text:#e8ece8;--muted:#9aa8a2;--faint:#6f7d77;--border:#2b353b;
  --accent:#3fca9e;--accent-weak:#123028;--accent-ink:#8fe6cb;
  --danger:#e07a60;--danger-weak:#3a201a;--warn:#e0a53a;--warn-weak:#332a15;--ok:#3fca9e;
}}
*{box-sizing:border-box}[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);font-size:15px;line-height:1.5}
button{font-family:inherit;cursor:pointer}
input,textarea{font-family:inherit}
.top{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--surface) 90%,transparent);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--border)}
.top-in{max-width:1080px;margin:0 auto;padding:12px 20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.glyph{width:34px;height:34px;border-radius:9px;background:linear-gradient(150deg,var(--accent),var(--accent-ink));
  display:grid;place-items:center;color:#fff;font-weight:700}
.brand h1{margin:0;font-size:15px;letter-spacing:-.01em}
.brand p{margin:0;font-size:11.5px;color:var(--muted)}
.stat{margin-left:auto;display:flex;gap:18px;flex-wrap:wrap;align-items:center}
.stat .kv{display:flex;flex-direction:column;line-height:1.15}
.stat .kv b{font-size:15px;font-variant-numeric:tabular-nums}
.stat .kv span{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.dot{width:9px;height:9px;border-radius:50%;background:var(--faint);display:inline-block}
.dot.on{background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 25%,transparent)}
.dot.off{background:var(--danger);box-shadow:0 0 0 3px color-mix(in srgb,var(--danger) 25%,transparent)}
.conn{display:flex;align-items:center;gap:8px;font-size:12.5px;font-weight:600}
main{max-width:1080px;margin:0 auto;padding:20px;display:grid;grid-template-columns:1fr 1fr;gap:16px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:15px 16px;
  box-shadow:0 1px 2px rgba(20,30,25,.04)}
.card.wide{grid-column:1/-1}
.card h2{margin:0 0 12px;font-size:13.5px;letter-spacing:.02em;text-transform:uppercase;color:var(--muted);
  display:flex;align-items:center;gap:8px}
.card h2 .c{margin-left:auto;font-size:11px;color:var(--faint);text-transform:none;letter-spacing:0}
label{font-size:12px;color:var(--muted);font-weight:600;display:block;margin:0 0 5px}
input[type=text],input[type=number],textarea{width:100%;padding:9px 11px;border:1px solid var(--border);
  background:var(--surface-2);color:var(--text);border-radius:8px;font-size:13.5px}
textarea{resize:vertical;min-height:52px}
input:focus,textarea:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
.row{display:flex;gap:9px;flex-wrap:wrap;align-items:flex-end}
.row>div{flex:1;min-width:90px}
.btn{border:1px solid var(--border);background:var(--surface);color:var(--text);padding:9px 14px;
  border-radius:8px;font-size:13px;font-weight:600;display:inline-flex;align-items:center;gap:7px;transition:.13s}
.btn:hover{background:var(--surface-2);border-color:var(--faint)}
.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.primary:hover{filter:brightness(1.06)}
.btn.danger{background:var(--danger-weak);border-color:var(--danger);color:var(--danger)}
.btn.danger.armed{background:var(--danger);color:#fff}
.btn.sm{padding:6px 10px;font-size:12px}
.btn:disabled{opacity:.5;cursor:not-allowed}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 9px;border-bottom:1px solid var(--border)}
th{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:700}
td.mono{font-family:var(--mono);font-size:11.5px;color:var(--muted)}
td.num{font-variant-numeric:tabular-nums;text-align:right}
.empty{color:var(--faint);font-size:13px;padding:14px 4px;text-align:center}
.log{font-family:var(--mono);font-size:12px;line-height:1.55;max-height:220px;overflow:auto;
  background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:10px}
.log .l{padding:2px 0;border-bottom:1px dashed var(--border);white-space:pre-wrap;word-break:break-word}
.log .l:last-child{border-bottom:0}
.log .t{color:var(--faint)}
.log .ok{color:var(--ok)}
.log .err{color:var(--danger)}
.hint{font-size:11.5px;color:var(--faint);margin-top:8px}
.toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%) translateY(16px);background:var(--text);
  color:var(--bg);padding:9px 16px;border-radius:20px;font-size:13px;font-weight:600;opacity:0;
  pointer-events:none;transition:.2s;z-index:9}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
@media (max-width:760px){main{grid-template-columns:1fr}.stat{width:100%;margin-left:0}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body>
<div class="top"><div class="top-in">
  <div class="glyph">P</div>
  <div class="brand"><h1 id="srvName">Palworld Admin</h1><p id="srvSub">đang kết nối…</p></div>
  <div class="stat">
    <span class="conn"><span class="dot" id="dot"></span><span id="connText">…</span></span>
    <div class="kv"><b id="mPlayers">–</b><span>người chơi</span></div>
    <div class="kv"><b id="mFps">–</b><span>fps</span></div>
    <div class="kv"><b id="mUptime">–</b><span>uptime</span></div>
  </div>
</div></div>

<main>
  <section class="card wide">
    <h2>Người chơi đang online <span class="c" id="pcount"></span>
      <button class="btn sm" id="btnRefresh" style="margin-left:auto">Làm mới</button></h2>
    <div id="playersWrap"><div class="empty">Chưa có dữ liệu.</div></div>
  </section>

  <section class="card">
    <h2>Thông báo toàn server</h2>
    <label for="annMsg">Nội dung</label>
    <textarea id="annMsg" placeholder="Ví dụ: Server se restart sau 10 phut, hay tim cho an toan!"></textarea>
    <div class="row" style="margin-top:9px"><button class="btn primary" id="btnAnnounce">Gửi thông báo</button></div>
    <p class="hint">Dùng REST API — hỗ trợ tiếng Việt/dấu cách tốt hơn Broadcast qua RCON.</p>
  </section>

  <section class="card">
    <h2>Thế giới</h2>
    <div class="row"><div style="flex:0 0 auto"><button class="btn" id="btnSave">Lưu game (Save)</button></div></div>
    <label for="sdWait" style="margin-top:12px">Tắt server êm sau (giây)</label>
    <div class="row">
      <div style="flex:0 0 110px"><input type="number" id="sdWait" value="30" min="0" max="3600"></div>
      <div><input type="text" id="sdMsg" placeholder="Lời nhắn khi tắt"></div>
    </div>
    <div class="row" style="margin-top:9px">
      <button class="btn danger" id="btnShutdown" data-confirm>Shutdown</button>
      <button class="btn danger" id="btnStop" data-confirm>Stop (tắt ngay)</button>
    </div>
    <p class="hint">Nút đỏ bấm 2 lần để xác nhận. Shutdown có đếm giờ + báo người chơi; Stop tắt ngay.</p>
  </section>

  <section class="card">
    <h2>Gỡ ban</h2>
    <label for="unbanId">User ID (steam_… / platform id)</label>
    <div class="row"><div><input type="text" id="unbanId" placeholder="steam_0123456789"></div>
      <div style="flex:0 0 auto"><button class="btn" id="btnUnban">Gỡ ban</button></div></div>
    <p class="hint">Kick/Ban thao tác trực tiếp ở bảng người chơi phía trên.</p>
  </section>

  <section class="card">
    <h2>RCON console</h2>
    <div class="row"><div><input type="text" id="rconCmd" placeholder="Info / ShowPlayers / Broadcast xin_chao"></div>
      <div style="flex:0 0 auto"><button class="btn" id="btnRcon">Chạy</button></div></div>
    <p class="hint">Cần RCONEnabled=True. Lưu ý Broadcast qua RCON không nhận dấu cách/unicode tốt.</p>
  </section>

  <section class="card wide">
    <h2>Nhật ký hoạt động <button class="btn sm" id="btnClear" style="margin-left:auto">Xóa log</button></h2>
    <div class="log" id="log"><div class="empty">Chưa có hoạt động.</div></div>
  </section>
</main>
<div class="toast" id="toast"></div>

<script>
var $=function(s){return document.querySelector(s)};
function toast(m){var t=$("#toast");t.textContent=m;t.classList.add("show");
  clearTimeout(t._t);t._t=setTimeout(function(){t.classList.remove("show")},1800);}
function pad(n){return n<10?"0"+n:""+n}
function now(){var d=new Date();return pad(d.getHours())+":"+pad(d.getMinutes())+":"+pad(d.getSeconds());}
function logLine(msg,cls){
  var box=$("#log");if(box.querySelector(".empty"))box.innerHTML="";
  var d=document.createElement("div");d.className="l";
  d.innerHTML='<span class="t">['+now()+']</span> <span class="'+(cls||"")+'">'+msg+'</span>';
  box.appendChild(d);box.scrollTop=box.scrollHeight;
}
function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}

async function call(method,path,body){
  var opt={method:method,headers:{}};
  if(body){opt.headers["Content-Type"]="application/json";opt.body=JSON.stringify(body);}
  var r=await fetch(path,opt);
  return await r.json();
}
function fmtUptime(sec){
  sec=parseInt(sec||0,10);if(!sec||sec<0)return "–";
  var h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60);
  return (h>0?h+"h":"")+m+"m";
}

async function refreshStatus(){
  try{
    var info=await call("GET","/api/info");
    if(info.ok && info.data){
      $("#srvName").textContent=info.data.servername||info.data.name||"Palworld Server";
      $("#srvSub").textContent=(info.data.version?("v"+info.data.version+" · "):"")+"REST API";
      setConn(true);
    }else{ setConn(false, info.status===401?"sai AdminPassword":(info.status===0?"không kết nối được (server tắt hoặc REST chưa bật?)":(info.text||("HTTP "+info.status)))); return; }
    var m=await call("GET","/api/metrics");
    if(m.ok&&m.data){
      $("#mPlayers").textContent=(m.data.currentplayernum!=null?m.data.currentplayernum:"–")+" / "+(m.data.maxplayernum!=null?m.data.maxplayernum:"–");
      $("#mFps").textContent=m.data.serverfps!=null?m.data.serverfps:"–";
      $("#mUptime").textContent=fmtUptime(m.data.uptime);
    }
  }catch(e){ setConn(false, e.message); }
}
function setConn(ok,err){
  var dot=$("#dot");
  dot.className="dot "+(ok?"on":"off");
  $("#connText").textContent=ok?"Đã kết nối":"Mất kết nối";
  if(!ok){ $("#srvSub").textContent="không gọi được REST API — "+(err||""); }
}

async function refreshPlayers(){
  var wrap=$("#playersWrap");
  try{
    var r=await call("GET","/api/players");
    if(!r.ok){ wrap.innerHTML='<div class="empty">Không lấy được danh sách (HTTP '+r.status+'). '+esc(r.text||"")+'</div>'; $("#pcount").textContent=""; return; }
    var list=(r.data&&r.data.players)||[];
    $("#pcount").textContent=list.length?("· "+list.length):"";
    if(!list.length){ wrap.innerHTML='<div class="empty">Không có ai online.</div>'; return; }
    var html='<div style="overflow-x:auto"><table><thead><tr><th>Tên</th><th>Level</th><th>Ping</th><th>User ID</th><th style="text-align:right">Thao tác</th></tr></thead><tbody>';
    list.forEach(function(p,i){
      var uid=p.userId||p.userid||p.playerId||"";
      html+='<tr><td>'+esc(p.name||"?")+'</td><td class="num">'+esc(p.level||p.level_num||"–")+
        '</td><td class="num">'+esc(p.ping!=null?Math.round(p.ping):"–")+'</td><td class="mono">'+esc(uid)+
        '</td><td style="text-align:right;white-space:nowrap">'+
        '<button class="btn sm danger" data-kick="'+esc(uid)+'" data-name="'+esc(p.name||"")+'">Kick</button> '+
        '<button class="btn sm danger" data-ban="'+esc(uid)+'" data-name="'+esc(p.name||"")+'">Ban</button></td></tr>';
    });
    html+='</tbody></table></div>';
    wrap.innerHTML=html;
    wireRowButtons();
  }catch(e){ wrap.innerHTML='<div class="empty">Lỗi: '+esc(e.message)+'</div>'; }
}

// two-click confirm for destructive buttons
function armConfirm(btn, action){
  if(btn._armed){ clearTimeout(btn._t); btn._armed=false; btn.classList.remove("armed"); btn.textContent=btn._label; action(); return; }
  btn._label=btn.textContent; btn._armed=true; btn.classList.add("armed"); btn.textContent="Chắc chắn?";
  btn._t=setTimeout(function(){btn._armed=false;btn.classList.remove("armed");btn.textContent=btn._label;},3000);
}
function wireRowButtons(){
  document.querySelectorAll("[data-kick]").forEach(function(b){
    b.onclick=function(){armConfirm(b,function(){doModerate("/api/kick",b.dataset.kick,b.dataset.name,"kick");});};
  });
  document.querySelectorAll("[data-ban]").forEach(function(b){
    b.onclick=function(){armConfirm(b,function(){doModerate("/api/ban",b.dataset.ban,b.dataset.name,"ban");});};
  });
}
async function doModerate(path,uid,name,verb){
  if(!uid){toast("Thiếu User ID");return;}
  var r=await call("POST",path,{userid:uid,message:"Ban da bi "+verb});
  report(verb+" "+(name||uid), r);
  refreshPlayers();
}

function report(action, r){
  if(r.ok){ logLine("✓ "+esc(action)+" — OK"+(r.text?(" · "+esc(r.text)):""), "ok"); toast("✓ "+action); }
  else { logLine("✗ "+esc(action)+" — HTTP "+r.status+" "+esc(r.text||""), "err"); toast("Lỗi: "+action); }
}

// actions
$("#btnAnnounce").onclick=async function(){
  var msg=$("#annMsg").value.trim(); if(!msg){toast("Nhập nội dung");return;}
  report("announce", await call("POST","/api/announce",{message:msg})); $("#annMsg").value="";
};
$("#btnSave").onclick=async function(){ report("save", await call("POST","/api/save",{})); };
$("#btnUnban").onclick=async function(){
  var id=$("#unbanId").value.trim(); if(!id){toast("Nhập User ID");return;}
  report("unban "+id, await call("POST","/api/unban",{userid:id})); $("#unbanId").value="";
};
$("#btnRcon").onclick=async function(){
  var cmd=$("#rconCmd").value.trim(); if(!cmd){toast("Nhập lệnh");return;}
  var r=await call("POST","/api/rcon",{command:cmd});
  if(r.ok) logLine("» "+esc(cmd)+"\n"+esc(r.text||r.data||""), "ok"); else logLine("» "+esc(cmd)+" ✗ "+esc(r.text||""), "err");
  toast(r.ok?"RCON OK":"RCON lỗi");
};
$("#rconCmd").addEventListener("keydown",function(e){if(e.key==="Enter")$("#btnRcon").click();});
$("#btnShutdown").onclick=function(){ armConfirm(this,async function(){
  report("shutdown", await call("POST","/api/shutdown",{waittime:parseInt($("#sdWait").value||"30",10),message:$("#sdMsg").value||"Server se tat"}));
});};
$("#btnStop").onclick=function(){ armConfirm(this,async function(){
  report("stop", await call("POST","/api/stop",{}));
});};
$("#btnRefresh").onclick=refreshPlayers;
$("#btnClear").onclick=function(){$("#log").innerHTML='<div class="empty">Chưa có hoạt động.</div>';};

refreshStatus(); refreshPlayers();
setInterval(refreshStatus,6000);
setInterval(refreshPlayers,10000);
</script></body></html>"""

# ---------------------------------------------------------------- main
def main():
    if not PASSWORD:
        print("‼  Chưa đặt PAL_ADMIN_PASSWORD.")
        print("   Windows PowerShell:")
        print('     $env:PAL_HOST="192.168.1.160"; $env:PAL_ADMIN_PASSWORD="mat-khau"; python admin-tool.py')
        print("   Linux/macOS/Git-Bash:")
        print("     PAL_HOST=192.168.1.160 PAL_ADMIN_PASSWORD=mat-khau python3 admin-tool.py")
        sys.exit(1)
    srv = ThreadingHTTPServer(("127.0.0.1", WEB_PORT), Handler)
    print("Palworld Admin Tool")
    print("  Server đích : %s (REST %d / RCON %d)" % (HOST, REST_PORT, RCON_PORT))
    print("  Mở trình duyệt: http://localhost:%d" % WEB_PORT)
    print("  Nhấn Ctrl+C để dừng.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng.")

if __name__ == "__main__":
    main()

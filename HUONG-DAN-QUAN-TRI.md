# Hướng dẫn quản trị Palworld Server bằng RCON & REST API

> Quản trị server từ xa không cần vào game: xem người chơi, thông báo, kick/ban, ép save, restart có báo trước, theo dõi hiệu năng. Đi kèm [HUONG-DAN-CONFIG.md](HUONG-DAN-CONFIG.md) (mục 8.2, 10, 11).

## Mục lục

1. [Bật RCON & REST API](#1-bật-rcon--rest-api)
2. [Quản trị bằng REST API (khuyên dùng)](#2-quản-trị-bằng-rest-api-khuyên-dùng)
3. [Quản trị bằng RCON](#3-quản-trị-bằng-rcon)
4. [Lệnh admin trong game](#4-lệnh-admin-trong-game)
5. [Kịch bản quản trị thường gặp](#5-kịch-bản-quản-trị-thường-gặp)
6. [Bảo mật](#6-bảo-mật)

---

## 1. Bật RCON & REST API

### Bước 1 — Sửa `Saved/Config/LinuxServer/PalWorldSettings.ini`

Tìm và sửa 5 giá trị sau trong dòng `OptionSettings=(...)`:

```
AdminPassword="mat-khau-manh-cua-ban"
RCONEnabled=True
RCONPort=25575
RESTAPIEnabled=True
RESTAPIPort=8212
```

> `AdminPassword` là chìa khóa của cả hai tool — **bắt buộc đặt, không được để rỗng**. Chỉ cần REST API thì có thể để `RCONEnabled=False`.

### Bước 2 — Mở port trong `compose.yaml` (chỉ trên localhost)

```yaml
    ports:
      - "192.168.1.160:8211:8211/udp"   # game
      - "127.0.0.1:25575:25575/tcp"     # RCON
      - "127.0.0.1:8212:8212/tcp"       # REST API
```

### Bước 3 — Restart và kiểm tra

```bash
docker compose down && docker compose up -d

# Kiểm tra REST API hoạt động:
curl -u admin:mat-khau-manh-cua-ban http://127.0.0.1:8212/v1/api/info
# → {"version":"v1.0.1.100619","servername":"...","description":"...","worldguid":"..."}
```

## 2. Quản trị bằng REST API (khuyên dùng)

Xác thực **HTTP Basic**: user luôn là `admin`, password là `AdminPassword`. Mọi endpoint nằm dưới `http://127.0.0.1:8212/v1/api/`. Đặt biến cho gọn:

```bash
PAL="curl -su admin:mat-khau-manh-cua-ban"
```

### Bảng endpoint đầy đủ

| Endpoint | Method | Chức năng |
|---|---|---|
| `/v1/api/info` | GET | Thông tin server (version, tên, mô tả, world GUID) |
| `/v1/api/players` | GET | Danh sách người chơi online (tên, playerId, userId, IP, ping, level, tọa độ) |
| `/v1/api/settings` | GET | Toàn bộ cấu hình server đang chạy |
| `/v1/api/metrics` | GET | Hiệu năng: FPS server, frametime, số người online, uptime |
| `/v1/api/announce` | POST | Gửi thông báo toàn server |
| `/v1/api/kick` | POST | Kick người chơi |
| `/v1/api/ban` | POST | Ban người chơi |
| `/v1/api/unban` | POST | Gỡ ban |
| `/v1/api/save` | POST | Ép lưu world ngay lập tức |
| `/v1/api/shutdown` | POST | Tắt server "êm": đếm ngược + thông báo + save |
| `/v1/api/stop` | POST | Tắt server NGAY lập tức (không báo, không đợi — hạn chế dùng) |

### Ví dụ từng thao tác

```bash
# Ai đang online?
$PAL http://127.0.0.1:8212/v1/api/players
# → {"players":[{"name":"HuyPham","playerId":"...","userId":"steam_7656119...",
#     "ip":"192.168.1.5","ping":23.4,"location_x":-121000,"location_y":-82000,"level":35}]}

# Thông báo toàn server
$PAL -X POST http://127.0.0.1:8212/v1/api/announce \
  -H "Content-Type: application/json" \
  -d '{"message":"Su kien x2 EXP bat dau luc 20h toi nay!"}'

# Kick (lấy userId từ /players, dạng steam_xxxx)
$PAL -X POST http://127.0.0.1:8212/v1/api/kick \
  -H "Content-Type: application/json" \
  -d '{"userid":"steam_76561198000000000","message":"Vi pham noi quy: spam chat"}'

# Ban
$PAL -X POST http://127.0.0.1:8212/v1/api/ban \
  -H "Content-Type: application/json" \
  -d '{"userid":"steam_76561198000000000","message":"Pha hoai base nguoi khac"}'

# Gỡ ban
$PAL -X POST http://127.0.0.1:8212/v1/api/unban \
  -H "Content-Type: application/json" \
  -d '{"userid":"steam_76561198000000000"}'

# Ép save (luôn chạy trước khi backup/restart)
$PAL -X POST http://127.0.0.1:8212/v1/api/save

# Tắt server êm: báo trước 60 giây
$PAL -X POST http://127.0.0.1:8212/v1/api/shutdown \
  -H "Content-Type: application/json" \
  -d '{"waittime":60,"message":"Server restart sau 60 giay!"}'

# Theo dõi hiệu năng
$PAL http://127.0.0.1:8212/v1/api/metrics
# → {"serverfps":58,"currentplayernum":5,"serverframetime":16.9,"maxplayernum":32,"uptime":86400}
```

> Server FPS lý tưởng ~60. Nếu `serverfps` tụt xuống dưới ~20 khi đông người → xem mục 9.4 của [HUONG-DAN-CONFIG.md](HUONG-DAN-CONFIG.md) để tối ưu.

## 3. Quản trị bằng RCON

RCON dùng giao thức Source RCON (TCP 25575), cần client riêng. Nhược điểm so với REST API: không có unban, lỗi vặt với tin nhắn có dấu cách/tiếng Việt. Chỉ dùng khi tool của bạn chỉ hỗ trợ RCON.

### Cài client

```bash
# Cách 1: rcon-cli qua Docker (không cần cài gì)
docker run -it --rm --network host itzg/rcon-cli \
  --host 127.0.0.1 --port 25575 --password "mat-khau-manh-cua-ban" Info

# Cách 2: tải binary rcon (gorcon)
# https://github.com/gorcon/rcon-cli/releases
rcon -a 127.0.0.1:25575 -p "mat-khau-manh-cua-ban" ShowPlayers
```

### Bảng lệnh RCON

| Lệnh | Chức năng |
|---|---|
| `Info` | Phiên bản + tên server |
| `ShowPlayers` | Danh sách người chơi (name, playeruid, steamid) — CSV |
| `Broadcast <msg>` | Thông báo toàn server (⚠️ không hỗ trợ dấu cách tốt — dùng `_` thay) |
| `KickPlayer <SteamID>` | Kick người chơi |
| `BanPlayer <SteamID>` | Ban người chơi (không có lệnh unban qua RCON — dùng REST API) |
| `Save` | Ép lưu world |
| `Shutdown <giây> <msg>` | Tắt êm có đếm ngược + thông báo |
| `DoExit` | Tắt NGAY lập tức |

## 4. Lệnh admin trong game

Ngoài 2 tool trên, bạn có thể quản trị ngay trong game: mở chat, gõ:

```
/AdminPassword mat-khau-manh-cua-ban
```

Sau khi thành admin, dùng được các lệnh chat:

| Lệnh | Chức năng |
|---|---|
| `/Broadcast <msg>` | Thông báo toàn server |
| `/KickPlayer <SteamID>` | Kick |
| `/BanPlayer <SteamID>` | Ban |
| `/TeleportToPlayer <SteamID>` | Dịch chuyển mình tới chỗ người chơi |
| `/TeleportToMe <SteamID>` | Kéo người chơi về chỗ mình |
| `/ShowPlayers` | Danh sách người chơi |
| `/Save` | Ép lưu world |
| `/Shutdown <giây> <msg>` | Tắt server êm |
| `/DoExit` | Tắt ngay |

## 5. Kịch bản quản trị thường gặp

### Restart server có báo trước (không ai mất đồ)

```bash
$PAL -X POST http://127.0.0.1:8212/v1/api/shutdown \
  -H "Content-Type: application/json" \
  -d '{"waittime":300,"message":"Server restart sau 5 phut, hay tim cho an toan!"}'
# Đợi hết 300s server tự save + tắt; restart: unless-stopped sẽ tự kéo container dậy.
# Nếu không dùng restart policy: docker compose up -d
```

### Xử lý người phá hoại

```bash
$PAL http://127.0.0.1:8212/v1/api/players          # 1. tìm userId của người vi phạm
$PAL -X POST .../v1/api/kick -d '{"userid":"steam_xxx","message":"Canh cao lan 1"}'   # 2. nhẹ thì kick
$PAL -X POST .../v1/api/ban  -d '{"userid":"steam_xxx","message":"Tai pham"}'         # 3. nặng thì ban
```

### Backup an toàn khi server đang chạy

```bash
$PAL -X POST http://127.0.0.1:8212/v1/api/save     # ép save để dữ liệu trên đĩa mới nhất
sleep 5
tar czf backup-$(date +%Y%m%d-%H%M).tar.gz Saved/  # rồi mới nén
```

### Giám sát tự động (cron mỗi 5 phút, cảnh báo khi lag)

```bash
#!/bin/sh
FPS=$($PAL http://127.0.0.1:8212/v1/api/metrics | grep -o '"serverfps":[0-9]*' | cut -d: -f2)
[ "$FPS" -lt 20 ] && echo "$(date): CANH BAO server lag, FPS=$FPS" >> /var/log/palworld-health.log
```

### Update tự động có báo trước

Xem script [update.sh](update.sh) — điền `ADMIN_PASSWORD` vào đầu script là bước announce/save trước khi restart tự kích hoạt.

## 6. Bảo mật

- **Chỉ bind localhost** (`127.0.0.1:8212`, `127.0.0.1:25575`) như mẫu — tuyệt đối không expose 2 port này ra Internet: giao thức không mã hóa, lộ `AdminPassword` là mất server.
- Cần quản trị từ máy khác? Dùng **SSH tunnel**: `ssh -L 8212:127.0.0.1:8212 user@ip-server` rồi gọi `http://127.0.0.1:8212` từ máy mình như bình thường.
- `AdminPassword` dài ≥ 16 ký tự, không trùng `ServerPassword`.
- Đừng gõ password trong lệnh trên máy dùng chung (lộ qua `history`/`ps`) — dùng `curl -su admin -K ~/.palworld-curlrc` hoặc biến môi trường.

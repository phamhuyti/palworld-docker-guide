# Palworld Docker Guide 🎮🐳

Hướng dẫn tiếng Việt **cấu hình đầy đủ** Palworld Dedicated Server chạy bằng Docker, dựa trên phân tích repo chính thức của Pocketpair: [pocketpairjp/palworld-dedicated-server-docker](https://github.com/pocketpairjp/palworld-dedicated-server-docker).

Tương ứng **Palworld 1.0.x** (bản 1.0 ra ngày 10/07/2026) — image mới nhất `ghcr.io/pocketpairjp/palserver:v1.0.1.100619`.

## Nội dung

| File | Mô tả |
|---|---|
| **[HUONG-DAN-CONFIG.md](HUONG-DAN-CONFIG.md)** | 📖 Tài liệu chính: phân tích repo, tham số dòng lệnh, **giải thích toàn bộ 119 thông số `PalWorldSettings.ini`**, cấu hình mẫu, firewall/bảo mật, auto-update, cập nhật & backup |
| **[HUONG-DAN-QUAN-TRI.md](HUONG-DAN-QUAN-TRI.md)** | 🛠️ Quản trị server bằng **RCON & REST API**: bật 2 tool, bảng endpoint/lệnh đầy đủ, kick/ban, restart có báo trước, giám sát, bảo mật |
| **[config-editor.html](config-editor.html)** | 🧩 **Trình chỉnh cấu hình trực quan** (web app): chỉnh cả 119 tham số bằng toggle/slider/dropdown, có note giải thích + preset, nhập file hiện tại & xuất ra `.ini` chuẩn. Mở trực tiếp bằng trình duyệt |
| **[admin-tool.py](admin-tool.py)** | 🎛️ **Bảng điều khiển quản trị + PWA** (Python, không cần cài gói): xem người chơi/FPS/uptime, thông báo, save, kick/ban/unban, shutdown/stop (tự save trước), **đếm ngược spam thông báo trước khi restart/tắt**, RCON console, và trang **`/config`** chỉnh trực tiếp 119 tham số `PalWorldSettings.ini` trên server đang chạy (nạp/lưu qua REST nội bộ, tự backup file trước khi ghi — không cần Chrome File System Access API hay đụng filesystem NAS). Có **đăng nhập + khóa chống dò mật khẩu + TLS** để dùng qua VPN từ điện thoại; cài được lên màn hình chính Android. Xem [mục dưới](#-quản-trị-từ-điện-thoại-admin-toolpy--pwa) |
| [compose.yaml](compose.yaml) | Docker Compose mẫu (RCON/REST bật sẵn trên localhost, auto-update qua tag `latest`) |
| [update.sh](update.sh) | Script auto-update giảm downtime (pull trước khi down, báo người chơi, backup) — chạy bằng cron |
| [helper.sh](helper.sh) | Script entrypoint (nguyên bản từ repo gốc) |
| [PalWorldSettings.ini.example](PalWorldSettings.ini.example) | File cấu hình mẫu đầy đủ 119 thông số với giá trị mặc định |

## Chạy nhanh

```bash
git clone https://github.com/phamhuyti/palworld-docker-guide.git palworld && cd palworld
chmod +x helper.sh
docker compose up -d
docker compose logs -f
```

Sau lần chạy đầu, sửa cấu hình tại `./Saved/Config/LinuxServer/PalWorldSettings.ini` rồi `docker compose restart`.

👉 Đọc [HUONG-DAN-CONFIG.md](HUONG-DAN-CONFIG.md) để hiểu ý nghĩa từng thông số, hoặc mở [config-editor.html](config-editor.html) để chỉnh cấu hình trực quan rồi xuất ra file `.ini`.

## 🎛️ Quản trị từ điện thoại (admin-tool.py — PWA)

Yêu cầu server đã bật `RESTAPIEnabled=True` (và `RCONEnabled=True` nếu dùng RCON console) + `AdminPassword`. Máy chạy tool phải tới được server (cùng LAN, hoặc qua VPN).

**Cách 1 — chạy như service Docker (khuyến nghị, luôn bật trên NAS).** Thêm service `palworld-admin` (đã có sẵn trong [compose.yaml](compose.yaml)) rồi tạo file `.env` cạnh compose:

```bash
cp .env.example .env      # rồi sửa PAL_ADMIN_PASSWORD = AdminPassword của bạn
docker compose up -d      # server + admin-tool cùng chạy
```

Service này kết nối tới server qua tên `palworld-server` trên mạng nội bộ compose và mở web UI ở cổng `8080`.

**Cách 2 — chạy trực tiếp bằng Python** (Windows PowerShell), bind ra LAN cho điện thoại (qua VPN) truy cập:

```powershell
$env:PAL_HOST="192.168.1.160"       # IP server Palworld
$env:PAL_ADMIN_PASSWORD="mk-admin"  # = AdminPassword của server
$env:PAL_APP_PASSWORD="mk-dang-nhap-app"   # mật khẩu ĐĂNG NHẬP app (khác AdminPassword)
$env:PAL_BIND="0.0.0.0"             # cho máy khác trong LAN/VPN truy cập
python admin-tool.py
```

Trên điện thoại (cả 2 cách): **vào VPN** → mở `http://<IP-LAN-host>:8080` bằng Chrome → đăng nhập → menu Chrome ⋮ → **"Thêm vào Màn hình chính"** để cài như app.

**Bật HTTPS** (khuyến nghị — để cài PWA đầy đủ + mã hóa). Tạo cert tự ký rồi trỏ tới:

```powershell
# tạo cert tự ký (cần openssl)
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 825 -subj "/CN=palworld-admin"
$env:PAL_TLS_CERT="cert.pem"; $env:PAL_TLS_KEY="key.pem"
python admin-tool.py   # giờ chạy https://
```

**Bảo mật đã tích hợp:** đăng nhập bằng mật khẩu app riêng (không phải AdminPassword) · phiên cookie HttpOnly · khóa 5 phút sau 5 lần sai · header bảo mật · AdminPassword không bao giờ ra tới trình duyệt · bắt buộc đặt mật khẩu app khi bind ra ngoài `localhost`. ⚠️ **Không** port-forward tool này ra Internet công khai — chỉ dùng qua VPN.

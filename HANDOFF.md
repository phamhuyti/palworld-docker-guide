# HANDOFF — Bối cảnh công việc Palworld Docker Guide

> File tổng hợp để tiếp tục công việc trên Claude Code desktop (hoặc phiên làm việc khác).
> Cập nhật lần cuối: 15/07/2026.

## 1. Mục tiêu dự án

Phân tích repo Docker **chính thức** của Pocketpair cho Palworld dedicated server và xây dựng bộ tài liệu tiếng Việt hướng dẫn cấu hình **tất cả thông số**, kèm file mẫu chạy được ngay + quản trị + auto-update.

- Repo gốc được phân tích: https://github.com/pocketpairjp/palworld-dedicated-server-docker
- Repo kết quả (của tôi, public): **https://github.com/phamhuyti/palworld-docker-guide** — nhánh `main`

## 2. Trạng thái hiện tại: HOÀN THÀNH các phần sau

| File trong repo | Nội dung | Trạng thái |
|---|---|---|
| `HUONG-DAN-CONFIG.md` | Tài liệu chính: phân tích repo gốc, tham số dòng lệnh, **toàn bộ 119 thông số `PalWorldSettings.ini`** (11 nhóm, mỗi thông số có mặc định/phạm vi/giải thích), 4 bộ cấu hình mẫu, firewall/bảo mật, RCON/REST API, update/backup, mục **auto-update 3 cách** | ✅ Xong |
| `HUONG-DAN-QUAN-TRI.md` | Quản trị bằng RCON & REST API: cách bật, bảng 11 endpoint REST + ví dụ curl, bảng lệnh RCON, lệnh admin trong game, kịch bản (kick/ban, restart báo trước, backup nóng, giám sát FPS), bảo mật/SSH tunnel | ✅ Xong |
| `compose.yaml` | Mẫu chuẩn: tag `latest`, RCON/REST bind `127.0.0.1`, `stop_grace_period: 30s`, chú thích tiếng Việt. **Thêm service `palworld-admin`** (image python:3.12-slim, mount admin-tool.py, PAL_HOST=palworld-server qua mạng nội bộ compose, mở 8080, mật khẩu từ `${PAL_ADMIN_PASSWORD}`/`.env`) | ✅ Xong |
| `.env.example` | Mẫu biến môi trường cho compose (PAL_ADMIN_PASSWORD, PAL_APP_PASSWORD tùy chọn). File `.env` thật đã gitignore | ✅ Xong |
| `update.sh` | Auto-update giảm downtime: pull khi server còn chạy → so digest (không có bản mới thì thoát) → announce+save qua REST API (nếu điền `ADMIN_PASSWORD`) → down → backup tar → up | ✅ Xong |
| `helper.sh` | Entrypoint nguyên bản từ repo gốc (chown Saved rồi exec PalServer.sh) | ✅ Xong |
| `PalWorldSettings.ini.example` | Đủ 119 key với giá trị mặc định, hướng dẫn định dạng 2 dòng bắt buộc | ✅ Xong |
| `config-editor.html` | **Web app chỉnh config trực quan** (standalone, offline): 119 tham số theo 11 nhóm, control theo kiểu (toggle/slider/dropdown/checkbox platform/password), note giải thích + cảnh báo ngoài phạm vi, badge "đã đổi", tìm kiếm, 5 preset, nhập file hiện tại (parse client-side, không nhúng mật khẩu), xuất `.ini` 2 dòng đúng thứ tự key CANON, tự lưu localStorage, light/dark. Cũng đã publish Artifact | ✅ Xong |
| `admin-tool.py` | **Bảng điều khiển quản trị + PWA** (Python stdlib, không cần cài gì): backend serve web + proxy tới REST API (Basic auth) và RCON (socket TCP tự implement Source RCON). Giao diện: trạng thái server (info+metrics auto-refresh), bảng người chơi kick/ban 2-click-confirm, announce, save, shutdown/stop, unban, RCON console, log. **Đếm ngược spam thông báo** (`/api/countdown` + cancel + status): luồng nền spam `/announce` dày dần (mỗi phút→30s→10s→5..1s) rồi save+shutdown; chỉ 1 lần chạy (trùng→409). **Save tường minh** trước cả Shutdown lẫn Stop. **Bảo mật tầng app**: đăng nhập PAL_APP_PASSWORD (tách khỏi AdminPassword) + phiên cookie HttpOnly (secrets token, hạn PAL_SESSION_HOURS) + khóa 5 phút sau 5 lần sai + header bảo mật; bind ra ngoài localhost thì BẮT BUỘC có PAL_APP_PASSWORD (nếu không, từ chối chạy). **PWA**: /manifest.webmanifest + /sw.js + icon PNG tự vẽ chữ P (encoder zlib, không cần Pillow) → cài lên Android. **TLS tùy chọn** qua PAL_TLS_CERT/PAL_TLS_KEY. Cấu hình toàn bộ qua ENV, KHÔNG hardcode. Chạy được như **service Docker `palworld-admin`** (xem compose). Đã test bằng curl+Chrome (không chạy lệnh sống lên server thật theo yêu cầu người dùng). Fix Windows cp1252 bằng stdout.reconfigure(utf-8) | ✅ Xong |
| `README.md` | Mục lục + chạy nhanh (đã thêm config-editor.html + admin-tool.py) | ✅ Xong |
| `.gitignore` | Thêm __pycache__, *.local.*, run-admin*, backup-*.tar.gz | ✅ Xong |

Lịch sử commit chính: `0e95884` (tài liệu ban đầu) → `558c5f0` (auto-update) → `264f1ad` (RCON/REST + update.sh) → `5933ca0` (HANDOFF) → `8dc6d31` (config-editor.html) → `08764f8` (editor mở/lưu file) → `fcc0518`/`773acb2` (admin-tool.py + bảo mật/PWA) → `6fab9ef` (service palworld-admin trong compose) → `0ee7a9d` (đếm ngược spam) → `7f0b2ce` (save trước shutdown/stop).

## 3. Thông tin phiên bản (quan trọng khi tiếp tục)

- Game hiện tại: **Palworld 1.0.x** — bản 1.0 (update 1.100.427) ra **10/07/2026**, kết thúc Early Access.
- Image chính thức: `ghcr.io/pocketpairjp/palserver` — tag mới nhất **`v1.0.1.100619`** (hotfix, ra 14/07/2026, = `latest`). Danh sách tag: https://github.com/pocketpairjp/palworld-dedicated-server-docker/pkgs/container/palserver
- Quy tắc: tag image phải khớp version client; restart container KHÔNG tự lấy bản mới (image chính thức đóng gói sẵn binary) — auto-update = tag `latest` + `update.sh`/cron, hoặc Watchtower, hoặc image cộng đồng (thijsvanloef/jammsen — tải qua SteamCMD mỗi lần khởi động).
- Bản 1.0 đổi vài mặc định so với Early Access (đã ghi chú trong tài liệu): `PalEggDefaultHatchingTime` 72→1, `DeathPenalty` All→Item, `bIsStartLocationSelectByMap` True→False. Đối chiếu chuẩn bằng: `docker compose exec palworld-server bash -c "cat /pal/Package/DefaultPalWorldSettings.ini"`

## 4. Setup máy của người dùng (server thật đang chạy)

- NAS Synology `CaoHuy_NAS`. Thư mục compose: `/volume4/docker/Palworld` = ổ mạng **`X:\Palworld`** trên Windows (SMB). Container chạy trên NAS; sửa file qua `X:\`, chạy `docker compose` phải SSH vào NAS.
- Compose: game bind **`192.168.1.160:8211/udp`** + RCON `25575` + REST `8212` (đều bind IP LAN). Image `latest`, `stop_grace_period: 30s`. **Đã thêm service `palworld-admin`** (admin-tool.py chạy trong Docker, mở `8080`, mật khẩu từ `.env`).
- **Đã LÀM trong phiên (16/07/2026):** bật `RCONEnabled=True`, `RESTAPIEnabled=True`, đặt `AdminPassword`; mở 2 port quản trị ra IP LAN; tạo `X:\Palworld\.env` (PAL_ADMIN_PASSWORD); copy `admin-tool.py` vào thư mục compose. Đã kiểm chứng REST+RCON **truy cập được** từ máy Windows (401 với mật khẩu giả).
- **Thay đổi gameplay đã ghi vào ini** (chờ restart để áp dụng): `PalDamageRateAttack=2`, `PalDamageRateDefense=0.5`, `PalStaminaDecreaceRate=0.25`, `bIsPvP=True`, `bEnablePlayerToPlayerDamage=True`, `bCanPickupOtherGuildDeathPenaltyDrop=True`, `DeathPenalty=ItemAndEquipment`.
- **Việc người dùng CẦN chạy trên NAS (SSH):** `cd /volume4/docker/Palworld && docker compose up -d` (bật admin-tool) và `docker compose restart palworld-server` (áp dụng các thay đổi ini). Truy cập tool: vào OpenVPN → `http://192.168.1.160:8080`.
- Còn tùy chọn: đặt lịch `update.sh` qua Synology Task Scheduler (`/volume4/docker/Palworld/update.sh`).

## 5. Nguồn dữ liệu & lưu ý kỹ thuật cho phiên sau

- Danh sách 119 thông số + default/range lấy từ source của tool pal-conf (`Bluefissure/pal-conf` — file `src/consts/entries.tsx`), đối chiếu với `DefaultPalWorldSettings.ini` trong các repo docker và wiki. Tool UI: https://pal-conf.bluefissure.com/
- Tài liệu chính thức: https://tech.palworldgame.com/ và https://docs.palworldgame.com/ — **các trang này (và palworld.wiki.gg, các blog hosting) trả 403 khi fetch qua proxy của môi trường remote**; GitHub và raw.githubusercontent thì truy cập được. Trên desktop có thể fetch trực tiếp bình thường.
- REST API: HTTP Basic `admin:AdminPassword`, base `http://127.0.0.1:8212/v1/api/` — endpoints: info, players, settings, metrics, announce, kick, ban, unban, save, shutdown, stop.
- RCON: TCP 25575, không có unban, kém với tin nhắn unicode/dấu cách.
- GitHub App của phiên remote **không có quyền tạo repo** (403 `create_repository`) — repo `palworld-docker-guide` do người dùng tự tạo trên web rồi push từ phiên.

## 6. Ý tưởng việc tiếp theo (chưa làm — gợi ý)

- [ ] Script backup định kỳ riêng (cron + xoay vòng giữ N bản gần nhất, hiện backup chỉ chạy khi update).
- [ ] Script restore backup + hướng dẫn khôi phục từng world.
- [ ] Bot Discord / web panel quản trị dựa trên REST API (announce, players, metrics).
- [ ] Giám sát: đẩy `/v1/api/metrics` vào Prometheus/Grafana (LogFormatType=Json + log collector).
- [ ] Thử nghiệm và ghi chép mục Server Clustering khi Pocketpair phát hành chính thức (đã công bố 06/2026 nhưng chưa có trong patch notes 1.0).
- [ ] Bổ sung `DenyTechnologyList` ID list đầy đủ (tham khảo `Bluefissure/pal-conf` file `src/consts/technologyNames.ts`).
- [ ] Dịch tài liệu sang tiếng Anh (README song ngữ).

## 7. Cách tiếp tục trên Claude Code desktop

```bash
git clone https://github.com/phamhuyti/palworld-docker-guide
cd palworld-docker-guide
claude   # rồi yêu cầu: "đọc HANDOFF.md và tiếp tục công việc"
```

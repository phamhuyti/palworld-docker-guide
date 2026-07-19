# HANDOFF — Bối cảnh công việc Palworld Docker Guide

> File tổng hợp để tiếp tục công việc trên Claude Code desktop (hoặc phiên làm việc khác).
> Cập nhật lần cuối: 19/07/2026.

## 1. Mục tiêu dự án

Phân tích repo Docker **chính thức** của Pocketpair cho Palworld dedicated server và xây dựng bộ tài liệu tiếng Việt hướng dẫn cấu hình **tất cả thông số**, kèm file mẫu chạy được ngay + quản trị + auto-update.

- Repo gốc được phân tích: https://github.com/pocketpairjp/palworld-dedicated-server-docker
- Repo kết quả (của tôi, public): **https://github.com/phamhuyti/palworld-docker-guide** — nhánh `main`

## 2. Trạng thái hiện tại: HOÀN THÀNH các phần sau

| File trong repo | Nội dung | Trạng thái |
|---|---|---|
| `HUONG-DAN-CONFIG.md` | Tài liệu chính: phân tích repo gốc, tham số dòng lệnh, **toàn bộ 119 thông số `PalWorldSettings.ini`** (11 nhóm, mỗi thông số có mặc định/phạm vi/giải thích), 4 bộ cấu hình mẫu, firewall/bảo mật, RCON/REST API, update/backup, mục **auto-update 3 cách** | ✅ Xong |
| `HUONG-DAN-QUAN-TRI.md` | Quản trị bằng RCON & REST API: cách bật, bảng 11 endpoint REST + ví dụ curl, bảng lệnh RCON, lệnh admin trong game, kịch bản (kick/ban, restart báo trước, backup nóng, giám sát FPS), bảo mật/SSH tunnel | ✅ Xong |
| `compose.yaml` | Mẫu chuẩn: tag `latest`, RCON/REST bind `127.0.0.1`, `stop_grace_period: 30s`, chú thích tiếng Việt. **Thêm service `palworld-admin`** (image python:3.12-slim, mount admin-tool.py, PAL_HOST=palworld-server qua mạng nội bộ compose, mở 8080, mật khẩu từ `${PAL_ADMIN_PASSWORD}`/`.env`). **Mới (16/07):** mount thêm `./Saved/Config/LinuxServer:/pal/Config/LinuxServer` + env `PAL_CONFIG_PATH` để admin-tool đọc/ghi trực tiếp `PalWorldSettings.ini` | ✅ Xong |
| `.env.example` | Mẫu biến môi trường cho compose (PAL_ADMIN_PASSWORD, PAL_APP_PASSWORD tùy chọn). File `.env` thật đã gitignore | ✅ Xong |
| `update.sh` | Auto-update giảm downtime: pull khi server còn chạy → so digest (không có bản mới thì thoát) → announce+save qua REST API (nếu điền `ADMIN_PASSWORD`) → down → backup tar → up | ✅ Xong |
| `helper.sh` | Entrypoint nguyên bản từ repo gốc (chown Saved rồi exec PalServer.sh) | ✅ Xong |
| `PalWorldSettings.ini.example` | Đủ 119 key với giá trị mặc định, hướng dẫn định dạng 2 dòng bắt buộc | ✅ Xong |
| `config-editor.html` | **Web app chỉnh config trực quan** (standalone, offline): 119 tham số theo 11 nhóm, control theo kiểu (toggle/slider/dropdown/checkbox platform/password), note giải thích + cảnh báo ngoài phạm vi, badge "đã đổi", tìm kiếm, 5 preset, nhập file hiện tại (parse client-side, không nhúng mật khẩu), xuất `.ini` 2 dòng đúng thứ tự key CANON, tự lưu localStorage, light/dark. Cũng đã publish Artifact | ✅ Xong |
| `admin-tool.py` | **Bảng điều khiển quản trị + PWA** (Python stdlib, không cần cài gì): backend serve web + proxy tới REST API (Basic auth) và RCON (socket TCP tự implement Source RCON). Giao diện: trạng thái server (info+metrics auto-refresh), bảng người chơi kick/ban 2-click-confirm, announce, save, shutdown/stop, unban, RCON console, log. **Đếm ngược spam thông báo** (`/api/countdown` + cancel + status): luồng nền spam `/announce` dày dần (mỗi phút→30s→10s→5..1s) rồi save+shutdown; chỉ 1 lần chạy (trùng→409). **Save tường minh** trước cả Shutdown lẫn Stop. **Bảo mật tầng app**: đăng nhập PAL_APP_PASSWORD (tách khỏi AdminPassword) + phiên cookie HttpOnly (secrets token, hạn PAL_SESSION_HOURS) + khóa 5 phút sau 5 lần sai + header bảo mật; bind ra ngoài localhost thì BẮT BUỘC có PAL_APP_PASSWORD (nếu không, từ chối chạy). **PWA**: /manifest.webmanifest + /sw.js + icon PNG tự vẽ chữ P (encoder zlib, không cần Pillow) → cài lên Android. **TLS tùy chọn** qua PAL_TLS_CERT/PAL_TLS_KEY. Cấu hình toàn bộ qua ENV, KHÔNG hardcode. Chạy được như **service Docker `palworld-admin`** (xem compose). Đã test bằng curl+Chrome (không chạy lệnh sống lên server thật theo yêu cầu người dùng). Fix Windows cp1252 bằng stdout.reconfigure(utf-8). **Mới (16/07):** thêm trang **`/config`** (auth-gated, cùng session cookie) — nhúng lại UI của `config-editor.html` nhưng thay 2 nút "Mở/Lưu file .ini" cục bộ (Chrome File System Access API — vô dụng khi dùng qua điện thoại/VPN) bằng **"Nạp từ server"/"Lưu vào server"** gọi 2 route mới `GET/POST /api/config`; backend `read_config()`/`write_config()` đọc/ghi thẳng file thật qua `PAL_CONFIG_PATH`, ghi atomic (`.tmp` + `os.replace`), tự backup `.bak-<timestamp>` trước mỗi lần ghi, từ chối ghi nếu thiếu `OptionSettings`. Không thêm nút restart container (tránh phải mount `docker.sock` — quyết định đã chốt với người dùng), chỉ nhắc restart thủ công. Cũng fix bug: `SW_JS` cache-first theo version cố định (`pal-admin-v3`) khiến PWA đã cài không thấy link điều hướng mới tới `/config` sau khi update — bump lên `v4` + tự xoá cache cũ ở bước `activate` | ✅ Xong |
| `README.md` | Mục lục + chạy nhanh (đã thêm config-editor.html + admin-tool.py) | ✅ Xong |
| `.gitignore` | Chặn `Saved/` (quan trọng nhất — toàn bộ save + `PalWorldSettings.ini` có AdminPassword thật), `.env` (password thật cho compose), `__pycache__/`, `*.local.*`, `run-admin*`, `backup-*.tar.gz` | ✅ Xong |

Lịch sử commit chính: `0e95884` (tài liệu ban đầu) → `558c5f0` (auto-update) → `264f1ad` (RCON/REST + update.sh) → `5933ca0` (HANDOFF) → `8dc6d31` (config-editor.html) → `08764f8` (editor mở/lưu file) → `fcc0518`/`773acb2` (admin-tool.py + bảo mật/PWA) → `6fab9ef` (service palworld-admin trong compose) → `0ee7a9d` (đếm ngược spam) → `7f0b2ce` (save trước shutdown/stop) → `346e057` (docs README+HANDOFF) → `3115e70` (trang /config + fix cache SW) → `aeb322c` (đối chiếu HANDOFF với thực tế).

**Nhánh `nas-deployment`** (tạo 17/07): snapshot cấu hình **thật đang chạy** trên NAS — khác `main` (template chung 127.0.0.1): `compose.yaml` giữ IP LAN thật `192.168.1.160`, `update.sh` bản thật nhưng đã sửa đọc password từ `.env` thay vì hardcode (không có secret trong repo). Các thay đổi cá nhân hoá (nút "Sửa save" trỏ IP LAN, service `palworld-savepal`) chỉ nằm trên nhánh này, KHÔNG đưa vào `main`.

## 3. Thông tin phiên bản (quan trọng khi tiếp tục)

- Game hiện tại: **Palworld 1.0.x** — bản 1.0 (update 1.100.427) ra **10/07/2026**, kết thúc Early Access.
- Image chính thức: `ghcr.io/pocketpairjp/palserver` — tag mới nhất **`v1.0.1.100619`** (hotfix, ra 14/07/2026, = `latest`). Danh sách tag: https://github.com/pocketpairjp/palworld-dedicated-server-docker/pkgs/container/palserver
- Quy tắc: tag image phải khớp version client; restart container KHÔNG tự lấy bản mới (image chính thức đóng gói sẵn binary) — auto-update = tag `latest` + `update.sh`/cron, hoặc Watchtower, hoặc image cộng đồng (thijsvanloef/jammsen — tải qua SteamCMD mỗi lần khởi động).
- Bản 1.0 đổi vài mặc định so với Early Access (đã ghi chú trong tài liệu): `PalEggDefaultHatchingTime` 72→1, `DeathPenalty` All→Item, `bIsStartLocationSelectByMap` True→False. Đối chiếu chuẩn bằng: `docker compose exec palworld-server bash -c "cat /pal/Package/DefaultPalWorldSettings.ini"`

## 4. Setup máy của người dùng (server thật đang chạy)

- NAS Synology `CaoHuy_NAS`. Thư mục compose: `/volume4/docker/Palworld` = ổ mạng **`X:\Palworld`** trên Windows (SMB). Container chạy trên NAS; sửa file qua `X:\`, chạy `docker compose` phải SSH vào NAS.
- Compose: game bind **`192.168.1.160:8211/udp`** + RCON `25575` + REST `8212` (đều bind IP LAN). Image `latest`, `stop_grace_period: 30s`. **Đã thêm service `palworld-admin`** (admin-tool.py chạy trong Docker, mở `8080`, mật khẩu từ `.env`).
- **Đã LÀM trong phiên (16/07/2026):** bật `RCONEnabled=True`, `RESTAPIEnabled=True`, đặt `AdminPassword`; mở 2 port quản trị ra IP LAN; tạo `X:\Palworld\.env` (PAL_ADMIN_PASSWORD); copy `admin-tool.py` vào thư mục compose. Đã kiểm chứng REST+RCON **truy cập được** từ máy Windows (401 với mật khẩu giả).
- **Thay đổi gameplay đã ghi vào ini — ĐÃ ÁP DỤNG** (xác nhận `palworld-server` đã restart sau khi ghi, giá trị trong ini thật khớp đúng): `PalDamageRateAttack=2`, `PalDamageRateDefense=0.5`, `PalStaminaDecreaceRate=0.25`, `bIsPvP=True`, `bEnablePlayerToPlayerDamage=True`, `bCanPickupOtherGuildDeathPenaltyDrop=True`, `DeathPenalty=ItemAndEquipment`.
- **Đã chạy xong trên NAS:** cả `palworld-server` và `palworld-admin` đang chạy (`docker compose up -d` đã thực hiện, restart đã áp dụng thay đổi ini). Truy cập tool: vào OpenVPN → `http://192.168.1.160:8080`.
- Còn tùy chọn: đặt lịch `update.sh` qua Synology Task Scheduler (`/volume4/docker/Palworld/update.sh`).
- **Đã LÀM tiếp trong phiên (16/07/2026, muộn hơn cùng ngày):** thêm trang `/config` vào admin-tool.py + mount config dir (xem mục 2). Đã deploy và test trực tiếp trên server thật: `docker compose up -d palworld-admin` (chỉ recreate service admin, không đụng `palworld-server`), login → `/config` → `GET/POST /api/config` round-trip OK, backup tự tạo đúng, sau đó đã khôi phục lại `PalWorldSettings.ini` về đúng bản gốc (test không để lại thay đổi thật). Cũng phát hiện + fix bug SW cache (xem mục 2) ngay trên server thật, đã restart `palworld-admin` để áp dụng.
- **Mới:** NAS giờ có sẵn **bản clone git thật** của repo tại `/volume4/docker/palworld-docker-guide` (tách biệt thư mục triển khai `/volume4/docker/Palworld`), push bằng **SSH deploy key riêng** `~/.ssh/id_ed25519_palworld_guide` (đã thêm public key vào Settings → Deploy keys của repo, có quyền write, `core.sshCommand` đã set sẵn trong clone này). Phiên sau muốn sửa tiếp trực tiếp trên NAS: `cd /volume4/docker/palworld-docker-guide && git pull`, sửa xong copy file cần thiết (vd. `admin-tool.py`) đè sang `/volume4/docker/Palworld/` để deploy, rồi commit+push từ đây — không cần tạo lại key. Nếu người dùng có clone riêng trên Windows, clone đó **không tự đồng bộ** — cần tự `git pull`.
- **Phiên 19/07/2026 — tích hợp trình sửa save (Palworld Save Pal):**
  - Xuất phát: người dùng muốn cài mod [Rainbow Trait Chance](https://www.nexusmods.com/palworld/mods/2070) — kết luận **KHÔNG cài được**: mod là file JSON cho **PalSchema**, PalSchema cần **UE4SS**, UE4SS trên dedicated server chỉ chạy bản **Windows** (server đang chạy bản Linux native trong Docker). Đã chốt hướng thay thế: sửa save trực tiếp.
  - **Fork `phamhuyti/palworld-save-pal`** (gốc: oMaN-Rod/palworld-save-pal — editor save Rust+Svelte, web qua Docker). Clone tại **`/volume4/docker/palworld-save-pal`**, deploy key riêng `~/.ssh/id_ed25519_palworld_savepal` (write, `core.sshCommand` đã set trong clone). 2 commit đã push lên fork: `0a5ecdc7` (Dockerfile: build UI bằng `node:24`/npm thay `oven/bun` — bun crash trên kernel 4.4 của Synology; node 24 do `ol-contextmenu` đòi node>=24) và `b265012d` (endpoint mới `GET /api/local-saves` quét `PSP_SAVES_DIR` liệt kê world + khung "Server saves" ở trang `/upload` — bấm chọn save của server trực tiếp, khỏi upload zip).
  - **Service mới `palworld-savepal`** trong compose thật trên NAS: build từ clone fork (`context: ../palworld-save-pal`, arg `PUBLIC_WS_URL=192.168.1.160:5174/ws`), bind `192.168.1.160:5174` (chỉ LAN/VPN, **không có đăng nhập** — đã chốt chấp nhận), env `PSP_SAVES_DIR=/saves`, mount `./Saved/SaveGames:/saves` (đọc/ghi) + `./savepal-db:/app/db`. **KHÔNG mount docker.sock** (đã chốt bỏ tính năng quản lý server của Save Pal). Đã build, chạy, kiểm chứng: `/api/local-saves` trả đúng world thật ("Autosave_W", id `A8FA00352DE24A1E9267A25FB999BA7B`).
  - `admin-tool.py` bản deploy thật: thêm nút **"Sửa save"** (mở `http://192.168.1.160:5174` tab mới) cạnh nút "Cấu hình", bump SW cache `v6`. Thay đổi này trỏ IP LAN cứng → chỉ commit vào nhánh `nas-deployment`, KHÔNG vào `main`.
  - **Quy trình dùng an toàn:** dừng `palworld-server` trước khi LƯU save trong Save Pal (server đang chạy sẽ ghi đè), lưu xong start lại.

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

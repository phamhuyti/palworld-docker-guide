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
| `compose.yaml` | Mẫu chuẩn: tag `latest`, RCON/REST bind `127.0.0.1`, `stop_grace_period: 30s`, chú thích tiếng Việt | ✅ Xong |
| `update.sh` | Auto-update giảm downtime: pull khi server còn chạy → so digest (không có bản mới thì thoát) → announce+save qua REST API (nếu điền `ADMIN_PASSWORD`) → down → backup tar → up | ✅ Xong |
| `helper.sh` | Entrypoint nguyên bản từ repo gốc (chown Saved rồi exec PalServer.sh) | ✅ Xong |
| `PalWorldSettings.ini.example` | Đủ 119 key với giá trị mặc định, hướng dẫn định dạng 2 dòng bắt buộc | ✅ Xong |
| `README.md` | Mục lục + chạy nhanh | ✅ Xong |

Lịch sử commit: `0e95884` (bộ tài liệu ban đầu) → `558c5f0` (mục auto-update) → `264f1ad` (quản trị RCON/REST + update.sh + bật port trong compose).

## 3. Thông tin phiên bản (quan trọng khi tiếp tục)

- Game hiện tại: **Palworld 1.0.x** — bản 1.0 (update 1.100.427) ra **10/07/2026**, kết thúc Early Access.
- Image chính thức: `ghcr.io/pocketpairjp/palserver` — tag mới nhất **`v1.0.1.100619`** (hotfix, ra 14/07/2026, = `latest`). Danh sách tag: https://github.com/pocketpairjp/palworld-dedicated-server-docker/pkgs/container/palserver
- Quy tắc: tag image phải khớp version client; restart container KHÔNG tự lấy bản mới (image chính thức đóng gói sẵn binary) — auto-update = tag `latest` + `update.sh`/cron, hoặc Watchtower, hoặc image cộng đồng (thijsvanloef/jammsen — tải qua SteamCMD mỗi lần khởi động).
- Bản 1.0 đổi vài mặc định so với Early Access (đã ghi chú trong tài liệu): `PalEggDefaultHatchingTime` 72→1, `DeathPenalty` All→Item, `bIsStartLocationSelectByMap` True→False. Đối chiếu chuẩn bằng: `docker compose exec palworld-server bash -c "cat /pal/Package/DefaultPalWorldSettings.ini"`

## 4. Setup máy của người dùng (server thật đang chạy)

- Compose cá nhân: port game bind IP LAN **`192.168.1.160:8211:8211/udp`**, image đang chạy `v1.0.1.100619` (= latest hiện tại). Đã được sửa: fix lỗi thụt lề YAML (file cũ để `palworld-server:` ngang hàng `services:` → compose lỗi), thêm 2 port quản trị bind localhost, `stop_grace_period`, chuyển tag `latest`.
- **Việc người dùng CHƯA chắc đã làm** (cần nhắc/kiểm tra khi tiếp tục):
  1. Sửa `./Saved/Config/LinuxServer/PalWorldSettings.ini`: `AdminPassword="..."`, `RCONEnabled=True`, `RESTAPIEnabled=True` (mở port trong compose thôi là CHƯA đủ).
  2. Điền `ADMIN_PASSWORD` vào đầu `update.sh`.
  3. Đặt cron: `0 5 * * * root /duong-dan/palworld/update.sh >> /var/log/palworld-update.log 2>&1`
  4. Kiểm tra REST API: `curl -u admin:MATKHAU http://127.0.0.1:8212/v1/api/info`

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

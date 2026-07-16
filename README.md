# Palworld Docker Guide 🎮🐳

Hướng dẫn tiếng Việt **cấu hình đầy đủ** Palworld Dedicated Server chạy bằng Docker, dựa trên phân tích repo chính thức của Pocketpair: [pocketpairjp/palworld-dedicated-server-docker](https://github.com/pocketpairjp/palworld-dedicated-server-docker).

Tương ứng **Palworld 1.0.x** (bản 1.0 ra ngày 10/07/2026) — image mới nhất `ghcr.io/pocketpairjp/palserver:v1.0.1.100619`.

## Nội dung

| File | Mô tả |
|---|---|
| **[HUONG-DAN-CONFIG.md](HUONG-DAN-CONFIG.md)** | 📖 Tài liệu chính: phân tích repo, tham số dòng lệnh, **giải thích toàn bộ 119 thông số `PalWorldSettings.ini`**, cấu hình mẫu, firewall/bảo mật, auto-update, cập nhật & backup |
| **[HUONG-DAN-QUAN-TRI.md](HUONG-DAN-QUAN-TRI.md)** | 🛠️ Quản trị server bằng **RCON & REST API**: bật 2 tool, bảng endpoint/lệnh đầy đủ, kick/ban, restart có báo trước, giám sát, bảo mật |
| **[config-editor.html](config-editor.html)** | 🧩 **Trình chỉnh cấu hình trực quan** (web app): chỉnh cả 119 tham số bằng toggle/slider/dropdown, có note giải thích + preset, nhập file hiện tại & xuất ra `.ini` chuẩn. Mở trực tiếp bằng trình duyệt |
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

# Palworld Docker Guide 🎮🐳

Hướng dẫn tiếng Việt **cấu hình đầy đủ** Palworld Dedicated Server chạy bằng Docker, dựa trên phân tích repo chính thức của Pocketpair: [pocketpairjp/palworld-dedicated-server-docker](https://github.com/pocketpairjp/palworld-dedicated-server-docker).

Tương ứng **Palworld 1.0** (update 1.100.427, 10/07/2026) — image `ghcr.io/pocketpairjp/palserver:v1.0.0.100427`.

## Nội dung

| File | Mô tả |
|---|---|
| **[HUONG-DAN-CONFIG.md](HUONG-DAN-CONFIG.md)** | 📖 Tài liệu chính: phân tích repo, tham số dòng lệnh, **giải thích toàn bộ 119 thông số `PalWorldSettings.ini`**, cấu hình mẫu, firewall/bảo mật, RCON/REST API, cập nhật & backup |
| [compose.yaml](compose.yaml) | Docker Compose mẫu (dựa trên bản chính thức, có chú thích tiếng Việt) |
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

👉 Đọc [HUONG-DAN-CONFIG.md](HUONG-DAN-CONFIG.md) để hiểu ý nghĩa từng thông số.

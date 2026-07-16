#!/bin/sh
# update.sh — tự động update Palworld server với downtime tối thiểu.
# Đặt cạnh compose.yaml. Chạy tay: ./update.sh
# Đặt lịch tự động trên Synology DSM: Control Panel > Task Scheduler >
#   Create > Scheduled Task > User-defined script, user "root",
#   chạy hằng ngày lúc 5h sáng, lệnh: /volume4/docker/Palworld/update.sh
#   Log tự nối vào update.log cạnh script (/volume4/docker/Palworld/update.log).
set -e
cd "$(dirname "$0")"

# AdminPassword đọc từ .env cạnh script (PAL_ADMIN_PASSWORD, cùng biến compose
# dùng) — phải trùng AdminPassword trong PalWorldSettings.ini. Dùng để báo
# trước người chơi qua REST API trước khi restart.
set -a; [ -f .env ] && . ./.env; set +a
ADMIN_PASSWORD="${PAL_ADMIN_PASSWORD:?PAL_ADMIN_PASSWORD chưa đặt trong .env}"
WARN_SECONDS=300   # báo trước 5 phút

{
echo "$(date): bat dau kiem tra update"

# 1. Kéo image mới TRONG LÚC server vẫn đang chạy bản cũ (giảm downtime)
docker compose pull -q

# 2. Không có bản mới thì thoát — server không bị restart oan
RUNNING=$(docker inspect --format '{{.Image}}' palworld-server)
LATEST=$(docker image inspect --format '{{.Id}}' ghcr.io/pocketpairjp/palserver:latest)
if [ "$RUNNING" = "$LATEST" ]; then
  echo "$(date): khong co ban moi"
  exit 0
fi

echo "$(date): co ban moi, tien hanh update..."

# 3. Báo trước người chơi + ép save
curl -su "admin:$ADMIN_PASSWORD" -X POST http://192.168.1.160:8212/v1/api/announce \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"Server update va restart sau $((WARN_SECONDS / 60)) phut, hay tim cho an toan!\"}" || true
sleep "$WARN_SECONDS"
curl -su "admin:$ADMIN_PASSWORD" -X POST http://192.168.1.160:8212/v1/api/save || true
sleep 5

# 4. Dừng → backup → khởi động bằng image mới (đã tải sẵn nên lên lại ngay)
docker compose down
tar czf "backup-$(date +%Y%m%d-%H%M).tar.gz" Saved/
docker compose up -d
echo "$(date): update xong"
} >> update.log 2>&1

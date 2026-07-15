#!/bin/sh
# update.sh — tự động update Palworld server với downtime tối thiểu.
# Đặt cạnh compose.yaml. Chạy tay: ./update.sh
# Chạy tự động 5h sáng mỗi ngày:
#   echo '0 5 * * * root /duong-dan/palworld/update.sh >> /var/log/palworld-update.log 2>&1' | sudo tee /etc/cron.d/palworld-update
set -e
cd "$(dirname "$0")"

# Điền AdminPassword (trùng với PalWorldSettings.ini) để được báo trước người chơi
# qua REST API trước khi restart. Để trống = restart ngay không báo.
ADMIN_PASSWORD=""
WARN_SECONDS=300   # báo trước 5 phút

# 1. Kéo image mới TRONG LÚC server vẫn đang chạy bản cũ (giảm downtime)
docker compose pull -q

# 2. Không có bản mới thì thoát — server không bị restart oan
RUNNING=$(docker inspect --format '{{.Image}}' palworld-server)
LATEST=$(docker image inspect --format '{{.Id}}' ghcr.io/pocketpairjp/palserver:latest)
[ "$RUNNING" = "$LATEST" ] && { echo "$(date): khong co ban moi"; exit 0; }

echo "$(date): co ban moi, tien hanh update..."

# 3. Báo trước người chơi + ép save (cần RESTAPIEnabled=True và ADMIN_PASSWORD)
if [ -n "$ADMIN_PASSWORD" ]; then
  curl -su "admin:$ADMIN_PASSWORD" -X POST http://127.0.0.1:8212/v1/api/announce \
    -H "Content-Type: application/json" \
    -d "{\"message\":\"Server update va restart sau $((WARN_SECONDS / 60)) phut, hay tim cho an toan!\"}" || true
  sleep "$WARN_SECONDS"
  curl -su "admin:$ADMIN_PASSWORD" -X POST http://127.0.0.1:8212/v1/api/save || true
  sleep 5
fi

# 4. Dừng → backup → khởi động bằng image mới (đã tải sẵn nên lên lại ngay)
docker compose down
tar czf "backup-$(date +%Y%m%d-%H%M).tar.gz" Saved/
docker compose up -d
echo "$(date): update xong"

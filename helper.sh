#!/bin/sh
sudo chown -R user:usergroup /pal/Package/Pal/Saved

# Chạy PalServer làm tiến trình con (không exec) để bắt được exit code của nó.
# PalServer-Linux-Shipping có bug đã biết: hay SIGSEGV ngay trong lúc tự tắt,
# kể cả khi tắt êm qua REST /shutdown do admin-tool gọi. Nếu để nguyên exit
# code đó thoát ra ngoài, Docker sẽ coi MỌI lần restart từ admin-tool là
# "container chết bất thường" — dù thực chất là chủ động.
#
# Ngược lại, không thể cứ luôn exit 0: như vậy sẽ che mất các lần crash THẬT
# (sập giữa lúc chơi, OOM, crash loop...) mà mình cần biết để xử lý.
#
# Giải pháp: admin-tool.py ghi 1 file đánh dấu (INTENT_MARKER) ngay trước khi
# gọi REST /shutdown hoặc /stop. Ở đây, sau khi PalServer thoát, nếu thấy dấu
# đó còn MỚI (vài phút gần nhất) thì coi là chủ động -> thoát 0 (sạch với
# Docker). Nếu không thấy dấu, hoặc dấu đã cũ -> coi là bất thường thật ->
# trả đúng exit code gốc để Docker/monitoring vẫn cảnh báo như bình thường.
MARKER=/pal/Package/Pal/Saved/Config/LinuxServer/.pal_intentional_exit
MARKER_MAX_AGE_SEC=180

/bin/sh /pal/Package/PalServer.sh "$@" &
child=$!

term_handler() {
    # docker stop / docker compose down: luôn là chủ động, không cần xét marker
    kill -TERM "$child" 2>/dev/null
    wait "$child"
    rm -f "$MARKER" 2>/dev/null
    exit 0
}
trap term_handler TERM INT

wait "$child"
rc=$?

intentional=0
if [ -f "$MARKER" ]; then
    now=$(date +%s)
    mtime=$(stat -c %Y "$MARKER" 2>/dev/null || echo 0)
    age=$((now - mtime))
    if [ "$age" -ge 0 ] && [ "$age" -le "$MARKER_MAX_AGE_SEC" ]; then
        intentional=1
    fi
    rm -f "$MARKER" 2>/dev/null
fi

if [ "$intentional" = "1" ]; then
    echo "helper.sh: PalServer thoat (ma loi $rc) do admin-tool chu dong yeu cau -- coi la binh thuong."
    exit 0
else
    echo "helper.sh: PalServer thoat BAT THUONG voi ma loi $rc (khong phai do admin-tool yeu cau)."
    exit "$rc"
fi

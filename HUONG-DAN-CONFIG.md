# Hướng dẫn cấu hình Palworld Dedicated Server bằng Docker (image chính thức)

> Phân tích repo chính thức [pocketpairjp/palworld-dedicated-server-docker](https://github.com/pocketpairjp/palworld-dedicated-server-docker) và giải thích **tất cả thông số cấu hình**: tham số dòng lệnh + toàn bộ 119 thông số trong `PalWorldSettings.ini` (bản game 1.0).
>
> Cập nhật: 14/07/2026 — tương ứng Palworld **1.0** (update 1.100.427, phát hành 10/07/2026).

## Mục lục

1. [Tổng quan & phiên bản](#1-tổng-quan--phiên-bản)
2. [So sánh image chính thức vs image cộng đồng](#2-so-sánh-image-chính-thức-vs-image-cộng-đồng)
3. [Yêu cầu hệ thống](#3-yêu-cầu-hệ-thống)
4. [Phân tích cấu trúc repo & cách hoạt động](#4-phân-tích-cấu-trúc-repo--cách-hoạt-động)
5. [Cài đặt & vận hành cơ bản](#5-cài-đặt--vận-hành-cơ-bản)
6. [Tham số dòng lệnh (command-line arguments)](#6-tham-số-dòng-lệnh-command-line-arguments)
7. [File PalWorldSettings.ini — định dạng & vị trí](#7-file-palworldsettingsini--định-dạng--vị-trí)
8. [Giải thích TẤT CẢ thông số PalWorldSettings.ini](#8-giải-thích-tất-cả-thông-số-palworldsettingsini)
9. [Cấu hình mẫu khuyến nghị](#9-cấu-hình-mẫu-khuyến-nghị)
10. [Mạng, firewall & bảo mật](#10-mạng-firewall--bảo-mật)
11. [RCON & REST API](#11-rcon--rest-api)
12. [Cập nhật phiên bản & backup](#12-cập-nhật-phiên-bản--backup)
13. [Nguồn tham khảo](#13-nguồn-tham-khảo)

---

## 1. Tổng quan & phiên bản

Repo `pocketpairjp/palworld-dedicated-server-docker` là repo **chính thức của Pocketpair**, cung cấp:

- **Docker image** phân phối qua GitHub Packages: `ghcr.io/pocketpairjp/palserver`
- **File `compose.yaml` mẫu** + script `helper.sh`

### Phiên bản hiện tại

| Tag image | Ngày phát hành | Tương ứng bản game |
|---|---|---|
| **`v1.0.0.100427`** (mới nhất, = `latest`) | ~10/07/2026 | **Palworld 1.0** (update 1.100.427) |
| `v0.7.3.90464` | ~04/2026 | 0.7.3 (Early Access) |
| `v0.7.2.87654` | ~02/2026 | 0.7.2 |
| `v0.7.1.86065` | ~01/2026 | 0.7.1 |
| `v0.7.0.84578` | ~12/2025 | 0.7.0 |

**Quan trọng:** tag của image phải **khớp với phiên bản game của client**. Khi game ra bản mới, client tự update qua Steam nhưng server Docker thì **không** — bạn phải đổi tag image thủ công (xem [mục 12](#12-cập-nhật-phiên-bản--backup)). Danh sách tag mới nhất xem tại [GitHub Packages](https://github.com/pocketpairjp/palworld-dedicated-server-docker/pkgs/container/palserver).

## 2. So sánh image chính thức vs image cộng đồng

| Tiêu chí | `ghcr.io/pocketpairjp/palserver` (chính thức) | [thijsvanloef/palworld-server-docker](https://github.com/thijsvanloef/palworld-server-docker), [jammsen/docker-palworld-dedicated-server](https://github.com/jammsen/docker-palworld-dedicated-server) (cộng đồng) |
|---|---|---|
| Nguồn binary server | Đóng gói sẵn trong image, pin theo tag | Tải qua SteamCMD lúc khởi động |
| Cập nhật | Thủ công: đổi tag image | Tự động (auto-update khi restart) |
| Cấu hình qua biến môi trường | ❌ Không — sửa trực tiếp `PalWorldSettings.ini` | ✅ Có (ví dụ `EXP_RATE=2` → `ExpRate=2`) |
| Tính năng thêm (backup định kỳ, RCON CLI, webhook…) | ❌ | ✅ |
| Được Pocketpair hỗ trợ chính thức | ✅ | ❌ |

Không image nào có thể "mới hơn" bản game hiện tại — Pocketpair phát hành server và client cùng lúc; image cộng đồng chỉ khác ở chỗ **tự tải bản mới nhất** thay vì pin cứng phiên bản. Hướng dẫn này tập trung vào image **chính thức**.

## 3. Yêu cầu hệ thống

- **OS:** Linux khuyến nghị. Repo chính thức **khuyến cáo KHÔNG chạy bằng Docker Desktop trên Windows/macOS** vì tốc độ đọc/ghi đĩa bị giới hạn nghiêm trọng.
- **CPU:** 4 core trở lên.
- **RAM:** tối thiểu 16 GB khuyến nghị (server ngốn RAM tăng dần theo thời gian chạy và số người chơi; 32 GB cho server đông người).
- **Đĩa:** ~20 GB trống cho server + save data (SSD khuyến nghị).
- **Mạng:** mở được port UDP 8211 (và TCP 25575/8212 nếu dùng RCON/REST API).
- Đã cài **Docker Engine + Docker Compose v2** (`docker compose version`).

## 4. Phân tích cấu trúc repo & cách hoạt động

Cấu trúc repo gốc:

```
palworld-dedicated-server-docker/
├── .github/workflows/     # CI build image
├── compose/
│   ├── compose.yaml       # File compose mẫu
│   ├── helper.sh          # Script entrypoint
│   ├── Saved/             # Thư mục save data (mount vào container)
│   └── .gitignore
├── README.md              # Tài liệu tiếng Anh
└── README-JA.md           # Tài liệu tiếng Nhật
```

### compose.yaml gốc (nguyên văn)

```yaml
services:
  palworld-server:
    # https://github.com/pocketpairjp/palworld-dedicated-server-docker/pkgs/container/palserver
    image: ghcr.io/pocketpairjp/palserver:v1.0.0.100427
    entrypoint: /pal/helper.sh
    # https://tech.palworldgame.com/settings-and-operation/arguments
    command:
      - -port=8211
      - -useperfthreads
      - -NoAsyncLoadingThread
      - -UseMultithreadForDS
    ports:
      - "8211:8211/udp"
    volumes:
      - ./helper.sh:/pal/helper.sh:ro
      - ./Saved:/pal/Package/Pal/Saved
```

### helper.sh gốc (nguyên văn)

```sh
#!/bin/sh

sudo chown -R user:usergroup /pal/Package/Pal/Saved

exec /bin/sh /pal/Package/PalServer.sh "$@"
```

### Cách hoạt động

1. Container khởi động → chạy `helper.sh` (entrypoint, mount từ host dạng read-only).
2. `helper.sh` sửa quyền sở hữu thư mục `Saved` (vì volume mount từ host thường thuộc root) rồi `exec` script khởi động server `PalServer.sh`, truyền nguyên các tham số trong `command:`.
3. Server ghi toàn bộ save data + file cấu hình vào `/pal/Package/Pal/Saved` → chính là thư mục `./Saved` trên host. **Xóa container không mất dữ liệu**; muốn backup chỉ cần copy thư mục `./Saved`.
4. File cấu hình mặc định (chỉ để tham khảo, sửa **không có tác dụng**) nằm ở `/pal/Package/DefaultPalWorldSettings.ini` trong image.

## 5. Cài đặt & vận hành cơ bản

```bash
# 1. Tải 2 file compose.yaml + helper.sh (từ repo này hoặc repo gốc thư mục compose/)
mkdir palworld && cd palworld
# ... đặt compose.yaml, helper.sh vào đây
chmod +x helper.sh

# 2. Khởi động server (lần đầu sẽ pull image ~vài GB)
docker compose up -d

# 3. Xem log
docker compose logs -f

# 4. Dừng server
docker compose down
```

- Lần chạy đầu tiên, server tự sinh save data và file cấu hình trong `./Saved`.
- Xem file cấu hình mặc định trong image:
  ```bash
  docker compose exec palworld-server bash -c "cat /pal/Package/DefaultPalWorldSettings.ini"
  ```
- Sau lần chạy đầu, sửa cấu hình tại `./Saved/Config/LinuxServer/PalWorldSettings.ini` rồi **restart** (`docker compose restart` hoặc `down`/`up -d`) để áp dụng.

## 6. Tham số dòng lệnh (command-line arguments)

Khai báo trong mục `command:` của `compose.yaml`, được truyền thẳng cho `PalServer.sh`. Tài liệu chính thức: [tech.palworldgame.com/settings-and-operation/arguments](https://tech.palworldgame.com/settings-and-operation/arguments).

### Nhóm hiệu năng (repo gốc bật sẵn — nên giữ)

| Tham số | Ý nghĩa |
|---|---|
| `-useperfthreads` | Dùng luồng hiệu năng cao, cải thiện đáng kể FPS server |
| `-NoAsyncLoadingThread` | Tắt luồng async loading (ổn định hơn cho dedicated server) |
| `-UseMultithreadForDS` | Bật đa luồng cho dedicated server |

### Nhóm mạng & hiển thị

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `-port=8211` | 8211 | Port UDP server lắng nghe. Nếu đổi, phải đổi cả mapping `ports:` trong compose |
| `-publicip=x.x.x.x` | (tự phát hiện) | IP công khai hiển thị trên community server list — cần khi server sau NAT |
| `-publicport=8211` | =port | Port công khai hiển thị trên list (khi NAT/forward port khác port nội bộ) |
| `-publiclobby` | tắt | Đăng server lên **community server list** (server công khai, không cần nhập IP) |
| `-players=32` | 32 | Số người chơi tối đa (ghi đè `ServerPlayerMaxNum`) |
| `-serverName="Tên"` | | Ghi đè `ServerName` |
| `-serverDescription="..."` | | Ghi đè `ServerDescription` |
| `-serverPassword=...` | | Ghi đè `ServerPassword` |
| `-adminPassword=...` | | Ghi đè `AdminPassword` |

### Nhóm quản trị & log

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `-RCONEnabled=True` | False | Bật RCON (ghi đè file ini) |
| `-RCONPort=25575` | 25575 | Port RCON |
| `-logformat=json` | text | Định dạng log (`text` hoặc `json` — tiện cho log collector) |

> Lưu ý: tham số dòng lệnh **ghi đè** giá trị tương ứng trong `PalWorldSettings.ini`. Nên quản lý tập trung trong file ini, chỉ dùng command-line cho `-port` và 3 flag hiệu năng như repo gốc.

## 7. File PalWorldSettings.ini — định dạng & vị trí

- **Vị trí (trên host):** `./Saved/Config/LinuxServer/PalWorldSettings.ini`
- **Định dạng bắt buộc — chỉ 2 dòng:**

```ini
[/Script/Pal.PalGameWorldSettings]
OptionSettings=(Difficulty=None,DayTimeSpeedRate=1.000000,ExpRate=1.000000,...)
```

Quy tắc quan trọng:

1. Toàn bộ thông số nằm trên **một dòng duy nhất** `OptionSettings=(...)`, phân cách bằng dấu phẩy, **không xuống dòng, không khoảng trắng thừa**.
2. Chuỗi phải đặt trong nháy kép: `ServerName="Ten Server"`. Boolean viết `True`/`False`. Số thực viết dạng `1.000000`.
3. Nếu file bị sai cú pháp hoặc **để trống**, server âm thầm dùng giá trị mặc định — triệu chứng phổ biến "sửa config không ăn".
4. Cách an toàn nhất: copy nội dung từ `DefaultPalWorldSettings.ini` trong image (lệnh ở mục 5) rồi sửa từng giá trị.
5. Sửa xong phải **restart server** mới áp dụng.

> ⚠️ Với world đã tạo, một số thiết lập thế giới bị "đóng băng" trong `WorldOption.sav` (đặc biệt world tạo từ single-player). Server dedicated ưu tiên `PalWorldSettings.ini`, nhưng nếu bạn chép world từ chơi đơn sang mà config không ăn, hãy xóa file `WorldOption.sav` trong thư mục save của world (backup trước).

## 8. Giải thích TẤT CẢ thông số PalWorldSettings.ini

Dưới đây là **toàn bộ 119 thông số** của bản 1.0, chia theo nhóm. "Mặc định" là giá trị trong `DefaultPalWorldSettings.ini`; cột "Phạm vi" là khoảng giá trị UI game chấp nhận.

> 📌 Bản 1.0 đổi một số mặc định so với Early Access (theo ghi nhận cộng đồng): `PalEggDefaultHatchingTime` 72 → **1**, `DeathPenalty` All → **Item**, `bIsStartLocationSelectByMap` True → **False**. Hãy đối chiếu `DefaultPalWorldSettings.ini` trong image của đúng phiên bản bạn chạy.

### 8.1. Server & kết nối

| Thông số | Mặc định | Phạm vi | Giải thích |
|---|---|---|---|
| `ServerName` | `"Default Palworld Server"` | chuỗi | Tên server hiển thị trên danh sách |
| `ServerDescription` | `""` | chuỗi | Mô tả server |
| `ServerPassword` | `""` | chuỗi | Mật khẩu vào server (rỗng = ai cũng vào được) |
| `AdminPassword` | `""` | chuỗi | Mật khẩu admin (dùng cho lệnh `/AdminPassword`, RCON, REST API). **Bắt buộc đặt** nếu server công khai |
| `ServerPlayerMaxNum` | `32` | 1–512 | Số người chơi tối đa |
| `CoopPlayerMaxNum` | `4` | 1–4 | Số người tối đa trong phiên co-op (không áp dụng cho dedicated server) |
| `PublicIP` | `""` | chuỗi | IP công khai khai báo lên server list (để trống = tự phát hiện) |
| `PublicPort` | `8211` | 1024–65535 | Port công khai khai báo lên server list (không đổi port thực — port thực là `-port`) |
| `Region` | `""` | chuỗi | Nhãn khu vực hiển thị trên server list |
| `bUseAuth` | `True` | bool | Xác thực tài khoản khi kết nối. Luôn để `True` (tắt sẽ cho phép fake identity) |
| `BanListURL` | `"https://api.palworldgame.com/api/banlist.txt"` | URL | Nguồn danh sách ban toàn cục của Pocketpair; có thể trỏ tới file riêng của bạn |
| `bIsMultiplay` | `False` | bool | Bật chế độ multiplayer cho world (dedicated server tự xử lý — thường không cần đổi) |
| `CrossplayPlatforms` | `(Steam,Xbox,PS5,Mac)` | tổ hợp | Các nền tảng được phép kết nối (crossplay). Bỏ bớt phần tử để giới hạn, ví dụ `(Steam)` chỉ cho Steam |
| `bShowPlayerList` | `False` | bool | Hiện danh sách người chơi trên màn hình ESC của client |
| `bIsShowJoinLeftMessage` | `True` | bool | Hiện thông báo "đã vào/rời server" trong chat |
| `bAllowClientMod` | `True` | bool | Cho phép client dùng mod (bản 1.0). Tắt nếu muốn hạn chế gian lận |

### 8.2. RCON & REST API

| Thông số | Mặc định | Phạm vi | Giải thích |
|---|---|---|---|
| `RCONEnabled` | `False` | bool | Bật RCON (điều khiển từ xa qua giao thức Source RCON) |
| `RCONPort` | `25575` | port | Port TCP của RCON |
| `RESTAPIEnabled` | `False` | bool | Bật REST API quản trị (khuyên dùng thay RCON từ v0.23+) |
| `RESTAPIPort` | `8212` | port | Port TCP của REST API |

### 8.3. Độ khó & Randomizer

| Thông số | Mặc định | Phạm vi | Giải thích |
|---|---|---|---|
| `Difficulty` | `None` | None/Casual/Normal/Hard | Preset độ khó. `None` = tùy chỉnh (dùng các thông số bên dưới). Đặt preset sẽ ghi đè nhiều rate |
| `RandomizerType` | `None` | None/Region/All | Xáo trộn vị trí spawn Pal: `Region` trộn trong từng vùng, `All` trộn toàn map |
| `RandomizerSeed` | `""` | chuỗi | Seed cho randomizer (cùng seed = cùng kết quả trộn) |
| `bIsRandomizerPalLevelRandom` | `False` | bool | Random luôn cấp độ Pal khi bật randomizer |

### 8.4. Thời gian, kinh nghiệm & tài nguyên thế giới

| Thông số | Mặc định | Phạm vi | Giải thích |
|---|---|---|---|
| `DayTimeSpeedRate` | `1.000000` | 0.1–5 | Tốc độ trôi thời gian ban ngày (2 = ngày ngắn gấp đôi) |
| `NightTimeSpeedRate` | `1.000000` | 0.1–5 | Tốc độ trôi thời gian ban đêm |
| `ExpRate` | `1.000000` | 0–20 | Hệ số kinh nghiệm nhận được (người chơi + Pal) |
| `WorkSpeedRate` | `1.000000` | 0.1–5 | Tốc độ làm việc tại base (craft, xây…) |
| `PalEggDefaultHatchingTime` | `72.000000` (1.0: `1`) | 0–240 | Số **giờ thực** để ấp trứng lớn nhất (trứng nhỏ tỉ lệ theo). `0` = nở ngay |
| `SupplyDropSpan` | `180` | 0–1000 | Chu kỳ (phút) xuất hiện supply drop (thùng tiếp tế). `0` = tắt |
| `EnemyDropItemRate` | `1.000000` | 0.5–5 | Hệ số vật phẩm rơi từ quái |
| `CollectionDropRate` | `1.000000` | 0.5–5 | Hệ số vật phẩm thu thập được (chặt cây, đập đá…) |
| `CollectionObjectHpRate` | `1.000000` | 0.5–3 | Máu của vật thể thu thập (cây, đá…). Giảm để chặt nhanh hơn |
| `CollectionObjectRespawnSpeedRate` | `1.000000` | 0.5–5 | Tốc độ hồi sinh vật thể thu thập (cao = mọc lại nhanh) |
| `ItemWeightRate` | `1.000000` | 0.1–5 | Hệ số cân nặng vật phẩm (giảm để vác được nhiều hơn) |
| `ItemCorruptionMultiplier` | `1.000000` | 0.1–10 | Hệ số tốc độ hỏng/ôi thiu của thực phẩm (bản 1.0) |

### 8.5. Pal & chiến đấu

| Thông số | Mặc định | Phạm vi | Giải thích |
|---|---|---|---|
| `PalCaptureRate` | `1.000000` | 0.5–5 | Tỉ lệ bắt Pal thành công |
| `PalSpawnNumRate` | `1.000000` | 0.5–5 | Mật độ Pal spawn ngoài map (tăng = đông hơn, tốn CPU hơn) |
| `PalDamageRateAttack` | `1.000000` | 0.1–5 | Sát thương Pal **gây ra** |
| `PalDamageRateDefense` | `1.000000` | 0.1–5 | Sát thương Pal **nhận vào** (tăng = Pal "giòn" hơn) |
| `PalStomachDecreaceRate` | `1.000000` | 0.1–5 | Tốc độ đói của Pal |
| `PalStaminaDecreaceRate` | `1.000000` | 0.1–5 | Tốc độ tụt stamina của Pal |
| `PalAutoHPRegeneRate` | `1.000000` | 0.1–5 | Tốc độ hồi máu tự nhiên của Pal |
| `PalAutoHpRegeneRateInSleep` | `1.000000` | 0.1–5 | Tốc độ hồi máu của Pal khi ngủ (trong Palbox) |
| `bEnableInvaderEnemy` | `True` | bool | Bật/tắt các đợt **raid tấn công base** |
| `EnablePredatorBossPal` | `True` | bool | Bật/tắt Predator Pal (boss hung dữ ngoài map, bản 0.4+) |
| `bActiveUNKO` | `False` | bool | Bật "UNKO" (vật phẩm 💩 joke item) |
| `MonsterFarmActionSpeedRate` | `1.000000` | 0.1–5 | Tốc độ làm việc của Pal ở trang trại/ranch (bản 1.0) |

### 8.6. Người chơi & sinh tồn

| Thông số | Mặc định | Phạm vi | Giải thích |
|---|---|---|---|
| `PlayerDamageRateAttack` | `1.000000` | 0.1–5 | Sát thương người chơi gây ra |
| `PlayerDamageRateDefense` | `1.000000` | 0.1–5 | Sát thương người chơi nhận vào |
| `PlayerStomachDecreaceRate` | `1.000000` | 0.1–5 | Tốc độ đói của người chơi |
| `PlayerStaminaDecreaceRate` | `1.000000` | 0.1–5 | Tốc độ tụt stamina người chơi |
| `PlayerAutoHPRegeneRate` | `1.000000` | 0.1–5 | Tốc độ hồi máu tự nhiên |
| `PlayerAutoHpRegeneRateInSleep` | `1.000000` | 0.1–5 | Tốc độ hồi máu khi ngủ |
| `DeathPenalty` | `All` (1.0: `Item`) | None/Item/ItemAndEquipment/All | Hình phạt khi chết: `None` không rơi gì; `Item` rơi đồ trong túi (trừ trang bị); `ItemAndEquipment` rơi cả trang bị; `All` rơi cả Pal trong túi |
| `bEnableNonLoginPenalty` | `True` | bool | Phạt người không đăng nhập lâu ngày (độ bền/thối đồ vẫn tính) |
| `bEnableFastTravel` | `True` | bool | Cho phép dịch chuyển nhanh qua Great Eagle Statue |
| `bEnableFastTravelOnlyBaseCamp` | `False` | bool | Chỉ cho fast-travel tới base camp (bản 1.0, tăng độ khó) |
| `bIsStartLocationSelectByMap` | `True` (1.0: `False`) | bool | Cho người mới chọn điểm xuất phát trên map (tắt = spawn cố định) |
| `bExistPlayerAfterLogout` | `False` | bool | Nhân vật **vẫn đứng trong world sau khi thoát game** (có thể bị giết/cướp đồ — chỉ nên bật server hardcore PvP) |
| `bEnableAimAssistPad` | `True` | bool | Hỗ trợ ngắm cho tay cầm |
| `bEnableAimAssistKeyboard` | `False` | bool | Hỗ trợ ngắm cho chuột + bàn phím |
| `EquipmentDurabilityDamageRate` | `1.000000` | 0.1–5 | Tốc độ hao độ bền trang bị |
| `BlockRespawnTime` | `5.000000` | 0–60 | Thời gian (giây) chặn respawn sau khi chết |
| `RespawnPenaltyDurationThreshold` | `0.000000` | 0–3600 | Ngưỡng (giây) kích hoạt phạt respawn khi chết liên tục (`0` = tắt) |
| `RespawnPenaltyTimeScale` | `2.000000` | 0–10 | Hệ số nhân thời gian chờ respawn mỗi lần chết liên tiếp |
| `bAllowEnhanceStat_Health` | `True` | bool | Cho phép cộng điểm chỉ số HP khi lên cấp |
| `bAllowEnhanceStat_Attack` | `True` | bool | Cho phép cộng điểm Attack |
| `bAllowEnhanceStat_Stamina` | `True` | bool | Cho phép cộng điểm Stamina |
| `bAllowEnhanceStat_Weight` | `True` | bool | Cho phép cộng điểm Weight (sức vác) |
| `bAllowEnhanceStat_WorkSpeed` | `True` | bool | Cho phép cộng điểm Work Speed |

### 8.7. Xây dựng & vật phẩm rơi trên đất

| Thông số | Mặc định | Phạm vi | Giải thích |
|---|---|---|---|
| `BuildObjectHpRate` | `1.000000` | 0.5–5 | Máu công trình xây dựng |
| `BuildObjectDamageRate` | `1.000000` | 0.5–3 | Sát thương công trình **nhận vào** (tăng = dễ phá) |
| `BuildObjectDeteriorationDamageRate` | `1.000000` | 0–10 | Tốc độ mục nát tự nhiên của công trình ngoài vùng base (`0` = không mục) |
| `bBuildAreaLimit` | `False` | bool | Cấm xây sát các vị trí quan trọng (fast-travel, boss tower…) |
| `MaxBuildingLimitNum` | `0` | 0–8 | Giới hạn số công trình **mỗi người chơi** (`0` = không giới hạn) |
| `DropItemMaxNum` | `3000` | 0–10000 | Số vật phẩm rơi tối đa tồn tại trong world (quá nhiều gây lag) |
| `DropItemMaxNum_UNKO` | `100` | 0–5000 | Giới hạn riêng cho UNKO |
| `DropItemAliveMaxHours` | `1.000000` | 0–240 | Số giờ vật phẩm rơi tồn tại trước khi biến mất |
| `PhysicsActiveDropItemMaxNum` | `-1` | -1–10000 | Số vật phẩm rơi có mô phỏng vật lý cùng lúc (`-1` = không giới hạn; đặt thấp để giảm lag) |
| `bEnableBuildingPlayerUIdDisplay` | `False` | bool | Hiện tên người xây trên công trình (tiện quản trị server đông) |
| `BuildingNameDisplayCacheTTLSeconds` | `60` | 1–3600 | TTL (giây) cache tên người xây hiển thị |

### 8.8. Base camp & Guild

| Thông số | Mặc định | Phạm vi | Giải thích |
|---|---|---|---|
| `BaseCampMaxNum` | `128` | 0–10240 | Tổng số base camp tối đa **toàn server** |
| `BaseCampMaxNumInGuild` | `3` | 1–50 | Số base camp tối đa **mỗi guild** (tăng cẩn thận — ảnh hưởng hiệu năng mạnh) |
| `BaseCampWorkerMaxNum` | `15` | 1–50 | Số Pal làm việc tối đa mỗi base (tăng = nặng server đáng kể) |
| `GuildPlayerMaxNum` | `20` | 1–100 | Số thành viên tối đa mỗi guild |
| `bAutoResetGuildNoOnlinePlayers` | `False` | bool | Tự **xóa guild** khi không ai online quá thời gian dưới đây (⚠️ mất base + đồ của guild đó) |
| `AutoResetGuildTimeNoOnlinePlayers` | `72.000000` | 0–240 | Số giờ không online trước khi guild bị reset |
| `GuildRejoinCooldownMinutes` | `0` | 0–1440 | Thời gian chờ (phút) trước khi được gia nhập lại guild vừa rời (chống lách luật PvP) |
| `AutoTransferMasterCheckIntervalSeconds` | `3600.000000` | 60–86400 | Chu kỳ (giây) kiểm tra guild master vắng mặt (bản 1.0) |
| `AutoTransferMasterThresholdDays` | `14` | 1–365 | Số ngày guild master không online thì tự chuyển quyền cho thành viên khác (bản 1.0) |
| `bInvisibleOtherGuildBaseCampAreaFX` | `False` | bool | Ẩn hiệu ứng vòng sáng vùng base của guild khác |
| `bEnableDefenseOtherGuildPlayer` | `False` | bool | Pal ở base tấn công người chơi guild khác đi vào vùng base |
| `MaxGuildsPerFrame` | `10` | 1–100 | Số guild xử lý mỗi frame (thông số hiệu năng — giữ mặc định) |

### 8.9. PvP & Hardcore

| Thông số | Mặc định | Phạm vi | Giải thích |
|---|---|---|---|
| `bIsPvP` | `False` | bool | Bật chế độ PvP toàn server |
| `bEnablePlayerToPlayerDamage` | `False` | bool | Người chơi gây sát thương lẫn nhau (kể cả ngoài chế độ PvP) |
| `bEnableFriendlyFire` | `False` | bool | Sát thương đồng đội (cùng guild) |
| `bCanPickupOtherGuildDeathPenaltyDrop` | `False` | bool | Cho phép nhặt đồ rơi khi chết của người thuộc guild khác |
| `bHardcore` | `False` | bool | Chế độ hardcore: **chết là mất nhân vật** |
| `bPalLost` | `False` | bool | Pal chết là mất vĩnh viễn (permadeath cho Pal) |
| `bCharacterRecreateInHardcore` | `False` | bool | Cho phép tạo lại nhân vật mới sau khi chết ở hardcore |
| `bDisplayPvPItemNumOnWorldMap_BaseCamp` | `False` | bool | (PvP) Hiện số vật phẩm tại base camp lên world map |
| `bDisplayPvPItemNumOnWorldMap_Player` | `False` | bool | (PvP) Hiện số vật phẩm người chơi mang theo lên world map |
| `bAdditionalDropItemWhenPlayerKillingInPvPMode` | `False` | bool | (PvP) Rơi thêm vật phẩm đặc biệt khi giết người chơi |
| `AdditionalDropItemWhenPlayerKillingInPvPMode` | `PlayerDropItem` | ID vật phẩm | (PvP) Loại vật phẩm rơi thêm khi giết người |
| `AdditionalDropItemNumWhenPlayerKillingInPvPMode` | `1` | 0–100 | (PvP) Số lượng vật phẩm rơi thêm |

### 8.10. Chat & Voice chat

| Thông số | Mặc định | Phạm vi | Giải thích |
|---|---|---|---|
| `ChatPostLimitPerMinute` | `10` | 0–100 | Giới hạn số tin chat mỗi phút mỗi người (chống spam) |
| `bEnableVoiceChat` | `False` | bool | Bật **proximity voice chat** tích hợp (bản 1.0) |
| `VoiceChatMaxVolumeDistance` | `3000.000000` | 100–50000 | Khoảng cách (cm) còn nghe rõ 100% âm lượng |
| `VoiceChatZeroVolumeDistance` | `15000.000000` | 100–50000 | Khoảng cách (cm) âm lượng về 0 (ngoài tầm không nghe) |

### 8.11. Lưu trữ, log & hiệu năng hệ thống

| Thông số | Mặc định | Phạm vi | Giải thích |
|---|---|---|---|
| `AutoSaveSpan` | `30.000000` | 30–3600 | Chu kỳ auto-save (giây) |
| `bIsUseBackupSaveData` | `True` | bool | Tự tạo bản backup save (thư mục `Saved/SaveGames/.../backup`) |
| `LogFormatType` | `Text` | Text/Json | Định dạng log server |
| `ServerReplicatePawnCullDistance` | `15000.000000` | 5000–15000 | Khoảng cách (cm) server đồng bộ pawn (Pal/người) tới client. Giảm để nhẹ mạng/CPU, đổi lại Pal "hiện muộn" khi lại gần |
| `ItemContainerForceMarkDirtyInterval` | `1.000000` | 0.1–10 | Chu kỳ (giây) đánh dấu đồng bộ container đồ — thông số hiệu năng, giữ mặc định |
| `PlayerDataPalStorageUpdateCheckTickInterval` | `1.000000` | 0.1–60 | Chu kỳ (giây) kiểm tra cập nhật Pal storage của người chơi (bản 1.0) |
| `bAllowGlobalPalboxExport` | `True` | bool | Cho phép **xuất** Pal lên Global Palbox (chuyển Pal giữa các server/world) |
| `bAllowGlobalPalboxImport` | `False` | bool | Cho phép **nhập** Pal từ Global Palbox vào server (tắt mặc định để tránh tuồn Pal "ngoài luồng") |
| `DenyTechnologyList` | `""` | danh sách ID | Danh sách công nghệ bị **cấm mở khóa** trên server, phân cách phẩy (ví dụ cấm lồng bắt người: `"HumanCage"`) |

## 9. Cấu hình mẫu khuyến nghị

Chỉ liệt kê các key khác mặc định — các key còn lại giữ nguyên trong dòng `OptionSettings`.

### 9.1. Server co-op bạn bè (nhẹ nhàng, đỡ cày)

```
Difficulty=None, ExpRate=2.000000, PalCaptureRate=1.500000,
WorkSpeedRate=1.500000, PalEggDefaultHatchingTime=1.000000,
DeathPenalty=None, CollectionDropRate=2.000000, EnemyDropItemRate=2.000000,
ServerPassword="mat-khau-nhom", ServerPlayerMaxNum=8,
bEnableInvaderEnemy=True, AutoSaveSpan=30.000000
```

### 9.2. Server public PvE

```
ServerName="Ten Server Cua Ban", ServerDescription="Server PvE Viet Nam",
AdminPassword="mat-khau-admin-manh", ServerPlayerMaxNum=32,
RESTAPIEnabled=True, bShowPlayerList=True, ChatPostLimitPerMinute=10,
bAutoResetGuildNoOnlinePlayers=True, AutoResetGuildTimeNoOnlinePlayers=240.000000,
DropItemMaxNum=3000, DropItemAliveMaxHours=1.000000, bBuildAreaLimit=True,
bIsUseBackupSaveData=True
```

Thêm `-publiclobby` vào `command:` nếu muốn lên community server list (kèm `-publicip` nếu sau NAT).

### 9.3. Server PvP / Hardcore

```
bIsPvP=True, bEnablePlayerToPlayerDamage=True,
bCanPickupOtherGuildDeathPenaltyDrop=True, DeathPenalty=All,
bHardcore=True, bCharacterRecreateInHardcore=True, bPalLost=False,
bEnableDefenseOtherGuildPlayer=True, GuildRejoinCooldownMinutes=60,
bDisplayPvPItemNumOnWorldMap_Player=True,
bAdditionalDropItemWhenPlayerKillingInPvPMode=True
```

### 9.4. Tối ưu server yếu / đông người

```
PalSpawnNumRate=1.000000 (không tăng!), DropItemMaxNum=1000,
PhysicsActiveDropItemMaxNum=200, DropItemAliveMaxHours=0.500000,
ServerReplicatePawnCullDistance=10000.000000,
BaseCampMaxNumInGuild=3, BaseCampWorkerMaxNum=15 (không tăng!),
AutoSaveSpan=60.000000, bInvisibleOtherGuildBaseCampAreaFX=True
```

## 10. Mạng, firewall & bảo mật

### Port cần mở

| Port | Giao thức | Mục đích | Mở ra Internet? |
|---|---|---|---|
| 8211 | **UDP** | Game traffic | ✅ Có |
| 25575 | TCP | RCON | ❌ **Không** — chỉ LAN/localhost |
| 8212 | TCP | REST API | ❌ **Không** — chỉ LAN/localhost |

```bash
# ufw (Ubuntu)
sudo ufw allow 8211/udp
```

Trong `compose.yaml`, nếu bật RCON/REST API, hãy bind vào localhost để không lộ ra ngoài:

```yaml
    ports:
      - "8211:8211/udp"
      - "127.0.0.1:25575:25575/tcp"   # RCON chỉ truy cập từ chính máy host
      - "127.0.0.1:8212:8212/tcp"     # REST API chỉ truy cập từ chính máy host
```

### Checklist bảo mật

- ✅ Đặt `AdminPassword` mạnh (RCON/REST API dùng mật khẩu này).
- ✅ Đặt `ServerPassword` nếu không muốn người lạ vào (khi không dùng `-publiclobby`).
- ✅ Giữ `bUseAuth=True`.
- ❌ Không publish port 25575/8212 ra Internet; cần quản trị từ xa thì dùng SSH tunnel/VPN.
- ✅ Bật `bIsUseBackupSaveData=True` + tự backup thư mục `./Saved` định kỳ (cron + rsync/tar).

## 11. RCON & REST API

### RCON

Bật `RCONEnabled=True`, đặt `AdminPassword`. Dùng client như `rcon-cli`:

```bash
rcon -a 127.0.0.1:25575 -p "mat-khau-admin" ShowPlayers
```

Lệnh phổ biến: `Info`, `ShowPlayers`, `Broadcast <msg>`, `KickPlayer <steamid>`, `BanPlayer <steamid>`, `Save`, `Shutdown <giây> <msg>`, `DoExit`.
Lưu ý RCON của Palworld không hỗ trợ tốt tin nhắn có dấu cách/unicode — REST API xử lý tốt hơn.

### REST API (khuyên dùng)

Bật `RESTAPIEnabled=True`. Xác thực HTTP Basic: user `admin`, password = `AdminPassword`.

```bash
# Thông tin server
curl -u admin:matkhau http://127.0.0.1:8212/v1/api/info
# Danh sách người chơi
curl -u admin:matkhau http://127.0.0.1:8212/v1/api/players
# Thông báo toàn server
curl -u admin:matkhau -X POST http://127.0.0.1:8212/v1/api/announce -H "Content-Type: application/json" -d '{"message":"Server bao tri sau 10 phut"}'
# Lưu game
curl -u admin:matkhau -X POST http://127.0.0.1:8212/v1/api/save
# Tắt server êm (60s, có thông báo)
curl -u admin:matkhau -X POST http://127.0.0.1:8212/v1/api/shutdown -H "Content-Type: application/json" -d '{"waittime":60,"message":"Server restart sau 60s"}'
```

Các endpoint khác: `/v1/api/settings`, `/v1/api/metrics`, `/v1/api/kick`, `/v1/api/ban`, `/v1/api/unban`, `/v1/api/stop`.

## 12. Cập nhật phiên bản & backup

Khi Palworld ra bản mới (client trên Steam tự update), server sẽ báo lỗi version mismatch cho tới khi bạn cập nhật image:

```bash
# 1. LUÔN backup trước khi update
docker compose down
tar czf palworld-backup-$(date +%Y%m%d).tar.gz Saved/

# 2. Xem tag mới nhất tại:
#    https://github.com/pocketpairjp/palworld-dedicated-server-docker/pkgs/container/palserver
#    rồi sửa dòng image: trong compose.yaml, ví dụ:
#    image: ghcr.io/pocketpairjp/palserver:v1.0.1.xxxxxx

# 3. Kéo image mới và khởi động lại
docker compose pull
docker compose up -d
docker compose logs -f   # kiểm tra khởi động OK
```

Khôi phục backup: `docker compose down` → giải nén đè thư mục `Saved/` → `docker compose up -d`.

> Có thể dùng tag `latest` để đỡ sửa file, nhưng **không khuyến nghị** cho server nghiêm túc: bạn sẽ không kiểm soát thời điểm update và dễ lệch version với save/mod.

### Tự động cập nhật (auto-update)

Image chính thức pin phiên bản trong tag nên **restart container KHÔNG tự lấy bản mới**. Có 3 cách tự động hóa:

#### Cách 1 — Tag `latest` + cron script (vẫn dùng image chính thức, khuyến nghị)

Đổi `image:` thành `ghcr.io/pocketpairjp/palserver:latest`, rồi tạo script `update.sh` cạnh `compose.yaml`:

```sh
#!/bin/sh
# update.sh — tự update Palworld server, downtime tối thiểu
set -e
cd "$(dirname "$0")"

# 1. Kéo image mới TRONG LÚC server vẫn đang chạy bản cũ
#    (bước tải nặng nhất diễn ra khi server còn sống => giảm downtime)
docker compose pull -q

# 2. So sánh image đang chạy với image vừa kéo — không có bản mới thì thoát,
#    server không bị restart oan
RUNNING=$(docker inspect --format '{{.Image}}' palworld-server)
LATEST=$(docker image inspect --format '{{.Id}}' ghcr.io/pocketpairjp/palserver:latest)
[ "$RUNNING" = "$LATEST" ] && exit 0

# 3. (Tùy chọn, cần RESTAPIEnabled=True) báo trước cho người chơi + ép save
# curl -su admin:MATKHAU -X POST http://127.0.0.1:8212/v1/api/announce \
#   -H "Content-Type: application/json" -d '{"message":"Server update sau 5 phut!"}'
# sleep 300
# curl -su admin:MATKHAU -X POST http://127.0.0.1:8212/v1/api/save

# 4. Dừng, backup, khởi động lại bằng image mới (đã có sẵn trên máy => lên ngay)
docker compose down
tar czf "backup-$(date +%Y%m%d-%H%M).tar.gz" Saved/
docker compose up -d
```

```bash
chmod +x update.sh
# Cron 5h sáng mỗi ngày:
echo '0 5 * * * root /path/palworld/update.sh >> /var/log/palworld-update.log 2>&1' | sudo tee /etc/cron.d/palworld-update
```

Điểm mấu chốt giảm downtime: **`pull` trước khi `down`** — image mới được tải về khi server còn chạy, nên thời gian chết chỉ còn đúng khoảng dừng + backup + khởi động (thường < 1–2 phút), thay vì phải chờ tải vài GB.

#### Cách 2 — Watchtower (tự động hoàn toàn)

Watchtower theo dõi registry, tự pull + recreate container khi tag `latest` có bản mới:

```yaml
  watchtower:
    image: containrrr/watchtower
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    command: --scope palworld --cleanup --interval 3600
  # thêm vào service palworld-server:
  #   labels: ["com.centurylinklabs.watchtower.scope=palworld"]
```

⚠️ Watchtower restart "thô": không cảnh báo người chơi, không backup. Giữ `stop_grace_period: 30s` trở lên để server kịp save.

#### Cách 3 — Image cộng đồng (restart là có bản mới nhất)

[thijsvanloef/palworld-server-docker](https://github.com/thijsvanloef/palworld-server-docker) tải server qua SteamCMD mỗi lần khởi động: `UPDATE_ON_BOOT=true` (restart = bản mới nhất), `AUTO_UPDATE_ENABLED=true` + `AUTO_UPDATE_CRON_EXPRESSION` (update theo lịch), `AUTO_UPDATE_WARN_MINUTES=30` (báo trước người chơi), kèm backup tự động. Đổi lại: không phải image chính thức của Pocketpair.

## 13. Nguồn tham khảo

- Repo chính thức: https://github.com/pocketpairjp/palworld-dedicated-server-docker
- Image: https://github.com/pocketpairjp/palworld-dedicated-server-docker/pkgs/container/palserver
- Tài liệu server chính thức: https://tech.palworldgame.com/ (bản mới: https://docs.palworldgame.com/)
  - Tham số dòng lệnh: https://tech.palworldgame.com/settings-and-operation/arguments
  - Thông số cấu hình: https://docs.palworldgame.com/settings-and-operation/configuration/
- Wiki cộng đồng về PalWorldSettings.ini: https://palworld.wiki.gg/wiki/PalWorldSettings.ini
- Công cụ sinh file cấu hình trực quan: https://pal-conf.bluefissure.com/
- Image cộng đồng (tự update qua SteamCMD): https://github.com/thijsvanloef/palworld-server-docker , https://github.com/jammsen/docker-palworld-dedicated-server

# LAN Remote — 區域網路遠端桌面（AnyDesk 風格）

在同一個 WiFi / 區域網路內，用瀏覽器就能操作另一台電腦：看畫面、控制滑鼠鍵盤、傳檔案、聊天、關機…
**Windows 與 macOS 都可以當「被控端」**；控制端只要有瀏覽器（電腦、手機、平板都行），不用裝任何東西。

```
┌─────────────┐   WiFi / 區網   ┌──────────────────────┐
│  控制端     │ ───────────────▶ │  被控端（裝 LAN Remote）│
│  任何瀏覽器 │  http://IP:7788  │  Windows / macOS      │
└─────────────┘                 └──────────────────────┘
```

## 功能

| 類別 | 功能 |
|---|---|
| 畫面 | 即時串流（只傳有變化的區域，省頻寬）、多螢幕切換、畫質 / FPS / 解析度調整、全螢幕、顯示對方游標、截圖存檔、暫停串流 |
| 控制 | 滑鼠移動 / 左中右鍵 / 拖曳 / 滾輪、鍵盤直接打字、**中文長文字輸入**（Unicode 直接送，不用切輸入法）、特殊組合鍵（Alt+Tab、Win、Cmd+Tab、Alt+F4、Ctrl+Shift+Esc…） |
| 檔案 | 瀏覽對方電腦的檔案、上傳（可多檔、顯示進度）、下載（資料夾自動打包 zip）、拖曳檔案到畫面上直接傳送、貼上截圖直接傳送、新增資料夾 / 重新命名 / 刪除、在對方電腦上直接開啟檔案 |
| 剪貼簿 | 雙向同步文字剪貼簿 |
| 溝通 | 文字聊天（被控端視窗也能回話） |
| 系統 | CPU / 記憶體 / 磁碟 / 電池資訊、鎖定 / 睡眠 / 登出 / 重開機 / 關機、遠端執行指令（需被控端開啟）、Wake-on-LAN 喚醒 |
| 探索 | 同一區網內的 LAN Remote 電腦自動列出，一鍵連線，不用記 IP |
| 安全 | 6 位數 PIN 碼（可自訂）、連線前「允許 / 拒絕」確認、唯讀模式、PIN 錯 5 次鎖 1 分鐘、連線 / 傳檔 / 關機等操作都有紀錄 |
| 手機 | 手機 / 平板瀏覽器也能操作：單指移動、點一下 = 左鍵、長按 = 右鍵、雙指 = 滾動 |

## 安裝（被控端，每台要被操作的電腦都裝）

需要 Python 3.9 以上。

### Windows
1. 安裝 Python：<https://www.python.org/downloads/>（安裝時勾選 **Add python.exe to PATH**）
2. 把 `lan-remote` 整個資料夾複製到電腦上，雙擊 **`install.bat`**（會自動建立環境、安裝套件、新增防火牆規則）
3. 雙擊 **`start.bat`** 啟動（`start-hidden.vbs` 可以不顯示黑色視窗）
4. 第一次啟動若 Windows 防火牆跳出詢問，請按「允許存取」

### macOS
1. 打開「終端機」執行 `xcode-select --install`（或到 python.org 安裝 Python 3）
2. 把 `lan-remote` 資料夾複製到 Mac 上，雙擊 **`install.command`**
   （若顯示無法開啟：對檔案按右鍵 → 打開；或在終端機執行 `chmod +x *.command`）
3. 雙擊 **`start.command`** 啟動
4. **授權（只需一次）**：系統設定 → 隱私權與安全性 →
   - **螢幕錄製**：加入「終端機」
   - **輔助使用**：加入「終端機」
   沒授權的話對方會看到黑畫面或無法控制滑鼠。授權後重新啟動 LAN Remote。

### 打包成單一執行檔（不用對方裝 Python）
```bash
python -m pip install pyinstaller
python build_exe.py        # 產生 dist/LanRemote.exe（Windows）或 dist/LanRemote.app（Mac）
```
把 `dist/` 裡的檔案複製到其他電腦即可直接執行。

## 使用

1. 被控端啟動後會跳出一個小視窗，顯示 **連線網址**（例如 `http://192.168.1.23:7788/`）和 **PIN 碼**
2. 在控制端的瀏覽器輸入那個網址（Chrome / Edge 效果最好）
3. 輸入 PIN 碼 → 連線
4. 直接在畫面上操作即可；上方工具列有「文字、按鍵、剪貼簿、檔案、聊天、系統、裝置、設定」

如果兩台電腦都裝了 LAN Remote，登入畫面下方「區網上的其他電腦」會自動列出對方，直接按「連線」。

### 命令列參數
```
python -m lanremote [--port 7788] [--pin 123456] [--name 客廳電腦]
                    [--no-gui] [--require-accept] [--view-only] [--allow-shell]
                    [--receive-dir 路徑] [--fs-root 路徑] [--reset-pin] [--demo]
```
設定會存在 `~/.lanremote/config.json`（PIN 碼固定不變，重開也一樣，像 AnyDesk 的密碼）。

| 參數 | 說明 |
|---|---|
| `--require-accept` | 每次有人連線，被控端都要按「允許」 |
| `--view-only` | 只能看畫面，不能操作 |
| `--allow-shell` | 允許在「系統」面板遠端執行指令（預設關閉） |
| `--fs-root` | 檔案功能只能存取這個資料夾 |
| `--no-gui` | 沒有桌面環境（例如伺服器）時用純終端機模式 |
| `--demo` | 示範 / 測試模式：假畫面，不真的控制電腦 |

## 專案結構
```
lan-remote/
├── lanremote/
│   ├── agent.py       進入點、設定檔、命令列
│   ├── server.py      aiohttp：登入、WebSocket 畫面串流 + 輸入、檔案 API、系統 API
│   ├── capture.py     螢幕擷取（mss）+ 變動區域 JPEG 編碼
│   ├── control.py     滑鼠鍵盤注入（pynput）、瀏覽器按鍵對應
│   ├── netutil.py     IP / UDP 廣播探索 / Wake-on-LAN
│   ├── sysactions.py  剪貼簿、電源、系統資訊、指令
│   ├── gui.py         被控端的 Tk 狀態視窗
│   └── static/        瀏覽器端（index.html / app.js / style.css）
├── install.bat / start.bat / start-hidden.vbs   Windows
├── install.command / start.command             macOS / Linux
├── build_exe.py                                 打包成執行檔
└── requirements.txt
```

### 通訊方式
- HTTP `7788`：網頁、API、檔案傳輸
- WebSocket `/ws`：畫面（二進位：4 bytes 標頭長度 + JSON 標頭 + JPEG）與輸入事件（JSON）
- UDP `7789` 廣播：每 3 秒廣播自己的名稱 / IP / 埠號，讓同網段電腦互相找到

## 常見問題

- **連不上**：確認兩台在同一個 WiFi；被控端防火牆要允許 7788（Windows 第一次啟動會詢問）。有些公司 / 學校 WiFi 開了「AP 隔離」，裝置之間無法互連，需換網路或用網路線。
- **區網裝置列表是空的**：UDP 廣播被路由器擋住（AP 隔離 / 訪客網路），直接手動輸入 `http://IP:7788/` 即可。
- **Mac 黑畫面 / 無法控制**：沒給「螢幕錄製」「輔助使用」權限，見上方安裝步驟。
- **Ctrl+Alt+Del**：Windows 不允許軟體模擬，請改用「Ctrl+Shift+Esc」開工作管理員，或 Win+L 鎖定。
- **瀏覽器快捷鍵被吃掉**（Ctrl+W、Cmd+Q…）：按「全螢幕」後 Chrome / Edge 會把大部分按鍵交給網頁；其餘請用「按鍵」選單。
- **Linux 被控端**：目前支援 X11，Wayland 下 mss / pynput 無法擷取與控制。
- **埠號衝突**：`--port 8000` 換一個。

## 延伸功能構想（尚未實作，可依需求加）

**畫質 / 效能**
- 改用 H.264 / VP9 影像串流（WebRTC 或 ffmpeg 硬體編碼）：動態畫面流暢度與頻寬比 JPEG 好很多
- 擷取真正的游標圖案（箭頭 / I 字 / 手指）
- 自動依網路狀況調整畫質 / FPS

**操作**
- 遠端音訊傳輸（聽到對方電腦的聲音）
- 麥克風語音通話
- 白板 / 標註：在對方畫面上畫箭頭、圈重點，方便遠端教學
- 畫面錄影成 mp4
- 手機端虛擬鍵盤、虛擬搖桿、手勢縮放
- 多人同時連線並顯示每個人的游標（協作模式）
- 剪貼簿同步圖片 / 檔案（不只文字）
- 列印導向：把對方的列印工作印到自己這邊的印表機

**管理 / 便利性**
- 系統匣常駐圖示（pystray）+ 開機自動啟動一鍵設定
- 固定裝置 ID + 通訊錄、記住上次連線
- 無人值守模式 vs 每次都要同意（已有）+ 連線時在被控端畫面顯示「某某正在控制」浮水印
- 隱私模式：控制時把被控端螢幕關掉 / 變黑
- 工作階段逾時自動斷線、閒置自動鎖定
- 把連線紀錄寄 Email / 傳 LINE 通知
- 遠端重啟後自動重新連線（Windows 服務 / macOS LaunchAgent 常駐）

**安全**
- HTTPS / WSS（自簽憑證）加密所有流量
- TOTP 二次驗證
- 每台控制端用獨立帳號與權限（例如只能傳檔不能控制）
- 一次性 PIN（每次連線自動更換）

**跨網段**
- 透過自架中繼伺服器（Relay）或 Tailscale / ZeroTier / WireGuard 在不同網路間使用
- mDNS / Bonjour 探索（`lanremote.local`），不依賴 UDP 廣播

**其他**
- 遠端終端機（xterm.js + pty），不只單行指令
- 遠端程序管理（列出 / 結束程式）
- 檔案同步資料夾（像 Dropbox 區網版）
- 多台電腦同時監看的牆面模式（教室 / 機房）

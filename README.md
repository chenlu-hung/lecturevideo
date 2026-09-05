# lecture-video-generator

一個自包含（self-contained）的 Claude Code agent skill：給定一個主題，自動產生完整的教學影片。

從**主題**出發，依序產生：

1. **教學大綱**（`outline.md`，可與使用者來回討論修訂）
2. **Marp 投影片**（`slides.md` → `slides.html` + `slides.pdf` + 每頁 PNG）
3. **逐頁 SRT 上課講稿**（多個 sub-agents 並行產生，含精確時間軸）
4. **HTML 教學影片**（reveal.js 風格的自動播放器，依講稿時間切換投影片並淡入/淡出投影片上的 overlay 標註）

整個流程完全在本地端執行，不依賴任何外部 plugin 或 skill。

---

## 為什麼要做這個 skill？

備課最花時間的不是寫投影片，而是把投影片內容**講出來**——把每張投影片該怎麼引入、舉例、過渡、總結，逐句寫成講稿。這個 skill 把這件事自動化：

- 你只要給一個主題，剩下的「大綱 → 投影片 → 講稿 → 影片」都可以一條龍跑完。
- 講稿是用「上課口吻」寫的（親切、詳細、有舉例、有過渡語），不是把投影片照唸。
- 講稿是 SRT 格式 + 精確時間，未來接 TTS（語音合成）就直接可以變成有配音的影片。
- 投影片上的 overlay（重點標註）出現/消失時間，會自動依講稿語意推導，不用手動對時間。

---

## 安裝

把整個 repo clone 或 symlink 到 Claude Code 的 skills 目錄：

```bash
# 全域使用（推薦）
git clone https://github.com/chenlu-hung/lecturevideo.git ~/.claude/skills/lecture-video-generator

# 或 symlink 到目前的開發目錄，邊改邊用
ln -s "$PWD" ~/.claude/skills/lecture-video-generator
```

下次開啟 Claude Code 時，這個 skill 會自動被偵測到。

### 必要條件

- `node` + `npx`（給 `@marp-team/marp-cli` 使用，第一次跑會自動安裝）
- `python3`（≥ 3.8，僅用標準函式庫，不需 `pip install`）
- 任何現代瀏覽器（用來打開最終的 `video/index.html`）

---

## 使用方式

在 Claude Code 中直接用自然語言觸發：

```
幫我做一個關於「反向傳播演算法」的上課影片，用繁體中文
```

或英文：

```
Generate a 15-minute lecture video on "Variational Autoencoders" in English
```

更多觸發片語：「生成教學影片」、「把主題做成教學影片」、「自動生成 marp 投影片」、「帶旁白腳本的投影片」、「make a teaching video on …」。

### 互動流程

1. **大綱階段**：skill 會先產 `outline.md` 給你看，問你要不要修。可以請它改、也可以自己直接編輯該檔案，再請它繼續。
2. **投影片階段**：skill 依大綱與你指定的 marp template（預設使用內建的 4:3 keynote 風格主題）產生 marp 投影片，並編成 HTML + PDF + 每頁 PNG。
3. **講稿階段**：skill 會把投影片切批次（預設每 5 頁一批），開多個 sub-agents 平行寫講稿。你可以指定模型（`opus` / `sonnet` / `haiku`，預設 `sonnet`）。
4. **影片階段**：把講稿合併成全域時間軸，組裝出可在瀏覽器打開的自動播放 HTML 影片。

### 可調參數

跟 skill 說話時，可以一併指定：

| 參數 | 預設 | 說明 |
|------|------|------|
| `topic` | （必填） | 主題；會被 slugify 當輸出資料夾名 |
| `language` | （必填） | 講稿與投影片語言（例：`繁體中文`、`English`） |
| `audience` | 大學部學生 | 影響深度與口吻 |
| `length_minutes` | 15 | 目標總長度（分鐘） |
| `marp_template_path` | 內建 `assets/marp/theme.css` | 自訂 marp theme |
| `subagent_model` | `sonnet` | 講稿產生用的 sub-agent 模型 |
| `pages_per_subagent` | 5 | 每個 sub-agent 負責幾頁 |
| `output_dir` | `./output` | 輸出根目錄 |

---

## 輸出結構

每個主題會在 `output/<slug-of-topic>/` 下產生：

```
output/<slug>/
├── outline.md              # 階段 1：可由你編輯
├── slides.md               # 階段 2：marp 原始檔
├── slides.html             # 階段 2：marp HTML
├── slides.pdf              # 階段 2：marp PDF
├── slides.images/01.png …  # 階段 2：每頁 PNG（影片用）
├── .slides.json            # 內部：頁面 + overlay 結構
├── scripts/01.srt …        # 階段 3：每頁 SRT 講稿
├── timeline.json           # 階段 4：全域時間軸
├── video/                  # 階段 4：打開 index.html 即可播放
└── .state.json             # 進度紀錄，可斷點續跑
```

---

## Overlay 重點標註

投影片可以在 markdown 裡用 HTML 註解標出「需要在講到時才出現的重點」：

```markdown
# 反向傳播的核心想法

- 從輸出層往回傳播誤差訊號
<!-- overlay-begin: id=chain-rule, label="鏈鎖律的角色" -->
- 每一層用**鏈鎖律**把誤差分解到參數
<!-- overlay-end: id=chain-rule -->
- 用梯度更新權重
```

講稿產生時，sub-agent 會在講解該重點的那段話前後加上 `[overlay:chain-rule] … [/overlay:chain-rule]` 標記；最終影片會在那段時間把對應的 overlay badge 淡入到投影片角落。

詳細語法見 [`references/marp-and-overlays.md`](references/marp-and-overlays.md)。

---

## 斷點續跑與重做

`output/<slug>/.state.json` 會紀錄已完成的階段。再次觸發 skill 時：

- 沒指定要重做 → 從尚未完成的階段繼續。
- 明確說「重做大綱／重寫第 5 頁的講稿／投影片要改」→ 從該階段重做，下游階段自動失效並重跑。

可重做的範圍見 [`references/workflow.md`](references/workflow.md) 「Re-doing a single phase」一節。

---

## 加上旁白配音（TTS）

預設產出的是「無聲、依時間軸自動換頁」的影片。若要產生**真正有人聲旁白**的影片,在觸發時加上 `tts` 並提供一段參考人聲:

```
幫我做一個關於「反向傳播」的上課影片,繁體中文,要有旁白,聲音用 my_voice.wav
```

兩種引擎可選,產出完全相同(同樣的 `timeline.json`、同樣的旁白音軌),差別只在合成跑在哪裡:

- **本機** — IndexTTS-2 MLX-Swift,Apple Silicon 上跑。約 RTF 8.4。
- **遠端** — 指定 `tts_host` 為一台 ssh 主機,在 NVIDIA GPU 上以 ONNX Runtime 合成(推論端不依賴 PyTorch)。本機只需要 `ssh` 與 `rsync`。RTX 4080 上約 RTF 0.42,快約 20 倍,而且不占用本機。設定方式見 [`references/remote-tts.md`](references/remote-tts.md)。

運作方式:

- zero-shot 聲音複製,只需幾秒乾淨人聲當參考。
- 逐句把講稿送進 TTS(單次 `--srt` 批次,模型只載入一次),合成 `narration.wav`(16-bit PCM mono 22.05 kHz),再轉成 `narration.mp3` 交付(`--audio-format wav`/`both` 可保留無損檔)。
- **依真實音訊長度重算 `timeline.json`**:投影片在該頁旁白唸完時才切換,overlay 也在實際講到時淡入/淡出——不是用講稿裡估計的時間戳。
- `[overlay:*]` 標記在送進 TTS 前會被移除,不會被唸出來。
- **繁體中文會先用 `opencc` 轉成簡體再送進 TTS**(IndexTTS-2 的詞表只有簡體,繁體字會發音錯誤);投影片與字幕仍維持繁體,只有「唸出來的文字」被轉換。

| 參數 | 預設 | 說明 |
|------|------|------|
| `tts` | `false` | 設為 true 才會合成旁白 |
| `voice_ref` | (必填) | 要複製的參考人聲 `.wav` |
| `emotion_ref` | 同 `voice_ref` | 另指定一段音檔來決定語氣情緒(兩種引擎都支援) |
| `tts_host` | `$LECTUREVIDEO_TTS_HOST` | 有值就走遠端引擎,留空就用本機 MLX |
| `indextts2_dir` | `$INDEXTTS2_DIR` | IndexTTS-2 MLX checkout 路徑(含已編譯 CLI 與模型);走遠端時不需要 |

需要條件:**本機引擎**要 Apple Silicon、已 `./build.sh Release` 編好的 IndexTTS-2 CLI 與轉好的模型權重;**遠端引擎**本機只要 `ssh` 與 `rsync`,引擎裝在對面那台。繁體中文兩者都需要 `opencc`(`brew install opencc`)。沒有任一引擎時無聲路徑照常可用。合成是運算密集的,可用 `--seed` 讓結果可重現。

底層腳本與時間軸重算細節見 [`references/player-architecture.md`](references/player-architecture.md) 「TTS audio」一節。

---

## 目錄結構

```
.
├── SKILL.md                     # skill 進入點（YAML frontmatter + 主流程）
├── README.md                    # 你正在看的這份
├── references/                  # 細節文件，需要時才會被載入
│   ├── workflow.md              # 各階段詳細步驟、重做矩陣
│   ├── marp-and-overlays.md     # marp 語法與 overlay 註解語法
│   ├── srt-and-timing.md        # SRT 格式與 [overlay:*] 標記規則
│   ├── subagent-prompts.md      # sub-agent 派發 prompt 範本
│   ├── player-architecture.md   # 影片播放器內部設計與未來 TTS 接點
│   └── remote-tts.md            # 遠端 GPU 配音（--remote-host）：傳輸、EP 策略、機器設定
├── scripts/                     # 確定性的工具腳本
│   ├── compile_marp.sh          # 一鍵 marp → HTML + PDF + PNG
│   ├── split_slides.py          # 拆 slides.md 為頁面 JSON
│   ├── plan_subagent_batches.py # 計算 sub-agent 批次
│   ├── derive_timeline.py       # SRT → timeline.json（無聲路徑）
│   ├── synthesize_tts.py        # TTS：合成旁白 narration.mp3 並依真實音訊重算時間軸
│   ├── remote/                  # --remote-host 的遠端 worker 與 launcher（ONNX，免 PyTorch）
│   └── build_video.py           # 組裝最終 HTML 影片
└── assets/
    ├── marp/theme.css           # 內建預設投影片主題（4:3 keynote 風格）
    └── player/                  # HTML 影片播放器模板
        ├── index.html
        ├── player.css
        └── player.js
```

---

## 路線圖

- [x] 接 TTS（語音合成）→ 真正的有聲教學影片（透過本地 IndexTTS-2 MLX，見下節）
- [ ] 提供額外 marp themes（深色、學術風、簡報風）
- [ ] 投影片裡的圖示自動產生（如：神經網路示意圖、流程圖）
- [ ] 匯出 mp4（透過 ffmpeg + headless chromium）

歡迎在 issues 提需求或回報 bug。

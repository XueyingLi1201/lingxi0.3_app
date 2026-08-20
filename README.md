# 灵溪 0.3 —— 语音对话版

基于灵溪 0.2（已修复 TTS 认证握手、音频播放、flet 版本兼容），新增**语音输入**：
点输入框右侧的 🎤 说话 → 再点一次结束 → 自动转成文字并发送 → 灵溪回复并语音播报。

## 功能

- 💬 文字聊天：DeepSeek（`deepseek-chat`），吴用人设，带对话记忆
- 🔊 语音播报：火山引擎双向流式 TTS（`seed-icl-2.0`）
- 🎤 语音输入：录音 → OpenAI 兼容的 `transcriptions` 接口转文字（默认 `whisper-1`）

## 环境变量 / config.py

优先级：环境变量 > `config.py`。二选一即可：

```bash
# Windows PowerShell 示例
$env:DEEPSEEK_API_KEY = "你的DeepSeek密钥"
$env:TTS_API_KEY      = "你的火山引擎TTS密钥"      # X-Api-Key，与资源 seed-icl-2.0 对应
$env:ASR_API_KEY      = "你的语音识别密钥"          # 可选，缺省时语音输入不可用
$env:ASR_BASE_URL     = "https://api.openai.com/v1" # 可选，任意 OpenAI 兼容识别服务
$env:ASR_MODEL        = "whisper-1"                 # 可选
python main.py
```

或者把 `config.example.py` 复制为 `config.py` 填好密钥（APK 打包时也用它注入）。

> ASR 说明：默认走 OpenAI Whisper。国内可改用任意 OpenAI 兼容的识别服务，
> 例如硅基流动（`https://api.siliconflow.cn/v1`，`whisper-1`）、Groq 等，
> 只需设置 `ASR_BASE_URL` / `ASR_MODEL` / `ASR_API_KEY`。

## 依赖与 flet 版本

- 桌面/打包（flet 0.25.0，`requirements.txt` 锁定）：`ft.Audio`、`ft.AudioRecorder` 内置，直接用。
- 新版 flet（≥0.76，例如 0.86）：Audio/AudioRecorder 已移出核心包，需 `pip install flet-audio`。
- 若当前 flet 既没有音频控件也没有安装 flet-audio：播报自动降级为系统播放器（Windows `winsound`），
  语音输入按钮会提示不可用。

## 运行

```bash
pip install -r requirements.txt
python main.py
```

## 打包 APK（GitHub Actions）

见 `.github/workflows/build-apk.yml`，在仓库 Secrets 中配置
`DEEPSEEK_API_KEY`、`TTS_API_KEY`、`ASR_API_KEY`（均可选填）后手动触发或 push 到 main。

### ⚠️ 仓库目录布局（最容易踩的坑）

工作流会自动兼容下面两种布局，但**推荐第 1 种**：

1. ✅ 把 `main.py`、`requirements.txt`、`.github/` 直接放在**仓库根目录**（推荐）；
2. ✅ 把整个应用文件夹（如 `lingxi0.3_app/`）作为**唯一的子目录**放进仓库，工作流会自动检测；
3. ❌ 不要多层嵌套（如 `lingxi0.3_app/lingxi0.3_app/main.py`），也不要只传部分文件——会报
   `Could not open requirements file: 'requirements.txt'`。

上传后可在仓库网页上确认：根目录应能看到 `main.py` 和 `requirements.txt`。


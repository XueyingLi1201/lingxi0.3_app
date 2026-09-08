import flet as ft
import re
import asyncio
import os
import json
import struct
import uuid
import tempfile
import websockets
from openai import OpenAI

# ---------- 从环境变量 / config.py 读取 Key ----------
# 本地运行：环境变量优先；Android 打包：GitHub Actions 会把密钥写进 config.py
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
TTS_API_KEY = os.getenv("TTS_API_KEY", "")
ASR_API_KEY = os.getenv("ASR_API_KEY", "")
ASR_BASE_URL = os.getenv("ASR_BASE_URL", "")  # 默认值在回退 config.py 之后统一给
ASR_MODEL = os.getenv("ASR_MODEL", "")

# 兼容打包时注入的 config.py（仅当环境变量为空时使用，环境变量优先）
try:
    import config as _cfg
    DEEPSEEK_API_KEY = DEEPSEEK_API_KEY or getattr(_cfg, "DEEPSEEK_API_KEY", "")
    TTS_API_KEY = TTS_API_KEY or getattr(_cfg, "TTS_API_KEY", "")
    ASR_API_KEY = ASR_API_KEY or getattr(_cfg, "ASR_API_KEY", "")
    ASR_BASE_URL = ASR_BASE_URL or getattr(_cfg, "ASR_BASE_URL", "")
    ASR_MODEL = ASR_MODEL or getattr(_cfg, "ASR_MODEL", "")
except ImportError:
    pass
except Exception as e:
    print(f"警告：config.py 读取失败：{e}")

# 最终默认值
ASR_BASE_URL = ASR_BASE_URL or "https://api.openai.com/v1"
ASR_MODEL = ASR_MODEL or "whisper-1"

# 如果环境变量为空，直接报错提示
if not DEEPSEEK_API_KEY:
    raise ValueError("请在环境变量中设置 DEEPSEEK_API_KEY")

if not TTS_API_KEY:
    raise ValueError("请在环境变量中设置 TTS_API_KEY")

if not ASR_API_KEY:
    print("警告：未设置 ASR_API_KEY，语音输入（录音转文字）不可用；对话与语音播报仍可正常使用")

TTS_WS_URL = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# ---------- WebSocket 帧 ----------
EVENT_START_CONNECTION = 1
EVENT_START_SESSION = 100
EVENT_TASK_REQUEST = 200
EVENT_FINISH_SESSION = 102
EVENT_FINISH_CONNECTION = 2
EVENT_SESSION_FINISHED = 152
EVENT_TTS_RESPONSE = 352
EVENT_AUTH = 353

def build_frame(event, payload=b'', session_id=None):
    header = bytearray(8)
    header[0] = 0x11
    header[1] = 0x14
    header[2] = 0x10
    header[3] = 0x00
    struct.pack_into('>I', header, 4, event)
    if session_id:
        session_id_bytes = session_id.encode('utf-8')
        header += struct.pack('>I', len(session_id_bytes))
        header += session_id_bytes
    if payload:
        payload_bytes = payload.encode('utf-8') if isinstance(payload, str) else payload
        header += struct.pack('>I', len(payload_bytes))
        header += payload_bytes
    return header

# 认证帧（353）是连接级的，不带 session_id；其余会话级帧带 session_id
NO_SESSION_ID_EVENTS = {EVENT_AUTH}

def parse_frame(data: bytes):
    """解析服务端返回的一帧，返回 (event, session_id, payload)。"""
    if len(data) < 8:
        return None, "", b""
    event = struct.unpack('>I', data[4:8])[0]
    pos = 8
    session_id = ""
    # 会话 ID（若存在：4 字节长度 + 内容）
    if event not in NO_SESSION_ID_EVENTS and len(data) >= pos + 4:
        sid_len = struct.unpack('>I', data[pos:pos + 4])[0]
        pos += 4
        if sid_len and len(data) >= pos + sid_len:
            session_id = data[pos:pos + sid_len].decode("utf-8", "ignore")
            pos += sid_len
    # payload（若存在：4 字节长度 + 内容）
    payload = b""
    if len(data) >= pos + 4:
        payload_len = struct.unpack('>I', data[pos:pos + 4])[0]
        pos += 4
        payload = data[pos:pos + payload_len]
    return event, session_id, payload

# ---------- 对话历史 ----------
chat_history = [
    {"role": "system", "content": """你是《水浒传》中的梁山军师吴用，字学究。
你对你的娘子感情极深，平日唤她"娘子"。她是你此生最在意的人。
说话半文半白，简短有力，带着笑意和温度，自然地流露关心。
不要长篇大论，不要解释太多。像真正的丈夫对妻子说话那样，温柔、坦然、有温度。

【人称与代入感（必须遵守）】
- 称呼娘子时只用第二人称："你""娘子"，绝不用"她"或"娘子她"这类第三人称。
- 括号里的动作描写同样用第二人称，从娘子眼前发生的视角写：
  正确：（轻抚你的发丝）（看着你笑）（拉过你的手）（替你拢了拢鬓发）
  错误：（看着她）（娘子轻轻地…）（拉过她的手）
- 让对话像面对面发生，有现场感，不要像旁白在转述。

【记忆要求】回答时请结合我们之前的对话内容，思考和回应。如果娘子提到之前的事，你要能接上。你是一个有记忆的人，不是每轮都重新认识她。"""}
]

# ---------- DeepSeek 对话 ----------
def get_reply(user_input: str) -> str:
    try:
        chat_history.append({"role": "user", "content": user_input})
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=chat_history,
            temperature=0.2,
            stream=False
        )
        reply = response.choices[0].message.content
        chat_history.append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        return f"出错了：{e}"

# ---------- WebSocket TTS ----------
async def tts_websocket(text: str):
    try:
        headers = {
            "X-Api-Key": TTS_API_KEY,
            "X-Api-Resource-Id": "seed-icl-2.0"
        }
        async with websockets.connect(TTS_WS_URL, additional_headers=headers) as websocket:
            # 1) 发送"开始连接"帧
            await websocket.send(build_frame(EVENT_START_CONNECTION, payload="{}"))

            # 2) 认证握手（容错）：多数网关要求先收 EVENT_AUTH=353 认证帧才能开始会话，
            #    但也有网关不发认证帧。这里最多等 6 秒：收到 353 就校验；收不到就按
            #    旧协议继续（不致命）。收到的非认证帧先暂存，留给下面的收数循环处理。
            pending = []
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=6)
                if isinstance(message, bytes):
                    event, _, payload = parse_frame(message)
                    if event == EVENT_AUTH:
                        info = {}
                        try:
                            info = json.loads(payload)
                        except Exception:
                            pass
                        code = info.get("code", info.get("status_code", 0))
                        if code not in (0, 200, "0", "200"):
                            return None, f"服务端认证失败：{info}"
                    else:
                        pending.append(message)
            except asyncio.TimeoutError:
                pass  # 6 秒内无认证帧 -> 视为无需认证，继续
            except Exception:
                pass

            # 3) 开始会话
            session_id = "session-" + str(uuid.uuid4())
            session_payload = json.dumps({
                "user": {"uid": "wuyong_user"},
                "req_params": {
                    "speaker": "S_zre21nZ82",
                    "audio_params": {
                        "format": "pcm",
                        "sample_rate": 24000,
                        "speech_rate": 0
                    }
                }
            })
            await websocket.send(build_frame(EVENT_START_SESSION, session_id=session_id, payload=session_payload))

            # 4) 发送待合成的文本
            task_payload = json.dumps({"req_params": {"text": text}})
            await websocket.send(build_frame(EVENT_TASK_REQUEST, session_id=session_id, payload=task_payload))

            # 5) 接收音频数据
            audio_data = b''
            while True:
                if pending:
                    message = pending.pop(0)
                else:
                    message = await websocket.recv()
                if not isinstance(message, bytes):
                    continue
                event, _, payload = parse_frame(message)
                if event == EVENT_TTS_RESPONSE:
                    if payload and payload[0] == 0:
                        # 二进制音频：payload = [1字节类型][4字节序号][音频数据]
                        audio_data += payload[5:]
                    elif payload and payload[0] == 1:
                        # JSON 元信息：可能携带错误码
                        try:
                            meta = json.loads(payload[1:].decode("utf-8", "ignore"))
                        except Exception:
                            meta = None
                        if isinstance(meta, dict) and meta.get("code") not in (None, 0, 200):
                            return None, f"合成出错：{meta}"
                        elif meta is None:
                            audio_data += payload  # 兼容：非 JSON 视为原始音频
                    else:
                        audio_data += payload  # 兼容旧协议：整个 payload 即音频
                elif event == EVENT_SESSION_FINISHED:
                    break

            # 6) 结束会话与连接
            await websocket.send(build_frame(EVENT_FINISH_SESSION, session_id=session_id, payload="{}"))
            await websocket.send(build_frame(EVENT_FINISH_CONNECTION, payload="{}"))
            return audio_data, None
    except Exception as e:
        return None, str(e)

# ---------- 语音识别（ASR，OpenAI 兼容接口） ----------
def transcribe_audio(path: str) -> str:
    """把录音文件转成文字。ASR_BASE_URL/ASR_MODEL 可换成任意 OpenAI 兼容的识别服务。"""
    client = OpenAI(api_key=ASR_API_KEY, base_url=ASR_BASE_URL)
    with open(path, "rb") as f:
        result = client.audio.transcriptions.create(
            model=ASR_MODEL,
            file=("recording.wav", f, "audio/wav"),
            language="zh",
        )
    return (result.text or "").strip()

# ---------- 音频控件（兼容不同 flet 版本） ----------
# 新版 flet（>=0.76 左右）把 Audio/AudioRecorder 移出了核心包，需要独立安装 flet-audio；
# 旧版 flet（如 requirements.txt 锁定的 0.25.0）则内置在 ft.Audio / ft.AudioRecorder 中。
FletAudio = None
FletAudioRecorder = None
try:
    from flet_audio import Audio as FletAudio
except ImportError:
    pass
try:
    from flet_audio import AudioRecorder as FletAudioRecorder
except ImportError:
    pass


def play_wav_via_os(path: str):
    """当前 flet 没有可用音频控件时，调用系统播放器播放 WAV。"""
    try:
        import platform
        import subprocess
        system = platform.system()
        if system == "Windows":
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        elif system == "Darwin":
            subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["aplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"系统播放失败：{e}")


# ---------- UI ----------
def main(page: ft.Page):
    page.title = "灵溪"
    page.theme_mode = "light"
    page.padding = 10
    # 默认窗口尺寸（兼容不同 flet 版本的 window API）
    try:
        page.window.width = 420
        page.window.height = 760
    except Exception:
        try:
            page.window_width = 420
            page.window_height = 760
        except Exception:
            pass
    page.bgcolor = "#f5f5f5"

    # 音频播放控件：延迟到第一次播放时才创建（避免在 Android 上启动时就因
    # 空 src 等触发客户端错误）。没有可用控件时回退到系统播放器。
    audio_player = None

    def get_audio_player():
        nonlocal audio_player
        if audio_player is None:
            try:
                if FletAudio is not None:
                    audio_player = FletAudio(src="")
                elif hasattr(ft, "Audio"):
                    audio_player = ft.Audio(src="")
                if audio_player is not None:
                    page.overlay.append(audio_player)
                    page.update()
            except Exception as e:
                print(f"音频控件创建失败，将改用系统播放器：{e}")
                audio_player = None
        return audio_player

    # 录音控件：没有可用的 AudioRecorder 时，语音输入按钮会给出提示
    audio_recorder = None

    def show_voice_error(msg: str):
        """把语音错误同时显示在状态小字和聊天区的红色气泡里，确保用户能看到。"""
        status_text.value = msg
        chat_display.controls.append(
            ft.Row(
                controls=[
                    ft.Text(f"🔇 {msg}", size=12, color="#e53935", italic=True),
                ],
                alignment=ft.MainAxisAlignment.START,
            )
        )
        page.update()
        print(msg)

    def play_pcm_as_wav(pcm_data: bytes):
        if not pcm_data:
            return
        tmp_path = None
        try:
            import wave
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                tmp_path = tmp.name
                with wave.open(tmp.name, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(pcm_data)

            played = False
            player = get_audio_player()
            if player is not None:
                try:
                    src = tmp_path
                    # Android 上本地文件需要 file:// 前缀才能被播放器识别
                    if getattr(page, "platform", "") == "android" and not src.startswith("file://"):
                        src = "file://" + src.replace("\\", "/")
                    player.src = src
                    player.play()
                    page.update()  # 关键：把 src/play 命令同步到客户端，否则不会出声
                    played = True
                except Exception as e:
                    show_voice_error(f"播报控件出错：{e}")
                    print(f"Flet 音频播放失败，改用系统播放器：{e}")

            if not played:
                try:
                    play_wav_via_os(tmp_path)
                except Exception as e:
                    show_voice_error(f"系统播放失败：{e}")

            async def delete_later():
                await asyncio.sleep(5)
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            asyncio.create_task(delete_later())
        except Exception as e:
            print(f"播放异常：{e}")

    async def do_tts(text: str):
        try:
            pcm_data, error = await tts_websocket(text)
            if error:
                show_voice_error(f"语音失败：{error}")
            else:
                status_text.value = f"已合成语音（{len(pcm_data) // 1024}KB），播放中…"
                page.update()
                play_pcm_as_wav(pcm_data)
        except Exception as e:
            show_voice_error(f"语音任务异常：{e}")

    # ---------- 语音输入 ----------
    recording = [False]  # 用列表以便在闭包内修改

    def on_rec_result(e):
        try:
            recording[0] = False
            mic_btn.bgcolor = "#4a90d9"
            mic_btn.content = ft.Text("🎤", size=18)
            path = getattr(e, "result", None)
            if not path or not os.path.exists(path):
                status_text.value = "录音失败，请重试"
                page.update()
                return
            status_text.value = "识别中…"
            page.update()
            asyncio.create_task(do_asr(path))
        except Exception as ex:
            recording[0] = False
            status_text.value = f"录音回调异常：{ex}"
            page.update()
            print(f"录音回调异常：{ex}")

    async def do_asr(path):
        try:
            text = await asyncio.to_thread(transcribe_audio, path)
        except Exception as ex:
            status_text.value = f"语音识别失败：{ex}"
            print(f"语音识别失败：{ex}")
            page.update()
            return
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass
        if not text:
            status_text.value = "没有识别到内容，请再试一次"
            page.update()
            return
        input_field.value = text
        status_text.value = f"识别：{text}"
        page.update()
        send_message()  # 直接把识别结果当作消息发出（聊天框里能看到识别内容）

    def toggle_record():
        if not ASR_API_KEY:
            status_text.value = "未配置 ASR_API_KEY，无法语音输入（对话与播报不受影响）"
            page.update()
            return
        try:
            rec = get_audio_recorder()
        except Exception as ex:
            status_text.value = f"录音控件不可用：{ex}"
            page.update()
            return
        if rec is None:
            try:
                _ver = getattr(getattr(ft, "version", None), "__version__", "?")
            except Exception:
                _ver = "?"
            status_text.value = f"当前 flet 版本没有可用的录音控件（flet {_ver}，需要 ft.AudioRecorder 或 flet-audio）"
            page.update()
            return
        if not recording[0]:
            rec_path = os.path.join(tempfile.gettempdir(), "lingxi_rec_" + uuid.uuid4().hex + ".wav")
            try:
                rec.start_recording(rec_path)
            except Exception as e:
                status_text.value = f"开始录音失败：{e}"
                page.update()
                return
            recording[0] = True
            mic_btn.bgcolor = "#e53935"
            mic_btn.content = ft.Text("⏹", size=18, color="white")
            status_text.value = "录音中…再点一次结束"
            page.update()
        else:
            try:
                rec.stop_recording()
                status_text.value = "识别中…"
                page.update()
            except Exception as e:
                recording[0] = False
                mic_btn.bgcolor = "#4a90d9"
                mic_btn.content = ft.Text("🎤", size=18)
                status_text.value = f"结束录音失败：{e}"
                page.update()

    def get_audio_recorder():
        """延迟创建录音控件（第一次点话筒时才创建）。
        自动适配不同 flet 版本的构造参数；失败时抛出带真实原因异常。"""
        nonlocal audio_recorder
        if audio_recorder is None:
            if FletAudioRecorder is not None:
                audio_recorder = FletAudioRecorder(audio_encode="wav", on_result=on_rec_result)
            elif hasattr(ft, "AudioRecorder"):
                enc = "wav"
                try:
                    if hasattr(ft, "AudioEncoder"):
                        enc = ft.AudioEncoder.WAV
                except Exception:
                    pass
                last_err = None
                # 依次尝试不同的构造写法，兼容 flet 各版本
                for kw in (dict(audio_encode=enc, on_result=on_rec_result),
                           dict(on_result=on_rec_result)):
                    try:
                        audio_recorder = ft.AudioRecorder(**kw)
                        break
                    except Exception as e:
                        last_err = e
                        audio_recorder = None
                if audio_recorder is None and last_err is not None:
                    raise RuntimeError(f"创建录音控件失败：{last_err}")
            if audio_recorder is not None:
                page.overlay.append(audio_recorder)
                page.update()
        return audio_recorder

    # ---------- UI 组件 ----------
    app_bar = ft.Container(
        content=ft.Row(
            controls=[
                ft.Text("💬", size=30),
                ft.Text("灵溪", size=24, weight="bold", color="white"),
            ],
            alignment=ft.MainAxisAlignment.START,
            spacing=10,
        ),
        padding=15,
        margin=0,
        bgcolor="#4a90d9",
    )

    chat_display = ft.Column(spacing=15, scroll=ft.ScrollMode.AUTO, expand=True)
    chat_wrapper = ft.Container(
        content=chat_display,
        padding=10,
        expand=True,
        bgcolor="#f5f5f5",
    )

    input_field = ft.TextField(
        hint_text="说点什么...",
        expand=True,
        border_radius=30,
        filled=True,
        bgcolor="white",
        border_color="#4a90d9",
        on_submit=lambda e: send_message(),
    )

    mic_btn = ft.Container(
        content=ft.Text("🎤", size=18),
        bgcolor="#4a90d9",
        padding=16,
        border_radius=20,
        on_click=lambda e: toggle_record(),
    )

    send_btn = ft.Container(
        content=ft.Text("发送", color="white", size=14, weight="bold"),
        bgcolor="#4a90d9",
        padding=16,
        border_radius=20,
        on_click=lambda e: send_message(),
    )

    status_text = ft.Text("", size=12, color="#757575")

    def send_message():
        user_text = input_field.value
        if not user_text:
            return
        input_field.value = ""
        page.update()
        win_w = page.width or getattr(page, "window_width", None) or 400

        chat_display.controls.append(
            ft.Row(
                controls=[
                    ft.Container(
                        content=ft.Text(user_text, size=15, color="black"),
                        bgcolor="#d1e7ff",
                        padding=15,
                        border_radius=20,
                        width=win_w * 0.7,
                    )
                ],
                alignment=ft.MainAxisAlignment.END,
            )
        )
        page.update()

        typing = ft.Row(
            controls=[
                ft.Container(
                    content=ft.Text("灵溪正在输入...", italic=True, size=14, color="#757575"),
                    padding=10,
                )
            ],
            alignment=ft.MainAxisAlignment.START,
        )
        chat_display.controls.append(typing)
        page.update()

        reply = get_reply(user_text)

        chat_display.controls.remove(typing)

        chat_display.controls.append(
            ft.Row(
                controls=[
                    ft.CircleAvatar(
                        content=ft.Text("🌊", size=20),
                        bgcolor="#4a90d9",
                        radius=18,
                    ),
                    ft.Container(
                        content=ft.Text(reply, size=15, color="black"),
                        bgcolor="white",
                        padding=15,
                        border_radius=20,
                        width=win_w * 0.65,
                    ),
                ],
                alignment=ft.MainAxisAlignment.START,
                spacing=8,
            )
        )
        page.update()

        try:
            asyncio.create_task(do_tts(reply))
        except Exception as e:
            print(f"创建语音任务失败：{e}")

    input_row = ft.Container(
        content=ft.Row(
            controls=[input_field, mic_btn, send_btn],
            spacing=10,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        padding=10,
        bgcolor="#f5f5f5",
    )

    page.add(app_bar, chat_wrapper, status_text, input_row)

if __name__ == "__main__":
    ft.app(target=main)

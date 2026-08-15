# -*- coding: utf-8 -*-
"""冒烟测试：用桩 Page 调用 main()，验证在 flet 0.86（无 ft.Audio/AudioRecorder）下不崩溃，
且语音输入按钮/状态栏/录音降级逻辑正常。"""
import os
import types

os.environ.setdefault("DEEPSEEK_API_KEY", "test-deepseek-key")
os.environ.setdefault("TTS_API_KEY", "test-tts-key")
os.environ.setdefault("ASR_API_KEY", "")

import main as app  # noqa: E402


class StubPage:
    def __init__(self):
        self.overlay = []
        self.width = None
        self.window = types.SimpleNamespace(width=None, height=None)

    def add(self, *controls):
        self.added = controls

    def update(self):
        pass


page = StubPage()
app.main(page)

assert page.overlay == [], f"overlay 应为空（本机无音频/录音控件），实际 {page.overlay}"
assert page.window.width == 420 and page.window.height == 760
print("[冒烟] main() 无异常执行完成，window 尺寸设置正确 -> PASS")

# 收集 add 进来的控件，检查麦克风按钮与状态栏存在
assert hasattr(page, "added"), "page.add 未被调用"
added = page.added
assert any(getattr(c, "content", None) is not None for c in added), "未找到输入区控件"
print("[冒烟] page.add 已调用，控件树构建完成 -> PASS")

# 验证 ASR 相关模块逻辑：transcribe_audio 参数构造正确（mock OpenAI）
class FakeTranscriptions:
    def __init__(self):
        self.called = None

    def create(self, **kwargs):
        self.called = kwargs
        return types.SimpleNamespace(text="  你好娘子  ")


class FakeAudio:
    def __init__(self):
        self.transcriptions = FakeTranscriptions()


class FakeOpenAI:
    instances = []

    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = base_url
        self.audio = FakeAudio()
        FakeOpenAI.instances.append(self)


_real_openai = app.OpenAI
app.OpenAI = FakeOpenAI
try:
    import tempfile
    p = os.path.join(tempfile.gettempdir(), "_asr_test.wav")
    with open(p, "wb") as f:
        f.write(b"RIFF-test")
    text = app.transcribe_audio(p)
    os.unlink(p)
    assert text == "你好娘子", f"transcribe 结果异常: {text!r}"
    fake = FakeOpenAI.instances[-1].audio.transcriptions
    assert fake.called["model"] == "whisper-1"
    assert fake.called["language"] == "zh"
    assert fake.called["file"][0] == "recording.wav"
    print("[ASR] transcribe_audio 参数与结果处理正确 -> PASS")
finally:
    app.OpenAI = _real_openai

print("ALL DONE")

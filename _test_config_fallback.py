# -*- coding: utf-8 -*-
"""模拟 Android 打包场景：GitHub Actions 把密钥写进 config.py，运行期无环境变量。
验证 main.py 能正确回退读取 config.py，且环境变量优先于 config.py。"""
import importlib
import os
import sys

for k in ("DEEPSEEK_API_KEY", "TTS_API_KEY", "ASR_API_KEY"):
    os.environ.pop(k, None)

# 写一个测试用 config.py（模拟打包时注入）
with open("config.py", "w", encoding="utf-8") as f:
    f.write('DEEPSEEK_API_KEY = "cfg-deepseek"\n')
    f.write('TTS_API_KEY = "cfg-tts"\n')
    f.write('ASR_API_KEY = "cfg-asr"\n')
    f.write('ASR_BASE_URL = "https://api.siliconflow.cn/v1"\n')
    f.write('ASR_MODEL = "whisper-1"\n')

try:
    # ---- 场景 B：环境变量优先 ----
    os.environ["DEEPSEEK_API_KEY"] = "env-deepseek"
    os.environ["TTS_API_KEY"] = "env-tts"
    os.environ["ASR_API_KEY"] = "env-asr"
    import main as app_b
    assert app_b.DEEPSEEK_API_KEY == "env-deepseek", app_b.DEEPSEEK_API_KEY
    assert app_b.TTS_API_KEY == "env-tts", app_b.TTS_API_KEY
    assert app_b.ASR_API_KEY == "env-asr", app_b.ASR_API_KEY
    print("[场景B] 环境变量优先于 config.py -> PASS")

    # ---- 场景 A：无环境变量，回退 config.py ----
    for k in ("DEEPSEEK_API_KEY", "TTS_API_KEY", "ASR_API_KEY"):
        os.environ.pop(k, None)
    for m in ("main", "config"):
        sys.modules.pop(m, None)
    import main as app_a
    assert app_a.DEEPSEEK_API_KEY == "cfg-deepseek", app_a.DEEPSEEK_API_KEY
    assert app_a.TTS_API_KEY == "cfg-tts", app_a.TTS_API_KEY
    assert app_a.ASR_API_KEY == "cfg-asr", app_a.ASR_API_KEY
    assert app_a.ASR_BASE_URL == "https://api.siliconflow.cn/v1", app_a.ASR_BASE_URL
    print("[场景A] 无环境变量时读取 config.py（含 ASR 配置） -> PASS")
finally:
    for k in ("DEEPSEEK_API_KEY", "TTS_API_KEY", "ASR_API_KEY"):
        os.environ.pop(k, None)
    try:
        os.unlink("config.py")
    except Exception:
        pass

print("ALL DONE")

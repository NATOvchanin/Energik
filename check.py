"""Проверка окружения перед запуском: python check.py"""
import importlib
import subprocess
import sys

import cuda_setup

cuda_setup.setup()


def ok(msg):
    print(f"  [ OK ] {msg}")


def bad(msg):
    print(f"  [ !! ] {msg}")


print("Проверка Мистера Энергика\n")
v = sys.version_info
(ok if v >= (3, 10) else bad)(f"Python {v.major}.{v.minor}.{v.micro}")

print("\nПакеты:")
for mod in ["numpy", "sounddevice", "torch", "faster_whisper", "silero_vad",
            "pedalboard", "num2words"]:
    try:
        importlib.import_module(mod)
        ok(mod)
    except Exception as e:
        bad(f"{mod}: {e}  ->  pip install -r requirements.txt")

print("\nВидеокарта:")
try:
    out = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.used,memory.total",
                          "--format=csv,noheader"], capture_output=True, text=True, timeout=5).stdout.strip()
    ok(out or "nvidia-smi ничего не вернул")
except Exception as e:
    bad(f"nvidia-smi не найден: {e}")

try:
    import ctranslate2
    n = ctranslate2.get_cuda_device_count()
    (ok if n else bad)(f"CUDA-устройств для Whisper: {n}")
except Exception as e:
    bad(f"ctranslate2: {e}")

print("\nАудиоустройства:")
try:
    import devices
    devices.list_devices()
except Exception as e:
    bad(f"sounddevice: {e}")

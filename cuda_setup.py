"""
Whisper на видеокарте на Windows нужны DLL из пакетов nvidia-cublas / nvidia-cudnn.
pip их ставит, но Windows сама их не находит, поэтому добавляем пути вручную.
Вызывать ДО импорта faster_whisper.
"""
import os
import sys


def setup() -> None:
    if sys.platform != "win32":
        return
    try:
        import nvidia  # namespace-пакет
    except ImportError:
        return
    for base in list(nvidia.__path__):
        for sub in os.listdir(base):
            bin_dir = os.path.join(base, sub, "bin")
            if os.path.isdir(bin_dir):
                os.add_dll_directory(bin_dir)
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ["PATH"]

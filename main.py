"""
Мистер Энергик. ЭТАП 3: любопытство и память.
Ты говоришь, он думает (Ollama) и отвечает в образе, озвучка идёт по предложениям.

Запуск:  python main.py
Другое:  python main.py --devices           (список микрофонов и колонок)
         python main.py --profile stream    (лёгкий режим для стрима)
"""
import subprocess
import sys
import time

import cuda_setup

cuda_setup.setup()  # ДО импорта faster_whisper

import config  # noqa: E402
import devices  # noqa: E402

EXIT_WORDS = {"стоп", "выход", "хватит", "закрой программу"}
RESET_WORDS = {"забудь всё", "забудь все", "сброс", "начнём заново", "начнем заново"}


def gpu_memory() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        used, total = [int(x) for x in out.split(",")]
        return f"{used} из {total} МБ"
    except Exception:
        return "не удалось узнать (nvidia-smi недоступен)"


def ram_usage() -> str:
    """Загрузка оперативной памяти Windows без сторонних библиотек."""
    try:
        import ctypes

        class MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
        s = MemStatus()
        s.dwLength = ctypes.sizeof(MemStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
        used = (s.ullTotalPhys - s.ullAvailPhys) / 2 ** 30
        return f"{s.dwMemoryLoad}% ({used:.1f} из {s.ullTotalPhys / 2 ** 30:.1f} ГБ)"
    except Exception:
        return "не удалось узнать"


def main() -> None:
    if "--devices" in sys.argv:
        devices.list_devices()
        return

    profile = None
    if "--profile" in sys.argv:
        profile = sys.argv[sys.argv.index("--profile") + 1]
        print(f"[ПРОФИЛЬ] {profile}")
    cfg = config.load(profile)
    import llm
    from brain import Brain
    from ears import Ears
    from memory import Memory
    from speaker import Speaker
    from voice import Voice

    dialogue, utility = llm.make(cfg)   # сначала проверка модели, чтобы падать рано
    mem = Memory()
    brain = Brain(cfg, dialogue, utility, mem)
    voice = Voice(cfg)
    speaker = Speaker(voice)
    ears = Ears(cfg)
    dialogue.warmup()
    removed = mem.prune()
    print(f"[ПАМЯТЬ] заметок в памяти: {mem.count()}" + (f" (убрал лишних: {removed})" if removed else ""))
    print(f"\n[ВИДЕОПАМЯТЬ] занято: {gpu_memory()}")
    print(f"[ОПЕРАТИВКА] занято: {ram_usage()}")
    print("[СОВЕТ] В другой консоли выполни `ollama ps`: в колонке PROCESSOR должно быть 100% GPU.\n")

    hello = brain.greeting() or "Босс, Энергик на связи. Что у нас сегодня, очередной провал?"
    speaker.say(hello)
    speaker.wait_done()
    ears.flush()

    print("Слушаю. Говори в микрофон (Ctrl+C для выхода).\n")
    replies = 0
    try:
        while True:
            text = ears.listen()
            if not text:
                continue
            t_heard = time.time()
            print(f"Ты: {text}   [распознавание: {ears.last_asr_s:.2f} с]")
            low = text.lower().strip(" .!?,")
            if low in EXIT_WORDS:
                speaker.say("Ухожу. Не скучай, Босс.")
                speaker.wait_done()
                break
            if low in RESET_WORDS:
                brain.reset()
                speaker.say("Разговор сбросил. Но тебя я всё равно помню, Босс.")
                speaker.wait_done()
                ears.flush()
                continue

            speaker.start_utterance()
            n = 0
            t_first_sentence = None
            try:
                for sentence in brain.reply(text):
                    if t_first_sentence is None:
                        t_first_sentence = time.time() - t_heard
                    speaker.say(sentence)
                    n += 1
            except Exception as e:
                print(f"[МОЗГ] Ошибка: {e}")
            speaker.wait_done()
            if speaker.first_play_ts and t_first_sentence is not None:
                print(f"   [ЗАДЕРЖКА] первое предложение от мозга: {t_first_sentence:.2f} с, "
                      f"первый звук: {speaker.first_play_ts - t_heard:.2f} с, "
                      f"предложений: {n}")
            ears.flush()  # не слушаем самого себя
            replies += 1
            if replies % 5 == 0:
                print(f"   [РЕСУРСЫ] видеопамять: {gpu_memory()}, оперативка: {ram_usage()}")
    except KeyboardInterrupt:
        print("\nВыход.")
    finally:
        try:
            brain.close()          # дописать дневник и итог разговора в память
        except Exception as e:
            print(f"[ПАМЯТЬ] не удалось сохранить итоги: {e}")
        voice.close()


if __name__ == "__main__":
    main()

"""Поиск аудиоустройств по части названия."""
import sounddevice as sd


def list_devices() -> None:
    print("\nВХОДЫ (микрофоны):")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            print(f"  [{i}] {d['name']}")
    print("\nВЫХОДЫ (колонки/наушники):")
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0:
            print(f"  [{i}] {d['name']}")
    print()


def find(name_part: str, kind: str):
    """kind: 'input' или 'output'. Возвращает индекс или None (= по умолчанию)."""
    if not name_part:
        return None
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    for i, d in enumerate(sd.query_devices()):
        if d[key] > 0 and name_part.lower() in d["name"].lower():
            return i
    print(f"[!] Устройство '{name_part}' ({kind}) не найдено, беру по умолчанию.")
    return None

"""
Просмотр и правка памяти Энергика (без программирования).

  python memory_tool.py                 показать все заметки
  python memory_tool.py поиск книга     найти заметки по словам
  python memory_tool.py забыть 7        удалить заметку с номером 7
  python memory_tool.py добавить "Босс любит стратегии"      добавить факт вручную (важность 5)
"""
import sys

from memory import Memory, ago


def show(rows) -> None:
    if not rows:
        print("Память пуста.")
    for r in rows:
        print(f"[{r['id']:>3}] {r['kind']:<10} важн.{r['importance']}  ({ago(r['ts'])})  {r['text']}")


def main() -> None:
    mem = Memory()
    args = sys.argv[1:]
    if not args:
        show(mem.all())
        print(f"\nВсего: {mem.count()}")
    elif args[0] == "поиск":
        show(mem.search(" ".join(args[1:]), limit=10))
    elif args[0] == "забыть" and len(args) > 1 and args[1].isdigit():
        print("Удалено." if mem.forget(int(args[1])) else "Нет заметки с таким номером.")
    elif args[0] == "добавить" and len(args) > 1:
        mem.add("fact", " ".join(args[1:]), 5)
        print("Добавлено.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

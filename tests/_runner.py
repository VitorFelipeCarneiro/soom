"""Mini executor de testes (sem dependências): roda as funções test_* de um módulo e mostra o
resultado. Os testes também funcionam com o pytest, se você preferir."""

import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(namespace: dict) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    tests = [(name, fn) for name, fn in namespace.items() if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in tests:
        t0 = time.perf_counter()
        try:
            fn()
            print(f"OK    {name}  ({time.perf_counter() - t0:.2f} s)")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FALHA {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passaram")
    return 1 if failed else 0

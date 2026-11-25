"""Soom: ditado por voz com IA, 100% local, para qualquer campo de texto do Windows.

Aperte Win+H para começar a ouvir, fale à vontade e aperte de novo para terminar. O texto vai
aparecendo onde está o cursor enquanto você fala. Veja o README.md.
"""

import os
import sys

# pythonw.exe não tem console: evita que bibliotecas quebrem ao escrever em stdout/stderr.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

from soom.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

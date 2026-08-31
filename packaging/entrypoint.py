"""Entry point usado só pelo PyInstaller.

engecad/__main__.py e engecad/app.py fazem import relativo (`from . import
...`), o que só funciona quando o Python carrega o arquivo como parte do
pacote `engecad`. O PyInstaller executa o script apontado no Analysis como
módulo `__main__` solto, fora do pacote - import relativo quebra
(`ImportError: attempted relative import with no known parent package`).
Por isso o build aponta pra este arquivo, que importa `engecad` de forma
absoluta, em vez de apontar direto pra engecad/__main__.py.
"""

import sys

from engecad.app import main

if __name__ == "__main__":
    sys.exit(main())

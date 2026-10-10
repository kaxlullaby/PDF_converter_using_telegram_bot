"""Suite tes Document Bot.

Berkas ini dijalankan SEBELUM modul tes mana pun, sehingga pustaka Telegram tiruan sudah
terpasang saat kode bot di-import. Jadi tes tidak butuh internet, token, maupun pustaka
python-telegram-bot yang asli.

Jalankan dari folder project:
    pytest                                      (butuh: pip install pytest)
    python -m unittest discover -s tests -t .   (tanpa instalasi apa pun)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import fakes  # noqa: E402

fakes.install_stubs()

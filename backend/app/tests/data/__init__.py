"""Пути к реальным документам банка. Происхождение файлов — в README.md."""

from pathlib import Path

DIR = Path(__file__).parent

# Тарифы для обслуживания физических лиц, 12 стр. — таблицы, суммы, сноски
TARIFY_FIZ_RU = DIR / "eskhata_tarify_fiz_ru.pdf"
# Шартномаи қарзӣ (кредитный договор, оферта), 7 стр. — таджикский целиком
SHARTNOMAI_QARZI_TJ = DIR / "eskhata_shartnomai_qarzi_tj.pdf"
# Договор карточного счёта, 11 стр. — страница 7 по-настоящему пустая
DOGOVOR_KART_SCHETA_RU = DIR / "eskhata_dogovor_kart_scheta_ru.pdf"

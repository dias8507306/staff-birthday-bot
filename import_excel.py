"""Загрузка сотрудников из Excel: python import_excel.py "Данные сотрудников.xlsx" """
import sys

import birthdays as bd

if len(sys.argv) != 2:
    raise SystemExit('Использование: python import_excel.py "путь\\к\\файлу.xlsx"')

store = bd.Store()
report = store.import_excel(sys.argv[1])
store.save()
print(bd.describe_import(report))
print(f"Сохранено в {store.path}")

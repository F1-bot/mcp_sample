"""
setup_db.py — створює store.db і наповнює початковими даними.

    python setup_db.py          # створити заново
    python setup_db.py --keep   # не чіпати, якщо база вже є

API-КЛЮЧІ: не потрібні.

ЧОМУ ОБМЕЖЕННЯ ЗАДАНІ В САМІЙ СХЕМІ
────────────────────────────────────────────────────────────────────
UNIQUE на назві товару, CHECK на ціні й залишку, FOREIGN KEY на
замовленнях — усе це можна було б перевіряти в Python, в інструментах
сервера. Але інструменти викликає МОДЕЛЬ, а вона помиляється.

Правило в схемі діє завжди: і коли інструмент забув перевірку, і коли
хтось зайшов у базу напряму, і коли ви допишете ще один інструмент
через місяць. Правило в Python діє лише там, де його згадали.
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

DB_FILE = Path(__file__).with_name("store.db")

SCHEMA = """
CREATE TABLE products (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT    NOT NULL UNIQUE,
    category TEXT    NOT NULL,
    price    REAL    NOT NULL CHECK (price >= 0),
    stock    INTEGER NOT NULL CHECK (stock >= 0)
);

CREATE TABLE orders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products (id),
    quantity   INTEGER NOT NULL CHECK (quantity > 0),
    order_date TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'shipped', 'delivered', 'cancelled'))
);

CREATE INDEX idx_orders_product ON orders (product_id);
CREATE INDEX idx_products_category ON products (category);
"""

PRODUCTS = [
    ("Ноутбук Pro X",     "Електроніка", 25000.00, 10),
    ("Бездротова мишка",  "Електроніка",   500.00, 50),
    ("Навушники",         "Електроніка",  1200.00, 30),
    ("Кавоварка",         "Техніка",      3500.00, 15),
    ("Крісло ергономічне", "Меблі",       4800.00,  8),
    ("Настільна лампа",   "Меблі",         650.00, 40),
]

# Замовлення задано явно, а не random: інакше кожен запуск дає інші
# числа, і перевірити відповідь моделі стає неможливо. Для лабораторної
# потрібна відтворюваність.
ORDERS = [
    ("Ноутбук Pro X",      1,  2, "delivered"),
    ("Бездротова мишка",   3,  5, "delivered"),
    ("Навушники",          2,  7, "shipped"),
    ("Кавоварка",          1, 10, "shipped"),
    ("Ноутбук Pro X",      2, 12, "pending"),
    ("Настільна лампа",    4, 15, "pending"),
    ("Крісло ергономічне", 1, 20, "cancelled"),
    ("Навушники",          1, 25, "delivered"),
]


def setup_database(keep: bool = False) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if DB_FILE.exists():
        if keep:
            print(f"База {DB_FILE.name} уже існує — не чіпаю (--keep).")
            return 0
        DB_FILE.unlink()
        print(f"Старий файл {DB_FILE.name} видалено.")

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)

    conn.executemany(
        "INSERT INTO products (name, category, price, stock) VALUES (?, ?, ?, ?)",
        PRODUCTS)

    ids = {name: pid for pid, name in
           conn.execute("SELECT id, name FROM products")}
    today = datetime.now()
    conn.executemany(
        "INSERT INTO orders (product_id, quantity, order_date, status) "
        "VALUES (?, ?, ?, ?)",
        [(ids[name], qty,
          (today - timedelta(days=days)).strftime("%Y-%m-%d"), status)
         for name, qty, days, status in ORDERS])

    conn.commit()

    products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    revenue = conn.execute("""
        SELECT ROUND(SUM(p.price * o.quantity), 2)
        FROM orders o JOIN products p ON p.id = o.product_id
        WHERE o.status <> 'cancelled'
    """).fetchone()[0]
    conn.close()

    print(f"База {DB_FILE.name} створена: {products} товарів, {orders} замовлень.")
    print(f"Виторг без скасованих: {revenue:.2f} грн "
          f"— цим числом можна перевіряти відповіді моделі.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Створює базу store.db")
    ap.add_argument("--keep", action="store_true",
                    help="не перестворювати, якщо база вже існує")
    sys.exit(setup_database(ap.parse_args().keep))

"""
server.py — MCP-сервер «Store Manager». Шаблон до Лабораторної роботи №4.

ЗАПУСК
    python setup_db.py                 # спершу база
    python server.py                   # stdio — так його запускає Claude Desktop
    python server.py --http            # Streamable HTTP на :8000/mcp
    python verify_server.py            # швидка перевірка без Claude й Inspector
    pytest -q                          # 45 тестів, без мережі й без моделі

    # MCP Inspector v2. НЕ `mcp dev` — та команда тягне застарілу 1.x:
    npx @modelcontextprotocol/inspector@latest --config inspector.config.json

API-КЛЮЧІ: не потрібні жодні.

════════════════════════════════════════════════════════════════════
ЩО ТУТ ЗРОБЛЕНО ІНАКШЕ, НІЖ У ТИПОВОМУ ПРИКЛАДІ З ІНТЕРНЕТУ
════════════════════════════════════════════════════════════════════

1. SQL-ІНСТРУМЕНТ НЕ ПЕРЕВІРЯЄ РЯДОК ЗАПИТУ.
   Поширений приклад робить так:

       if not query.strip().upper().startswith("SELECT"):
           raise ValueError("Only SELECT queries are allowed")

   Пряме DELETE це справді відкине. Але захистом не є: нижче, у
   коментарі до read_store_query, наведено запит, який ПОЧИНАЄТЬСЯ з
   SELECT і крутить мільйони ітерацій. Замість перевірки рядка тут
   три механізми рушія БД: з'єднання mode=ro, авторизатор операцій і
   бюджет кроків.

2. СПИСАННЯ ЗІ СКЛАДУ — ОДНИМ АТОМАРНИМ UPDATE.
   Типовий код читає залишок, порівнює в Python, потім пише. Між
   читанням і записом стан може змінитись — класичний TOCTOU. Тут
   умова «товару достатньо» живе всередині UPDATE ... WHERE stock >= ?,
   і база сама вирішує, чи дозволити операцію.

3. ІНСТРУМЕНТИ ПОЗНАЧЕНІ АНОТАЦІЯМИ.
   readOnlyHint / destructiveHint — підказки клієнтові, що саме робить
   інструмент. Протокол називає їх саме «hint»: це підказка, а не
   гарантія, і клієнт не зобов'язаний їм вірити. Але без них клієнт не
   має жодного шансу відрізнити читання від запису.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psutil

# ══════════════════════════════════════════════════════════════════
# СУМІСНІСТЬ SDK: 2.x і 1.x
# ══════════════════════════════════════════════════════════════════
# В офіційному MCP Python SDK 2.0 клас FastMCP перейменовано на
# MCPServer, а модуль mcp.server.fastmcp прибрано — імпорт із нього
# кидає ModuleNotFoundError з підказкою про міграцію.
#
# Чому не просто написати новий імпорт: деякі популярні адаптери
# (langchain-mcp-adapters, smolagents[mcp]) досі вимагають mcp<2 і
# ТИХО відкочують вам SDK. Код нижче працює на обох версіях — на дроті
# протокол однаковий, тож клієнт на 1.x спокійно говорить із сервером
# на 2.x.
try:
    from mcp.server.mcpserver import MCPServer as _ServerClass   # SDK 2.x
    SDK_MAJOR = 2
except ModuleNotFoundError:                                       # SDK 1.x
    from mcp.server.fastmcp import FastMCP as _ServerClass
    SDK_MAJOR = 1

try:
    from mcp.types import ToolAnnotations
except ImportError:                       # дуже старі 1.x без анотацій
    ToolAnnotations = None


def _annot(**kwargs):
    """Анотації інструмента, якщо встановлений SDK їх підтримує."""
    return ToolAnnotations(**kwargs) if ToolAnnotations is not None else None


def setup_console() -> None:
    """UTF-8 на виводі: інакше український текст падає з UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


setup_console()

DB_FILE = Path(__file__).with_name("store.db")
MAX_ROWS = 200              # скільком рядкам дозволяємо повернутись
MAX_STEP_BATCHES = 2_000    # бюджет «кроків» SQLite (× 1000 операцій)

mcp = _ServerClass("Store Manager")


# ══════════════════════════════════════════════════════════════════
# ДОСТУП ДО БАЗИ
# ══════════════════════════════════════════════════════════════════
def _require_db() -> None:
    if not DB_FILE.exists():
        raise FileNotFoundError(
            f"{DB_FILE.name} не знайдено. Спершу запустіть: python setup_db.py")


# Операції, потрібні звичайному SELECT. Усе інше авторизатор відкине.
_ALLOWED_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT,     # сам оператор SELECT
    sqlite3.SQLITE_READ,       # читання конкретної колонки
    sqlite3.SQLITE_FUNCTION,   # COUNT, ROUND, LENGTH, …
    sqlite3.SQLITE_RECURSIVE,  # WITH RECURSIVE
})


def _authorizer(action: int, arg1, arg2, db_name, trigger):
    """
    Білий список операцій SQLite.

    Навіщо він, якщо з'єднання вже відкрите як mode=ro. Бо mode=ro
    стосується ЛИШЕ головної бази. Команда

        ATTACH DATABASE 'evil.db' AS e

    приєднує НОВИЙ файл зі звичайними правами на запис, і далі
    CREATE TABLE e.x (…) спокійно виконується.

    set_authorizer працює на рівні рушія: SQLite питає дозволу на кожну
    операцію ще до виконання. ATTACH, PRAGMA, запис, load_extension —
    усе це просто не має дозволу.
    """
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def connect_ro() -> sqlite3.Connection:
    """З'єднання ТІЛЬКИ ДЛЯ ЧИТАННЯ — для SQL, який прислала модель."""
    _require_db()
    conn = sqlite3.connect(f"file:{DB_FILE.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.set_authorizer(_authorizer)

    # Обмежувач «кроків»: SQLite періодично викликає цей обробник, і
    # якщо він повертає щось правдиве — запит скасовується. Саме це
    # зупиняє рекурсивні бомби, які починаються зі слова SELECT.
    budget = {"n": 0}

    def guard() -> int:
        budget["n"] += 1
        return 1 if budget["n"] > MAX_STEP_BATCHES else 0

    conn.set_progress_handler(guard, 1000)
    return conn


def connect_rw() -> sqlite3.Connection:
    """З'єднання для запису — лише для інструментів, що змінюють дані."""
    _require_db()
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ══════════════════════════════════════════════════════════════════
# RESOURCES — пасивні дані, аналог GET
# ══════════════════════════════════════════════════════════════════
@mcp.resource("store://products")
def list_products() -> str:
    """Повний каталог товарів у форматі JSON."""
    with connect_ro() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, name, category, price, stock FROM products ORDER BY id")]
    return json.dumps(rows, indent=2, ensure_ascii=False)


@mcp.resource("store://orders")
def list_orders() -> str:
    """Усі замовлення з назвами товарів і сумами рядків."""
    with connect_ro() as conn:
        rows = [dict(r) for r in conn.execute("""
            SELECT o.id, p.name AS product, o.quantity, p.price,
                   ROUND(p.price * o.quantity, 2) AS total,
                   o.order_date, o.status
            FROM orders o JOIN products p ON p.id = o.product_id
            ORDER BY o.order_date DESC, o.id DESC
        """)]
    return json.dumps(rows, indent=2, ensure_ascii=False)


@mcp.resource("store://schema")
def db_schema() -> str:
    """Схема бази (DDL) — щоб модель бачила, з чим працює."""
    with connect_ro() as conn:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type DESC"
        ).fetchall()
    return "\n\n".join(r[0] for r in rows)


# ══════════════════════════════════════════════════════════════════
# TOOLS — читання
# ══════════════════════════════════════════════════════════════════
@mcp.tool(annotations=_annot(read_only_hint=True, idempotent_hint=True,
                             open_world_hint=False))
def read_store_query(query: str) -> str:
    """Виконує SQL-запит НА ЧИТАННЯ і повертає рядки як JSON-РЯДОК.

    Повертається саме рядок, не словник: розберіть його json.loads()
    перед індексуванням. Формат: {"row_count": int, "rows": [ {...} ]}.

    Хай роботу робить SQL — GROUP BY, SUM, AVG, JOIN доступні. Не
    тягніть сирі рядки, щоб складати їх самостійно.

    З'єднання відкрите тільки для читання й захищене авторизатором
    SQLite, тому запис, ATTACH і PRAGMA неможливі. Схему дивіться в
    store://schema, а змінювати дані — через add_product, create_order
    і restock_product.

    Args:
        query: Один SQL-оператор, який лише читає дані
    """
    try:
        with connect_ro() as conn:
            cursor = conn.execute(query)
            rows = [dict(r) for r in cursor.fetchmany(MAX_ROWS)]
            truncated = cursor.fetchone() is not None
    except sqlite3.Warning as exc:
        # sqlite3.Warning НЕ є підклас sqlite3.Error — окрема гілка
        # потрібна, інакше складений запит ("SELECT 1; DROP TABLE x")
        # вилітає необробленим винятком замість зрозумілої відмови.
        return f"Error: {exc} Надсилайте рівно один оператор за виклик."
    except sqlite3.Error as exc:
        text = str(exc).lower()
        if "not authorized" in text:
            return ("Error: цей інструмент лише читає. Запис, ATTACH, PRAGMA і "
                    "load_extension заблоковані авторизатором SQLite.")
        if "readonly database" in text:
            return ("Error: інструмент відкриває базу тільки для читання. "
                    "Для змін є add_product, create_order, restock_product.")
        if "interrupted" in text:
            return ("Error: запит вичерпав бюджет кроків. Додайте WHERE або LIMIT. "
                    "Саме так зупиняються рекурсивні запити, які починаються "
                    "словом SELECT.")
        return f"SQL error: {type(exc).__name__}: {exc}"
    except FileNotFoundError as exc:
        return f"Error: {exc}"

    payload: dict = {"row_count": len(rows), "rows": rows}
    if truncated:
        payload["note"] = (f"Показано лише перші {MAX_ROWS} рядків. "
                           "Звужте запит через WHERE або LIMIT.")
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════
# TOOLS — запис
# ══════════════════════════════════════════════════════════════════
@mcp.tool(annotations=_annot(read_only_hint=False, destructive_hint=False,
                             idempotent_hint=False, open_world_hint=False))
def add_product(name: str, category: str, price: float, stock: int) -> str:
    """Додає новий товар до каталогу.

    Args:
        name: Назва товару (мусить бути унікальною)
        category: Категорія, напр. 'Електроніка' або 'Меблі'
        price: Ціна в гривнях, >= 0
        stock: Початковий залишок на складі, >= 0
    """
    if price < 0 or stock < 0:
        return "Error: ціна й залишок не можуть бути від'ємними."
    if not name.strip():
        return "Error: назва не може бути порожньою."

    try:
        with connect_rw() as conn:
            cursor = conn.execute(
                "INSERT INTO products (name, category, price, stock) "
                "VALUES (?, ?, ?, ?)",
                (name.strip(), category.strip(), float(price), int(stock)))
            return (f"OK: додано «{name}» ({category}, {price:.2f} грн, "
                    f"залишок {stock}), id={cursor.lastrowid}")
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc):
            return f"Error: товар із назвою «{name}» уже існує."
        return f"Error: база відхилила цей рядок ({exc})."
    except (sqlite3.Error, FileNotFoundError) as exc:
        return f"Error: {exc}"


@mcp.tool(annotations=_annot(read_only_hint=False, destructive_hint=True,
                             idempotent_hint=False, open_world_hint=False))
def create_order(product_name: str, quantity: int = 1) -> str:
    """Створює замовлення й списує товар зі складу одним атомарним кроком.

    Args:
        product_name: Повна або часткова назва товару
        quantity: Скільько одиниць замовити, > 0
    """
    if quantity <= 0:
        return "Error: кількість мусить бути більшою за нуль."

    try:
        with connect_rw() as conn:
            matches = conn.execute(
                "SELECT id, name, price, stock FROM products WHERE name LIKE ? "
                "ORDER BY LENGTH(name) LIMIT 5",
                (f"%{product_name}%",)).fetchall()

            if not matches:
                return f"Error: товару за запитом «{product_name}» не знайдено."
            if len(matches) > 1 and matches[0]["name"].lower() != product_name.lower():
                names = ", ".join(f"«{m['name']}»" for m in matches)
                return (f"Error: «{product_name}» неоднозначно — підходять {names}. "
                        "Повторіть виклик із точною назвою.")

            product = matches[0]

            # ОДИН атомарний UPDATE: умову наявності перевіряє база в
            # межах цієї ж операції, тож між перевіркою і списанням
            # ніхто не встигне влізти. Саме цим він відрізняється від
            # типового «прочитати stock → порівняти в Python → записати».
            cursor = conn.execute(
                "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                (quantity, product["id"], quantity))

            if cursor.rowcount == 0:
                return (f"Error: на складі лише {product['stock']} од. "
                        f"товару «{product['name']}».")

            conn.execute(
                "INSERT INTO orders (product_id, quantity, order_date, status) "
                "VALUES (?, ?, ?, 'pending')",
                (product["id"], quantity, datetime.now().strftime("%Y-%m-%d")))

            total = product["price"] * quantity
            left = product["stock"] - quantity
            return (f"OK: замовлено {quantity}× «{product['name']}» = "
                    f"{total:.2f} грн. Залишок: {left}.")
    except (sqlite3.Error, FileNotFoundError) as exc:
        return f"Error: {exc}"


@mcp.tool(annotations=_annot(read_only_hint=False, destructive_hint=False,
                             idempotent_hint=False, open_world_hint=False))
def restock_product(product_name: str, quantity: int) -> str:
    """Збільшує залишок наявного товару на складі.

    Args:
        product_name: Точна назва товару
        quantity: Скільки одиниць додати, > 0
    """
    if quantity <= 0:
        return "Error: кількість мусить бути більшою за нуль."
    try:
        with connect_rw() as conn:
            cursor = conn.execute(
                "UPDATE products SET stock = stock + ? WHERE name = ?",
                (quantity, product_name))
            if cursor.rowcount == 0:
                return f"Error: товару з точною назвою «{product_name}» немає."
            new_stock = conn.execute(
                "SELECT stock FROM products WHERE name = ?", (product_name,)
            ).fetchone()["stock"]
            return f"OK: «{product_name}» поповнено на {quantity}, тепер {new_stock}."
    except (sqlite3.Error, FileNotFoundError) as exc:
        return f"Error: {exc}"


# ══════════════════════════════════════════════════════════════════
# TOOLS — системні, щоб показати сервер не лише над базою
# ══════════════════════════════════════════════════════════════════
@mcp.tool(annotations=_annot(read_only_hint=True, idempotent_hint=False,
                            open_world_hint=False))
def get_system_metrics() -> str:
    """Поточні метрики машини: CPU, пам'ять, диск. Повертає JSON-рядок."""
    # Диск беремо той, на якому лежить сервер. У вихідному прикладі тут
    # стояв '/', і на Windows це давало метрики випадкового диска.
    disk_root = os.path.abspath(os.sep if os.name != "nt"
                                else Path(__file__).resolve().anchor)
    return json.dumps({
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory": dict(psutil.virtual_memory()._asdict()),
        "disk_root": disk_root,
        "disk": dict(psutil.disk_usage(disk_root)._asdict()),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2)


@mcp.tool(annotations=_annot(read_only_hint=True, idempotent_hint=False,
                            open_world_hint=False))
def generate_uuid_v7() -> str:
    """Генерує UUID версії 7 — впорядкований за часом (RFC 9562)."""
    # uuid.uuid7() з'явився лише в Python 3.14. Щоб працювало і на
    # 3.10, збираємо вручну за RFC 9562:
    #   48 біт — час Unix у мілісекундах
    #    4 біти — версія (7)
    #   12 біт — випадкові
    #    2 біти — варіант (0b10)
    #   62 біти — випадкові
    if hasattr(uuid, "uuid7"):                   # Python 3.14+
        return str(uuid.uuid7())

    ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=value))


@mcp.resource("system://processes")
def get_top_processes() -> str:
    """Десять процесів із найбільшим навантаженням на CPU, у JSON."""
    # cpu_percent() при ПЕРШОМУ виклику завжди повертає 0.0: він міряє
    # різницю між двома замірами. Тому проходимо список двічі — інакше
    # усі значення будуть нулями.
    procs = list(psutil.process_iter(["pid", "name"]))
    for p in procs:
        try:
            p.cpu_percent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    time.sleep(0.5)                       # інтервал між замірами

    cpu_count = psutil.cpu_count() or 1
    results = []
    for p in procs:
        try:
            info = dict(p.info)
            # Ділимо на кількість ядер, щоб числа збігалися з Диспетчером
            # задач: psutil рахує 100% як одне повністю завантажене ядро.
            info["cpu_percent"] = round(p.cpu_percent() / cpu_count, 1)
            results.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    top = sorted(results, key=lambda x: x["cpu_percent"] or 0, reverse=True)[:10]
    return json.dumps(top, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════
# PROMPT — шаблон запиту
# ══════════════════════════════════════════════════════════════════
@mcp.prompt()
def sql_analyst(question: str) -> str:
    """Готує промпт для відповіді на бізнес-питання через SQL.

    Args:
        question: Бізнес-питання, на яке треба відповісти
    """
    try:
        schema = db_schema()
    except FileNotFoundError:
        schema = "(база ще не створена — запустіть setup_db.py)"

    return f"""Ти SQL-аналітик і працюєш із цією базою SQLite.

Схема:
{schema}

Питання: {question}

Як діяти:
1. Напиши ОДИН SQL-запит, який відповідає на питання.
2. Виконай його через read_store_query. З'єднання відкрите тільки для
   читання, тому зіпсувати дані неможливо — це зроблено навмисно.
3. Назви числа, які повернув запит. Не оцінюй «на око», не округлюй
   далі двох знаків і не додавай цифр, яких у результаті немає.
"""


# ══════════════════════════════════════════════════════════════════
# ЗАПУСК
# ══════════════════════════════════════════════════════════════════
def _note(text: str) -> None:
    """
    Службове повідомлення — лише коли на stderr дивиться людина.

    У stdout писати не можна взагалі: там живе JSON-RPC, і будь-який
    print зламає протокол. А коли сервер піднімає клієнт, банер
    перетворюється на сміття в його логах.
    """
    if sys.stderr.isatty():
        print(f"  {text}", file=sys.stderr)


if __name__ == "__main__":
    if not DB_FILE.exists():
        _note(f"[!] {DB_FILE.name} немає. Спершу: python setup_db.py")

    if "--http" in sys.argv:
        _note(f"MCP SDK {SDK_MAJOR}.x · Streamable HTTP: http://127.0.0.1:8000/mcp")
        mcp.run(transport="streamable-http")
    else:
        _note(f"MCP SDK {SDK_MAJOR}.x · stdio")
        mcp.run()

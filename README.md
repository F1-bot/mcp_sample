# Store Manager — MCP-сервер. Шаблон до Лабораторної роботи №4

Робочий MCP-сервер над базою SQLite: **Tools + Resources + Prompts**.
Беріть його як відправну точку для своєї предметної області.

Перевірено на **MCP Python SDK 2.3.0**, Python 3.10, Windows.
**API-ключі не потрібні жодні.**

> Про ревізію протоколу. SDK 2.3.0 підтримує `2026-07-28`
> (`mcp.types.LATEST_PROTOCOL_VERSION`), але конкретна ревізія
> **узгоджується в рукостисканні** з клієнтом. У нашому прогоні по
> stdio клієнт і сервер зійшлися на `2025-11-25`. Тобто версія в
> `initialize` — це не версія вашого SDK, і плутати їх не варто.

---

## Швидкий старт

```bash
git clone https://github.com/F1-bot/mcp_sample.git
cd mcp_sample

python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Mac/Linux

pip install -r requirements.txt

python setup_db.py              # створити базу
python verify_server.py         # перевірити, що все на місці
pytest -q                       # 41 тест, без мережі й моделі
```

Очікуваний результат `verify_server.py`:

```
  Store Manager · MCP SDK 2.x
  пакет mcp 2.3.0

── TOOLS ──
  add_product            [пише]
  create_order           [ПИШЕ, руйнівний]
  generate_uuid_v7       [читає]
  get_system_metrics     [читає]
  read_store_query       [читає]
  restock_product        [пише]
  всього: 6

── RESOURCES ──  4      ── PROMPTS ──  1

── ПЕРЕВІРКА ЗАХИСТУ ──
  ok  читання працює
  ok  DELETE заблоковано
  ok  ATTACH заблоковано
  ok  PRAGMA заблоковано
```

---

## ⚠️ Перше, на що треба звернути увагу: версія SDK

У 2026 році офіційний MCP Python SDK перейшов на **2.x**, і клас
`FastMCP` перейменовано на `MCPServer`. Модуль `mcp.server.fastmcp`
**прибрано**:

```python
>>> from mcp.server.fastmcp import FastMCP
ModuleNotFoundError: No module named 'mcp.server.fastmcp'. This is mcp 2.x,
where FastMCP was renamed to MCPServer (from mcp.server.mcpserver import
MCPServer) ... see the migration guide
```

Переважна більшість прикладів в інтернеті досі на 1.x і просто **не
запуститься**. У `server.py` стоїть шим, який бере потрібний клас:

```python
try:
    from mcp.server.mcpserver import MCPServer as _ServerClass   # SDK 2.x
    SDK_MAJOR = 2
except ModuleNotFoundError:
    from mcp.server.fastmcp import FastMCP as _ServerClass       # SDK 1.x
    SDK_MAJOR = 1
```

Навіщо шим, якщо можна просто написати новий імпорт: деякі популярні
адаптери (`langchain-mcp-adapters`, `smolagents[mcp]`) вимагають
`mcp<2` і **тихо відкочують** вам SDK. На дроті протокол однаковий, тож
клієнт на 1.x спокійно говорить із сервером на 2.x.

---

## Склад репозиторію

| Файл | Що це |
|---|---|
| `setup_db.py` | створює `store.db`: 2 таблиці, фіксовані дані |
| `server.py` | сам сервер: 6 Tools, 4 Resources, 1 Prompt |
| `verify_server.py` | перевірка без Claude й Inspector — запускайте першим |
| `tests/test_server.py` | 41 тест |
| `requirements.txt` | зафіксовані версії |
| `claude_desktop_config.example.json` | приклад конфігу для Claude Desktop |

---

## Підключення до Claude Desktop

`File → Settings → Developer → Edit Config`, потім перезапустити Claude.

```json
{
  "mcpServers": {
    "StoreManager": {
      "command": "C:\\шлях\\до\\mcp_sample\\.venv\\Scripts\\python.exe",
      "args": ["C:\\шлях\\до\\mcp_sample\\server.py"]
    }
  }
}
```

Два важливі моменти:

**Шлях до Python — із вашого venv, не системний.** Claude Desktop
запускає сервер власним процесом і нічого не знає про активоване
середовище. Якщо вказати системний `python`, сервер не знайде `mcp` і
тихо не підніметься.

**Шляхи абсолютні.** Робоча тека процесу, який запускає Claude, вам
невідома.

Готовий зразок — у `claude_desktop_config.example.json`.

### MCP Inspector — швидше за Claude

```bash
mcp dev server.py
```

Відкриється веб-інтерфейс, де видно всі Tools / Resources / Prompts і
де їх можна викликати руками. Для розробки це значно швидше, ніж
перезапускати Claude після кожної правки.

---

## Що тут зроблено інакше, ніж у типовому приміксі з інтернету

### 1. SQL-інструмент не перевіряє рядок запиту

Поширений приклад виглядає так:

```python
if not query.strip().upper().startswith("SELECT"):
    raise ValueError("Only SELECT queries are allowed for safety.")
```

Пряме `DELETE` це справді відкине — перевірено. **Але захистом не є.**
Ось запит, який цю перевірку проходить:

```sql
SELECT count(*) FROM (WITH RECURSIVE c(x) AS
  (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 50000000) SELECT x FROM c)
```

Починається словом `SELECT`, а крутить 50 мільйонів ітерацій. На нашому
замірі версія без обмежень відпрацювала 3 млн ітерацій за 0.58 с —
збільште число, і сервер займе ядро на хвилини.

Другий приклад: `SELECT a.id FROM products a, products b, products c,
products d, products e` з таблиці на 6 рядків дає **7776 рядків і 85 КБ
JSON**. Усе це поїде в контекст моделі й буде оплачене.

Замість перевірки рядка тут три механізми **рушія БД**:

```python
conn = sqlite3.connect("file:store.db?mode=ro", uri=True)  # 1. тільки читання
conn.set_authorizer(_authorizer)                            # 2. білий список операцій
conn.set_progress_handler(guard, 1000)                      # 3. бюджет кроків
```

Не «ми не дозволяємо», а «воно не вміє».

**Чому `mode=ro` недостатньо саме по собі.** Він стосується лише
головної бази. `ATTACH DATABASE 'evil.db' AS e` приєднує **новий** файл
зі звичайними правами на запис. Тримає це авторизатор — є тест
`test_attach_creates_no_file`.

### 2. Списання зі складу — одним атомарним UPDATE

Типовий код:

```python
cursor.execute("SELECT stock FROM products WHERE id = ?", (pid,))
if stock < quantity:                 # ← перевірка
    return "Недостатньо товару"
cursor.execute("UPDATE products SET stock = stock - ?", ...)   # ← і запис
```

Між перевіркою і записом стан може змінитися — класичний **TOCTOU**. А
MCP-сервер цілком може обслуговувати кілька клієнтів одночасно. Тут
умова живе всередині UPDATE:

```python
conn.execute("UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
             (quantity, pid, quantity))
if cursor.rowcount == 0:
    return f"Error: на складі лише {stock} од."
```

База сама вирішує, чи дозволити операцію. Тести:
`test_cannot_oversell`, `test_order_decrements_stock_exactly_once`.

### 3. Інструменти позначені анотаціями

```python
@mcp.tool(annotations=ToolAnnotations(read_only_hint=False,
                                      destructive_hint=True))
def create_order(...):
```

`readOnlyHint` / `destructiveHint` кажуть клієнтові, що робить
інструмент — щоб той знав, коли спитати дозволу в людини. Протокол
називає їх саме «hint»: це підказка, не гарантія, і клієнт не
зобов'язаний їм вірити. Але без них клієнт **не має жодного шансу**
відрізнити читання від запису.

### 4. Обмеження задані в схемі, а не лише в Python

`UNIQUE` на назві, `CHECK (price >= 0)`, `CHECK (status IN (...))`,
`FOREIGN KEY`. Інструменти викликає **модель**, і вона помиляється.
Правило в схемі діє завжди; правило в Python — лише там, де його
згадали. Тести `test_schema_blocks_*` перевіряють саме це.

### 5. `generate_uuid_v7` справді повертає v7

У вихідному прикладі функція звалася `generate_uuid_v7`, а повертала
`uuid4` — у докстрінгу стояло «simulated as v4 for this demo». Тут
справжній RFC 9562: 48 біт часу, версія 7, варіант, решта випадкова.
`uuid.uuid7()` зі стандартної бібліотеки з'явився лише в Python 3.14,
тому для 3.10 збираємо вручну. Тести перевіряють і версію, і
впорядкованість за часом.

---

## API сервера

### Tools

| Інструмент | Анотація | Що робить |
|---|---|---|
| `read_store_query(query)` | читає | SQL на читання, повертає JSON-рядок |
| `add_product(name, category, price, stock)` | пише | додає товар |
| `create_order(product_name, quantity)` | **руйнівний** | замовлення + атомарне списання |
| `restock_product(product_name, quantity)` | пише | поповнює склад |
| `get_system_metrics()` | читає | CPU, пам'ять, диск |
| `generate_uuid_v7()` | читає | UUID v7 за RFC 9562 |

### Resources

| URI | Що віддає |
|---|---|
| `store://products` | каталог товарів, JSON |
| `store://orders` | замовлення з назвами й сумами, JSON |
| `store://schema` | DDL бази |
| `system://processes` | 10 процесів за навантаженням на CPU |

### Prompts

| Промпт | Аргументи |
|---|---|
| `sql_analyst(question)` | бізнес-питання → інструкція зі схемою БД |

---

## Схема бази

```sql
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
               CHECK (status IN ('pending','shipped','delivered','cancelled'))
);
```

Дані фіксовані, не випадкові: **виторг без скасованих = 86 200.00 грн**.
Цим числом зручно перевіряти, чи модель не вигадала цифру.

---

## Що робити далі (ваше завдання)

1. Оберіть свою предметну область із таблиці варіантів.
2. Перепишіть `setup_db.py` під свої дві таблиці — **з обмеженнями в
   схемі**, не лише в Python.
3. Замініть ресурси, інструменти й промпт на свої. Мінімум:
   один Resource, один Tool на запис, один Tool на читання, один Prompt.
4. **Залиште механізми захисту**: `mode=ro` + авторизатор для
   читального SQL, атомарний UPDATE там, де списуються ресурси.
5. Допишіть тести під свою область — за зразком `tests/test_server.py`.
6. Перевірте через `verify_server.py`, потім `mcp dev server.py`, потім
   Claude Desktop.

---

## Якщо не вийшло

| Симптом | Причина |
|---|---|
| `ModuleNotFoundError: mcp.server.fastmcp` | ви на SDK 2.x, а приклад — на 1.x. Дивіться шим у `server.py` |
| `store.db не знайдено` | `python setup_db.py` |
| сервер не з'явився в Claude | шлях до `python` має бути з venv і абсолютний |
| Claude бачить сервер, але не інструменти | подивіться лог Claude Desktop; найчастіше сервер упав на імпорті |
| щось пише у stdout і протокол ламається | у stdio-режимі stdout належить JSON-RPC. Друкуйте лише в stderr |
| `mcp dev` не запускається | потрібен Node.js: Inspector ставиться через `npx` |

---

## Посилання

- Специфікація MCP: https://modelcontextprotocol.io
- Python SDK і **гід міграції 1.x → 2.x**: https://py.sdk.modelcontextprotocol.io
- Репозиторій SDK: https://github.com/modelcontextprotocol/python-sdk
- Каталог серверів: https://mcp.so
- RFC 9562 (UUID v7): https://www.rfc-editor.org/rfc/rfc9562

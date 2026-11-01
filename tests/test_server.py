"""
Тести до Лабораторної роботи №4. Без мережі, без моделі, без Claude.

    pytest -q

Чому тести є в лабораторній з MCP. Інструмент сервера — це звичайна
функція, і саме її треба покривати тестами. Модель у перевірках не
потрібна: вона повільна, недетермінована і нічого не додає до питання
«чи правильно рахує SQL і чи справді заблокований запис».

Кожен тест нижче відповідає на одне питання, яке можна поставити на
захисті роботи.
"""
import json
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server as srv          # noqa: E402
import setup_db               # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Свіжа база у тимчасовій теці — тести не чіпають вашу store.db."""
    db = tmp_path / "store.db"
    monkeypatch.setattr(setup_db, "DB_FILE", db)
    setup_db.setup_database()
    monkeypatch.setattr(srv, "DB_FILE", db)
    return db


# ══════════════════════════════════════════════════════════════════
# 1. Читання працює
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("query", [
    "SELECT COUNT(*) AS n FROM products",
    "SELECT name, price FROM products ORDER BY price DESC LIMIT 2",
    "SELECT category, SUM(stock) AS total FROM products GROUP BY category",
    "SELECT p.name, SUM(o.quantity) AS sold FROM orders o "
    "JOIN products p ON p.id = o.product_id GROUP BY p.name",
    "WITH t AS (SELECT * FROM products) SELECT COUNT(*) AS n FROM t",
])
def test_reads_work(store, query):
    out = json.loads(srv.read_store_query(query))
    assert out["row_count"] >= 1


def test_sql_does_the_arithmetic(store):
    """
    Виторг має рахувати SQL, а не модель. Число фіксоване в setup_db,
    тому його можна перевіряти.
    """
    out = json.loads(srv.read_store_query("""
        SELECT ROUND(SUM(p.price * o.quantity), 2) AS revenue
        FROM orders o JOIN products p ON p.id = o.product_id
        WHERE o.status <> 'cancelled'
    """))
    assert out["rows"][0]["revenue"] == pytest.approx(86200.00)


# ══════════════════════════════════════════════════════════════════
# 2. Запис через SQL-інструмент неможливий
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("hostile", [
    "DELETE FROM products",
    "UPDATE products SET price = 0",
    "DROP TABLE orders",
    "INSERT INTO products (name, category, price, stock) VALUES ('x','y',1,1)",
    "PRAGMA writable_schema = 1",
    "ATTACH DATABASE 'evil.db' AS e",
    "SELECT load_extension('evil')",
])
def test_writes_are_blocked(store, hostile):
    out = srv.read_store_query(hostile)
    assert out.startswith(("Error", "SQL error")), f"пропущено: {hostile}"


def test_attach_creates_no_file(store, tmp_path, monkeypatch):
    """
    mode=ro сам по собі НЕ тримає: ATTACH приєднує новий файл зі
    звичайними правами на запис. Тримає авторизатор.
    """
    monkeypatch.chdir(tmp_path)
    evil = tmp_path / "evil.db"
    srv.read_store_query(f"ATTACH DATABASE '{evil.as_posix()}' AS e")
    assert not evil.exists(), "ATTACH створив файл — авторизатор не працює"


def test_stacked_statements_rejected(store):
    """
    sqlite3.Warning НЕ є підклас sqlite3.Error. Без окремої гілки
    except цей запит вилітає необробленим винятком.
    """
    out = srv.read_store_query("SELECT 1; DELETE FROM products")
    assert "one statement" in out.lower()
    with sqlite3.connect(store) as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 6


def test_step_budget_stops_a_query_that_starts_with_select(store):
    """
    Головний аргумент проти перевірки startswith("SELECT"): цей запит
    її проходить. Зупиняє його бюджет кроків.
    """
    bomb = ("SELECT count(*) FROM (WITH RECURSIVE c(x) AS "
            "(SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 50000000) "
            "SELECT x FROM c)")
    assert bomb.strip().upper().startswith("SELECT")
    out = srv.read_store_query(bomb)
    assert "бюджет" in out


def test_row_limit_is_reported(store):
    """Декартів добуток із шести рядків дає 7776 — модель не має їх бачити."""
    out = json.loads(srv.read_store_query(
        "SELECT a.id FROM products a, products b, products c, products d, products e"))
    assert out["row_count"] == srv.MAX_ROWS
    assert "note" in out


# ══════════════════════════════════════════════════════════════════
# 3. Списання зі складу — атомарне
# ══════════════════════════════════════════════════════════════════
def test_cannot_oversell(store):
    out = srv.create_order("Крісло ергономічне", 999)
    assert out.startswith("Error")
    with sqlite3.connect(store) as conn:
        stock = conn.execute(
            "SELECT stock FROM products WHERE name = 'Крісло ергономічне'"
        ).fetchone()[0]
    assert stock == 8, "залишок змінився, хоча замовлення відхилено"


def test_order_decrements_stock_exactly_once(store):
    with sqlite3.connect(store) as conn:
        before = conn.execute(
            "SELECT stock FROM products WHERE name = 'Навушники'").fetchone()[0]

    assert srv.create_order("Навушники", 4).startswith("OK")

    with sqlite3.connect(store) as conn:
        after = conn.execute(
            "SELECT stock FROM products WHERE name = 'Навушники'").fetchone()[0]
        orders = conn.execute(
            "SELECT COUNT(*) FROM orders o JOIN products p ON p.id = o.product_id "
            "WHERE p.name = 'Навушники'").fetchone()[0]
    assert after == before - 4
    assert orders == 3, "замовлення не створено або створено двічі"


def test_ambiguous_name_is_refused(store):
    """«а» підходить до кількох товарів — краще перепитати, ніж угадати."""
    out = srv.create_order("а", 1)
    assert out.startswith("Error")
    assert "неоднозначно" in out


@pytest.mark.parametrize("quantity", [0, -1, -100])
def test_non_positive_quantity_refused(store, quantity):
    assert srv.create_order("Навушники", quantity).startswith("Error")
    assert srv.restock_product("Навушники", quantity).startswith("Error")


# ══════════════════════════════════════════════════════════════════
# 4. Обмеження, задані в СХЕМІ, діють
# ══════════════════════════════════════════════════════════════════
def test_duplicate_name_refused(store):
    out = srv.add_product("Навушники", "Електроніка", 100, 1)
    assert out.startswith("Error")
    assert "уже існує" in out


def test_negative_price_refused_by_tool(store):
    assert srv.add_product("Новий", "Тест", -5, 1).startswith("Error")


def test_schema_blocks_negative_price_even_without_the_tool(store):
    """
    Перевірка в інструменті — не те саме, що обмеження в схемі.
    Інструмент можна обійти, схему — ні.
    """
    with sqlite3.connect(store) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO products (name, category, price, stock) "
                         "VALUES ('Поганий', 'Тест', -1, 1)")


def test_schema_blocks_unknown_order_status(store):
    with sqlite3.connect(store) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO orders (product_id, quantity, order_date, status) "
                         "VALUES (1, 1, '2026-01-01', 'teleported')")


def test_restock_requires_exact_name(store):
    assert srv.restock_product("Навуш", 5).startswith("Error")
    assert srv.restock_product("Навушники", 5).startswith("OK")


# ══════════════════════════════════════════════════════════════════
# 5. Анотації інструментів
# ══════════════════════════════════════════════════════════════════
def test_every_tool_has_annotations():
    """
    Без readOnlyHint/destructiveHint клієнт не може відрізнити
    читання від запису й не знає, коли питати дозволу в людини.
    """
    import asyncio

    tools = asyncio.run(srv.mcp.list_tools())
    missing = [t.name for t in tools if t.annotations is None]
    assert not missing, f"без анотацій: {missing}"


def test_write_tools_are_marked_as_writes():
    import asyncio

    tools = {t.name: t for t in asyncio.run(srv.mcp.list_tools())}
    for name in ("add_product", "create_order", "restock_product"):
        assert tools[name].annotations.read_only_hint is False, name
    assert tools["create_order"].annotations.destructive_hint is True
    assert tools["read_store_query"].annotations.read_only_hint is True


def test_server_exposes_all_three_capabilities():
    """Tools, Resources і Prompts — мінімум лабораторної."""
    import asyncio

    assert len(asyncio.run(srv.mcp.list_tools())) >= 3
    assert len(asyncio.run(srv.mcp.list_resources())) >= 2
    assert len(asyncio.run(srv.mcp.list_prompts())) >= 1


# ══════════════════════════════════════════════════════════════════
# 6. Сумісність SDK і чистота імпорту
# ══════════════════════════════════════════════════════════════════
def test_sdk_shim_picked_a_real_class():
    assert srv.SDK_MAJOR in (1, 2)
    assert srv.mcp is not None


def test_fastmcp_module_absent_exactly_on_sdk_2():
    """Шим існує саме тому, що модуль прибрали у 2.x."""
    import importlib

    try:
        importlib.import_module("mcp.server.fastmcp")
        present = True
    except ModuleNotFoundError:
        present = False
    assert present == (srv.SDK_MAJOR == 1)


def test_import_does_not_create_a_database(tmp_path, monkeypatch):
    """
    Імпорт модуля не має мати побічних ефектів: Inspector і тести
    імпортують server.py, і створення файлів при імпорті — сюрприз.
    """
    monkeypatch.chdir(tmp_path)
    import importlib

    importlib.reload(srv)
    assert not list(tmp_path.glob("*.db"))


# ══════════════════════════════════════════════════════════════════
# 7. Системні інструменти
# ══════════════════════════════════════════════════════════════════
def test_uuid_v7_is_really_v7():
    """
    У вихідному прикладі функція звалася generate_uuid_v7, а повертала
    uuid4 — «simulated as v4 for this demo». Тут справжній RFC 9562.
    """
    u = uuid.UUID(srv.generate_uuid_v7())
    assert u.version == 7


def test_uuid_v7_is_time_ordered():
    """Сенс сьомої версії — у тому, що рядки сортуються за часом."""
    import time

    first = srv.generate_uuid_v7()
    time.sleep(0.01)
    second = srv.generate_uuid_v7()
    assert first < second


def test_system_metrics_is_valid_json():
    data = json.loads(srv.get_system_metrics())
    assert 0 <= data["cpu_percent"] <= 100
    assert data["memory"]["total"] > 0
    assert data["disk"]["total"] > 0


def test_top_processes_measures_cpu_twice():
    """
    psutil.cpu_percent() при першому виклику завжди 0.0 — він міряє
    різницю. Без двох проходів усі значення були б нулями.
    """
    source = (ROOT / "server.py").read_text(encoding="utf-8")
    body = source[source.index("def get_top_processes"):]
    body = body[:body.index("@mcp.prompt")]
    assert body.count("cpu_percent()") >= 2
    assert "sleep" in body


# ══════════════════════════════════════════════════════════════════
# 8. Матеріали репозиторію
# ══════════════════════════════════════════════════════════════════
def test_requirements_are_pinned():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    pkgs = [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    unpinned = [p for p in pkgs if "==" not in p]
    assert not unpinned, f"без піна: {unpinned}"


def test_gitignore_excludes_the_database():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "*.db" in text or "store.db" in text


def test_readme_warns_about_the_sdk_trap():
    """
    README мусить попереджати про перейменування FastMCP -> MCPServer:
    це головна причина, чому приклади з інтернету не запускаються.

    Перевіряємо не відсутність старої назви — вона згадується
    навмисно, у шимі й у попередженні, — а наявність нової та самого
    попередження.
    """
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "mcp.server.mcpserver" in text, "немає актуального імпорту"
    assert "MCPServer" in text
    assert "ModuleNotFoundError" in text, "немає попередження про пастку"


def test_server_prefers_the_modern_import():
    """
    Шим мусить пробувати 2.x ПЕРШИМ: на машині з обома SDK інакше
    візьметься старий клас.

    Перевіряємо по ДЕРЕВУ РОЗБОРУ, а не пошуком підрядка: у коментарі
    зверху стара назва згадується раніше за нову, бо там пояснюється
    сама пастка. Пошук у тексті дав би хибне спрацювання.
    """
    import ast

    tree = ast.parse((ROOT / "server.py").read_text(encoding="utf-8"))
    order = [n.module for n in ast.walk(tree)
             if isinstance(n, ast.ImportFrom)
             and (n.module or "").startswith("mcp.server.")]
    assert order[:2] == ["mcp.server.mcpserver", "mcp.server.fastmcp"], \
        f"порядок у шимі: {order}"


def test_readme_recommends_inspector_v2_not_mcp_dev():
    """
    `mcp dev` тягне Inspector 1.x, і npm позначає його deprecated:
    «v1 is deprecated. Upgrade to v2». Перевірено під час підготовки.
    """
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "npx @modelcontextprotocol/inspector@latest" in text
    bad = [ln for ln in text.splitlines()
           if "mcp dev" in ln and "не користуйтеся" not in ln.lower()
           and "deprecated" not in ln.lower()]
    assert not bad, f"README досі радить mcp dev: {bad}"


def test_inspector_config_example_is_valid_json():
    import json

    cfg = json.loads((ROOT / "inspector.config.example.json")
                     .read_text(encoding="utf-8"))
    assert "Store Manager" in cfg["mcpServers"]
    assert cfg["mcpServers"]["Store Manager"]["args"] == ["server.py"]


def test_no_machine_specific_config_is_committed():
    """inspector.config.json — особистий файл із локальним шляхом."""
    assert not (ROOT / "inspector.config.json").exists(), \
        "приберіть особистий конфіг, лишіть лише .example"

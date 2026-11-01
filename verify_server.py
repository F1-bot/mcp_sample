"""
verify_server.py — швидка перевірка сервера без Claude Desktop і Inspector.

Запускайте ПЕРШИМ, коли щось не працює: він питає сам сервер, що той
віддає, і одразу видно, чи проблема в сервері, чи в клієнті.

    python verify_server.py

API-КЛЮЧІ: не потрібні.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402


def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


async def main() -> int:
    setup_console()
    mcp = server.mcp

    print("=" * 68)
    print(f"  Store Manager · MCP SDK {server.SDK_MAJOR}.x")
    try:
        from importlib.metadata import version
        import mcp.types as mcp_types  # noqa: F401
        print(f"  пакет mcp {version('mcp')}")
    except Exception:  # noqa: BLE001
        pass
    print("=" * 68)

    problems: list[str] = []

    if not server.DB_FILE.exists():
        problems.append(f"немає {server.DB_FILE.name} — запустіть: python setup_db.py")
        print(f"\n  [!] {problems[-1]}")

    # ── Інструменти ──────────────────────────────────────────────
    print("\n── TOOLS ──")
    tools = await mcp.list_tools()
    for t in sorted(tools, key=lambda x: x.name):
        ann = t.annotations
        if ann is None:
            mark = "?? немає анотацій"
        elif getattr(ann, "read_only_hint", None):
            mark = "читає"
        elif getattr(ann, "destructive_hint", None):
            mark = "ПИШЕ, руйнівний"
        else:
            mark = "пише"
        print(f"  {t.name:<22} [{mark}]")
        if ann is None:
            problems.append(f"{t.name}: немає анотацій readOnlyHint/destructiveHint")
    print(f"  всього: {len(tools)}")

    # ── Ресурси ──────────────────────────────────────────────────
    print("\n── RESOURCES ──")
    resources = await mcp.list_resources()
    for r in sorted(resources, key=lambda x: str(x.uri)):
        print(f"  {str(r.uri):<24} {r.name or ''}")
    print(f"  всього: {len(resources)}")

    # ── Промпти ──────────────────────────────────────────────────
    print("\n── PROMPTS ──")
    prompts = await mcp.list_prompts()
    for p in sorted(prompts, key=lambda x: x.name):
        args = ", ".join(a.name for a in (p.arguments or []))
        print(f"  {p.name}({args})")
    print(f"  всього: {len(prompts)}")

    # ── Жива перевірка захисту ───────────────────────────────────
    if server.DB_FILE.exists():
        print("\n── ПЕРЕВІРКА ЗАХИСТУ ──")
        checks = [
            ("читання працює", "SELECT COUNT(*) AS n FROM products", False),
            ("DELETE заблоковано", "DELETE FROM products", True),
            ("ATTACH заблоковано", "ATTACH DATABASE 'evil.db' AS e", True),
            ("PRAGMA заблоковано", "PRAGMA writable_schema=1", True),
        ]
        for label, query, expect_error in checks:
            out = server.read_store_query(query)
            failed = out.startswith("Error") or out.startswith("SQL error")
            ok = failed == expect_error
            print(f"  {'ok ' if ok else 'FAIL'} {label}")
            if not ok:
                problems.append(label)

    # ── Підсумок ─────────────────────────────────────────────────
    print("\n" + "=" * 68)
    if problems:
        print("  ПРОБЛЕМИ:")
        for p in problems:
            print(f"    · {p}")
        print("=" * 68)
        return 1

    print("  Усе на місці. Далі:")
    print("    mcp dev server.py          — Inspector")
    print("    python server.py --http    — Streamable HTTP")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

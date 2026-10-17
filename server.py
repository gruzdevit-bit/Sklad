#!/usr/bin/env python3
"""Склад: учёт остатков. Одна страница, SQLite, без внешних зависимостей."""
import html
import os
import re
import sqlite3
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

UNITS = ("шт", "кг", "упак")
JOURNAL_LIMIT = 20
MAX_NAME = 200
MAX_QTY_DIGITS = 9

DEFAULT_DB = os.environ.get(
    "SKLAD_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sklad.db"),
)

LOCK = threading.Lock()

ERR_NAME = "Введите название"
ERR_DUP = "Такой товар уже есть"
ERR_QTY = "Введите целое число от 1"
ERR_STOCK = "Недостаточно на складе"
ERR_UNIT = "Выберите единицу: шт, кг или упак"
ERR_PRODUCT = "Выберите товар"
ERR_LONG = "Название слишком длинное (до %d символов)" % MAX_NAME

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    unit TEXT NOT NULL,
    stock INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0)
);
CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id),
    kind TEXT NOT NULL CHECK (kind IN ('in', 'out')),
    qty INTEGER NOT NULL CHECK (qty >= 1)
);
"""


def connect(db_path):
    folder = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---------- бизнес-логика ----------

def add_product(conn, name, unit):
    """Возвращает (ошибка, id товара для подсветки)."""
    name = (name or "").strip()
    if not name:
        return ERR_NAME, None
    if len(name) > MAX_NAME:
        return ERR_LONG, None
    if unit not in UNITS:
        return ERR_UNIT, None
    key = name.casefold()
    row = conn.execute("SELECT id FROM products WHERE name_key = ?", (key,)).fetchone()
    if row:
        return ERR_DUP, row["id"]
    try:
        conn.execute(
            "INSERT INTO products (name, name_key, unit, stock) VALUES (?, ?, ?, 0)",
            (name, key, unit),
        )
    except sqlite3.IntegrityError:
        row = conn.execute("SELECT id FROM products WHERE name_key = ?", (key,)).fetchone()
        return ERR_DUP, row["id"] if row else None
    return None, None


def parse_qty(raw):
    raw = (raw or "").strip()
    if not re.fullmatch(r"[0-9]+", raw) or len(raw) > MAX_QTY_DIGITS:
        return None
    value = int(raw)
    return value if value >= 1 else None


def move(conn, product_id, kind, qty_raw):
    """Приход/расход. Возвращает (ошибка, id товара)."""
    qty = parse_qty(qty_raw)
    if qty is None:
        return ERR_QTY, None
    try:
        pid = int(product_id)
    except (TypeError, ValueError):
        return ERR_PRODUCT, None
    if kind not in ("in", "out"):
        return ERR_PRODUCT, pid
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT id, stock FROM products WHERE id = ?", (pid,)).fetchone()
        if not row:
            conn.execute("ROLLBACK")
            return ERR_PRODUCT, None
        if kind == "out" and qty > row["stock"]:
            conn.execute("ROLLBACK")
            return ERR_STOCK, pid
        delta = qty if kind == "in" else -qty
        conn.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (delta, pid))
        conn.execute(
            "INSERT INTO operations (created_at, product_id, kind, qty) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), pid, kind, qty),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return None, pid


# ---------- представление ----------

CSS = """
*{box-sizing:border-box}
body{margin:0;font-family:Arial,Helvetica,sans-serif;background:#f2f4f7;color:#1f2933;font-size:14px}
header{background:#1d4e89;color:#fff;padding:14px 28px;display:flex;align-items:baseline;gap:16px}
header h1{margin:0;font-size:20px}
header span{opacity:.75;font-size:13px}
main{max-width:900px;margin:24px auto;padding:0 20px}
section{background:#fff;border:1px solid #d9dee5;border-radius:6px;padding:16px 18px;margin-bottom:20px}
h2{margin:0 0 12px;font-size:16px}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:12px;color:#667085;font-weight:600;border-bottom:1px solid #d9dee5;padding:6px 8px}
td{padding:8px;border-bottom:1px solid #eef0f3}
tr:last-child td{border-bottom:0}
.num{text-align:right;white-space:nowrap}
th.num{text-align:right}
.zero{color:#b54708;font-weight:600}
.hl td{background:#fff8e6}
.empty{padding:24px 8px;text-align:center;color:#667085}
.tag{display:inline-block;padding:2px 10px;border-radius:10px;font-size:12px;font-weight:600}
.in{background:#dcf5e4;color:#176b38}
.out{background:#fde9d3;color:#9a4a07}
label{display:block;font-size:12px;color:#667085;margin:0 0 4px}
input,select{width:100%;padding:8px 10px;border:1px solid #b9c1cc;border-radius:4px;font-size:14px;background:#fff}
input.err{border-color:#c62828;background:#fff6f6}
select:disabled,input:disabled{background:#eef0f3;color:#98a2b3}
.form{display:grid;gap:12px;align-items:start}
.f-new{grid-template-columns:2fr 1fr 140px}
.f-op{grid-template-columns:2fr 1fr 120px 120px}
.lbl-sp{visibility:hidden}
button{width:100%;padding:9px 12px;border:0;border-radius:4px;color:#fff;font-size:14px;font-weight:600;cursor:pointer}
.b-in{background:#2e8b57}.b-out{background:#d9731a}.b-main{background:#1d4e89}
button:disabled{background:#c5cbd3;cursor:default}
.errmsg{color:#c62828;font-size:13px;font-weight:600;margin-top:6px}
.hint{font-size:12px;color:#667085;margin-top:10px}
@media(max-width:700px){.f-new,.f-op{grid-template-columns:1fr 1fr}}
"""


def esc(value):
    return html.escape(str(value), quote=True)


def fmt_date(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return iso


def render(conn, form=None):
    form = form or {}
    name_val = form.get("name", "")
    unit_sel = form.get("unit", UNITS[0])
    name_err = form.get("name_err")
    op_err = form.get("op_err")
    op_product = form.get("product")
    qty_val = form.get("qty", "")
    hl = form.get("hl")

    products = conn.execute("SELECT id, name, unit, stock FROM products ORDER BY id").fetchall()
    ops = conn.execute(
        "SELECT o.created_at, o.kind, o.qty, p.name FROM operations o "
        "JOIN products p ON p.id = o.product_id ORDER BY o.id DESC LIMIT ?",
        (JOURNAL_LIMIT,),
    ).fetchall()

    out = []
    out.append('<!DOCTYPE html>\n<html lang="ru">\n<head>\n<meta charset="utf-8">\n')
    out.append('<meta name="viewport" content="width=device-width, initial-scale=1">\n')
    out.append("<title>Склад</title>\n<style>" + CSS + "</style>\n</head>\n<body>\n")
    out.append('<header><h1>Склад</h1><span>Программа кладовщика</span></header>\n<main>\n')

    # Новый товар
    out.append('<section id="new-product">\n<h2>Новый товар</h2>\n')
    out.append('<form class="form f-new" method="post" action="/products">\n')
    out.append('<div><label for="name">Название</label>')
    out.append('<input id="name" name="name" placeholder="Например, Гвозди" autocomplete="off" '
               'value="%s"%s>' % (esc(name_val), ' class="err"' if name_err else ""))
    if name_err:
        out.append('<div class="errmsg" id="name-error">%s</div>' % esc(name_err))
    out.append("</div>\n")
    out.append('<div><label for="unit">Единица</label><select id="unit" name="unit">')
    for u in UNITS:
        out.append('<option value="%s"%s>%s</option>' % (u, " selected" if u == unit_sel else "", u))
    out.append("</select></div>\n")
    out.append('<div><label class="lbl-sp">.</label><button class="b-main" type="submit">Добавить</button></div>\n')
    out.append("</form>\n")
    out.append('<div class="hint">Остаток нового товара — 0, пока не будет прихода.</div>\n</section>\n')

    # Остатки
    out.append('<section id="stock">\n<h2>Остатки товаров</h2>\n')
    out.append('<table id="stock-table"><tr><th>Товар</th><th>Единица</th><th class="num">Остаток</th></tr>')
    for p in products:
        cls = ' class="hl"' if hl == p["id"] else ""
        zero = " zero" if p["stock"] == 0 else ""
        out.append('<tr%s><td>%s</td><td>%s</td><td class="num%s">%d</td></tr>'
                   % (cls, esc(p["name"]), esc(p["unit"]), zero, p["stock"]))
    out.append("</table>\n")
    if not products:
        out.append('<div class="empty">Товаров пока нет. Добавьте первый товар в форме выше.</div>\n')
    out.append("</section>\n")

    # Приход / расход
    disabled = " disabled" if not products else ""
    out.append('<section id="operation">\n<h2>Приход / расход</h2>\n')
    out.append('<form class="form f-op" method="post" action="/operations">\n')
    out.append('<div><label for="product">Товар</label><select id="product" name="product"%s>' % disabled)
    if products:
        for p in products:
            sel = " selected" if str(p["id"]) == str(op_product) else ""
            out.append('<option value="%d"%s>%s</option>' % (p["id"], sel, esc(p["name"])))
    else:
        out.append("<option>Нет товаров</option>")
    out.append("</select></div>\n")
    out.append('<div><label for="qty">Количество</label><input id="qty" name="qty" '
               'inputmode="numeric" autocomplete="off" placeholder="целое число от 1" value="%s"%s%s></div>\n'
               % (esc(qty_val), ' class="err"' if op_err else "", disabled))
    out.append('<div><label class="lbl-sp">.</label><button class="b-in" type="submit" '
               'name="kind" value="in"%s>Приход</button></div>\n' % disabled)
    out.append('<div><label class="lbl-sp">.</label><button class="b-out" type="submit" '
               'name="kind" value="out"%s>Расход</button></div>\n' % disabled)
    out.append("</form>\n")
    if op_err:
        out.append('<div class="errmsg" id="op-error">%s</div>\n' % esc(op_err))
    out.append("</section>\n")

    # Журнал
    out.append('<section id="journal">\n<h2>Последние операции</h2>\n')
    out.append('<table id="journal-table"><tr><th>Дата</th><th>Товар</th><th>Операция</th>'
               '<th class="num">Количество</th></tr>')
    for o in ops:
        label = "приход" if o["kind"] == "in" else "расход"
        out.append('<tr><td>%s</td><td>%s</td><td><span class="tag %s">%s</span></td>'
                   '<td class="num">%d</td></tr>'
                   % (esc(fmt_date(o["created_at"])), esc(o["name"]), o["kind"], label, o["qty"]))
    out.append("</table>\n")
    if ops:
        out.append('<div class="hint">Показаны последние %d операций, новые сверху.</div>\n' % JOURNAL_LIMIT)
    else:
        out.append('<div class="empty">Операций пока не было.</div>\n')
    out.append("</section>\n</main>\n</body>\n</html>\n")
    return "".join(out)


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "Sklad/1.0"

    def _send(self, status, body=b"", ctype="text/html; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _page(self, form=None):
        with LOCK:
            conn = connect(self.server.db_path)
            try:
                body = render(conn, form).encode("utf-8")
            finally:
                conn.close()
        self._send(200, body, headers={"Cache-Control": "no-store"})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._page()
        else:
            self._send(404, "Страница не найдена".encode("utf-8"), "text/plain; charset=utf-8")

    do_HEAD = do_GET

    def _read_form(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        length = max(0, min(length, 65536))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        data = parse_qs(raw, keep_blank_values=True)
        return {k: v[0] for k, v in data.items()}

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/products", "/operations"):
            self._send(404, "Страница не найдена".encode("utf-8"), "text/plain; charset=utf-8")
            return
        data = self._read_form()
        with LOCK:
            conn = connect(self.server.db_path)
            try:
                if path == "/products":
                    name = data.get("name", "")
                    unit = data.get("unit", "")
                    err, hl = add_product(conn, name, unit)
                    form = None if not err else {
                        "name": name, "unit": unit if unit in UNITS else UNITS[0],
                        "name_err": err, "hl": hl,
                    }
                else:
                    err, hl = move(conn, data.get("product"), data.get("kind"), data.get("qty", ""))
                    form = None if not err else {
                        "product": data.get("product"), "qty": data.get("qty", ""),
                        "op_err": err, "hl": hl,
                    }
                body = render(conn, form).encode("utf-8") if err else b""
            finally:
                conn.close()
        if err:
            self._send(200, body, headers={"Cache-Control": "no-store"})
        else:
            self._send(303, b"", headers={"Location": "/"})


def make_server(db_path=DEFAULT_DB, host="0.0.0.0", port=8080):
    connect(db_path).close()
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.db_path = db_path
    return srv


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    make_server(DEFAULT_DB, "0.0.0.0", port).serve_forever()

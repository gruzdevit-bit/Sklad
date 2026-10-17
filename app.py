import os
import re
import sqlite3
from datetime import datetime

from flask import Flask, g, jsonify, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UNITS = ("шт", "кг", "упак")
TITLE_MAX = 100
NOTE_MAX = 500
NAME_MAX = 100
LAST_OPERATIONS = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    unit TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0
);
"""

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def create_app(db_path=None):
    app = Flask(
        __name__,
        static_folder=os.path.join(BASE_DIR, "static"),
        static_url_path="/static",
    )
    path = db_path or os.environ.get("SKLAD_DB") or os.path.join(BASE_DIR, "data", "sklad.db")
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    app.config["DB_PATH"] = path

    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.commit()
    con.close()

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DB_PATH"])
            g.db.row_factory = sqlite3.Row
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.after_request
    def no_cache(resp):
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def body_dict():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def fail(message, status=400, **extra):
        payload = {"error": message}
        payload.update(extra)
        return jsonify(payload), status

    # ---------- страница ----------
    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    # ---------- склад ----------
    def products_with_balance():
        rows = db().execute(
            "SELECT p.id, p.name, p.unit, "
            "COALESCE(SUM(CASE WHEN m.kind='in' THEN m.quantity ELSE -m.quantity END), 0) AS balance "
            "FROM products p LEFT JOIN movements m ON m.product_id = p.id "
            "GROUP BY p.id ORDER BY p.id"
        ).fetchall()
        return [dict(r) for r in rows]

    def balance_of(product_id):
        row = db().execute(
            "SELECT COALESCE(SUM(CASE WHEN kind='in' THEN quantity ELSE -quantity END), 0) AS b "
            "FROM movements WHERE product_id=?",
            (product_id,),
        ).fetchone()
        return row["b"]

    @app.get("/api/products")
    def list_products():
        return jsonify(products_with_balance())

    @app.post("/api/products")
    def add_product():
        data = body_dict()
        name = str(data.get("name") or "").strip()
        unit = str(data.get("unit") or "").strip()
        if not name:
            return fail("Введите название товара")
        if len(name) > NAME_MAX:
            return fail("Название не длиннее 100 символов")
        if unit not in UNITS:
            return fail("Выберите единицу измерения")
        for row in db().execute("SELECT name FROM products").fetchall():
            if row["name"].strip().lower() == name.lower():
                return fail("Такой товар уже есть")
        cur = db().execute("INSERT INTO products(name, unit) VALUES (?, ?)", (name, unit))
        db().commit()
        return jsonify({"id": cur.lastrowid, "name": name, "unit": unit, "balance": 0}), 201

    @app.get("/api/movements")
    def list_movements():
        rows = db().execute(
            "SELECT m.id, m.kind, m.quantity, m.created_at, p.name AS product, p.unit AS unit "
            "FROM movements m JOIN products p ON p.id = m.product_id "
            "ORDER BY m.id DESC LIMIT ?",
            (LAST_OPERATIONS,),
        ).fetchall()
        result = []
        for r in rows:
            try:
                when = datetime.strptime(r["created_at"], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
            except ValueError:
                when = r["created_at"]
            result.append(
                {
                    "id": r["id"],
                    "date": when,
                    "product": r["product"],
                    "unit": r["unit"],
                    "kind": r["kind"],
                    "quantity": r["quantity"],
                }
            )
        return jsonify(result)

    @app.post("/api/movements")
    def add_movement():
        data = body_dict()
        kind = data.get("kind")
        if kind not in ("in", "out"):
            return fail("Неизвестная операция")
        try:
            product_id = int(data.get("product_id"))
        except (TypeError, ValueError):
            return fail("Выберите товар")
        product = db().execute("SELECT id FROM products WHERE id=?", (product_id,)).fetchone()
        if product is None:
            return fail("Товар не найден", 404)
        raw = data.get("quantity")
        qty = None
        if isinstance(raw, bool):
            qty = None
        elif isinstance(raw, int):
            qty = raw
        elif isinstance(raw, str) and re.fullmatch(r"\s*\d+\s*", raw):
            qty = int(raw)
        if qty is None or qty < 1:
            return fail("Введите целое число от 1")
        if kind == "out" and qty > balance_of(product_id):
            return fail("Недостаточно на складе")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db().execute(
            "INSERT INTO movements(product_id, kind, quantity, created_at) VALUES (?, ?, ?, ?)",
            (product_id, kind, qty, now),
        )
        db().commit()
        return jsonify({"ok": True}), 201

    # ---------- календарь ----------
    def task_dict(row):
        return {
            "id": row["id"],
            "title": row["title"],
            "date": row["date"],
            "time": row["time"],
            "note": row["note"],
            "done": bool(row["done"]),
        }

    def validate_task(data):
        errors = {}
        title = str(data.get("title") or "").strip()
        date = str(data.get("date") or "").strip()
        time = str(data.get("time") or "").strip()
        note = str(data.get("note") or "").strip()
        if not title:
            errors["title"] = "Введите название задачи"
        elif len(title) > TITLE_MAX:
            errors["title"] = "Название не длиннее 100 символов"
        if not date:
            errors["date"] = "Укажите дату"
        else:
            ok = bool(DATE_RE.match(date))
            if ok:
                try:
                    datetime.strptime(date, "%Y-%m-%d")
                except ValueError:
                    ok = False
            if not ok:
                errors["date"] = "Укажите корректную дату"
        if time and not TIME_RE.match(time):
            errors["time"] = "Время в формате ЧЧ:ММ"
        if len(note) > NOTE_MAX:
            errors["note"] = "Заметка не длиннее 500 символов"
        return errors, {"title": title, "date": date, "time": time, "note": note}

    @app.get("/api/tasks")
    def list_tasks():
        month = request.args.get("month", "")
        sql = "SELECT * FROM tasks"
        params = ()
        if month:
            if not MONTH_RE.match(month):
                return fail("Неверный месяц")
            sql += " WHERE substr(date, 1, 7) = ?"
            params = (month,)
        sql += " ORDER BY date, time, id"
        rows = db().execute(sql, params).fetchall()
        return jsonify([task_dict(r) for r in rows])

    @app.post("/api/tasks")
    def add_task():
        errors, clean = validate_task(body_dict())
        if errors:
            return fail(next(iter(errors.values())), errors=errors)
        cur = db().execute(
            "INSERT INTO tasks(title, date, time, note, done) VALUES (?, ?, ?, ?, 0)",
            (clean["title"], clean["date"], clean["time"], clean["note"]),
        )
        db().commit()
        row = db().execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone()
        return jsonify(task_dict(row)), 201

    @app.put("/api/tasks/<int:task_id>")
    def update_task(task_id):
        row = db().execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return fail("Задача не найдена", 404)
        errors, clean = validate_task(body_dict())
        if errors:
            return fail(next(iter(errors.values())), errors=errors)
        db().execute(
            "UPDATE tasks SET title=?, date=?, time=?, note=? WHERE id=?",
            (clean["title"], clean["date"], clean["time"], clean["note"], task_id),
        )
        db().commit()
        row = db().execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return jsonify(task_dict(row))

    @app.patch("/api/tasks/<int:task_id>")
    def patch_task(task_id):
        row = db().execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return fail("Задача не найдена", 404)
        data = body_dict()
        if "done" not in data:
            return fail("Не указано состояние")
        db().execute("UPDATE tasks SET done=? WHERE id=?", (1 if data["done"] else 0, task_id))
        db().commit()
        row = db().execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return jsonify(task_dict(row))

    @app.delete("/api/tasks/<int:task_id>")
    def delete_task(task_id):
        cur = db().execute("DELETE FROM tasks WHERE id=?", (task_id,))
        db().commit()
        if cur.rowcount == 0:
            return fail("Задача не найдена", 404)
        return jsonify({"ok": True})

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)

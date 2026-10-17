import os
import re
import sqlite3
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.environ.get("DB_PATH", os.path.join(BASE, "data", "sklad.db"))
UNITS = ("шт", "кг", "упак")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def create_app(db_path=None):
    path = db_path or DEFAULT_DB
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    app = Flask(__name__, static_folder=os.path.join(BASE, "static"), static_url_path="/static")

    def conn():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS products(
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                unit TEXT NOT NULL, qty INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS operations(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
                product_id INTEGER NOT NULL, product TEXT NOT NULL,
                kind TEXT NOT NULL, qty INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                date TEXT NOT NULL, time TEXT, note TEXT NOT NULL DEFAULT '',
                done INTEGER NOT NULL DEFAULT 0);
            """
        )
    c = None

    def err(msg, code=400):
        return jsonify({"error": msg}), code

    def body():
        return request.get_json(silent=True) or {}

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    # ---------- склад ----------
    @app.get("/api/products")
    def products():
        with conn() as c:
            rows = c.execute("SELECT id,name,unit,qty FROM products ORDER BY id").fetchall()
        return jsonify([dict(r) for r in rows])

    @app.post("/api/products")
    def add_product():
        d = body()
        name = str(d.get("name") or "").strip()
        unit = d.get("unit")
        if not name:
            return err("Введите название товара")
        if len(name) > 100:
            return err("Название не длиннее 100 символов")
        if unit not in UNITS:
            return err("Выберите единицу")
        with conn() as c:
            names = [r["name"].lower() for r in c.execute("SELECT name FROM products")]
            if name.lower() in names:
                return err("Такой товар уже есть")
            cur = c.execute("INSERT INTO products(name,unit,qty) VALUES(?,?,0)", (name, unit))
            pid = cur.lastrowid
        return jsonify({"id": pid, "name": name, "unit": unit, "qty": 0}), 201

    @app.get("/api/operations")
    def operations():
        with conn() as c:
            rows = c.execute(
                "SELECT id,ts,product,kind,qty FROM operations ORDER BY id DESC LIMIT 20"
            ).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.post("/api/operations")
    def add_operation():
        d = body()
        kind = d.get("kind")
        qs = str(d.get("qty", "")).strip()
        if kind not in ("in", "out"):
            return err("Неизвестная операция")
        if not re.fullmatch(r"\d+", qs) or int(qs) < 1:
            return err("Введите целое количество от 1")
        qty = int(qs)
        try:
            pid = int(d.get("product_id"))
        except (TypeError, ValueError):
            return err("Выберите товар")
        with conn() as c:
            p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            if not p:
                return err("Выберите товар")
            if kind == "out" and qty > p["qty"]:
                return err("Недостаточно на складе")
            new = p["qty"] + qty if kind == "in" else p["qty"] - qty
            c.execute("UPDATE products SET qty=? WHERE id=?", (new, pid))
            c.execute(
                "INSERT INTO operations(ts,product_id,product,kind,qty) VALUES(?,?,?,?,?)",
                (datetime.now().strftime("%d.%m.%Y %H:%M"), pid, p["name"], kind, qty),
            )
        return jsonify({"id": pid, "qty": new}), 201

    # ---------- задачи ----------
    def task_dict(r):
        return {"id": r["id"], "title": r["title"], "date": r["date"],
                "time": r["time"] or "", "note": r["note"], "done": bool(r["done"])}

    def validate(d):
        title = str(d.get("title") or "").strip()
        date = str(d.get("date") or "").strip()
        time = str(d.get("time") or "").strip()
        note = str(d.get("note") or "").strip()
        if not title:
            return None, "Введите название задачи"
        if len(title) > 100:
            return None, "Название не длиннее 100 символов"
        try:
            datetime.strptime(date, "%Y-%m-%d")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                raise ValueError
        except ValueError:
            return None, "Укажите дату"
        if time and not TIME_RE.match(time):
            return None, "Время в формате ЧЧ:ММ"
        if len(note) > 500:
            return None, "Заметка не длиннее 500 символов"
        return {"title": title, "date": date, "time": time or None, "note": note}, None

    @app.get("/api/tasks")
    def tasks():
        with conn() as c:
            rows = c.execute("SELECT * FROM tasks ORDER BY date, COALESCE(time,''), id").fetchall()
        return jsonify([task_dict(r) for r in rows])

    @app.post("/api/tasks")
    def add_task():
        v, e = validate(body())
        if e:
            return err(e)
        with conn() as c:
            cur = c.execute(
                "INSERT INTO tasks(title,date,time,note,done) VALUES(?,?,?,?,0)",
                (v["title"], v["date"], v["time"], v["note"]),
            )
            r = c.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone()
        return jsonify(task_dict(r)), 201

    @app.put("/api/tasks/<int:tid>")
    def edit_task(tid):
        v, e = validate(body())
        if e:
            return err(e)
        with conn() as c:
            if not c.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone():
                return err("Задача не найдена", 404)
            c.execute("UPDATE tasks SET title=?,date=?,time=?,note=? WHERE id=?",
                      (v["title"], v["date"], v["time"], v["note"], tid))
            r = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return jsonify(task_dict(r))

    @app.patch("/api/tasks/<int:tid>")
    def done_task(tid):
        done = 1 if body().get("done") else 0
        with conn() as c:
            if not c.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone():
                return err("Задача не найдена", 404)
            c.execute("UPDATE tasks SET done=? WHERE id=?", (done, tid))
            r = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return jsonify(task_dict(r))

    @app.delete("/api/tasks/<int:tid>")
    def del_task(tid):
        with conn() as c:
            c.execute("DELETE FROM tasks WHERE id=?", (tid,))
        return jsonify({"ok": True})

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

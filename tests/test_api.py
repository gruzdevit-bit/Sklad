import pytest

from app import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(str(tmp_path / "t.db"))
    return app.test_client()


def add(client, **kw):
    data = {"title": "Задача", "date": "2025-05-15", "time": "", "note": ""}
    data.update(kw)
    return client.post("/api/tasks", json=data)


def test_index_ok(client):
    assert client.get("/").status_code == 200


def test_add_task_with_time_and_note(client):
    r = add(client, title="Принять фуру от ООО Ромашка", time="09:30", note="Ворота 2")
    assert r.status_code == 201
    d = r.get_json()
    assert d["time"] == "09:30" and d["note"] == "Ворота 2" and d["done"] is False


def test_add_task_without_time(client):
    r = add(client, title="Инвентаризация стеллажа А", date="2025-05-20")
    assert r.status_code == 201 and r.get_json()["time"] == ""


def test_empty_title(client):
    r = add(client, title="   ")
    assert r.status_code == 400
    assert r.get_json()["error"] == "Введите название задачи"


def test_empty_date(client):
    r = add(client, title="Проверить накладные", date="")
    assert r.status_code == 400
    assert r.get_json()["error"] == "Укажите дату"


def test_long_title(client):
    assert add(client, title="а" * 100).status_code == 201
    r = add(client, title="а" * 101)
    assert r.status_code == 400
    assert r.get_json()["error"] == "Название не длиннее 100 символов"


def test_order_in_day(client):
    add(client, title="Позвонить поставщику", date="2025-05-12", time="14:00")
    add(client, title="Проверить накладные", date="2025-05-12")
    items = [t for t in client.get("/api/tasks").get_json() if t["date"] == "2025-05-12"]
    assert [t["title"] for t in items] == ["Проверить накладные", "Позвонить поставщику"]


def test_done_toggle_and_persist(client):
    tid = add(client).get_json()["id"]
    assert client.patch("/api/tasks/%d" % tid, json={"done": True}).get_json()["done"] is True
    assert client.get("/api/tasks").get_json()[0]["done"] is True
    assert client.patch("/api/tasks/%d" % tid, json={"done": False}).get_json()["done"] is False


def test_edit_moves_date(client):
    tid = add(client, time="09:30", note="Ворота 2").get_json()["id"]
    r = client.put("/api/tasks/%d" % tid, json={"title": "Задача", "date": "2025-05-16", "time": "09:30", "note": "Ворота 2"})
    d = r.get_json()
    assert d["date"] == "2025-05-16" and d["time"] == "09:30" and d["note"] == "Ворота 2"


def test_delete(client):
    tid = add(client).get_json()["id"]
    assert client.delete("/api/tasks/%d" % tid).status_code == 200
    assert client.get("/api/tasks").get_json() == []


def test_stock_flow(client):
    p = client.post("/api/products", json={"name": "Гвозди", "unit": "кг"}).get_json()
    r = client.post("/api/operations", json={"product_id": p["id"], "kind": "in", "qty": "10"})
    assert r.status_code == 201 and r.get_json()["qty"] == 10
    r = client.post("/api/operations", json={"product_id": p["id"], "kind": "out", "qty": "11"})
    assert r.status_code == 400 and r.get_json()["error"] == "Недостаточно на складе"
    r = client.post("/api/operations", json={"product_id": p["id"], "kind": "out", "qty": "4"})
    assert r.get_json()["qty"] == 6
    ops = client.get("/api/operations").get_json()
    assert ops[0]["kind"] == "out" and ops[1]["kind"] == "in"

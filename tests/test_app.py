import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import server  # noqa: E402


class SkladTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")
        self.srv = server.make_server(self.db, "127.0.0.1", 0)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def post(self, path, **data):
        body = urllib.parse.urlencode(data).encode()
        with urllib.request.urlopen(self.base + path, data=body) as r:
            return r.read().decode()

    def get(self):
        with urllib.request.urlopen(self.base + "/") as r:
            return r.status, r.read().decode()

    def add(self, name, unit="шт"):
        return self.post("/products", name=name, unit=unit)

    def product_id(self, name):
        conn = server.connect(self.db)
        try:
            return conn.execute("SELECT id FROM products WHERE name=?", (name,)).fetchone()["id"]
        finally:
            conn.close()

    def op(self, name, kind, qty):
        return self.post("/operations", product=self.product_id(name), kind=kind, qty=qty)

    def stock_rows(self, page):
        block = page.split('id="stock-table"')[1].split("</table>")[0]
        return re.findall(r'<td>([^<]*)</td><td>([^<]*)</td><td class="num[^"]*">(\d+)</td>', block)

    def journal_rows(self, page):
        block = page.split('id="journal-table"')[1].split("</table>")[0]
        return re.findall(
            r'<td>([\d.: ]+)</td><td>([^<]*)</td><td><span class="tag \w+">([^<]*)</span></td>'
            r'<td class="num">(\d+)</td>', block)

    def stock_of(self, name):
        _, page = self.get()
        return {n: int(s) for n, u, s in self.stock_rows(page)}[name]

    def test_root_ok_and_empty(self):
        status, page = self.get()
        self.assertEqual(status, 200)
        self.assertIn("Товаров пока нет", page)

    def test_01_add(self):
        self.add("Гвозди", "кг")
        _, page = self.get()
        self.assertEqual(self.stock_rows(page), [("Гвозди", "кг", "0")])

    def test_02_03_empty_name(self):
        self.add("Гвозди", "кг")
        for name in ("", "   "):
            page = self.add(name)
            self.assertIn("Введите название", page)
            self.assertEqual(len(self.stock_rows(page)), 1)

    def test_04_05_duplicates(self):
        self.add("Гвозди", "кг")
        page = self.add("Гвозди", "кг")
        self.assertIn("Такой товар уже есть", page)
        page = self.add(" гвозди ", "шт")
        self.assertIn("Такой товар уже есть", page)
        self.assertEqual(self.stock_rows(page), [("Гвозди", "кг", "0")])

    def test_06_units(self):
        _, page = self.get()
        unit_select = page.split('id="unit"')[1].split("</select>")[0]
        self.assertEqual(re.findall(r">([^<]+)</option>", unit_select), ["шт", "кг", "упак"])

    def test_07_08_in_out(self):
        self.add("Гвозди", "кг")
        page = self.op("Гвозди", "in", "10")
        self.assertEqual(self.stock_rows(page), [("Гвозди", "кг", "10")])
        self.assertEqual(self.journal_rows(page)[0][1:], ("Гвозди", "приход", "10"))
        page = self.op("Гвозди", "out", "4")
        self.assertEqual(self.stock_rows(page), [("Гвозди", "кг", "6")])
        self.assertEqual(self.journal_rows(page)[0][1:], ("Гвозди", "расход", "4"))

    def test_09_10_11_stock_limits(self):
        self.add("Гвозди", "кг")
        self.op("Гвозди", "in", "6")
        page = self.op("Гвозди", "out", "7")
        self.assertIn("Недостаточно на складе", page)
        self.assertEqual(self.stock_of("Гвозди"), 6)
        self.assertEqual(len(self.journal_rows(page)), 1)
        self.op("Гвозди", "out", "6")
        self.assertEqual(self.stock_of("Гвозди"), 0)
        self.add("Мешки", "упак")
        page = self.op("Мешки", "out", "1")
        self.assertIn("Недостаточно на складе", page)
        self.assertEqual(self.stock_of("Мешки"), 0)

    def test_12_to_15_bad_qty(self):
        self.add("Гвозди", "кг")
        self.op("Гвозди", "in", "6")
        for bad in ("0", "-5", "2,5", "2.5", "", "abc"):
            for kind in ("in", "out"):
                page = self.op("Гвозди", kind, bad)
                self.assertIn("Введите целое число от 1", page)
        self.assertEqual(self.stock_of("Гвозди"), 6)
        _, page = self.get()
        self.assertEqual(len(self.journal_rows(page)), 1)

    def test_16_17_sequence(self):
        self.add("Гвозди", "кг")
        self.add("Болты", "шт")
        self.op("Гвозди", "in", "6")
        self.op("Болты", "in", "10")
        self.op("Болты", "out", "4")
        self.op("Болты", "in", "1")
        self.assertEqual(self.stock_of("Болты"), 7)
        rows = self.journal_rows(self.get()[1])
        self.assertEqual([(r[1], r[2], r[3]) for r in rows[:3]],
                         [("Болты", "приход", "1"), ("Болты", "расход", "4"), ("Болты", "приход", "10")])
        self.op("Болты", "in", "3")
        self.assertEqual(self.stock_of("Болты"), 10)
        self.assertEqual(self.stock_of("Гвозди"), 6)

    def test_18_persistence(self):
        self.add("Гвозди", "кг")
        self.op("Гвозди", "in", "10")
        before = self.get()[1]
        self.srv.shutdown()
        self.srv.server_close()
        self.srv = server.make_server(self.db, "127.0.0.1", 0)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        after = self.get()[1]
        self.assertEqual(self.stock_rows(before), self.stock_rows(after))
        self.assertEqual(self.journal_rows(before), self.journal_rows(after))

    def test_journal_limit_20(self):
        self.add("Гвозди", "кг")
        for _ in range(25):
            self.op("Гвозди", "in", "1")
        self.assertEqual(len(self.journal_rows(self.get()[1])), 20)


if __name__ == "__main__":
    unittest.main()

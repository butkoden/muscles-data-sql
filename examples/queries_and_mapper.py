"""SQL JOIN, model mapping and rollback with a temporary in-memory database."""
from muscles import Column, Integer, Model, String
from muscles_data_sql import ModelMapper, SqlDatabase


class OrderSummary(Model):
    name = Column(String)
    total = Column(Integer)


def main():
    db = SqlDatabase.sqlite(":memory:")
    mapper = ModelMapper(OrderSummary, {"name": "customer", "total": "total"})
    try:
        with db.transaction() as tx:
            tx.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            tx.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, amount INTEGER)")
            tx.execute("INSERT INTO customers VALUES (?, ?)", (1, "Denis"))
            tx.execute("INSERT INTO orders VALUES (?, ?, ?)", (1, 1, 25))
            tx.execute("INSERT INTO orders VALUES (?, ?, ?)", (2, 1, 15))
        with db.transaction() as tx:
            rows = tx.fetch_all("""
                SELECT c.name AS customer, SUM(o.amount) AS total
                FROM customers AS c JOIN orders AS o ON o.customer_id = c.id
                WHERE c.id = ? GROUP BY c.id, c.name
            """, (1,))
            print("summary:", [item.as_dict() for item in mapper.from_records(rows)])
        try:
            with db.transaction() as tx:
                tx.execute("INSERT INTO orders VALUES (?, ?, ?)", (3, 1, 999))
                raise ValueError("example rollback")
        except ValueError:
            pass
        with db.transaction() as tx:
            print("orders after rollback:", tx.fetch_one("SELECT COUNT(*) AS count FROM orders")["count"])
    finally:
        db.close()


if __name__ == "__main__":
    main()

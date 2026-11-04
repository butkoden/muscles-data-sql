revision = "0001"
dialects = ("sqlite",)


def upgrade(tx):
    tx.execute("CREATE TABLE demo_users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")


def downgrade(tx):
    tx.execute("DROP TABLE demo_users")

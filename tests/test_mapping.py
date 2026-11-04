from muscles.data.sql.mapping import map_model
from muscles import Column
from muscles import Integer as MusclesInteger
from muscles import String as MusclesString


class FakeColumn:
    def __init__(self, field_type, primary_key=False, nullable=True):
        self.field_type = field_type
        self.primary_key = primary_key
        self.nullable = nullable


class Integer:
    pass


class String:
    pass


class UserModel:
    id = FakeColumn(Integer, primary_key=True, nullable=False)
    name = FakeColumn(String, nullable=False)


def test_map_model_builds_table():
    table = map_model(UserModel, "users")
    assert table.name == "users"
    assert "id" in table.columns
    assert "name" in table.columns
    assert list(table.primary_key.columns)[0].name == "id"
    assert table.columns["id"].autoincrement is True


class RealMusclesModel:
    id = Column(MusclesInteger, primary_key=True, nullable=False)
    name = Column(MusclesString, nullable=False)


def test_map_model_supports_field_type_instance_and_pk_autoincrement():
    from sqlalchemy import create_engine

    table = map_model(RealMusclesModel, "real_users")
    assert table.columns["id"].type.__class__.__name__ == "Integer"
    assert table.columns["id"].autoincrement is True

    engine = create_engine("sqlite:///:memory:")
    table.metadata.create_all(engine)
    with engine.begin() as conn:
        result = conn.execute(table.insert().values(name="Denis"))
    assert result.inserted_primary_key[0] is not None

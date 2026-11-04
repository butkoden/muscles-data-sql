from importlib import import_module
from pathlib import Path
import re


def test_sql_distribution_and_public_module_use_data_sql_name():
    metadata = (Path(__file__).parents[1] / "pyproject.toml").read_text()
    assert re.search(r'^name\s*=\s*"muscles-data-sql"\s*$', metadata, re.MULTILINE)
    assert import_module("muscles.data.sql").SqlDatabase is not None

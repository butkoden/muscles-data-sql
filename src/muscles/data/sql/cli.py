import json
from pathlib import Path

import click

from .config import DatabaseConfig
from .connections import SqlConnectionRegistry, load_connection_registry
from .providers import SqlGenerationRequest, SqlResourceGeneratorProvider


@click.group()
def main():
    pass


def _registry_from_options(url: str | None, config_path: str | None) -> SqlConnectionRegistry:
    if config_path:
        return load_connection_registry(config_path)
    if url:
        return SqlConnectionRegistry.from_database_config(DatabaseConfig(url=url))
    raise click.UsageError("Use --url or --config.")


@main.command("doctor")
@click.option("--url", required=False)
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False), required=False)
@click.option("--connection", default="default", show_default=True)
@click.option("--all", "all_connections", is_flag=True, default=False)
def doctor(url: str | None, config_path: str | None, connection: str, all_connections: bool):
    registry = _registry_from_options(url, config_path)
    report = registry.inspect_all() if all_connections else registry.inspect(connection)
    click.echo(json.dumps(report, ensure_ascii=False))


@main.command("inspect")
@click.option("--url", required=False)
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False), required=False)
@click.option("--connection", default="default", show_default=True)
def inspect_cmd(url: str | None, config_path: str | None, connection: str):
    registry = _registry_from_options(url, config_path)
    click.echo(json.dumps(registry.inspect(connection), ensure_ascii=False))


@main.group("generate")
def generate():
    pass


@generate.command("resource")
@click.argument("name")
@click.option("--sql", is_flag=True, default=False)
def generate_resource(name: str, sql: bool):
    if sql:
        provider = SqlResourceGeneratorProvider()
        generated = provider.generate(
            project_root=Path.cwd(),
            request=SqlGenerationRequest(
                generator_type="sql-resource",
                name=name,
                force=False,
                with_tests=False,
            ),
        )
        click.echo(generated[0])
        return

    root = Path("generated")
    root.mkdir(exist_ok=True)
    target = root / f"{name}.py"
    content = f"class {name.title().replace('_', '')}Resource:\n    sql_enabled = {sql}\n"
    target.write_text(content, encoding="utf-8")
    click.echo(str(target))


if __name__ == "__main__":
    main()

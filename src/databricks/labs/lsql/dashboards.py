import dataclasses
import json
from pathlib import Path
from typing import TypeVar

import sqlglot
import yaml
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.dashboards import Dashboard as SDKDashboard
from databricks.sdk.service.workspace import ExportFormat

from databricks.labs.lsql.lakeview import (
    ControlFieldEncoding,
    CounterEncodingMap,
    CounterFieldEncoding,
    CounterSpec,
    Dashboard,
    Dataset,
    Field,
    Layout,
    NamedQuery,
    Page,
    Position,
    Query,
    Widget,
)

T = TypeVar("T")


class Dashboards:
    def __init__(self, ws: WorkspaceClient):
        self._ws = ws

    def get_dashboard(self, dashboard_path: str) -> Dashboard:
        with self._ws.workspace.download(dashboard_path, format=ExportFormat.SOURCE) as f:
            raw = f.read().decode("utf-8")
            as_dict = json.loads(raw)
            return Dashboard.from_dict(as_dict)

    def save_to_folder(self, dashboard: Dashboard, local_path: Path) -> Dashboard:
        local_path.mkdir(parents=True, exist_ok=True)
        dashboard = self._with_better_names(dashboard)
        for dataset in dashboard.datasets:
            query = self._format_query(dataset.query)
            with (local_path / f"{dataset.name}.sql").open("w") as f:
                f.write(query)
        for page in dashboard.pages:
            with (local_path / f"{page.name}.yml").open("w") as f:
                yaml.safe_dump(page.as_dict(), f)
        return dashboard

    @staticmethod
    def _format_query(query: str) -> str:
        try:
            parsed_query = sqlglot.parse(query)
        except sqlglot.ParseError:
            return query
        statements = []
        for statement in parsed_query:
            if statement is None:
                continue
            # see https://sqlglot.com/sqlglot/generator.html#Generator
            statements.append(
                statement.sql(
                    dialect="databricks",
                    normalize=True,  # normalize identifiers to lowercase
                    pretty=True,  # format the produced SQL string
                    normalize_functions="upper",  # normalize function names to uppercase
                    max_text_width=80,  # wrap text at 120 characters
                )
            )
        formatted_query = ";\n".join(statements)
        return formatted_query

    @staticmethod
    def create_dashboard(dashboard_folder: Path) -> Dashboard:
        """Create a dashboard from code, i.e. configuration and queries."""
        datasets, layouts = [], []
        for query_path in dashboard_folder.glob("*.sql"):
            with query_path.open("r") as query_file:
                raw_query = query_file.read()
            dataset = Dataset(name=query_path.stem, display_name=query_path.stem, query=raw_query)
            datasets.append(dataset)

            fields = [Field(name="count", expression="`count`")]
            query = Query(dataset_name=dataset.name, fields=fields, disaggregated=True)
            # As for as testing went, a NamedQuery should always have "main_query" as name
            named_query = NamedQuery(name="main_query", query=query)
            counter_field_encoding = CounterFieldEncoding(field_name="count", display_name="count")
            counter_spec = CounterSpec(CounterEncodingMap(value=counter_field_encoding))
            widget = Widget(name=dataset.name, queries=[named_query], spec=counter_spec)
            position = Position(x=0, y=0, width=1, height=3)
            layout = Layout(widget=widget, position=position)
            layouts.append(layout)

        page = Page(name=dashboard_folder.name, display_name=dashboard_folder.name, layout=layouts)
        lakeview_dashboard = Dashboard(datasets=datasets, pages=[page])
        return lakeview_dashboard

    def deploy_dashboard(
        self, lakeview_dashboard: Dashboard, *, display_name: str | None = None, dashboard_id: str | None = None
    ) -> SDKDashboard:
        """Deploy a lakeview dashboard."""
        if (display_name is None and dashboard_id is None) or (display_name is not None and dashboard_id is not None):
            raise ValueError("Give either display_name or dashboard_id.")
        if display_name is not None:
            dashboard = self._ws.lakeview.create(
                display_name, serialized_dashboard=json.dumps(lakeview_dashboard.as_dict())
            )
        else:
            assert dashboard_id is not None
            dashboard = self._ws.lakeview.update(
                dashboard_id, serialized_dashboard=json.dumps(lakeview_dashboard.as_dict())
            )
        return dashboard

    def _with_better_names(self, dashboard: Dashboard) -> Dashboard:
        """Replace names with human-readable names."""
        better_names = {}
        for dataset in dashboard.datasets:
            if dataset.display_name is not None:
                better_names[dataset.name] = dataset.display_name
        for page in dashboard.pages:
            if page.display_name is not None:
                better_names[page.name] = page.display_name
        return self._replace_names(dashboard, better_names)

    def _replace_names(self, node: T, better_names: dict[str, str]) -> T:
        # walk every dataclass instance recursively and replace names
        if dataclasses.is_dataclass(node):
            for field in dataclasses.fields(node):
                value = getattr(node, field.name)
                if isinstance(value, list):
                    setattr(node, field.name, [self._replace_names(item, better_names) for item in value])
                elif dataclasses.is_dataclass(value):
                    setattr(node, field.name, self._replace_names(value, better_names))
        if isinstance(node, Dataset):
            node.name = better_names.get(node.name, node.name)
        elif isinstance(node, Page):
            node.name = better_names.get(node.name, node.name)
        elif isinstance(node, Query):
            node.dataset_name = better_names.get(node.dataset_name, node.dataset_name)
        elif isinstance(node, NamedQuery) and node.query:
            # 'dashboards/01eeb077e38c17e6ba3511036985960c/datasets/01eeb081882017f6a116991d124d3068_...'
            if node.name.startswith("dashboards/"):
                parts = [node.query.dataset_name]
                for query_field in node.query.fields:
                    parts.append(query_field.name)
                new_name = "_".join(parts)
                better_names[node.name] = new_name
            node.name = better_names.get(node.name, node.name)
        elif isinstance(node, ControlFieldEncoding):
            node.query_name = better_names.get(node.query_name, node.query_name)
        elif isinstance(node, Widget):
            if node.spec is not None:
                node.name = node.spec.as_dict().get("widgetType", node.name)
        return node
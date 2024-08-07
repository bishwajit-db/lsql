import json
import logging
import webbrowser
from pathlib import Path

import pytest
from databricks.labs.blueprint.entrypoint import is_in_debug
from databricks.sdk.core import DatabricksError
from databricks.sdk.service.dashboards import Dashboard as SDKDashboard

from databricks.labs.lsql.dashboards import DashboardMetadata, Dashboards
from databricks.labs.lsql.lakeview.model import Dashboard

logger = logging.getLogger(__name__)


def factory(name, create, remove):
    cleanup = []

    def inner(**kwargs):
        x = create(**kwargs)
        logger.debug(f"added {name} fixture: {x}")
        cleanup.append(x)
        return x

    yield inner
    logger.debug(f"clearing {len(cleanup)} {name} fixtures")
    for x in cleanup:
        try:
            logger.debug(f"removing {name} fixture: {x}")
            remove(x)
        except DatabricksError as e:
            # TODO: fix on the databricks-labs-pytester level
            logger.debug(f"ignoring error while {name} {x} teardown: {e}")


@pytest.fixture
def make_dashboard(ws, make_random):
    """Clean the lakeview dashboard"""

    def create(display_name: str = "") -> SDKDashboard:
        if len(display_name) == 0:
            display_name = f"created_by_lsql_{make_random()}"
        else:
            display_name = f"{display_name} ({make_random()})"
        dashboard = ws.lakeview.create(display_name)
        if is_in_debug():
            dashboard_url = f"{ws.config.host}/sql/dashboardsv3/{dashboard.dashboard_id}"
            webbrowser.open(dashboard_url)
        return dashboard

    def delete(dashboard: SDKDashboard) -> None:
        ws.lakeview.trash(dashboard.dashboard_id)

    yield from factory("dashboard", create, delete)


@pytest.fixture
def tmp_path(tmp_path, make_random):
    """Adds a random subfolder name.

    The folder name becomes the dashboard name, which then becomes the Lakeview file name with the
    `.lvdash.json` extension. `tmp_path` last subfolder contains the test name cut off at thirty characters plus a
    number starting at zero indicating the test run. `tmp_path` adds randomness in the parent folders. Because most test
    start with `test_dashboards_deploys_dashboard_`, the dashboard name for most tests ends up being
    `test_dashboard_deploys_dashboa0.lvdash.json`, causing collisions. This is solved by adding a random subfolder name.
    """
    folder = tmp_path / f"created_by_lsql_{make_random()}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def test_dashboards_deploys_exported_dashboard_definition(ws, make_dashboard):
    dashboards = Dashboards(ws)
    sdk_dashboard = make_dashboard()

    dashboard_file = Path(__file__).parent / "dashboards" / "dashboard.lvdash.json"
    lakeview_dashboard = Dashboard.from_dict(json.loads(dashboard_file.read_text()))

    sdk_dashboard = dashboards.deploy_dashboard(lakeview_dashboard, dashboard_id=sdk_dashboard.dashboard_id)
    new_dashboard = dashboards.get_dashboard(sdk_dashboard.path)

    assert (
        dashboards._with_better_names(lakeview_dashboard).as_dict()
        == dashboards._with_better_names(new_dashboard).as_dict()
    )


def test_dashboard_deploys_dashboard_the_same_as_created_dashboard(ws, make_dashboard, tmp_path):
    dashboards = Dashboards(ws)
    sdk_dashboard = make_dashboard()

    (tmp_path / "counter.sql").write_text("SELECT 10 AS count")
    dashboard_metadata = DashboardMetadata.from_path(tmp_path)
    lakeview_dashboard = dashboard_metadata.as_lakeview()

    sdk_dashboard = dashboards.deploy_dashboard(lakeview_dashboard, dashboard_id=sdk_dashboard.dashboard_id)
    new_dashboard = dashboards.get_dashboard(sdk_dashboard.path)

    assert (
        dashboards._with_better_names(lakeview_dashboard).as_dict()
        == dashboards._with_better_names(new_dashboard).as_dict()
    )


def test_dashboard_deploys_dashboard_with_ten_counters(ws, make_dashboard, tmp_path):
    dashboards = Dashboards(ws)
    sdk_dashboard = make_dashboard()

    for i in range(10):
        (tmp_path / f"counter_{i}.sql").write_text(f"SELECT {i} AS count")
    dashboard_metadata = DashboardMetadata.from_path(tmp_path)
    lakeview_dashboard = dashboard_metadata.as_lakeview()

    sdk_dashboard = dashboards.deploy_dashboard(lakeview_dashboard, dashboard_id=sdk_dashboard.dashboard_id)

    assert ws.lakeview.get(sdk_dashboard.dashboard_id)


def test_dashboard_deploys_dashboard_with_display_name(ws, make_dashboard, tmp_path):
    dashboards = Dashboards(ws)
    sdk_dashboard = make_dashboard(display_name="Counter")

    (tmp_path / "dashboard.yml").write_text("display_name: Counter")
    (tmp_path / "counter.sql").write_text("SELECT 102132 AS count")
    dashboard_metadata = DashboardMetadata.from_path(tmp_path)
    lakeview_dashboard = dashboard_metadata.as_lakeview()

    sdk_dashboard = dashboards.deploy_dashboard(lakeview_dashboard, dashboard_id=sdk_dashboard.dashboard_id)

    assert ws.lakeview.get(sdk_dashboard.dashboard_id)


def test_dashboard_deploys_dashboard_with_counter_variation(ws, make_dashboard, tmp_path):
    dashboards = Dashboards(ws)
    sdk_dashboard = make_dashboard()

    (tmp_path / "counter.sql").write_text("SELECT 10 AS `Something Else Than Count`")
    dashboard_metadata = DashboardMetadata.from_path(tmp_path)
    lakeview_dashboard = dashboard_metadata.as_lakeview()

    sdk_dashboard = dashboards.deploy_dashboard(lakeview_dashboard, dashboard_id=sdk_dashboard.dashboard_id)

    assert ws.lakeview.get(sdk_dashboard.dashboard_id)


def test_dashboard_deploys_dashboard_with_big_widget(ws, make_dashboard, tmp_path):
    dashboards = Dashboards(ws)
    sdk_dashboard = make_dashboard()

    query = """-- --width 6 --height 3\nSELECT 82917019218921 AS big_number_needs_big_widget"""
    (tmp_path / "counter.sql").write_text(query)
    dashboard_metadata = DashboardMetadata.from_path(tmp_path)
    lakeview_dashboard = dashboard_metadata.as_lakeview()

    sdk_dashboard = dashboards.deploy_dashboard(lakeview_dashboard, dashboard_id=sdk_dashboard.dashboard_id)

    assert ws.lakeview.get(sdk_dashboard.dashboard_id)


def test_dashboards_deploys_dashboard_with_order_overwrite_in_query_header(ws, make_dashboard, tmp_path):
    dashboards = Dashboards(ws)
    sdk_dashboard = make_dashboard()

    for query_name in range(6):
        (tmp_path / f"{query_name}.sql").write_text(f"SELECT {query_name} AS count")
    # Move the '4' inbetween '1' and '2' query. Note that the order 1 puts '4' on the same position as '1', but with an
    # order tiebreaker the query name decides the final order.
    (tmp_path / "4.sql").write_text("-- --order 1\nSELECT 4 AS count")
    dashboard_metadata = DashboardMetadata.from_path(tmp_path)
    lakeview_dashboard = dashboard_metadata.as_lakeview()

    sdk_dashboard = dashboards.deploy_dashboard(lakeview_dashboard, dashboard_id=sdk_dashboard.dashboard_id)

    assert ws.lakeview.get(sdk_dashboard.dashboard_id)


def test_dashboards_deploys_dashboard_with_order_overwrite_in_dashboard_yaml(ws, make_dashboard, tmp_path):
    dashboards = Dashboards(ws)
    sdk_dashboard = make_dashboard()

    # Move the '4' inbetween '1' and '2' query. Note that the order 1 puts '4' on the same position as '1', but with an
    # order tiebreaker the query name decides the final order.
    content = """
display_name: Counters

tiles:
  query_4:
    order: 1
""".lstrip()
    (tmp_path / "dashboard.yml").write_text(content)
    for query_name in range(6):
        (tmp_path / f"query_{query_name}.sql").write_text(f"SELECT {query_name} AS count")
    dashboard_metadata = DashboardMetadata.from_path(tmp_path)
    lakeview_dashboard = dashboard_metadata.as_lakeview()

    sdk_dashboard = dashboards.deploy_dashboard(lakeview_dashboard, dashboard_id=sdk_dashboard.dashboard_id)

    assert ws.lakeview.get(sdk_dashboard.dashboard_id)


def test_dashboard_deploys_dashboard_with_table(ws, make_dashboard):
    dashboards = Dashboards(ws)
    sdk_dashboard = make_dashboard()

    dashboard_folder = Path(__file__).parent / "dashboards" / "one_table"
    dashboard_metadata = DashboardMetadata.from_path(dashboard_folder)
    lakeview_dashboard = dashboard_metadata.as_lakeview()

    sdk_dashboard = dashboards.deploy_dashboard(lakeview_dashboard, dashboard_id=sdk_dashboard.dashboard_id)

    assert ws.lakeview.get(sdk_dashboard.dashboard_id)


def test_dashboards_deploys_dashboard_with_invalid_query(ws, make_dashboard, tmp_path):
    dashboards = Dashboards(ws)
    sdk_dashboard = make_dashboard()

    for query_name in range(6):
        (tmp_path / f"{query_name}.sql").write_text(f"SELECT {query_name} AS count")
    (tmp_path / "4.sql").write_text("SELECT COUNT(* AS invalid_column")
    dashboard_metadata = DashboardMetadata.from_path(tmp_path)
    lakeview_dashboard = dashboard_metadata.as_lakeview()

    sdk_dashboard = dashboards.deploy_dashboard(lakeview_dashboard, dashboard_id=sdk_dashboard.dashboard_id)

    assert ws.lakeview.get(sdk_dashboard.dashboard_id)


def test_dashboards_deploys_dashboard_with_markdown_header(ws, make_dashboard, tmp_path):
    dashboards = Dashboards(ws)
    sdk_dashboard = make_dashboard()

    for count, query_name in enumerate("abcdef"):
        (tmp_path / f"{query_name}.sql").write_text(f"SELECT {count} AS count")
    (tmp_path / "z_description.md").write_text("---\norder: -1\n---\nBelow you see counters.")
    dashboard_metadata = DashboardMetadata.from_path(tmp_path)
    lakeview_dashboard = dashboard_metadata.as_lakeview()

    sdk_dashboard = dashboards.deploy_dashboard(lakeview_dashboard, dashboard_id=sdk_dashboard.dashboard_id)

    assert ws.lakeview.get(sdk_dashboard.dashboard_id)


def test_dashboards_deploys_dashboard_with_widget_title_and_description(ws, make_dashboard, tmp_path):
    dashboards = Dashboards(ws)
    sdk_dashboard = make_dashboard()

    description = "-- --title 'Counting' --description 'The answer to life'\nSELECT 42"
    (tmp_path / "counter.sql").write_text(description)
    dashboard_metadata = DashboardMetadata.from_path(tmp_path)
    lakeview_dashboard = dashboard_metadata.as_lakeview()

    sdk_dashboard = dashboards.deploy_dashboard(lakeview_dashboard, dashboard_id=sdk_dashboard.dashboard_id)

    assert ws.lakeview.get(sdk_dashboard.dashboard_id)


def test_dashboards_deploys_dashboard_from_query_with_cte(ws, make_dashboard, tmp_path):
    dashboards = Dashboards(ws)
    sdk_dashboard = make_dashboard()

    table_query_path = Path(__file__).parent / "dashboards/one_table/databricks_office_locations.sql"
    office_locations = table_query_path.read_text()
    query_with_cte = (
        f"WITH data AS ({office_locations})\n"
        "-- --title 'Databricks Office Locations'\n"
        "SELECT Address, State, Country FROM data"
    )
    (tmp_path / "table.sql").write_text(query_with_cte)
    dashboard_metadata = DashboardMetadata.from_path(tmp_path)
    lakeview_dashboard = dashboard_metadata.as_lakeview()

    sdk_dashboard = dashboards.deploy_dashboard(lakeview_dashboard, dashboard_id=sdk_dashboard.dashboard_id)

    assert ws.lakeview.get(sdk_dashboard.dashboard_id)


def test_dashboards_deploys_dashboard_with_filters(ws, make_dashboard, tmp_path):
    dashboards = Dashboards(ws)
    sdk_dashboard = make_dashboard()

    table_query_path = Path(__file__).parent / "dashboards/one_table/databricks_office_locations.sql"
    office_locations = table_query_path.read_text()
    (tmp_path / "table.sql").write_text(f"-- --width 2 --filter City State Country\n{office_locations}")
    dashboard_metadata = DashboardMetadata.from_path(tmp_path)
    lakeview_dashboard = dashboard_metadata.as_lakeview()

    sdk_dashboard = dashboards.deploy_dashboard(lakeview_dashboard, dashboard_id=sdk_dashboard.dashboard_id)

    assert ws.lakeview.get(sdk_dashboard.dashboard_id)


def test_dashboard_deploys_dashboard_with_empty_title(ws, make_dashboard, tmp_path):
    dashboards = Dashboards(ws)
    sdk_dashboard = make_dashboard()

    query = '-- --overrides \'{"spec": {"frame": {"showTitle": true}}}\'\nSELECT 102132 AS count'
    (tmp_path / "counter.sql").write_text(query)
    dashboard_metadata = DashboardMetadata.from_path(tmp_path)
    lakeview_dashboard = dashboard_metadata.as_lakeview()

    sdk_dashboard = dashboards.deploy_dashboard(lakeview_dashboard, dashboard_id=sdk_dashboard.dashboard_id)

    assert ws.lakeview.get(sdk_dashboard.dashboard_id)

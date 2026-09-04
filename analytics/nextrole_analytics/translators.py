"""Asset-key translators that stitch the dlt and dbt halves of the graph together."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dagster import AssetKey
from dagster_dlt import DagsterDltTranslator

if TYPE_CHECKING:
    from dagster import AssetSpec
    from dagster_dlt.translator import DltResourceTranslatorData


class RawDatasetTranslator(DagsterDltTranslator):
    """Key dlt assets as ``[dataset, resource]`` (e.g. ``raw_app/run``).

    dbt derives source asset keys as ``[source_name, table_name]``, and the dbt
    sources are named after the dlt datasets — so with this translator the dlt
    outputs and the dbt source dependencies are the *same* asset keys, which is
    what connects extraction → staging → marts into one lineage graph with no
    dbt-side translator work.
    """

    def __init__(self, dataset_name: str) -> None:
        """Remember the dlt dataset name used as the asset-key prefix."""
        super().__init__()
        self._dataset_name = dataset_name

    def get_asset_spec(self, data: DltResourceTranslatorData) -> AssetSpec:
        """Rewrite only the key; every other attribute keeps its default."""
        spec = super().get_asset_spec(data)
        return spec.replace_attributes(
            key=AssetKey([self._dataset_name, data.resource.name]),
        )

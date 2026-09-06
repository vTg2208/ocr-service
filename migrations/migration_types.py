"""Frozen cross-dialect column types used by historical migration revisions."""

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.types import UserDefinedType


class GeoJSONMultiPolygon(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **_kwargs):
        return "geometry(MultiPolygon,4326)"


@compiles(GeoJSONMultiPolygon, "postgresql")
def compile_postgis_multipolygon(_type, _compiler, **_kwargs):
    return "geometry(MultiPolygon,4326)"


@compiles(GeoJSONMultiPolygon, "sqlite")
def compile_sqlite_geojson(_type, _compiler, **_kwargs):
    return "JSON"

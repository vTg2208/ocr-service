import unittest
from datetime import date

from app.services.stac_imagery import STACClient, STACConfigurationError


GEOMETRY = {"type": "MultiPolygon", "coordinates": [[[[79, 10], [79.1, 10], [79.1, 10.1], [79, 10], [79, 10]]]]}


def item(identifier, acquired, cloud, *, href="https://data.example.org/private.tif?token=secret"):
    return {
        "type": "Feature", "id": identifier,
        "geometry": {"type": "Polygon", "coordinates": [[[79, 10], [79.1, 10], [79.1, 10.1], [79, 10]]]},
        "collection": "landsat-c2-l2",
        "properties": {"datetime": acquired, "eo:cloud_cover": cloud},
        "assets": {"visual": {"href": href, "type": "image/tiff"}},
        "links": [{"rel": "license", "href": "https://example.org/license"}],
    }


class STACImageryTests(unittest.TestCase):
    def test_search_builds_bounded_spatial_query_paginates_and_ranks_scenes(self):
        calls = []
        pages = [
            {"features": [item("cloudy", "2005-01-01T00:00:00Z", 25)], "links": [{"rel": "next", "href": "https://stac.example.org/next"}]},
            {"features": [item("least-cloud-nearest-date", "2005-06-01T00:00:00Z", 5), item("least-cloud-far", "2004-01-01T00:00:00Z", 5)], "links": []},
        ]
        def transport(endpoint, payload, timeout):
            calls.append((endpoint, payload, timeout)); return pages[len(calls) - 1]
        client = STACClient(
            "https://stac.example.org/search", allowed_hosts={"stac.example.org"},
            allowed_collections={"landsat-c2-l2"}, transport=transport,
            max_pages=3, max_results=10,
        )
        result = client.search(GEOMETRY, (date(2004, 1, 1), date(2006, 12, 31)), ["landsat-c2-l2"], 30)
        self.assertEqual(result[0].scene_id, "least-cloud-nearest-date")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1]["intersects"], GEOMETRY)
        self.assertEqual(calls[0][1]["query"]["eo:cloud_cover"]["lte"], 30)
        self.assertNotIn("token=secret", repr(result[0]))
        self.assertIn("token=secret", result[0].private_asset_references["visual"]["href"])

    def test_search_skips_malformed_and_over_cloud_items_with_warning(self):
        response = {"features": [item("too-cloudy", "2005-01-01T00:00:00Z", 80), {"id": "broken"}], "links": []}
        client = STACClient(
            "https://stac.example.org/search", allowed_hosts={"stac.example.org"},
            allowed_collections={"landsat-c2-l2"}, transport=lambda *_args: response,
        )
        self.assertEqual(client.search(GEOMETRY, (date(2005, 1, 1), date(2005, 12, 31)), ["landsat-c2-l2"], 30), [])
        self.assertTrue(client.last_warnings)

    def test_endpoint_collection_and_next_link_are_allowlisted(self):
        with self.assertRaisesRegex(STACConfigurationError, "allow-listed"):
            STACClient("https://evil.example/search", allowed_hosts={"stac.example.org"}, allowed_collections={"landsat-c2-l2"})
        client = STACClient(
            "https://stac.example.org/search", allowed_hosts={"stac.example.org"},
            allowed_collections={"landsat-c2-l2"},
            transport=lambda *_args: {"features": [], "links": [{"rel": "next", "href": "https://evil.example/next"}]},
        )
        with self.assertRaisesRegex(STACConfigurationError, "next link"):
            client.search(GEOMETRY, (date(2005, 1, 1), date(2005, 12, 31)), ["landsat-c2-l2"], 30)


if __name__ == "__main__": unittest.main()

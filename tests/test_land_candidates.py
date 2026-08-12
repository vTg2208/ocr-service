import unittest

from app.services.land_candidates import extract_land_candidates


class LandCandidateTests(unittest.TestCase):
    def test_extracts_separate_survey_area_pairs(self):
        candidates = extract_land_candidates(
            "207/9 (0.04.00), 208/2B1 (0.08.50), total area 0.12.50 hectares"
        )
        self.assertEqual(
            [(item.survey_number, item.area_raw) for item in candidates.parcels],
            [("207/9", "0.04.00"), ("208/2B1", "0.08.50")],
        )

    def test_extracts_only_explicit_valid_coordinate_pairs(self):
        candidates = extract_land_candidates(
            "Latitude: 12.6934 Longitude: 79.9757, Kanchipuram District"
        )
        self.assertEqual(len(candidates.coordinates), 1)
        self.assertEqual(candidates.coordinates[0].latitude, 12.6934)
        self.assertEqual(candidates.coordinates[0].longitude, 79.9757)
        self.assertEqual(extract_land_candidates("Kanchipuram District").coordinates, [])
        self.assertEqual(
            extract_land_candidates("Latitude: 120 Longitude: 300").coordinates,
            [],
        )

    def test_extracts_administrative_locations_dates_and_references(self):
        candidates = extract_land_candidates(
            "Pazhaveri Village, Uthiramerur Taluk, Kanchipuram District, "
            "Ref No.346/Q3/2022 dated 03.02.2024"
        )
        self.assertEqual(
            [(item.kind, item.value) for item in candidates.locations],
            [
                ("village", "Pazhaveri"),
                ("taluk", "Uthiramerur"),
                ("district", "Kanchipuram"),
            ],
        )
        self.assertEqual([item.value for item in candidates.dates], ["03.02.2024"])
        self.assertEqual(
            [item.value for item in candidates.reference_numbers],
            ["346/Q3/2022"],
        )


class DMSCoordinateTests(unittest.TestCase):
    def test_converts_explicit_dms_coordinates(self):
        candidates = extract_land_candidates('12°41\'36.2"N 79°58\'32.5"E')
        coordinate = candidates.coordinates[0]
        self.assertEqual(round(coordinate.latitude, 5), 12.69339)
        self.assertEqual(round(coordinate.longitude, 5), 79.97569)
        self.assertEqual(coordinate.format, "dms")


if __name__ == "__main__":
    unittest.main()

"""
Tests for converting RTZ resource files and verifying generated output formats.
"""
import base64
import unittest
from pathlib import Path

import pandas as pd

import app


RESOURCE_DIR = Path(__file__).resolve().parents[1] / "sources"
UNUSED_TEMPLATE_COLUMNS = {
    "VesselName",
    "VesselIMO",
    "LegBearing",
    "ETA",
    "ETD",
    "StayDuration",
}


class ResourceConversionTests(unittest.TestCase):
    def test_all_rtz_resource_files_convert_to_coordinate_rows(self):
        expected_rows = {
            "Navig8 Honor - 08062026084147.rtz": 38,
            "Port Allen - Mississippi to Corpus Christi.rtz": 346,
            "TO AMSTERDAM.rtz": 45,
        }

        for filename, row_count in expected_rows.items():
            with self.subTest(filename=filename):
                path = RESOURCE_DIR / filename
                content = path.read_text(encoding="utf-8")
                df = app.convert_to_dataframe(path.name, content)

                self.assertEqual(len(df), row_count)
                self.assertIn("Latitude", df.columns)
                self.assertIn("Longitude", df.columns)
                self.assertEqual(df["Latitude"].notna().sum(), row_count)
                self.assertEqual(df["Longitude"].notna().sum(), row_count)
                self.assertFalse(UNUSED_TEMPLATE_COLUMNS.intersection(df.columns))

    def test_combined_resource_export_has_no_unused_template_columns(self):
        frames = []
        for path in RESOURCE_DIR.iterdir():
            if not path.is_file() or path.suffix.lower() != ".rtz":
                continue
            content = path.read_text(encoding="utf-8")
            df = app.convert_to_dataframe(path.name, content)
            df.insert(0, "SourceFile", path.name)
            frames.append(df)

        combined = app._drop_empty_columns(pd.concat(frames, ignore_index=True, sort=False))

        self.assertFalse(UNUSED_TEMPLATE_COLUMNS.intersection(combined.columns))
        for column in combined.columns:
            has_value = combined[column].notna() & (combined[column].astype(str).str.strip() != "")
            self.assertTrue(has_value.any(), column)

    def test_can_generate_rt3_from_resource_and_reparse(self):
        """Ensure RT3 generation produces valid XML that can be parsed again."""
        path = RESOURCE_DIR / "TO AMSTERDAM.rtz"
        original_df = app.convert_to_dataframe(path.name, path.read_text(encoding="utf-8"))

        rt3_content = app.generate_rt3(original_df)

        # The generated RT3 contains an embedded <route>; parse_rtz should extract it
        reparsed_df = app.parse_rtz(rt3_content)
        self.assertEqual(len(reparsed_df), len(original_df))
        self.assertAlmostEqual(reparsed_df["Latitude"].iloc[0],
                               original_df["Latitude"].iloc[0], places=6)
        self.assertAlmostEqual(reparsed_df["Longitude"].iloc[0],
                               original_df["Longitude"].iloc[0], places=6)

    def test_can_generate_txt_from_resource(self):
        """Ensure TXT generation produces valid tab-separated output."""
        path = RESOURCE_DIR / "TO AMSTERDAM.rtz"
        df = app.convert_to_dataframe(path.name, path.read_text(encoding="utf-8"))

        txt_content = app.generate_txt(df)
        lines = txt_content.strip().split("\n")

        # Header + data rows
        self.assertEqual(len(lines), len(df) + 1)
        self.assertIn("NAME", lines[0])
        self.assertIn("LAT", lines[0])
        self.assertIn("LON", lines[0])

    def test_can_generate_rtm_from_resource(self):
        """Ensure RTM generation produces valid binary output."""
        path = RESOURCE_DIR / "TO AMSTERDAM.rtz"
        df = app.convert_to_dataframe(path.name, path.read_text(encoding="utf-8"))

        rtm_content = app.generate_rtm(df)
        self.assertTrue(rtm_content.startswith(b"Route File Version"))
        self.assertGreater(len(rtm_content), 0)

    def test_can_generate_csv_from_resource(self):
        """Ensure CSV generation produces valid JRC ECDIS Route Sheet output."""
        path = RESOURCE_DIR / "TO AMSTERDAM.rtz"
        df = app.convert_to_dataframe(path.name, path.read_text(encoding="utf-8"))

        csv_content = app.generate_csv(df)
        # Check JRC ECDIS header lines
        self.assertIn("// ROUTE SHEET exported by JRC ECDIS.", csv_content)
        self.assertIn("// WPT No.", csv_content)
        self.assertIn("PORT[NM]", csv_content)
        self.assertIn("STBD[NM]", csv_content)
        self.assertIn("Arr. Rad[NM]", csv_content)
        self.assertIn("Speed[kn]", csv_content)
        self.assertIn("Sail(RL/GC)", csv_content)
        self.assertIn("ROT[deg/min]", csv_content)
        self.assertIn("Turn Rad[NM]", csv_content)
        # Check data rows have DMS format (degrees, minutes, hemisphere)
        lines = csv_content.strip().split("\n")
        data_lines = [l for l in lines if not l.startswith("//")]
        self.assertGreater(len(data_lines), 0)
        # First data line should have DMS components
        first_data = data_lines[0]
        self.assertRegex(first_data, r"\d{3},\d{2},\d{2}\.\d{3},[NS],\d{2},\d{2}\.\d{3},[EW]")
        # Check for *** on first waypoint navigation fields
        self.assertIn("***", first_data)

    def test_upload_callback_accepts_rtz_resource(self):
        path = RESOURCE_DIR / "TO AMSTERDAM.rtz"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")

        store, _status, _preview_style, _download_style, csv_disabled, txt_disabled, rt3_disabled, rtm_disabled, _title, badge, _table = app.on_upload(
            f"data:application/octet-stream;base64,{encoded}",
            path.name,
        )

        self.assertFalse(csv_disabled)
        self.assertFalse(txt_disabled)
        self.assertFalse(rt3_disabled)
        self.assertFalse(rtm_disabled)
        self.assertEqual(badge, "45 rows")
        self.assertEqual(len(store["records"]), 45)
        self.assertEqual(store["filename"], path.name)


if __name__ == "__main__":
    unittest.main()
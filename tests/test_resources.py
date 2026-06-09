import base64
import unittest
from pathlib import Path

import pandas as pd

import app


RESOURCE_DIR = Path(__file__).resolve().parents[1] / "resources"
UNUSED_TEMPLATE_COLUMNS = {
    "VesselName",
    "VesselIMO",
    "LegBearing",
    "ETA",
    "ETD",
    "StayDuration",
}


class ResourceConversionTests(unittest.TestCase):
    def test_all_resource_files_convert_to_coordinate_rows(self):
        expected_rows = {
            "2602L HOUSTON TO PORT LOUIS.rtm": 95,
            "2602L HOUSTON TO PORT LOUIS.rtz": 95,
            "GUAIBA PILOT TO PORT LOUIS.txt": 14,
            "Hirohata to Balb-Punta arenas DEV.rtz": 5,
            "Navig8 Honor - 08062026084147.rtz": 38,
            "Port Allen - Mississippi to Corpus Christi.rtz": 346,
            "TO AMSTERDAM.rtz": 45,
            "V21 SPORE - PDM 3.rtz": 75,
        }

        for filename, row_count in expected_rows.items():
            with self.subTest(filename=filename):
                path = RESOURCE_DIR / filename
                content = path.read_bytes() if path.suffix.lower() == ".rtm" else path.read_text(encoding="utf-8")
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
            if not path.is_file():
                continue
            content = path.read_bytes() if path.suffix.lower() == ".rtm" else path.read_text(encoding="utf-8")
            df = app.convert_to_dataframe(path.name, content)
            df.insert(0, "SourceFile", path.name)
            frames.append(df)

        combined = app._drop_empty_columns(pd.concat(frames, ignore_index=True, sort=False))

        self.assertFalse(UNUSED_TEMPLATE_COLUMNS.intersection(combined.columns))
        for column in combined.columns:
            has_value = combined[column].notna() & (combined[column].astype(str).str.strip() != "")
            self.assertTrue(has_value.any(), column)

    def test_txt_resource_converts_degrees_minutes_to_decimal(self):
        path = RESOURCE_DIR / "GUAIBA PILOT TO PORT LOUIS.txt"
        df = app.convert_to_dataframe(path.name, path.read_text(encoding="utf-8"))

        self.assertEqual(df.loc[0, "Name"], "SEPETIBA")
        self.assertAlmostEqual(df.loc[0, "Latitude"], -23.1394666667)
        self.assertAlmostEqual(df.loc[0, "Longitude"], -44.0401333333)

    def test_binary_rtm_resource_matches_companion_rtz_route(self):
        rtm_path = RESOURCE_DIR / "2602L HOUSTON TO PORT LOUIS.rtm"
        rtz_path = RESOURCE_DIR / "2602L HOUSTON TO PORT LOUIS.rtz"

        rtm_df = app.convert_to_dataframe(rtm_path.name, rtm_path.read_bytes())
        rtz_df = app.convert_to_dataframe(rtz_path.name, rtz_path.read_text(encoding="utf-8"))

        self.assertEqual(rtm_df.loc[0, "Name"], rtz_df.loc[0, "Name"])
        self.assertAlmostEqual(rtm_df.loc[0, "Latitude"], rtz_df.loc[0, "Latitude"], places=4)
        self.assertAlmostEqual(rtm_df.loc[0, "Longitude"], rtz_df.loc[0, "Longitude"], places=4)
        self.assertAlmostEqual(rtm_df.loc[94, "Latitude"], rtz_df.loc[94, "Latitude"], places=4)
        self.assertAlmostEqual(rtm_df.loc[94, "Longitude"], rtz_df.loc[94, "Longitude"], places=4)

    def test_upload_callback_accepts_binary_rtm_resource(self):
        path = RESOURCE_DIR / "2602L HOUSTON TO PORT LOUIS.rtm"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")

        store, _status, _preview_style, _download_style, disabled, _title, badge, _table = app.on_upload(
            f"data:application/octet-stream;base64,{encoded}",
            path.name,
        )

        self.assertFalse(disabled)
        self.assertEqual(badge, "95 rows")
        self.assertEqual(len(store["records"]), 95)
        self.assertEqual(store["records"][0]["SourceFile"], path.name)


if __name__ == "__main__":
    unittest.main()

"""
Tests for RTZ parsing and format generation.
"""
import base64
import unittest

import pandas as pd

import app


class ParserTests(unittest.TestCase):
    def test_parse_rtz_namespaced_route_with_position_attributes(self):
        content = """<?xml version="1.0"?>
        <route version="1.0" xmlns="http://www.cirm.org/RTZ/1/0">
          <routeInfo routeName="Harbor Approach" />
          <waypoints>
            <waypoint id="1" name="Pilot">
              <position lat="12.34" lon="56.78" />
            </waypoint>
            <waypoint id="2" name="Berth" turnRadius="0.5">
              <position lat="13.34" lon="57.78" />
            </waypoint>
          </waypoints>
        </route>
        """

        df = app.parse_rtz(content)

        self.assertEqual(len(df), 2)
        self.assertEqual(df.loc[0, "RouteName"], "Harbor Approach")
        self.assertEqual(df.loc[0, "Name"], "Pilot")
        self.assertEqual(df.loc[0, "Latitude"], 12.34)
        self.assertEqual(df.loc[1, "TurnRadius"], 0.5)

    def test_parse_rtz_child_coordinate_tags(self):
        content = """<routes>
          <route routeName="Legacy">
            <waypoint id="A"><name>Alpha</name><lat>1.2</lat><lon>3.4</lon></waypoint>
          </route>
        </routes>"""

        df = app.parse_rtz(content)

        self.assertEqual(df.loc[0, "RouteName"], "Legacy")
        self.assertEqual(df.loc[0, "Name"], "Alpha")
        self.assertEqual(df.loc[0, "Latitude"], 1.2)
        self.assertEqual(df.loc[0, "Longitude"], 3.4)

    def test_parse_rtz_empty_results_in_empty_df(self):
        content = """<routes></routes>"""
        df = app.parse_rtz(content)
        self.assertTrue(df.empty)

    def test_decode_upload_data_uri(self):
        raw = "Name,Latitude,Longitude\nWP1,12.3,45.6\n"
        encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")

        self.assertEqual(app.decode_upload(f"data:text/plain;base64,{encoded}"), raw)

    def test_health_check(self):
        response = app.server.test_client().get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_convert_to_dataframe_rejects_unsupported(self):
        df = app.convert_to_dataframe("test.txt", "some text content")
        self.assertTrue(df.empty)

    def test_convert_to_dataframe_accepts_rt3(self):
        """convert_to_dataframe dispatches .rt3 files to parse_rt3."""
        rtz_content = """<route routeName="Test">
          <waypoint id="1" name="WP1"><position lat="12.3" lon="45.6" /></waypoint>
        </route>"""
        # Wrap in RT3 structure
        rt3_content = """<?xml version="1.0"?>
        <report>
          <voyage><id>TEST</id></voyage>
          <route routeName="Test">
            <waypoints>
              <waypoint id="1" name="WP1">
                <position lat="12.3" lon="45.6" />
              </waypoint>
            </waypoints>
          </route>
        </report>"""
        df = app.convert_to_dataframe("route.rt3", rt3_content)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.loc[0, "Name"], "WP1")
        self.assertEqual(df.loc[0, "Latitude"], 12.3)

    def test_convert_to_dataframe_accepts_rtm(self):
        """convert_to_dataframe dispatches .rtm files to parse_rtm."""
        df_in = pd.DataFrame({
            "RouteName": ["TestRoute"],
            "WaypointId": ["1"],
            "Name": ["WP1"],
            "Latitude": [12.34],
            "Longitude": [56.78],
        })
        rtm_bytes = app.generate_rtm(df_in)
        df_out = app.convert_to_dataframe("route.rtm", rtm_bytes)
        self.assertEqual(len(df_out), 1)
        self.assertEqual(df_out.loc[0, "Name"], "WP1")
        self.assertAlmostEqual(df_out.loc[0, "Latitude"], 12.34, places=6)
        self.assertAlmostEqual(df_out.loc[0, "Longitude"], 56.78, places=6)

    def test_parse_rt3_round_trip(self):
        """Generate an RT3 from a DataFrame, then parse it back and verify fields."""
        df_in = pd.DataFrame({
            "RouteName": ["RoundTripTest", "RoundTripTest"],
            "WaypointId": ["1", "2"],
            "Name": ["Start", "End"],
            "Latitude": [10.0, 20.0],
            "Longitude": [30.0, 40.0],
            "SpeedMax": [12.5, None],
            "TurnRadius": [0.5, None],
            "LegGeometryType": ["GC", ""],
        })
        rt3_content = app.generate_rt3(df_in)
        df_out = app.parse_rt3(rt3_content)

        self.assertEqual(len(df_out), 2)
        self.assertEqual(df_out.loc[0, "RouteName"], "RoundTripTest")
        self.assertEqual(df_out.loc[0, "Name"], "Start")
        self.assertEqual(df_out.loc[1, "Name"], "End")
        self.assertAlmostEqual(df_out.loc[0, "Latitude"], 10.0, places=6)
        self.assertAlmostEqual(df_out.loc[0, "Longitude"], 30.0, places=6)
        self.assertEqual(df_out.loc[0, "SpeedMax"], 12.5)
        self.assertEqual(df_out.loc[0, "LegGeometryType"], "GC")

    def test_parse_rtm_round_trip(self):
        """Generate an RTM from a DataFrame, then parse it back and verify fields."""
        df_in = pd.DataFrame({
            "RouteName": ["RTMRoundTrip", "RTMRoundTrip"],
            "WaypointId": ["1", "2"],
            "Name": ["Alpha", "Beta"],
            "Latitude": [15.5, -25.3],
            "Longitude": [120.1, -80.9],
        })
        rtm_bytes = app.generate_rtm(df_in)
        df_out = app.parse_rtm(rtm_bytes)

        self.assertEqual(len(df_out), 2)
        self.assertEqual(df_out["RouteName"].iloc[0], "RTMRoundTrip")
        self.assertEqual(df_out.loc[0, "Name"], "Alpha")
        self.assertEqual(df_out.loc[1, "Name"], "Beta")
        self.assertAlmostEqual(df_out.loc[0, "Latitude"], 15.5, places=6)
        self.assertAlmostEqual(df_out.loc[0, "Longitude"], 120.1, places=6)
        self.assertAlmostEqual(df_out.loc[1, "Latitude"], -25.3, places=6)
        self.assertAlmostEqual(df_out.loc[1, "Longitude"], -80.9, places=6)

    def test_parse_rt3_direct_wraps_to_rtz(self):
        """When parse_rt3 receives a bare <route>, it delegates to parse_rtz."""
        content = """<route routeName="BareRoute">
          <waypoint id="1" name="WP1"><position lat="1.0" lon="2.0" /></waypoint>
        </route>"""
        df = app.parse_rt3(content)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.loc[0, "RouteName"], "BareRoute")
        self.assertEqual(df.loc[0, "Latitude"], 1.0)

    def test_parse_rtm_from_resource_file(self):
        """Parse a real .rtm resource file (binary).

        Note: The real .rtm file may use a different binary layout than our
        generated format, so we verify that we can extract waypoints with
        reasonable coordinate values even if the header parsing differs.
        """
        from pathlib import Path
        rtm_path = Path(__file__).resolve().parents[1] / "sources" / "2602L HOUSTON TO PORT LOUIS.rtm"
        rtm_bytes = rtm_path.read_bytes()
        df = app.parse_rtm(rtm_bytes)
        self.assertGreater(len(df), 0)
        self.assertIn("Latitude", df.columns)
        self.assertIn("Longitude", df.columns)
        # Verify coordinates are reasonable (within valid ranges)
        for _, row in df.iterrows():
            lat = row.get("Latitude")
            lon = row.get("Longitude")
            if lat is not None and not (isinstance(lat, float) and pd.isna(lat)):
                self.assertGreaterEqual(lat, -90)
                self.assertLessEqual(lat, 90)
            if lon is not None and not (isinstance(lon, float) and pd.isna(lon)):
                self.assertGreaterEqual(lon, -180)
                self.assertLessEqual(lon, 180)

    def test_upload_callback_accepts_rt3(self):
        """The upload callback can process an RT3 file."""
        import base64
        rt3 = """<?xml version="1.0"?>
        <report>
          <voyage><id>T</id></voyage>
          <route routeName="RouteA">
            <waypoints>
              <waypoint id="1" name="WP1"><position lat="13.3" lon="46.6" /></waypoint>
            </waypoints>
          </route>
        </report>"""
        def data_uri(value):
            encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
            return f"data:text/plain;base64,{encoded}"

        store, _status, preview_style, download_style, csv_disabled, title, badge, _table = app.on_upload(
            data_uri(rt3),
            "route.rt3",
        )

        self.assertFalse(csv_disabled)
        self.assertEqual(preview_style["display"], "block")
        self.assertEqual(download_style["display"], "block")
        self.assertEqual(title, "Preview — route.rt3")
        self.assertEqual(badge, "1 rows")
        self.assertEqual(len(store["records"]), 1)

    def test_upload_callback_accepts_rtm(self):
        """The upload callback can process an RTM binary file."""
        import base64
        df_in = pd.DataFrame({
            "RouteName": ["RTMTest"],
            "WaypointId": ["1"],
            "Name": ["WP1"],
            "Latitude": [13.3],
            "Longitude": [46.6],
        })
        rtm_bytes = app.generate_rtm(df_in)

        def data_uri(value):
            encoded = base64.b64encode(value).decode("ascii")
            return f"data:application/octet-stream;base64,{encoded}"

        store, _status, preview_style, download_style, csv_disabled, title, badge, _table = app.on_upload(
            data_uri(rtm_bytes),
            "route.rtm",
        )

        self.assertFalse(csv_disabled)
        self.assertEqual(preview_style["display"], "block")
        self.assertEqual(download_style["display"], "block")
        self.assertEqual(badge, "1 rows")
        self.assertEqual(len(store["records"]), 1)

    def test_convert_to_dataframe_accepts_rtz(self):
        content = """<route routeName="Test">
          <waypoint id="1" name="WP1"><position lat="12.3" lon="45.6" /></waypoint>
        </route>"""
        df = app.convert_to_dataframe("route.rtz", content)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.loc[0, "Name"], "WP1")

    def test_generate_csv(self):
        df = pd.DataFrame({
            "RouteName": ["Test"],
            "WaypointId": ["1"],
            "Name": ["WP1"],
            "Latitude": [12.34],
            "Longitude": [56.78],
        })
        csv_content = app.generate_csv(df)
        # Check JRC ECDIS header
        self.assertIn("// ROUTE SHEET exported by JRC ECDIS.", csv_content)
        self.assertIn("// WPT No.", csv_content)
        # Check data contains the waypoint name
        self.assertIn("WP1", csv_content)
        # Check DMS coordinate components are present (lat: 12°20.400'N, lon: 56°46.800'N)
        self.assertIn("12", csv_content.split("\n")[4])
        self.assertIn("20.400", csv_content)
        self.assertIn("56", csv_content.split("\n")[4])
        self.assertIn("46.800", csv_content)

    def test_generate_txt(self):
        df = pd.DataFrame({
            "RouteName": ["Test"],
            "WaypointId": ["1"],
            "Name": ["WP1"],
            "Latitude": [12.34],
            "Longitude": [56.78],
        })
        txt_content = app.generate_txt(df)
        self.assertIn("NAME", txt_content)
        self.assertIn("WP1", txt_content)
        self.assertIn("LAT", txt_content)
        self.assertIn("LON", txt_content)

    def test_generate_rt3(self):
        df = pd.DataFrame({
            "RouteName": ["TestRoute"],
            "WaypointId": ["1"],
            "Name": ["WP1"],
            "Latitude": [12.34],
            "Longitude": [56.78],
        })
        rt3_content = app.generate_rt3(df)
        self.assertIn("<report>", rt3_content)
        self.assertIn("<route", rt3_content)
        self.assertIn('routeName="TestRoute"', rt3_content)
        self.assertIn('lat="12.34"', rt3_content)
        self.assertIn('lon="56.78"', rt3_content)

    def test_generate_rtm(self):
        df = pd.DataFrame({
            "RouteName": ["TestRoute"],
            "WaypointId": ["1"],
            "Name": ["WP1"],
            "Latitude": [12.34],
            "Longitude": [56.78],
        })
        rtm_content = app.generate_rtm(df)
        self.assertIsInstance(rtm_content, bytes)
        self.assertTrue(rtm_content.startswith(b"Route File Version"))

    def test_upload_callback_happy_path(self):
        rtz = """<route routeName="Route A">
          <waypoint id="1" name="WP1"><position lat="13.3" lon="46.6" /></waypoint>
        </route>"""

        def data_uri(value):
            encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
            return f"data:text/plain;base64,{encoded}"

        store, _status, preview_style, download_style, csv_disabled, title, badge, _table = app.on_upload(
            data_uri(rtz),
            "route.rtz",
        )

        self.assertFalse(csv_disabled)
        self.assertEqual(preview_style["display"], "block")
        self.assertEqual(download_style["display"], "block")
        self.assertEqual(title, "Preview — route.rtz")
        self.assertEqual(badge, "1 rows")
        self.assertEqual(len(store["records"]), 1)


if __name__ == "__main__":
    unittest.main()
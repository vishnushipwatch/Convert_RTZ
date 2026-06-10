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

    def test_convert_to_dataframe_rejects_non_rtz(self):
        with self.assertRaises(ValueError):
            app.convert_to_dataframe("test.txt", "some text content")

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

        store, _status, preview_style, download_style, csv_disabled, txt_disabled, rt3_disabled, rtm_disabled, title, badge, _table = app.on_upload(
            data_uri(rtz),
            "route.rtz",
        )

        self.assertFalse(csv_disabled)
        self.assertFalse(txt_disabled)
        self.assertFalse(rt3_disabled)
        self.assertFalse(rtm_disabled)
        self.assertEqual(preview_style["display"], "block")
        self.assertEqual(download_style["display"], "block")
        self.assertEqual(title, "Preview — route.rtz")
        self.assertEqual(badge, "1 rows")
        self.assertEqual(len(store["records"]), 1)


if __name__ == "__main__":
    unittest.main()
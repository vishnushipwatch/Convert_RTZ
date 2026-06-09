import base64
import unittest

import pandas as pd

import app


class ParserTests(unittest.TestCase):
    def test_parse_txt_comma_delimited(self):
        df = app.parse_txt("Name,Latitude,Longitude\nWP1,12.3,45.6\nWP2,13.3,46.6\n")

        self.assertEqual(list(df.columns), ["Name", "Latitude", "Longitude"])
        self.assertEqual(len(df), 2)
        self.assertEqual(df.loc[0, "Name"], "WP1")

    def test_parse_txt_whitespace_delimited(self):
        df = app.parse_txt("Name Latitude Longitude\nWP1 12.3 45.6\n")

        self.assertEqual(list(df.columns), ["Name", "Latitude", "Longitude"])
        self.assertEqual(df.loc[0, "Longitude"], 45.6)

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

    def test_parse_rt3_embedded_route(self):
        content = """<report>
          <voyage><id>V001</id></voyage>
          <route>
            <routeInfo routeName="Embedded" />
            <waypoint id="1" name="Start"><position lat="10" lon="20" /></waypoint>
          </route>
        </report>"""

        df = app.parse_rt3(content)

        self.assertEqual(len(df), 1)
        self.assertEqual(df.loc[0, "RouteName"], "Embedded")
        self.assertEqual(df.loc[0, "Latitude"], 10.0)

    def test_decode_upload_data_uri(self):
        raw = "Name,Latitude,Longitude\nWP1,12.3,45.6\n"
        encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")

        self.assertEqual(app.decode_upload(f"data:text/plain;base64,{encoded}"), raw)

    def test_upload_callback_combines_multiple_files(self):
        txt = "Name,Latitude,Longitude\nWP1,12.3,45.6\n"
        rtz = """<route routeName="Route A">
          <waypoint id="1" name="WP2"><position lat="13.3" lon="46.6" /></waypoint>
        </route>"""

        def data_uri(value):
            encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
            return f"data:text/plain;base64,{encoded}"

        store, _status, preview_style, download_style, disabled, title, badge, _table = app.on_upload(
            [data_uri(txt), data_uri(rtz)],
            ["points.txt", "route.rtz"],
        )

        self.assertFalse(disabled)
        self.assertEqual(preview_style["display"], "block")
        self.assertEqual(download_style["display"], "block")
        self.assertEqual(title, "Preview — 2 file(s)")
        self.assertEqual(badge, "2 rows")
        self.assertEqual(len(store["records"]), 2)
        self.assertEqual(pd.DataFrame(store["records"]).loc[1, "SourceFile"], "route.rtz")


if __name__ == "__main__":
    unittest.main()

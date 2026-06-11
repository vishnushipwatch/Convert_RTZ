"""Tests for the TSH_Route (proprietary RT3 variant) parser."""
import unittest
import pandas as pd
import app


class TestTSHRouteParser(unittest.TestCase):
    """Test that parse_tsh_route handles TSH_Route files correctly."""

    TSH_SAMPLE = """<?xml version="1.0"?>
<TSH_Route RtName="VOY129 B JEBEL ALI -SOH fuj to KANDLA" RtVersion="3">
    TSH RtServer route data file. Info: amo.
    <WayPoints WPCount="3" IdCounter="62">
        <WayPoint Id="18" WPName="JEBEL ALI BERTH" LegType="0"
            Lat="1499.705000" Lon="3303.204000"
            PortXTE="0.060000" StbXTE="0.060000"
            TurnRadius="0.100000" ArrivalC="0.000000" />
        <WayPoint Id="20" WPName="" LegType="0"
            Lat="1499.873000" Lon="3303.034607"
            PortXTE="0.060000" StbXTE="0.060000"
            TurnRadius="0.300000" ArrivalC="0.000000" />
        <WayPoint Id="10" WPName="JEBEL ALI PILOT" LegType="0"
            Lat="1509.142347" Lon="3293.999415"
            PortXTE="0.060000" StbXTE="0.060000"
            TurnRadius="0.300000" ArrivalC="0.000000" />
    </WayPoints>
</TSH_Route>"""

    def test_parse_tsh_route_basic(self):
        """Parse a TSH_Route file and verify waypoints & coordinates."""
        df = app.parse_tsh_route(self.TSH_SAMPLE)

        self.assertEqual(len(df), 3)
        self.assertEqual(df["RouteName"].iloc[0],
                         "VOY129 B JEBEL ALI -SOH fuj to KANDLA")

        # First waypoint: JEBEL ALI BERTH
        self.assertEqual(df.iloc[0]["Name"], "JEBEL ALI BERTH")
        self.assertEqual(df.iloc[0]["WaypointId"], "18")

        # Coordinate conversion: minutes / 60 = decimal degrees
        # 1499.705000 / 60 = 24.995083...
        self.assertAlmostEqual(df.iloc[0]["Latitude"], 1499.705 / 60, places=6)
        self.assertAlmostEqual(df.iloc[0]["Longitude"], 3303.204 / 60, places=6)

        # Second waypoint has empty name
        self.assertEqual(df.iloc[1]["Name"], "")
        self.assertAlmostEqual(df.iloc[1]["Latitude"], 1499.873 / 60, places=6)

        # Third waypoint
        self.assertEqual(df.iloc[2]["Name"], "JEBEL ALI PILOT")
        self.assertAlmostEqual(df.iloc[2]["Latitude"], 1509.142347 / 60, places=6)
        self.assertAlmostEqual(df.iloc[2]["Longitude"], 3293.999415 / 60, places=6)

    def test_parse_tsh_route_xtd_fields(self):
        """Verify PortXTE/StbXTE are mapped to PortsideXTD/StarboardXTD."""
        df = app.parse_tsh_route(self.TSH_SAMPLE)

        # All have PortXTE=StbXTE=0.06
        for i in range(3):
            self.assertAlmostEqual(df.iloc[i]["PortsideXTD"], 0.06, places=4)
            self.assertAlmostEqual(df.iloc[i]["StarboardXTD"], 0.06, places=4)

    def test_parse_tsh_route_turn_radius(self):
        """Verify TurnRadius is parsed."""
        df = app.parse_tsh_route(self.TSH_SAMPLE)

        self.assertAlmostEqual(df.iloc[0]["TurnRadius"], 0.1, places=4)
        self.assertAlmostEqual(df.iloc[1]["TurnRadius"], 0.3, places=4)

    def test_parse_tsh_route_fallback_to_rt3(self):
        """When content is NOT a TSH_Route, parse_tsh_route should delegate to parse_rt3."""
        rt3_content = """<report>
          <voyage><id>TEST</id></voyage>
          <route routeName="StandardRT3">
            <waypoints>
              <waypoint id="1" name="WP1">
                <position lat="12.3" lon="45.6" />
              </waypoint>
            </waypoints>
          </route>
        </report>"""
        df = app.parse_tsh_route(rt3_content)

        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["Name"], "WP1")

    def test_parse_tsh_route_empty_waypoints(self):
        """Empty WayPoints section should return empty DataFrame."""
        content = """<TSH_Route RtName="EmptyRoute">
          <WayPoints WPCount="0">
          </WayPoints>
        </TSH_Route>"""
        df = app.parse_tsh_route(content)
        self.assertTrue(df.empty)

    def test_convert_to_dataframe_dispatches_tsh_route(self):
        """convert_to_dataframe should route .rt3 files through parse_tsh_route."""
        df = app.convert_to_dataframe("route.rt3", self.TSH_SAMPLE)

        self.assertEqual(len(df), 3)
        self.assertAlmostEqual(df.iloc[0]["Latitude"], 1499.705 / 60, places=6)


if __name__ == "__main__":
    unittest.main()
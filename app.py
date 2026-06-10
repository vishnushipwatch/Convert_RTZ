"""
RTZ to Multiple Formats Converter
A Dash application that converts maritime RTZ route files to CSV, TXT, RT3, and RTM formats.
"""

import base64
import csv
import io
import re
import struct
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import dash
from dash import dcc, html, dash_table, Input, Output, State
import dash_bootstrap_components as dbc
import pandas as pd


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _local(tag):
    """Return the local part of an XML tag, stripping the namespace."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _text(el, default=""):
    if el is None or el.text is None:
        return default
    return el.text.strip()


def _find_child(el, name):
    """Find a direct child by local tag name, ignoring XML namespaces."""
    if el is None:
        return None
    for child in el:
        if _local(child.tag) == name:
            return child
    return None


def _child_text(el, name, default=""):
    return _text(_find_child(el, name), default)


def _attr_or_child(el, attr_name, child_name=None, default=""):
    if el is not None and attr_name in el.attrib:
        return el.attrib.get(attr_name, default)
    return _child_text(el, child_name or attr_name, default)


def _to_float(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coordinate_to_decimal(value, default=None):
    """
    Convert decimal or degrees/minutes/cardinal route coordinates to decimal degrees.
    Examples: -95.13035, "23 08.368 S", "044 02.408 W".
    """
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    decimal = _to_float(text)
    if decimal is not None:
        return decimal

    match = re.match(r"^(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*([NSEW])$", text, re.IGNORECASE)
    if not match:
        return default

    degrees, minutes, hemisphere = match.groups()
    result = float(degrees) + float(minutes) / 60
    if hemisphere.upper() in ("S", "W"):
        result *= -1
    return result


def _clean_binary_text(raw):
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


def _first_printable_text(raw, default=""):
    match = re.search(rb"[ -~]{3,}", raw)
    if not match:
        return default
    return match.group(0).decode("utf-8", errors="replace").strip()


def _drop_empty_columns(df):
    """Remove columns that have no useful values in the converted output."""
    if df is None or df.empty:
        return df

    keep = []
    for column in df.columns:
        values = df[column]
        has_value = values.notna() & (values.astype(str).str.strip() != "")
        if has_value.any():
            keep.append(column)
    return df.loc[:, keep]


def parse_rtz(content):
    """Parse an RTZ (XML) route file and return a DataFrame of waypoints."""
    root = ET.fromstring(content)

    rows = []
    route_index = 0
    for route in root.iter():
        if _local(route.tag) != "route":
            continue
        route_index += 1
        route_info = _find_child(route, "routeInfo")
        route_name = (
            _attr_or_child(route_info, "routeName")
            or _attr_or_child(route, "routeName")
            or _child_text(route, "routeName")
        )

        waypoint_list = [w for w in route.iter() if _local(w.tag) == "waypoint"]

        for wp_index, wp in enumerate(waypoint_list, start=1):
            position = _find_child(wp, "position")
            leg = _find_child(wp, "leg")
            row = {
                "RouteIndex": route_index,
                "RouteName": route_name,
                "WaypointId": wp.attrib.get("id", f"WP{wp_index}"),
                "Revision": wp.attrib.get("revision", ""),
                "Name": _attr_or_child(wp, "name"),
                "Latitude": _to_float(_attr_or_child(position, "lat") or _child_text(wp, "lat")),
                "Longitude": _to_float(_attr_or_child(position, "lon") or _child_text(wp, "lon")),
                "Radius": _to_float(_attr_or_child(wp, "radius")),
                "SpeedMax": _to_float(_attr_or_child(wp, "speedMax")),
                "SpeedMin": _to_float(_attr_or_child(wp, "speedMin")),
                "Course": _to_float(_attr_or_child(wp, "course")),
                "LegDistance": _to_float(_attr_or_child(wp, "legDistance")),
                "TurnRadius": _to_float(_attr_or_child(wp, "turnRadius")),
                "LegGeometryType": _attr_or_child(leg, "geometryType"),
                "StarboardXTD": _to_float(_attr_or_child(leg, "starboardXTD")),
                "PortsideXTD": _to_float(_attr_or_child(leg, "portsideXTD")),
            }
            rows.append(row)
    return _drop_empty_columns(pd.DataFrame(rows))


# ---------------------------------------------------------------------------
# Format generators
# ---------------------------------------------------------------------------

def _decimal_to_dms(decimal, is_lat):
    """Convert decimal degrees to degrees, minutes, and hemisphere string."""
    if decimal is None:
        return ""
    hemisphere = "N" if decimal >= 0 else "S" if is_lat else "E" if decimal >= 0 else "W"
    abs_val = abs(decimal)
    degrees = int(abs_val)
    minutes = (abs_val - degrees) * 60
    return f"{degrees:02d} {minutes:06.3f} {hemisphere}"


def _decimal_to_dms_components(decimal, is_lat):
    """Convert decimal degrees to (degrees, minutes, hemisphere) tuple for CSV output."""
    if decimal is None:
        return ("", "", "")
    hemisphere = "N" if decimal >= 0 else "S" if is_lat else "E" if decimal >= 0 else "W"
    abs_val = abs(decimal)
    degrees = int(abs_val)
    minutes = (abs_val - degrees) * 60
    return (f"{degrees:02d}", f"{minutes:06.3f}", hemisphere)


def generate_csv(df):
    """
    Generate CSV content from the DataFrame in JRC ECDIS Route Sheet format.
    Produces comment header lines and comma-separated data rows with DMS coordinates.
    """
    if df.empty:
        return ""

    route_name = df["RouteName"].iloc[0] if "RouteName" in df.columns else "Converted Route"
    # Derive a short code from route name (first 3 chars + first 3 chars after hyphen, uppercase)
    short_code = "".join(
        p[:3].upper() for p in route_name.replace("-", " ").split() if p.strip()
    )[:12]

    output = io.StringIO()

    # Write header lines manually (avoid csv.writer quoting the route line with commas)
    output.write("// ROUTE SHEET exported by JRC ECDIS.\n")
    output.write("// <<NOTE>>This strings // indicate comment column/cells. You can edit freely.\n")
    output.write(f"// {route_name},<Normal>,{short_code}\n")
    output.write("// WPT No.,LAT,,,LON,,,PORT[NM],STBD[NM],Arr. Rad[NM],Speed[kn],Sail(RL/GC),ROT[deg/min],Turn Rad[NM],Time Zone,,Name\n")

    writer = csv.writer(output, lineterminator="\n")

    for idx, (_, row) in enumerate(df.iterrows()):
        lat = row.get("Latitude")
        lon = row.get("Longitude")

        if pd.notna(lat) and lat is not None:
            lat_deg, lat_min, lat_hemi = _decimal_to_dms_components(lat, is_lat=True)
        else:
            lat_deg, lat_min, lat_hemi = "", "", ""

        if pd.notna(lon) and lon is not None:
            lon_deg, lon_min, lon_hemi = _decimal_to_dms_components(lon, is_lat=False)
        else:
            lon_deg, lon_min, lon_hemi = "", "", ""

        # Fetch fields with defaults
        port_xtd = row.get("PortsideXTD")
        stbd_xtd = row.get("StarboardXTD")
        radius = row.get("Radius")
        speed = row.get("SpeedMax")
        leg_geom = row.get("LegGeometryType")
        turn_radius = row.get("TurnRadius")
        wp_name = row.get("Name") or row.get("WaypointId") or ""

        # Helper: format value or "***" for missing
        def _val(v):
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return "***"
            if isinstance(v, float):
                return f"{v:.2f}".rstrip("0").rstrip(".") if "." in f"{v:.2f}" else f"{v:.2f}"
            s = str(v).strip()
            return s if s else "***"

        # Compute ROT if both speed and turn radius are available
        speed_f = _to_float(speed)
        turn_rad_f = _to_float(turn_radius)
        if speed_f is not None and turn_rad_f is not None and turn_rad_f > 0 and _to_float(speed) and pd.notna(speed):
            # ROT (deg/min) = speed / turn_radius * 180 / (π * 60)
            rot = speed_f / turn_rad_f * 180 / (3.141592653589793 * 60)
            rot_str = f"{rot:.2f}"
        else:
            rot_str = "***"

        # For the first (start) waypoint, use *** for navigation fields like the sample
        is_first = (idx == 0)

        row_out = [
            f"{idx:03d}",
            lat_deg, lat_min, lat_hemi,
            lon_deg, lon_min, lon_hemi,
            "***" if is_first else _val(port_xtd),
            "***" if is_first else _val(stbd_xtd),
            "***" if is_first else _val(radius),
            "***" if is_first else _val(speed),
            "***" if is_first else _val(leg_geom),
            "***" if is_first else rot_str,
            "***" if is_first else _val(turn_radius),
            "00:00",
            "E",
            str(wp_name) if wp_name else "",
        ]
        writer.writerow(row_out)

    return output.getvalue()


def generate_txt(df):
    """Generate TXT (tab-separated) content from the DataFrame."""
    if df.empty:
        return ""

    lines = []
    # Header
    header_cols = ["NAME", "LAT", "LON", "LEG_TYPE", "TURN_RADIUS",
                   "CHN_LIMIT", "PLANNED_SPEED", "SPEED_MIN", "SPEED_MAX",
                   "COURSE", "LENGTH", "DO_PLAN", "HFO_PLAN", "HFO_LEFT",
                   "DO_LEFT", "ETA_DAY", "ETA_TIME"]
    lines.append("\t".join(header_cols))

    for _, row in df.iterrows():
        lat = row.get("Latitude")
        lon = row.get("Longitude")
        lat_str = _decimal_to_dms(lat, is_lat=True) if pd.notna(lat) else ""
        lon_str = _decimal_to_dms(lon, is_lat=False) if pd.notna(lon) else ""

        vals = [
            str(row.get("Name", "") or ""),
            lat_str,
            lon_str,
            str(row.get("LegGeometryType", "") or ""),
            str(row.get("TurnRadius", "") or ""),
            "",
            str(row.get("SpeedMax", "") or ""),
            str(row.get("SpeedMin", "") or ""),
            str(row.get("SpeedMax", "") or ""),
            str(row.get("Course", "") or ""),
            str(row.get("LegDistance", "") or ""),
            "",
            "",
            "",
            "",
            "",
            "",
        ]
        lines.append("\t".join(vals))

    return "\n".join(lines)


def generate_rt3(df):
    """Generate RT3 (XML voyage report with embedded route) content from the DataFrame."""
    root = ET.Element("report")

    voyage = ET.SubElement(root, "voyage")
    ET.SubElement(voyage, "id").text = "CONVERTED"

    route = ET.SubElement(root, "route")
    route.set("version", "1.0")

    route_info = ET.SubElement(route, "routeInfo")
    route_name = df["RouteName"].iloc[0] if "RouteName" in df.columns and not df.empty else "Converted Route"
    route_info.set("routeName", route_name)

    waypoints = ET.SubElement(route, "waypoints")
    for _, row in df.iterrows():
        wp = ET.SubElement(waypoints, "waypoint")
        wp_id = str(row.get("WaypointId", "")) if pd.notna(row.get("WaypointId")) else ""
        wp_name = str(row.get("Name", "")) if pd.notna(row.get("Name")) else ""
        if wp_id:
            wp.set("id", wp_id)
        if wp_name:
            wp.set("name", wp_name)

        position = ET.SubElement(wp, "position")
        lat = row.get("Latitude")
        lon = row.get("Longitude")
        if pd.notna(lat):
            position.set("lat", str(lat))
        if pd.notna(lon):
            position.set("lon", str(lon))

        turn_radius = row.get("TurnRadius")
        if pd.notna(turn_radius):
            wp.set("turnRadius", str(turn_radius))

        speed_max = row.get("SpeedMax")
        if pd.notna(speed_max):
            wp.set("speedMax", str(speed_max))

        speed_min = row.get("SpeedMin")
        if pd.notna(speed_min):
            wp.set("speedMin", str(speed_min))

        course = row.get("Course")
        if pd.notna(course):
            wp.set("course", str(course))

        leg_distance = row.get("LegDistance")
        if pd.notna(leg_distance):
            wp.set("legDistance", str(leg_distance))

        leg = ET.SubElement(wp, "leg")
        leg_geom = row.get("LegGeometryType")
        if pd.notna(leg_geom) and str(leg_geom).strip():
            leg.set("geometryType", str(leg_geom))

        starboard_xtd = row.get("StarboardXTD")
        if pd.notna(starboard_xtd):
            leg.set("starboardXTD", str(starboard_xtd))

        portside_xtd = row.get("PortsideXTD")
        if pd.notna(portside_xtd):
            leg.set("portsideXTD", str(portside_xtd))

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def generate_rtm(df):
    """Generate binary RTM content from the DataFrame."""
    if df.empty:
        return b""

    route_name = df["RouteName"].iloc[0] if "RouteName" in df.columns else "RTZ Route"
    waypoint_count = len(df)

    # Build the binary structure
    buf = io.BytesIO()

    # Header: "Route File Version" + padding
    buf.write(b"Route File Version 1.00")
    buf.write(b"\x00" * (32 - buf.tell()))

    # Waypoint count at byte 32
    buf.write(struct.pack("<I", waypoint_count))

    # Route name at byte 36
    name_bytes = route_name.encode("utf-8", errors="replace")[:128]
    buf.write(name_bytes)
    buf.write(b"\x00" * (164 - buf.tell()))

    # Padding up to record start (byte 250)
    buf.write(b"\x00" * (250 - buf.tell()))

    record_size = 304
    position_offset = 130

    for _, row in df.iterrows():
        base = buf.tell()

        # Waypoint name at record+0
        wp_name = str(row.get("Name", "")) if pd.notna(row.get("Name")) else ""
        name_bytes = wp_name.encode("utf-8", errors="replace")[:128]
        buf.write(name_bytes)
        buf.write(b"\x00" * (128 - len(name_bytes)))

        # Padding up to position offset
        buf.write(b"\x00" * (position_offset - (buf.tell() - base)))

        # Latitude at record+130
        lat = float(row.get("Latitude", 0)) if pd.notna(row.get("Latitude")) else 0.0
        buf.write(struct.pack("<d", lat))

        # Longitude at record+138
        lon = float(row.get("Longitude", 0)) if pd.notna(row.get("Longitude")) else 0.0
        buf.write(struct.pack("<d", lon))

        # Padding to fill the record
        remaining = record_size - (buf.tell() - base)
        if remaining > 0:
            buf.write(b"\x00" * remaining)

    return buf.getvalue()


# ---------------------------------------------------------------------------
# Conversion dispatch
# ---------------------------------------------------------------------------

def convert_to_dataframe(filename, content):
    """Detect the file type by extension and return a normalised DataFrame."""
    name = (filename or "").lower()
    if name.endswith(".rtz"):
        return parse_rtz(content)
    # Unknown extension: best effort XML parse
    if isinstance(content, str) and content.lstrip().startswith("<"):
        return parse_rtz(content)
    raise ValueError(f"Unsupported file format: {filename}. Only RTZ files are supported.")


# ---------------------------------------------------------------------------
# File decoding helper
# ---------------------------------------------------------------------------

def decode_upload(contents, as_bytes=False):
    """Decode the contents string from dcc.Upload into a plain text string."""
    if not contents:
        return b"" if as_bytes else ""
    if contents.startswith("data:"):
        try:
            _, _, b64 = contents.partition(",")
            raw = base64.b64decode(b64)
            if as_bytes:
                return raw
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return b"" if as_bytes else ""
    return contents


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

external_stylesheets = [
    dbc.themes.FLATLY,
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css",
]

app = dash.Dash(
    __name__,
    external_stylesheets=external_stylesheets,
    title="RTZ → Multiple Formats Converter",
    suppress_callback_exceptions=True,
    update_title=None,
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)
server = app.server  # for gunicorn


@server.route("/health")
def health_check():
    return {"status": "ok"}, 200


# ---------------------------------------------------------------------------
# Layout components
# ---------------------------------------------------------------------------

NAVBAR = dbc.Navbar(
    dbc.Container(
        [
            html.A(
                dbc.Row(
                    [
                        dbc.Col(html.I(className="bi bi-compass",
                                       style={"fontSize": "1.8rem", "color": "#ffffff"})),
                        dbc.Col(
                            dbc.NavbarBrand("RTZ → Multiple Formats Converter",
                                            className="ms-2",
                                            style={"fontWeight": "600", "color": "white"}),
                        ),
                    ],
                    align="center",
                    className="g-0",
                ),
                href="/",
                style={"textDecoration": "none"},
            ),
        ],
        fluid=True,
    ),
    color="primary",
    dark=True,
    className="mb-4 shadow-sm",
    style={"background": "linear-gradient(90deg, #2C3E50 0%, #4CA1AF 100%)"},
)


UPLOAD_CARD = dbc.Card(
    dbc.CardBody(
        [
            html.Div(
                [
                    html.I(className="bi bi-cloud-arrow-up-fill",
                           style={"fontSize": "2.5rem", "color": "#2C3E50"}),
                    html.H4("Drop your RTZ file here", className="mt-3 mb-1",
                            style={"fontWeight": "600"}),
                    html.P("or click to browse — supports .rtz files only",
                           className="text-muted mb-3"),
                ],
                className="text-center",
            ),
            dcc.Upload(
                id="upload-data",
                children=html.Div(
                    [
                        html.I(className="bi bi-upload me-2"),
                        "Drag & Drop or ",
                        html.A("Select File", className="text-primary",
                               style={"fontWeight": "600"}),
                    ]
                ),
                style={
                    "width": "100%",
                    "height": "70px",
                    "lineHeight": "70px",
                    "borderWidth": "2px",
                    "borderStyle": "dashed",
                    "borderRadius": "10px",
                    "borderColor": "#2C3E50",
                    "textAlign": "center",
                    "backgroundColor": "#F8F9FA",
                    "cursor": "pointer",
                },
                multiple=False,
                accept=".rtz",
            ),
            html.Div(id="upload-status", className="mt-3"),
        ]
    ),
    className="shadow-sm",
    style={"border": "none", "borderRadius": "8px"},
)


PREVIEW_CARD = dbc.Card(
    dbc.CardBody(
        [
            html.Div(
                [
                    html.H5(id="preview-title", children="Data Preview",
                            style={"fontWeight": "600"}),
                    dbc.Badge(id="row-count-badge", color="info", className="ms-2"),
                ],
                className="d-flex align-items-center mb-3",
            ),
            html.Div(id="preview-table"),
        ]
    ),
    id="preview-card",
    className="shadow-sm mt-4",
    style={"display": "none", "border": "none", "borderRadius": "8px"},
)


DOWNLOAD_CARD = dbc.Card(
    dbc.CardBody(
        [
            html.H5([html.I(className="bi bi-download me-2"), "Export"],
                    style={"fontWeight": "600"}),
            html.P("Download the converted data in your preferred format.",
                   className="text-muted"),
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Button(
                            [html.I(className="bi bi-filetype-csv me-2"), "CSV"],
                            id="download-csv-btn",
                            color="success",
                            className="w-100",
                            disabled=True,
                            size="lg",
                        ),
                        className="mb-2",
                    ),
                    dbc.Col(
                        dbc.Button(
                            [html.I(className="bi bi-filetype-txt me-2"), "TXT"],
                            id="download-txt-btn",
                            color="primary",
                            className="w-100",
                            disabled=True,
                            size="lg",
                        ),
                        className="mb-2",
                    ),
                ],
            ),
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Button(
                            [html.I(className="bi bi-filetype-xml me-2"), "RT3"],
                            id="download-rt3-btn",
                            color="warning",
                            className="w-100",
                            disabled=True,
                            size="lg",
                        ),
                        className="mb-2",
                    ),
                    dbc.Col(
                        dbc.Button(
                            [html.I(className="bi bi-file-binary me-2"), "RTM"],
                            id="download-rtm-btn",
                            color="info",
                            className="w-100",
                            disabled=True,
                            size="lg",
                        ),
                        className="mb-2",
                    ),
                ],
            ),
            dcc.Download(id="download-csv"),
            dcc.Download(id="download-txt"),
            dcc.Download(id="download-rt3"),
            dcc.Download(id="download-rtm"),
        ]
    ),
    id="download-card",
    className="shadow-sm mt-4",
    style={"display": "none", "border": "none", "borderRadius": "8px"},
)


ABOUT_CARD = dbc.Card(
    dbc.CardBody(
        [
            html.H5([html.I(className="bi bi-info-circle me-2"), "About"],
                    style={"fontWeight": "600"}),
            html.P(
                "This tool converts maritime RTZ (Route Exchange Format) files into "
                "multiple output formats including CSV, TXT, RT3, and RTM.",
                className="text-muted",
            ),
            html.Hr(),
            html.P([html.Strong("RTZ (Input): "), "Standardised XML route format with named waypoints, "
                                                    "coordinates and turn radii."], className="mb-2"),
            html.P([html.Strong("CSV: "), "Comma-separated values for spreadsheets and data analysis."],
                   className="mb-2"),
            html.P([html.Strong("TXT: "), "Tab-separated waypoint list with DMS coordinates."],
                   className="mb-2"),
            html.P([html.Strong("RT3: "), "XML voyage report format with embedded route data."],
                   className="mb-2"),
            html.P([html.Strong("RTM: "), "Binary route file format compatible with legacy systems."],
                   className="mb-2"),
            html.Hr(),
            html.P([html.Strong("Tips: "), "Files are processed in memory for the current request "
                                            "and are not stored."],
                   className="text-muted mb-0 small"),
        ]
    ),
    className="shadow-sm mt-4",
    style={"border": "none", "borderRadius": "8px"},
)


app.layout = dbc.Container(
    [
        NAVBAR,
        dbc.Row(
            [
                dbc.Col([UPLOAD_CARD, PREVIEW_CARD, DOWNLOAD_CARD], lg=8),
                dbc.Col(ABOUT_CARD, lg=4),
            ],
            className="g-4",
        ),
        html.Hr(className="my-4"),
        html.Footer(
            html.Small(
                f"© {datetime.now(timezone.utc).year} Convert_RTZ · Built with Dash · Hosted on Render",
                className="text-muted",
            ),
            className="text-center pb-4",
        ),
        dcc.Store(id="df-store"),
    ],
    fluid=True,
    style={"maxWidth": "1200px",
           "fontFamily": "Inter, system-ui, -apple-system, sans-serif"},
)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@app.callback(
    Output("df-store", "data"),
    Output("upload-status", "children"),
    Output("preview-card", "style"),
    Output("download-card", "style"),
    Output("download-csv-btn", "disabled"),
    Output("download-txt-btn", "disabled"),
    Output("download-rt3-btn", "disabled"),
    Output("download-rtm-btn", "disabled"),
    Output("preview-title", "children"),
    Output("row-count-badge", "children"),
    Output("preview-table", "children"),
    Input("upload-data", "contents"),
    State("upload-data", "filename"),
)
def on_upload(contents, filename):
    if not contents:
        return (None, "", {"display": "none"}, {"display": "none"},
                True, True, True, True, "Data Preview", "", "")

    try:
        text = decode_upload(contents)
        parsed = convert_to_dataframe(filename, text)
    except Exception as exc:
        msg = dbc.Alert(f"Failed to parse file: {exc}", color="danger", className="mt-3")
        return (None, msg, {"display": "none"}, {"display": "none"},
                True, True, True, True, "Data Preview", "", "")

    if parsed is None or parsed.empty:
        msg = dbc.Alert(
            "No tabular data could be extracted from the uploaded file.",
            color="warning",
            className="mt-3",
        )
        return (None, msg, {"display": "none"}, {"display": "none"},
                True, True, True, True, "Data Preview", "", "")

    df = _drop_empty_columns(parsed)
    store = {"filename": filename, "records": df.to_dict(orient="records")}

    status = dbc.Alert(
        [
            html.I(className="bi bi-check-circle-fill me-2"),
            "Successfully loaded ",
            html.Strong(filename or "file"),
            f" — {len(df)} row(s), {len(df.columns)} column(s).",
        ],
        color="success",
        className="mt-3 d-flex align-items-center",
    )

    preview_df = df.head(50)
    table = dash_table.DataTable(
        data=preview_df.to_dict(orient="records"),
        columns=[{"name": c, "id": c} for c in preview_df.columns],
        page_size=10,
        sort_action="native",
        filter_action="native",
        style_table={"overflowX": "auto", "borderRadius": "8px"},
        style_cell={
            "textAlign": "left",
            "padding": "10px 12px",
            "fontFamily": "Inter, system-ui, -apple-system, sans-serif",
            "fontSize": "0.875rem",
            "minWidth": "120px",
        },
        style_header={
            "backgroundColor": "#2C3E50",
            "color": "white",
            "fontWeight": "600",
            "border": "none",
        },
        style_data={
            "border": "none",
            "borderBottom": "1px solid #ECF0F1",
        },
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": "#F8F9FA"},
            {"if": {"row_index": "even"}, "backgroundColor": "#FFFFFF"},
        ],
        style_filter={
            "backgroundColor": "#ECF0F1",
            "border": "1px solid #BDC3C7",
            "borderRadius": "4px",
        },
    )

    return (
        store,
        status,
        {"display": "block", "border": "none", "borderRadius": "8px"},
        {"display": "block", "border": "none", "borderRadius": "8px"},
        False, False, False, False,
        f"Preview — {filename or 'file'}",
        f"{len(df)} rows",
        table,
    )


def _get_base_filename(store):
    """Extract the base filename (without extension) from the store."""
    if not store:
        return "converted_route"
    filename = store.get("filename", "route.rtz") or "route.rtz"
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    return base


@app.callback(
    Output("download-csv", "data"),
    Input("download-csv-btn", "n_clicks"),
    State("df-store", "data"),
    prevent_initial_call=True,
)
def on_download_csv(n_clicks, store):
    if not store or not store.get("records"):
        return dash.no_update
    df = _drop_empty_columns(pd.DataFrame(store["records"]))
    base = _get_base_filename(store)
    content = generate_csv(df)
    return dict(content=content, filename=f"{base}.csv")


@app.callback(
    Output("download-txt", "data"),
    Input("download-txt-btn", "n_clicks"),
    State("df-store", "data"),
    prevent_initial_call=True,
)
def on_download_txt(n_clicks, store):
    if not store or not store.get("records"):
        return dash.no_update
    df = _drop_empty_columns(pd.DataFrame(store["records"]))
    base = _get_base_filename(store)
    content = generate_txt(df)
    return dict(content=content, filename=f"{base}.txt")


@app.callback(
    Output("download-rt3", "data"),
    Input("download-rt3-btn", "n_clicks"),
    State("df-store", "data"),
    prevent_initial_call=True,
)
def on_download_rt3(n_clicks, store):
    if not store or not store.get("records"):
        return dash.no_update
    df = _drop_empty_columns(pd.DataFrame(store["records"]))
    base = _get_base_filename(store)
    content = generate_rt3(df)
    return dict(content=content, filename=f"{base}.rt3")


@app.callback(
    Output("download-rtm", "data"),
    Input("download-rtm-btn", "n_clicks"),
    State("df-store", "data"),
    prevent_initial_call=True,
)
def on_download_rtm(n_clicks, store):
    if not store or not store.get("records"):
        return dash.no_update
    df = _drop_empty_columns(pd.DataFrame(store["records"]))
    base = _get_base_filename(store)
    content = generate_rtm(df)
    return dcc.send_bytes(content, f"{base}.rtm")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8050))
    app.run(host="0.0.0.0", port=port, debug=False)
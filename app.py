"""
RTZ / RT3 / RTM / TXT to CSV Converter
A Dash application that converts maritime route files (RTZ, RT3, RTM) and TXT files to CSV.
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


def parse_rt3(content):
    """
    Parse an RT3 (XML) report file and return a DataFrame of report rows.
    RT3 contains a 'report' root with an embedded RTZ <route> element.
    We extract the embedded RTZ route; if not present, we fall back to
    returning report-level metadata.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return _parse_rt3_text(content)

    # If the file already has a <route> at top-level, parse it as RTZ
    if _local(root.tag) == "route":
        return parse_rtz(content)

    route_elems = [el for el in root.iter() if _local(el.tag) == "route"]
    if route_elems:
        tmp_root = ET.Element("routes")
        for r in route_elems:
            tmp_root.append(r)
        return parse_rtz(ET.tostring(tmp_root, encoding="unicode"))

    return _parse_rt3_metadata(root)


def parse_rtm(content):
    """
    Parse a binary RTM route file.
    The observed RTM route samples use a fixed-width waypoint table:
    count at byte 32, waypoint name at record+0, and lat/lon doubles at record+130.
    """
    data = content if isinstance(content, bytes) else str(content).encode("latin-1", errors="ignore")
    if not data.startswith(b"Route File Version") or len(data) < 396:
        raise ValueError("Unsupported RTM binary route layout")

    route_name = _first_printable_text(data[36:164], "RTM route")
    waypoint_count = struct.unpack_from("<I", data, 32)[0]
    record_start = 250
    record_size = 304
    position_offset = 130

    rows = []
    for wp_index in range(waypoint_count):
        base = record_start + wp_index * record_size
        lat_offset = base + position_offset
        lon_offset = lat_offset + 8
        if lon_offset + 8 > len(data):
            break

        latitude = struct.unpack_from("<d", data, lat_offset)[0]
        longitude = struct.unpack_from("<d", data, lon_offset)[0]
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue

        rows.append({
            "RouteIndex": 1,
            "RouteName": route_name,
            "WaypointId": str(wp_index),
            "Name": _clean_binary_text(data[base:base + 128]),
            "Latitude": latitude,
            "Longitude": longitude,
        })

    return _drop_empty_columns(pd.DataFrame(rows))


def _parse_rt3_metadata(root):
    rows = []
    for el in root.iter():
        local = _local(el.tag)
        if local in ("report", "voyage", "vessel", "reportHeader", "position", "routeInfo"):
            for child in el:
                rows.append({
                    "Section": local,
                    "Field": _local(child.tag),
                    "Value": _text(child),
                })
    if not rows:
        rows.append({"Section": "report", "Field": "raw",
                     "Value": ET.tostring(root, encoding="unicode")})
    return _drop_empty_columns(pd.DataFrame(rows))


def _parse_rt3_text(content):
    """Best-effort fallback for malformed RT3: split lines into key:value pairs."""
    rows = []
    for i, line in enumerate(content.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            rows.append({"Line": i, "Field": key.strip(), "Value": value.strip()})
        else:
            rows.append({"Line": i, "Field": "text", "Value": line})
    return pd.DataFrame(rows)


TXT_ROUTE_COLUMNS = {
    "NAME": "Name",
    "LAT": "LatitudeText",
    "LON": "LongitudeText",
    "LEG_TYPE": "LegType",
    "TURN_RADIUS": "TurnRadius",
    "CHN_LIMIT": "ChannelLimit",
    "PLANNED_SPEED": "PlannedSpeed",
    "SPEED_MIN": "SpeedMin",
    "SPEED_MAX": "SpeedMax",
    "COURSE": "Course",
    "LENGTH": "LegDistance",
    "DO_PLAN": "DOPlan",
    "HFO_PLAN": "HFOPlan",
    "HFO_LEFT": "HFOLeft",
    "DO_LEFT": "DOLeft",
    "ETA_DAY": "ETADay",
    "ETA_TIME": "ETATime",
}


def _split_tab_route_line(line):
    return [part.strip() for part in line.split("\t") if part != ""]


def _parse_txt_route_table(content):
    """Parse route-table TXT files with metadata blocks followed by NAME/LAT/LON rows."""
    lines = content.splitlines()
    header_index = None
    headers = []
    for i, line in enumerate(lines):
        parts = _split_tab_route_line(line)
        if len(parts) >= 3 and parts[0].upper() == "NAME" and parts[1].upper() == "LAT" and parts[2].upper() == "LON":
            header_index = i
            headers = parts
            break

    if header_index is None:
        return None

    rows = []
    normalized_headers = [TXT_ROUTE_COLUMNS.get(header.upper(), header.title().replace("_", "")) for header in headers]
    for route_index, line in enumerate(lines[header_index + 1:], start=1):
        parts = _split_tab_route_line(line)
        if not parts:
            continue
        if len(parts) < len(headers):
            parts.extend([""] * (len(headers) - len(parts)))
        row = dict(zip(normalized_headers, parts[:len(headers)]))
        row["WaypointId"] = route_index
        row["Latitude"] = _coordinate_to_decimal(row.get("LatitudeText"))
        row["Longitude"] = _coordinate_to_decimal(row.get("LongitudeText"))
        for field in (
            "TurnRadius", "ChannelLimit", "PlannedSpeed", "SpeedMin", "SpeedMax",
            "Course", "LegDistance", "DOPlan", "HFOPlan", "HFOLeft", "DOLeft",
        ):
            if field in row:
                row[field] = _to_float(row[field], row[field])
        rows.append(row)

    return pd.DataFrame(rows)


def parse_txt(content):
    """
    Parse a TXT route/waypoint file.
    Supports comma-, tab-, semicolon- and whitespace-separated columns.
    Lines starting with '#' are treated as comments.
    """
    route_df = _parse_txt_route_table(content)
    if route_df is not None:
        return route_df

    sample = "\n".join(content.splitlines()[:20])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except Exception:
        delimiter = None

    if delimiter is None:
        try:
            return pd.read_csv(io.StringIO(content), sep=r"\s+", comment="#", engine="python")
        except Exception:
            return pd.DataFrame({"raw": content.splitlines()})
    try:
        return pd.read_csv(io.StringIO(content), sep=delimiter, comment="#")
    except Exception:
        return pd.DataFrame({"raw": content.splitlines()})


# ---------------------------------------------------------------------------
# Conversion dispatch
# ---------------------------------------------------------------------------

def convert_to_dataframe(filename, content):
    """Detect the file type by extension and return a normalised DataFrame."""
    name = (filename or "").lower()
    if name.endswith(".rtz"):
        return parse_rtz(content)
    if name.endswith(".rt3"):
        return parse_rt3(content)
    if name.endswith(".rtm"):
        return parse_rtm(content)
    if name.endswith(".txt"):
        return parse_txt(content)
    # Unknown extension: best effort
    if isinstance(content, bytes):
        return parse_rtm(content)
    if content.lstrip().startswith("<"):
        return parse_rt3(content)
    return parse_txt(content)


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
    title="RTZ / RT3 / RTM / TXT → CSV Converter",
    suppress_callback_exceptions=True,
    update_title=None,
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)
server = app.server  # for gunicorn


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
                            dbc.NavbarBrand("RTZ / RT3 / RTM / TXT → CSV Converter",
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
                    html.H4("Drop your file here", className="mt-3 mb-1",
                            style={"fontWeight": "600"}),
                    html.P("or click to browse — supports .rtz, .rt3, .rtm and .txt files",
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
                multiple=True,
                accept=".rtz,.rt3,.rtm,.txt",
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
            html.P("Download the converted data as a CSV file.",
                   className="text-muted"),
            dbc.Button(
                [html.I(className="bi bi-download me-2"), "Download CSV"],
                id="download-btn",
                color="success",
                className="w-100",
                disabled=True,
                size="lg",
            ),
            dcc.Download(id="download-csv"),
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
                "This tool converts maritime route files in RTZ (Route Exchange Format), RT3 "
                "(voyage reports), RTM and plain TXT files into clean CSV spreadsheets.",
                className="text-muted",
            ),
            html.Hr(),
            html.P([html.Strong("RTZ: "), "Standardised XML route format with named waypoints, "
                                            "coordinates and turn radii."], className="mb-2"),
            html.P([html.Strong("RT3: "), "Voyage report format containing an embedded route plus "
                                            "voyage metadata."], className="mb-2"),
            html.P([html.Strong("RTM: "), "Binary route files with fixed-width waypoint records."],
                   className="mb-2"),
            html.P([html.Strong("TXT: "), "Free-form waypoint lists; the converter auto-detects the "
                                           "delimiter (comma, tab, semicolon or whitespace)."]),
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
    Output("download-btn", "disabled"),
    Output("preview-title", "children"),
    Output("row-count-badge", "children"),
    Output("preview-table", "children"),
    Input("upload-data", "contents"),
    State("upload-data", "filename"),
)
def on_upload(contents, filename):
    if not contents:
        return (None, "", {"display": "none"}, {"display": "none"}, True,
                "Data Preview", "", "")

    content_items = contents if isinstance(contents, list) else [contents]
    filenames = filename if isinstance(filename, list) else [filename]

    frames = []
    failures = []
    try:
        for item, name in zip(content_items, filenames):
            is_binary_route = (name or "").lower().endswith(".rtm")
            text = decode_upload(item, as_bytes=is_binary_route)
            try:
                parsed = convert_to_dataframe(name, text)
            except Exception as exc:
                failures.append(f"{name or 'file'}: {exc}")
                continue
            if parsed is None or parsed.empty:
                failures.append(f"{name or 'file'}: no tabular data found")
                continue
            parsed.insert(0, "SourceFile", name or "uploaded-file")
            frames.append(parsed)
    except Exception as exc:
        msg = dbc.Alert(f"Failed to read upload: {exc}", color="danger", className="mt-3")
        return (None, msg, {"display": "none"}, {"display": "none"}, True,
                "Data Preview", "", "")

    if not frames:
        msg = dbc.Alert(
            "No tabular data could be extracted from the uploaded file(s).",
            color="warning",
            className="mt-3",
        )
        return (None, msg, {"display": "none"}, {"display": "none"}, True,
                "Data Preview", "", "")

    df = _drop_empty_columns(pd.concat(frames, ignore_index=True, sort=False))
    loaded_count = len(frames)
    file_label = ", ".join(name or "file" for name in filenames[:3])
    if len(filenames) > 3:
        file_label += f", +{len(filenames) - 3} more"
    store = {"filenames": filenames, "records": df.to_dict(orient="records")}

    status_children = [
        html.I(className="bi bi-check-circle-fill me-2"),
        "Successfully loaded ",
        html.Strong(file_label),
        f" — {len(df)} row(s), {len(df.columns)} column(s) from {loaded_count} file(s).",
    ]
    if failures:
        status_children.extend([
            html.Br(),
            html.Small("Skipped: " + "; ".join(failures), className="text-muted"),
        ])

    status = dbc.Alert(
        [
            html.Div(status_children),
        ],
        color="warning" if failures else "success",
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
        False,
        f"Preview — {loaded_count} file(s)",
        f"{len(df)} rows",
        table,
    )


@app.callback(
    Output("download-csv", "data"),
    Input("download-btn", "n_clicks"),
    State("df-store", "data"),
    prevent_initial_call=True,
)
def on_download(n_clicks, store):
    if not store or not store.get("records"):
        return dash.no_update

    df = _drop_empty_columns(pd.DataFrame(store["records"]))
    filenames = store.get("filenames") or []
    filename = filenames[0] if len(filenames) == 1 else "converted_routes"
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    csv_name = f"{base}.csv"

    return dcc.send_data_frame(df.to_csv, csv_name, index=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8050))
    app.run(host="0.0.0.0", port=port, debug=False)

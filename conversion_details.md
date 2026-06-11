# Conversion Process: RT3/RTM → CSV

This document describes in detail how this application converts maritime route files
(RT3, TSH_Route, and RTM) into CSV (comma-separated values) output.

---

## 1. Supported Input Formats

| Format       | Extension | Description                                                                 |
|--------------|-----------|-----------------------------------------------------------------------------|
| **RT3**      | `.rt3`    | XML voyage report format that wraps an embedded `<route>` alongside `<voyage>` metadata. |
| **TSH_Route**| `.rt3`    | Proprietary XML variant of RT3, detected by the `<TSH_Route>` root element. Coordinates are stored as *arc-minutes*. |
| **RTM**      | `.rtm`    | Binary route file format with fixed-size records and little-endian doubles for coordinates. |

The entry-point function `convert_to_dataframe()` in `app.py` inspects the file extension
to dispatch parsing:

```
.rt3  → parse_tsh_route()   (auto-detects TSH_Route vs. standard RT3)
.rtm  → parse_rtm()
```

> **Internal detail:** If an uploaded file has an unrecognised extension but its content
> starts with `<`, the parser makes a best-effort XML parse via `parse_rtz()` as a
> fallback. RTZ (Route Exchange Format) is not a user-facing upload format but is used
> internally as a delegate by the RT3 parser.

---

## 2. Parsing Pipeline

### 2.1 RTZ Parsing (`parse_rtz`)

The RTZ format follows the RTZ 1.0 XML schema. The parser:

1. **Loads XML** using `xml.etree.ElementTree`.
2. **Iterates** over all `<route>` elements in the document (supporting multiple routes).
3. **Extracts** waypoint data from each `<route>`:

   - **Route metadata** from `<routeInfo>` child (e.g., `routeName` attribute).
   - **Waypoint details** from each `<waypoint>` element:
     - `id`, `name`, `revision` — waypoint identity attributes.
     - `radius`, `speedMax`, `speedMin`, `course`, `legDistance`, `turnRadius` — optional navigation parameters.
     - `<position>` child with `lat` / `lon` attributes (decimal degrees).
     - `<leg>` child with `geometryType`, `starboardXTD`, `portsideXTD`.

   Fallback: coordinates may also appear as direct child tags `<lat>` / `<lon>` inside
   the `<waypoint>` (for non-standard RTZ variants).

4. **Returns** a `pandas.DataFrame` with one row per waypoint.

**Namespace handling**: The helper `_local(tag)` strips XML namespaces (e.g.,
`{http://www.cirm.org/RTZ/1/0}route` → `route`), so namespaced files are parsed
identically to non-namespaced ones.

### 2.2 RT3 Parsing (`parse_rt3`)

RT3 wraps a standard `<route>` inside a `<report>` structure:

```xml
<report>
  <voyage> <id>...</id> </voyage>
  <route routeName="..."> ... </route>
</report>
```

The parser:
1. **Loads XML** and checks the root element.
2. If the root is directly a `<route>`, it **delegates** to `parse_rtz()` (for bare routes).
3. Otherwise, it **traverses** the document for `<route>` elements and calls
   `_parse_waypoints_from_route()` — the same function used by `parse_rtz`.

### 2.3 TSH_Route Parsing (`parse_tsh_route`)

TSH_Route is a proprietary XML format used by some voyage data recorders. Its structure:

```xml
<TSH_Route RtName="...">
  <WayPoints>
    <WayPoint Lat="1499.705" Lon="6398.333" WPName="..." />
  </WayPoints>
</TSH_Route>
```

Key differences from standard RT3:
- **Root element** is `<TSH_Route>` instead of `<report>`.
- **Coordinates** are stored as **arc-minutes** (minutes × 60), not decimal degrees.
  - Conversion: `decimal_degrees = arc_minutes / 60`.
  - Example: `Lat="1499.705"` → `1499.705 / 60 = 24.995°N`.
- **Waypoint fields** use different attribute names:
  - `ArrivalC` → `Radius` (arrival circle radius).
  - `TurnRate` → `Course` (rate of turn / course).
  - `TurnRadius` → `TurnRadius`.
  - `StbXTE` / `PortXTE` → `StarboardXTD` / `PortsideXTD`.

The parser:
1. **Checks** the root element; if it is not `<TSH_Route>`, it delegates to `parse_rt3()`.
2. Otherwise, **extracts** waypoints from `<WayPoints>` / `<WayPoint>` elements.
3. **Converts** each coordinate by dividing by 60.
4. **Returns** a `DataFrame` with the standard column set.

### 2.4 RTM Parsing (`parse_rtm`)

RTM is a **binary** format with the following layout:

| Byte Offset | Size | Description                          |
|-------------|------|--------------------------------------|
| 0           | 32   | Header signature: `"Route File Version 1.00"` + null padding |
| 32          | 4    | Waypoint count (`uint32`, little-endian) |
| 36          | 128  | Route name (UTF-8, null-terminated)  |
| 164         | 86   | Padding (zeros)                      |
| **250**     | —    | Start of waypoint records            |

Each waypoint record is **304 bytes**:

| Record Offset | Size | Description                          |
|---------------|------|--------------------------------------|
| 0             | 128  | Waypoint name (UTF-8, null-terminated) |
| 128           | 2    | Padding                               |
| **130**       | 8    | Latitude (`double`, little-endian)    |
| **138**       | 8    | Longitude (`double`, little-endian)   |
| 146           | 158  | Padding to fill 304 bytes             |

The parser:
1. **Reads** the header and validates it.
2. **Extracts** the waypoint count (unsigned 32-bit little-endian integer).
3. **Reads** the route name (128-byte null-terminated UTF-8 string).
4. **Iterates** over the waypoint records:
   - Reads name bytes and cleans null terminators.
   - Unpacks latitude and longitude as `"<d"` (double, little-endian) using `struct.unpack_from`.
5. **Populates** a `DataFrame` with the standard columns; navigation fields like
   `SpeedMax`, `TurnRadius`, etc. are set to `None` since the binary format does not
   store them.

---

## 3. The Common DataFrame Schema

All parsers produce a `DataFrame` with a consistent set of columns:

| Column            | Type   | Description                                    | Source Formats       |
|-------------------|--------|------------------------------------------------|----------------------|
| `RouteIndex`      | int    | Sequential index of the route in the file      | RTZ, RT3, RTM        |
| `RouteName`       | str    | Name of the route                              | All                  |
| `WaypointId`      | str    | Identifier for the waypoint                    | All                  |
| `Revision`        | str    | Revision string (rarely used)                  | RTZ, RT3             |
| `Name`            | str    | Display name of the waypoint                   | All                  |
| `Latitude`        | float  | Latitude in decimal degrees                    | All                  |
| `Longitude`       | float  | Longitude in decimal degrees                   | All                  |
| `Radius`          | float  | Arrival circle radius (NM)                     | RTZ, RT3, TSH_Route  |
| `SpeedMax`        | float  | Maximum speed (knots)                          | RTZ, RT3             |
| `SpeedMin`        | float  | Minimum speed (knots)                          | RTZ, RT3             |
| `Course`          | float  | Course over ground (degrees) / Turn rate       | RTZ, RT3, TSH_Route  |
| `LegDistance`     | float  | Distance of the leg (NM)                       | RTZ, RT3             |
| `TurnRadius`      | float  | Turning radius (NM)                            | RTZ, RT3, TSH_Route  |
| `LegGeometryType` | str    | Great Circle (`GC`) or Rhumb Line (`RL`)       | RTZ, RT3             |
| `StarboardXTD`    | float  | Starboard cross-track distance (NM)            | RTZ, RT3, TSH_Route  |
| `PortsideXTD`     | float  | Port side cross-track distance (NM)            | RTZ, RT3, TSH_Route  |

After parsing, `_drop_empty_columns()` removes any column where every row value is
`None`, `NaN`, or empty string — keeping only populated fields.

---

## 4. CSV Generation (`generate_csv`)

### 4.1 Output Format

The CSV generator produces a **JRC ECDIS Route Sheet** format:

- **Comment lines** start with `//` and describe the route.
- **Data rows** are comma-separated with the following columns:

  | #   | Column         | Description                                      |
  |-----|----------------|--------------------------------------------------|
  | 1   | WPT No.        | 0-based waypoint index formatted as `000`, `001`, etc. |
  | 2-4 | LAT            | Degrees, minutes (6 decimal places), hemisphere (`N`/`S`) |
  | 5-7 | LON            | Degrees, minutes (6 decimal places), hemisphere (`E`/`W`) |
  | 8   | PORT [NM]      | Port side XTD (or `***` for start WP)            |
  | 9   | STBD [NM]      | Starboard XTD (or `***` for start WP)            |
  | 10  | Arr. Rad [NM]  | Arrival radius (or `***` for start WP)           |
  | 11  | Speed [kn]     | Max speed (or `***` for start WP)                |
  | 12  | Sail (RL/GC)   | Leg geometry type (or `***` for start WP)        |
  | 13  | ROT [deg/min]  | Rate of turn in degrees/minute (or `***` for start WP) |
  | 14  | Turn Rad [NM]  | Turning radius (or `***` for start WP)           |
  | 15  | Time Zone      | Always `00:00`                                   |
  | 16  | —              | Always `E`                                       |
  | 17  | Name           | Waypoint name or identifier                      |

### 4.2 Coordinate Conversion: Decimal → DMS

Coordinates are converted from decimal degrees to degrees/minutes/hemisphere for the
CSV output. The helper `_decimal_to_dms_components()` performs this:

```
decimal_degrees → (DDD, MM.mmm, H)
```

- **Positive** latitude → `N`, **negative** latitude → `S`
- **Positive** longitude → `E`, **negative** longitude → `W`

For example: `12.34°` → `12° 20.400' N`

### 4.3 Missing Values

When a value is `None` or `NaN`, the CSV output uses `***` as a placeholder.
For the first waypoint (index 0), navigation fields like PortXTD, StarboardXTD,
Arrival Radius, Speed, etc. are **always** set to `***` because a start waypoint
has no inbound leg.

### 4.4 Rate of Turn (ROT) Calculation

When both `SpeedMax` and `TurnRadius` are available, the ROT is computed:

```
ROT (deg/min) = speed / turn_radius × 180 / (π × 60)
```

If either value is missing or `turn_radius ≤ 0`, ROT is set to `***`.

---

## 5. Other Output Formats

The application also supports generating other formats from the parsed DataFrame:

| Generator          | Output       | Purpose                                          |
|--------------------|--------------|--------------------------------------------------|
| `generate_csv`     | CSV (text)   | JRC ECDIS Route Sheet for spreadsheet analysis.  |
| `generate_txt`     | TSV (text)   | Tab-separated values with a simplified schema.   |
| `generate_rt3`     | XML (text)   | Round-trip back to RT3 (voyage report) format.   |
| `generate_rtm`     | Binary       | Round-trip back to binary RTM format.            |

---

## 6. End-to-End Data Flow

```
User Uploads File
       │
       ▼
decode_upload()          — Decodes base64 data URI → raw bytes or text
       │
       ▼
convert_to_dataframe()   — Dispatches to the correct parser based on extension
       │
       ├── .rt3  → parse_tsh_route()  (→ parse_rt3() → parse_rtz() for standard RT3)
       └── .rtm  → parse_rtm()
       │
       ▼
_drop_empty_columns()    — Removes all-None/NaN/empty columns
       │
       ▼
pandas.DataFrame         — Normalised waypoint table
       │
       ▼
generate_csv(df)         — Converts to JRC ECDIS CSV format
       │
       ▼
Download as .csv file
```

---

## 7. Edge Cases & Behaviours

| Scenario                              | Behaviour                                                      |
|---------------------------------------|----------------------------------------------------------------|
| **Empty file / no waypoints**         | Returns an empty `DataFrame`; upload callback shows a warning. |
| **Unknown file extension**            | Tries XML parse; raises `ValueError` if not XML.               |
| **Binary RTM with short header**      | Tolerates a non-standard header but still reads the structure. |
| **Partial waypoint record (RTM)**     | Stops parsing at the last complete record.                     |
| **Missing route name**                | Falls back to an empty string or the parent route name.        |
| **Multiple `<route>` elements**       | Assigns sequential `RouteIndex` values, enumerating waypoints across all routes. |
| **XML namespaces**                    | Stripped via `_local()`, so `cirm.org/RTZ/1/0` and raw tags parse identically. |
| **RTM coordinate validation**         | All coordinates are checked to be within [-90,90] latitude and [-180,180] longitude bounds. |
| **TSH_Route auto-detection**          | `parse_tsh_route` checks for `<TSH_Route>` root; if absent, delegates to standard RT3 parsing. |

---

## 8. Code Map

| File            | Key Functions / Classes                            | Responsibility                            |
|-----------------|----------------------------------------------------|-------------------------------------------|
| `app.py`        | `parse_rtz()`, `parse_rt3()`, `parse_tsh_route()`, `parse_rtm()` | XML & binary parsers                      |
|                 | `generate_csv()`, `generate_txt()`, `generate_rt3()`, `generate_rtm()` | Output format generators                  |
|                 | `convert_to_dataframe()`                           | Dispatch router                           |
|                 | `_decimal_to_dms_components()`, `_coordinate_to_decimal()` | Coordinate conversion utilities           |
|                 | `_drop_empty_columns()`                            | Post-processing cleanup                   |
|                 | `decode_upload()`                                  | Base64 data URI decoder                   |
| `tests/test_parsers.py` | `ParserTests`                              | Unit tests for all parsers and generators |
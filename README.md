# RTZ / RT3 / RTM / TXT → CSV Converter

A clean, modern Dash web application that converts maritime route files
(**RTZ**, **RT3**, **RTM**, **TXT**) into downloadable CSV spreadsheets.

![preview](https://img.shields.io/badge/python-3.11-blue) ![dash](https://img.shields.io/badge/dash-2.17-119DFF) ![deploy](https://img.shields.io/badge/render-free-46E3B7)

## ✨ Features

- **Drag & drop** upload for `.rtz`, `.rt3`, `.rtm` and `.txt` files
- **Smart parsing** — auto-detects route-table TXT, namespaces in XML and fixed-width RTM records
- **Live preview** of the parsed data in a paginated, searchable table
- **One-click CSV export**
- Beautiful Bootstrap 5 / Flatly UI with Bootstrap Icons
- Built to be hosted on the **Render free tier**

## 🧩 Supported formats

| Format | Description |
|--------|-------------|
| **RTZ** | IALA Route Exchange Format — XML, contains waypoints, vessel and route metadata |
| **RT3** | Route Exchange Format 3 voyage report — may contain an embedded RTZ route |
| **RTM** | Binary route files with fixed-width waypoint records |
| **TXT**  | Free-form waypoint lists (comma-, tab-, semicolon- or whitespace-separated) |

## 🚀 Local development

```bash
# 1. Create a virtual environment
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
python app.py
```

The app will be available at <http://localhost:8050>.

## ☁️ Deploying to Render (free tier)

The repo includes a `render.yaml` Blueprint, so the easiest way to deploy is:

1. Push this repo to GitHub.
2. In Render, click **New → Blueprint Instance**, select the repo.
3. Render reads `render.yaml` and provisions a free web service.
4. Once built, your app is live at `https://<service-name>.onrender.com`.

Manual deploy (without Blueprint):

- **Environment:** `Python`
- **Build command:** `pip install --upgrade pip && pip install -r requirements.txt`
- **Start command:** `gunicorn app:server -b 0.0.0.0:$PORT --workers 2 --timeout 120`
- **Instance type:** `Free`

## 🗂 Project structure

```
.
├── app.py              # Dash application (layout, callbacks, parsers)
├── resources/          # Sample route files used by regression tests
├── tests/              # Unit and sample-file conversion tests
├── requirements.txt    # Python dependencies
├── runtime.txt         # Python version for Render
├── render.yaml         # Render Blueprint configuration
└── README.md
```

## 🛠 Tech stack

- [Dash](https://dash.plotly.com/) 2.17
- [dash-bootstrap-components](https://dash-bootstrap-components.opensource.asidate.com/) 1.6
- [Pandas](https://pandas.pydata.org/) 2.2
- [Gunicorn](https://gunicorn.org/) 22 (production WSGI server)
- Bootstrap Icons (via CDN)

# FI Login URL Validator

## Setup

It is recommended to create a virtual environment before running the app.
The reachability check now uses Selenium with headless Chrome, so Chrome and ChromeDriver must also be available on your machine.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

The app starts a local server at `http://127.0.0.1:8000`.

## If you get `ModuleNotFoundError: No module named 'requests'`

Install the required dependencies:

```bash
python -m pip install -r requirements.txt
```

If you prefer, you can install them directly:

```bash
python -m pip install requests beautifulsoup4 selenium
```

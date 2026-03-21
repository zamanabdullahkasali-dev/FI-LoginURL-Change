import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from fi_login_url_validator import (
    check_url_reachable,
    detect_merger,
    find_login_link,
    get_home_url_from_user,
    parse_account_type,
    process_ticket,
    search_home_url,
    validate_home_url,
)


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
HOST = "127.0.0.1"
PORT = 8000


class AppHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path, content_type):
        if not file_path.exists():
            self._send_json({"error": "Not found"}, status=404)
            return
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b"{}"
        if not raw_body:
            return {}
        return json.loads(raw_body.decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/static/app.js":
            self._send_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
            return
        if parsed.path == "/static/style.css":
            self._send_file(STATIC_DIR / "style.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/health":
            self._send_json({"status": "ok"})
            return
        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            payload = self._read_json_body()
            LOGGER.info("Handling API request", extra={"path": parsed.path})

            if parsed.path == "/api/check-url":
                self._send_json(check_url_reachable(payload.get("url", "")))
                return
            if parsed.path == "/api/get-home-url":
                self._send_json(
                    get_home_url_from_user(
                        payload.get("login_url", ""),
                        payload.get("user_input", ""),
                    )
                )
                return
            if parsed.path == "/api/search-home-url":
                self._send_json(search_home_url(payload.get("login_url", "")))
                return
            if parsed.path == "/api/validate-home-url":
                self._send_json(validate_home_url(payload.get("home_url", "")))
                return
            if parsed.path == "/api/parse-account-type":
                self._send_json(parse_account_type(payload.get("fi_name", "")))
                return
            if parsed.path == "/api/find-login-link":
                self._send_json(
                    find_login_link(
                        payload.get("home_url", ""),
                        payload.get("account_type", "Personal"),
                    )
                )
                return
            if parsed.path == "/api/detect-merger":
                self._send_json(detect_merger(payload.get("fi_name", "")))
                return
            if parsed.path == "/api/process-ticket":
                self._send_json(process_ticket(payload.get("ticket", {})))
                return

            self._send_json({"error": "Not found"}, status=404)
        except json.JSONDecodeError as exc:
            self._send_json({"error": f"Invalid JSON: {exc}"}, status=400)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Unhandled API error")
            self._send_json({"error": str(exc)}, status=500)


def run_server(host=HOST, port=PORT):
    server = HTTPServer((host, port), AppHandler)
    LOGGER.info("Starting server", extra={"host": host, "port": port})
    print(f"Server running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()

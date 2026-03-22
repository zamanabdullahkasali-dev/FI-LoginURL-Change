import importlib
import json
import logging
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10
RETRY_COUNT = 3
RETRY_WAIT_SECONDS = 5
BROWSER_LOAD_WAIT_SECONDS = 15
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}
SEARCH_ENGINES = [
    "https://www.google.com/search?q={query}",
    "https://html.duckduckgo.com/html/?q={query}",
    "https://www.bing.com/search?q={query}",
]
TRACKING_DOMAINS = (
    "google.com",
    "googleadservices.com",
    "doubleclick.net",
    "duckduckgo.com",
    "bing.com",
)
LOGIN_PATH_HINTS = (
    "login",
    "signon",
    "signin",
    "auth",
    "account",
    "online-banking",
    "onlinebanking",
)
MERGER_PATTERNS = [
    re.compile(r"merged with\s+([A-Z][A-Za-z0-9&.,'\- ]+)", re.IGNORECASE),
    re.compile(r"acquired by\s+([A-Z][A-Za-z0-9&.,'\- ]+)", re.IGNORECASE),
    re.compile(r"now part of\s+([A-Z][A-Za-z0-9&.,'\- ]+)", re.IGNORECASE),
    re.compile(r"became part of\s+([A-Z][A-Za-z0-9&.,'\- ]+)", re.IGNORECASE),
]


def _normalize_url(url):
    if not url:
        return ""
    normalized = str(url).strip()
    if not normalized:
        return ""
    if not re.match(r"^https?://", normalized, re.IGNORECASE):
        normalized = f"https://{normalized}"
    return normalized


def _request_get(url, **kwargs):
    return requests.get(url, headers=DEFAULT_HEADERS, allow_redirects=True, timeout=REQUEST_TIMEOUT, **kwargs)


def _response_headers_dict(response):
    return dict(response.headers.items()) if response is not None else {}


def _reachability_reason(status_code):
    reasons = {
        200: "HTTP 200 OK",
        301: "HTTP 301 Moved Permanently",
        302: "HTTP 302 Found",
        400: "HTTP 400 Bad Request",
        401: "HTTP 401 Unauthorized",
        403: "HTTP 403 Forbidden",
        404: "HTTP 404 Not Found",
        405: "HTTP 405 Method Not Allowed",
        408: "HTTP 408 Request Timeout",
        429: "HTTP 429 Too Many Requests",
        500: "HTTP 500 Internal Server Error",
        502: "HTTP 502 Bad Gateway",
        503: "HTTP 503 Service Unavailable",
        504: "HTTP 504 Gateway Timeout",
    }
    return reasons.get(status_code, f"HTTP {status_code}")


def _load_selenium_components():
    webdriver_module = importlib.import_module("selenium.webdriver")
    options_module = importlib.import_module("selenium.webdriver.chrome.options")
    service_module = importlib.import_module("selenium.webdriver.chrome.service")
    support_ui_module = importlib.import_module("selenium.webdriver.support.ui")

    return {
        "webdriver": webdriver_module,
        "chrome_options": options_module.Options,
        "chrome_service": service_module.Service,
        "web_driver_wait": support_ui_module.WebDriverWait,
    }


def _build_browser_driver():
    selenium_components = _load_selenium_components()
    options = selenium_components["chrome_options"]()
    options.add_argument("--headless=new")
    options.add_argument("--disable-http2")
    options.add_argument("--disable-quic")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--window-size=1440,900")
    options.set_capability("goog:loggingPrefs", {"browser": "ALL", "performance": "ALL"})

    service = selenium_components["chrome_service"]()
    driver = selenium_components["webdriver"].Chrome(service=service, options=options)
    driver.set_page_load_timeout(BROWSER_LOAD_WAIT_SECONDS)
    return driver, selenium_components


def _wait_for_network_idle_and_get_document_status(driver, max_wait_seconds):
    active_requests = set()
    document_request = None
    network_idle_started_at = None
    start_time = time.time()
    logs_seen = []
    document_response_count = 0
    loading_failed_errors = []

    while time.time() - start_time < max_wait_seconds:
        performance_logs = driver.get_log("performance")
        logs_seen.extend(performance_logs)

        for entry in performance_logs:
            message_text = entry.get("message", "{}")
            try:
                message = json.loads(message_text).get("message", {})
            except json.JSONDecodeError:
                continue

            method = message.get("method")
            params = message.get("params", {})

            if method == "Network.requestWillBeSent":
                request_id = params.get("requestId")
                if request_id:
                    active_requests.add(request_id)

            elif method == "Network.loadingFinished":
                request_id = params.get("requestId")
                if request_id:
                    active_requests.discard(request_id)

            elif method == "Network.loadingFailed":
                request_id = params.get("requestId")
                if request_id:
                    active_requests.discard(request_id)
                error_text = params.get("errorText")
                if error_text:
                    loading_failed_errors.append(error_text)

            elif method == "Network.responseReceived":
                response = params.get("response", {})
                resource_type = params.get("type")
                if resource_type == "Document":
                    document_response_count += 1
                    if document_request is None:
                        document_request = {
                            "url": response.get("url") or driver.current_url,
                            "status_code": response.get("status"),
                            "status_text": response.get("statusText", ""),
                            "type": resource_type,
                        }

        ready_state = driver.execute_script("return document.readyState")
        if not active_requests:
            if network_idle_started_at is None:
                network_idle_started_at = time.time()
            elif time.time() - network_idle_started_at >= 2:
                LOGGER.info("Network idle detected", extra={"url": driver.current_url, "mode": "no_active_requests_for_2_seconds"})
                if document_request is not None:
                    break
        else:
            network_idle_started_at = None

        if ready_state == "complete" and document_request is not None:
            LOGGER.info("Network idle detected", extra={"url": driver.current_url, "mode": "document_ready_complete"})
            break

        time.sleep(0.25)

    return document_request, logs_seen, document_response_count, loading_failed_errors


def check_url_reachable(url):
    normalized_url = _normalize_url(url)
    LOGGER.info("Checking URL reachability", extra={"url": normalized_url})
    if not normalized_url:
        return {
            "request_url": "",
            "status_code": None,
            "reachable": False,
            "reason": "Empty URL",
            "next_step": "STEP 2 — OBTAIN HOME URL",
        }

    last_error = None
    last_error_code = None
    last_status = None
    attempt_history = []

    for attempt in range(1, RETRY_COUNT + 1):
        driver = None
        try:
            LOGGER.info("URL loaded", extra={"url": normalized_url, "attempt": attempt})
            driver, selenium_components = _build_browser_driver()
            web_driver_wait = selenium_components["web_driver_wait"]

            driver.get(normalized_url)
            web_driver_wait(driver, BROWSER_LOAD_WAIT_SECONDS).until(
                lambda browser: browser.execute_script("return document.readyState") in {"interactive", "complete"}
            )
            document_request, performance_logs, document_response_count, loading_failed_errors = _wait_for_network_idle_and_get_document_status(driver, BROWSER_LOAD_WAIT_SECONDS)
            current_url = driver.current_url or normalized_url

            LOGGER.info(
                "Network logs scanned",
                extra={
                    "url": normalized_url,
                    "attempt": attempt,
                    "total_network_events_found": len(performance_logs),
                    "document_responses_found": document_response_count,
                    "loading_failed_errors": loading_failed_errors,
                },
            )

            if "net::ERR_HTTP2_PROTOCOL_ERROR" in loading_failed_errors and document_request is None:
                LOGGER.info(
                    "HTTP/1.1 fallback retry triggered",
                    extra={"url": normalized_url, "attempt": attempt, "error": "net::ERR_HTTP2_PROTOCOL_ERROR"},
                )
                last_error = "Retrying after HTTP/1.1 fallback"
                last_error_code = "net::ERR_HTTP2_PROTOCOL_ERROR"
                continue

            if document_request:
                LOGGER.info(
                    "Document request captured",
                    extra={"url": normalized_url, "attempt": attempt, "document_url": document_request.get("url"), "status_code": document_request.get("status_code")},
                )
                LOGGER.info(
                    "Doc filter applied",
                    extra={"url": normalized_url, "attempt": attempt, "resource_type": document_request.get("type")},
                )

            last_status = document_request.get("status_code") if document_request else None
            reason = "Something went wrong" if last_status != 200 else "HTTP 200 OK"
            attempt_history.append(
                {
                    "attempt": attempt,
                    "request_url": normalized_url,
                    "final_url": current_url,
                    "status_code": last_status,
                    "headers": {},
                    "reason": reason,
                    "performance_logs": performance_logs,
                    "document_request": document_request,
                }
            )

            LOGGER.info("Extracted status code", extra={"url": normalized_url, "attempt": attempt, "status_code": last_status})
            if last_status == 200:
                LOGGER.info("Final decision", extra={"url": normalized_url, "attempt": attempt, "reachable": True, "status_code": last_status})
                return {
                    "login_url": normalized_url,
                    "reachable": True,
                    "http_status_code": 200,
                    "status": "Web page reachable",
                    "attempts": attempt_history,
                }
            last_error = document_request.get("status_text") if document_request else "Something went wrong"
            last_error_code = str(last_status) if last_status is not None else (document_request.get("error_text") if document_request else None)
        except Exception as exc:
            last_error = str(exc)
            error_code = exc.__class__.__name__
            last_error_code = error_code
            attempt_history.append(
                {
                    "attempt": attempt,
                    "request_url": normalized_url,
                    "final_url": None,
                    "status_code": None,
                    "headers": {},
                    "reason": str(exc),
                    "error_code": error_code,
                }
            )
            LOGGER.exception("URL attempt failed", extra={"url": normalized_url, "attempt": attempt})
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    LOGGER.exception("Failed to close browser", extra={"url": normalized_url, "attempt": attempt})

        if attempt < RETRY_COUNT:
            LOGGER.info("Retrying URL after wait", extra={"url": normalized_url, "attempt": attempt, "wait_seconds": RETRY_WAIT_SECONDS})
            time.sleep(RETRY_WAIT_SECONDS)

    LOGGER.info("Final decision", extra={"url": normalized_url, "reachable": False, "status_code": last_status, "reason": last_error})
    return {
        "login_url": normalized_url,
        "reachable": False,
        "http_status_code": last_status,
        "status": "Web page not reachable",
        "reason": "Something went wrong",
        "attempts": attempt_history,
        "error_code": last_error_code or (str(last_status) if last_status is not None else None),
        "next_step": "STEP 2 — OBTAIN HOME URL",
    }


def get_home_url_from_user(login_url, user_input=None):
    provided = "" if user_input is None else str(user_input).strip()

    LOGGER.info("Received Home URL input", extra={"login_url": login_url, "user_input": provided})
    if provided:
        return {"home_url": _normalize_url(provided), "source": "user"}

    fallback = search_home_url(login_url)
    return {"home_url": fallback.get("home_url"), "source": fallback.get("source", "search"), "details": fallback}


def _domain_parts(url):
    parsed = urlparse(_normalize_url(url))
    domain = parsed.netloc.lower()
    root = domain[4:] if domain.startswith("www.") else domain
    return domain, root


def _extract_candidate_urls_from_search(html, engine_url):
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for tag in soup.find_all(["a", "cite"]):
        href = tag.get("href") if tag.name == "a" else tag.get_text(" ", strip=True)
        if not href:
            continue
        href = href.strip()
        parsed = urlparse(href)
        if parsed.scheme in {"http", "https"}:
            candidates.append(href)
            continue
        if "google.com" in engine_url and href.startswith("/url?"):
            match = re.search(r"[?&]q=(https?://[^&]+)", href)
            if match:
                candidates.append(match.group(1))
    return candidates


def _reject_search_result(candidate):
    normalized = _normalize_url(candidate)
    parsed = urlparse(normalized)
    domain = parsed.netloc.lower()
    path = parsed.path.lower()

    if not domain:
        return True
    if any(domain.endswith(blocked) for blocked in TRACKING_DOMAINS):
        return True
    if any(hint in path for hint in LOGIN_PATH_HINTS):
        return True
    if any(token in normalized.lower() for token in ("adurl=", "gclid=", "utm_", "facebook.com", "linkedin.com", "yelp.com")):
        return True
    return False


def _score_home_candidate(candidate, login_url):
    normalized = _normalize_url(candidate)
    candidate_domain, candidate_root = _domain_parts(normalized)
    score = 0

    if re.match(r"^https?://", str(login_url or ""), re.IGNORECASE):
        login_domain, login_root = _domain_parts(login_url)
        if candidate_domain == login_domain:
            score += 100
        elif candidate_root == login_root:
            score += 90
        target_parts = set(re.split(r"[^a-z0-9]+", login_root))
    else:
        target_parts = set(re.split(r"[^a-z0-9]+", str(login_url).lower()))

    candidate_parts = set(re.split(r"[^a-z0-9]+", candidate_root))
    overlap = len({part for part in target_parts & candidate_parts if part})
    score += overlap * 10

    parsed = urlparse(normalized)
    if parsed.path in {"", "/"}:
        score += 25

    return score


def search_home_url(login_url):
    original_value = str(login_url or "").strip()
    normalized_value = _normalize_url(original_value) if re.match(r"^https?://", original_value, re.IGNORECASE) else original_value
    LOGGER.info("Searching for Home URL", extra={"login_url": normalized_value or original_value})
    if re.match(r"^https?://", original_value, re.IGNORECASE):
        _, login_root = _domain_parts(original_value)
        query_term = login_root or original_value
    else:
        query_term = original_value
    query = requests.utils.quote(query_term)
    ranked = []

    for engine in SEARCH_ENGINES:
        search_url = engine.format(query=query)
        try:
            LOGGER.info("Submitting search request", extra={"search_url": search_url})
            response = _request_get(search_url)
            if response.status_code != 200:
                LOGGER.info("Search engine non-200 response", extra={"search_url": search_url, "status_code": response.status_code})
                continue
            for candidate in _extract_candidate_urls_from_search(response.text, search_url):
                if _reject_search_result(candidate):
                    continue
                normalized = _normalize_url(candidate)
                parsed = urlparse(normalized)
                root_candidate = f"{parsed.scheme}://{parsed.netloc}"
                score = _score_home_candidate(root_candidate, original_value)
                LOGGER.info(
                    "Scored Home URL candidate",
                    extra={"candidate": root_candidate, "score": score, "search_url": search_url},
                )
                ranked.append((score, root_candidate, search_url))
            if ranked:
                break
        except (requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.RequestException) as exc:
            LOGGER.exception("Search request failed", extra={"search_url": search_url, "error": str(exc)})

    if not ranked:
        return {"home_url": None, "source": "search", "candidates": []}

    ranked.sort(key=lambda item: (-item[0], item[1]))
    best_score, best_candidate, source = ranked[0]
    return {
        "home_url": best_candidate,
        "source": source,
        "score": best_score,
        "candidates": [candidate for _, candidate, _ in ranked],
    }


def validate_home_url(home_url):
    home_url = _normalize_url(home_url)
    validation = check_url_reachable(home_url)
    LOGGER.info("Validated Home URL", extra={"home_url": home_url, "reachable": validation.get("reachable")})
    return {
        "home_url": validation.get("final_url") or home_url,
        "valid": validation.get("reachable", False),
        "details": validation,
    }


def parse_account_type(fi_name):
    fi_name = (fi_name or "").strip()
    parts = re.split(r"\s*-\s*", fi_name, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) > 1 and parts[1].strip():
        account_type = parts[1].strip()
    else:
        account_type = "Personal"
    LOGGER.info("Parsed account type", extra={"fi_name": fi_name, "account_type": account_type})
    return {"account_type": account_type}


def _score_login_candidate(candidate_url, combined_text, account_type):
    score = 0
    lowered_text = combined_text.lower()
    lowered_url = candidate_url.lower()
    normalized_account_type = (account_type or "").lower()

    if normalized_account_type and normalized_account_type in lowered_text:
        score += 100
    if "personal" in lowered_text and normalized_account_type != "business":
        score += 60
    if "business" in lowered_text and normalized_account_type == "business":
        score += 90
    if any(keyword in lowered_text for keyword in ("log in", "login", "sign in", "sign-in", "online banking", "account access")):
        score += 40
    if any(keyword in lowered_url for keyword in LOGIN_PATH_HINTS):
        score += 25

    return score


def find_login_link(home_url, account_type):
    home_url = _normalize_url(home_url)
    LOGGER.info("Finding login link", extra={"home_url": home_url, "account_type": account_type})
    try:
        response = _request_get(home_url)
        if response.status_code != 200:
            return {"login_url": None, "valid": False, "candidates": [], "error": f"Home page status code {response.status_code}"}
    except (requests.exceptions.SSLError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.RequestException) as exc:
        LOGGER.exception("Home page fetch failed", extra={"home_url": home_url, "error": str(exc)})
        return {"login_url": None, "valid": False, "candidates": [], "error": str(exc)}

    soup = BeautifulSoup(response.text, "html.parser")
    candidates = []

    for element in soup.find_all(["a", "button"]):
        href = element.get("href") or element.get("data-href") or element.get("onclick") or ""
        text = element.get_text(" ", strip=True)
        aria_label = element.get("aria-label", "")
        combined_text = " ".join(part for part in [href, text, aria_label] if part).strip()
        if not combined_text:
            continue
        if not any(keyword in combined_text.lower() for keyword in ("login", "log in", "sign in", "sign-in", "banking", "account")):
            continue

        resolved_url = ""
        if href:
            onclick_match = re.search(r"https?://[^'\")\s]+", href)
            if onclick_match:
                resolved_url = onclick_match.group(0)
            elif href.lower().startswith("javascript:"):
                data_url = element.get("data-url") or element.get("data-link") or ""
                resolved_url = data_url
            else:
                resolved_url = urljoin(home_url, href)

        if not resolved_url:
            continue

        score = _score_login_candidate(resolved_url, combined_text, account_type)
        LOGGER.info(
            "Scored login link candidate",
            extra={"candidate": resolved_url, "score": score, "account_type": account_type},
        )
        candidates.append({
            "login_url": resolved_url,
            "score": score,
            "text": text,
            "aria_label": aria_label,
        })

    if not candidates:
        return {"login_url": None, "valid": False, "candidates": [], "error": "No login candidate found"}

    candidates.sort(key=lambda item: (-item["score"], item["login_url"]))
    chosen = candidates[0]
    validation = check_url_reachable(chosen["login_url"])
    return {
        "login_url": validation.get("final_url") or chosen["login_url"],
        "valid": validation.get("reachable", False),
        "validation": validation,
        "candidates": candidates,
    }


def _extract_search_snippets(html):
    soup = BeautifulSoup(html, "html.parser")
    snippets = []
    for tag in soup.find_all(["div", "span", "p"]):
        text = tag.get_text(" ", strip=True)
        if text and len(text.split()) >= 4:
            snippets.append(text)
    return snippets


def _extract_merged_name(text, original_name):
    for pattern in MERGER_PATTERNS:
        match = pattern.search(text)
        if match:
            merged_name = match.group(1).strip(" .,:;-")
            if merged_name and merged_name.lower() != (original_name or "").lower():
                return merged_name
    return None


def detect_merger(fi_name):
    fi_name = (fi_name or "").strip()
    LOGGER.info("Detecting merger", extra={"fi_name": fi_name})
    query = requests.utils.quote(f"{fi_name} merged with which bank")

    merged_name = None
    source_url = None
    for engine in SEARCH_ENGINES:
        search_url = engine.format(query=query)
        try:
            LOGGER.info("Submitting merger search", extra={"search_url": search_url})
            response = _request_get(search_url)
            if response.status_code != 200:
                continue
            snippets = _extract_search_snippets(response.text)
            for snippet in snippets:
                merged_name = _extract_merged_name(snippet, fi_name)
                if merged_name:
                    source_url = search_url
                    break
            if merged_name:
                break
        except (requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.RequestException) as exc:
            LOGGER.exception("Merger search failed", extra={"search_url": search_url, "error": str(exc)})

    if not merged_name:
        return {
            "merged": False,
            "merged_fi_name": None,
            "merged_home_url": None,
            "merged_login_url": None,
            "source": source_url,
        }

    home_search = search_home_url(merged_name)
    merged_home_url = home_search.get("home_url")
    account_type = parse_account_type(fi_name).get("account_type")
    login_result = find_login_link(merged_home_url, account_type) if merged_home_url else {"login_url": None, "valid": False}

    return {
        "merged": bool(merged_home_url and login_result.get("login_url") and login_result.get("valid")),
        "merged_fi_name": merged_name,
        "merged_home_url": merged_home_url,
        "merged_login_url": login_result.get("login_url"),
        "source": source_url,
        "home_search": home_search,
        "login_result": login_result,
    }


def process_ticket(ticket):
    ticket = ticket or {}
    fi_name = ticket.get("fi_name", "")
    stored_login_url = ticket.get("login_url", "")
    LOGGER.info("Processing ticket", extra={"fi_name": fi_name, "login_url": stored_login_url})

    existing_login_validation = check_url_reachable(stored_login_url)
    if existing_login_validation.get("reachable"):
        return {"result": "Login URL working", "login_url": existing_login_validation.get("final_url") or stored_login_url}

    if existing_login_validation.get("status_code") == 404:
        LOGGER.info("Login URL returned HTTP 404 — proceeding to Home URL discovery")

    home_url = ticket.get("home_url")
    if home_url:
        LOGGER.info("Using stored Home URL", extra={"home_url": home_url})
        home_result = validate_home_url(home_url)
        if not home_result.get("valid"):
            search_result = search_home_url(stored_login_url)
            home_url = search_result.get("home_url")
        else:
            home_url = home_result.get("home_url")
    else:
        user_home = get_home_url_from_user(stored_login_url, ticket.get("user_home_url_input"))
        home_url = user_home.get("home_url")
        if home_url:
            validated = validate_home_url(home_url)
            if not validated.get("valid"):
                search_result = search_home_url(stored_login_url)
                home_url = search_result.get("home_url")
            else:
                home_url = validated.get("home_url")

    if not home_url:
        return {"result": "Home URL not found", "home_url": None}

    account_type = parse_account_type(fi_name).get("account_type")
    login_result = find_login_link(home_url, account_type)
    new_login_url = login_result.get("login_url") if login_result.get("valid") else None

    if new_login_url and _normalize_url(new_login_url) != _normalize_url(stored_login_url):
        return {
            "result": "Login URL change",
            "old_login_url": stored_login_url,
            "new_login_url": new_login_url,
            "home_url": home_url,
        }

    if not new_login_url:
        merger_result = detect_merger(fi_name)
        if merger_result.get("merged"):
            return {
                "result": "FI merged",
                "merged_fi_name": merger_result.get("merged_fi_name"),
                "merged_home_url": merger_result.get("merged_home_url"),
                "merged_login_url": merger_result.get("merged_login_url"),
            }
        return {"result": "Login URL not found", "home_url": home_url}

    return {"result": "Login URL not found", "home_url": home_url, "login_url": new_login_url}

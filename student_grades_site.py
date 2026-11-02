#!/usr/bin/env python3
"""Serve a SmartNation grades dashboard locally or as a Render web service."""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import requests

from student_grades import LoginError, SmartNationClient


ROOT = Path(__file__).resolve().parent
SITE_ROOT = ROOT / "site"
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"
HOST = "0.0.0.0" if IS_PRODUCTION else "127.0.0.1"
PORT = int(os.environ.get("PORT", "8765"))
SESSION_TTL_SECONDS = 8 * 60 * 60
MAX_REQUEST_BYTES = 16_384
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
JOURNAL_ID = re.compile(r"journal_(\d+)_table_raw\.json$")
IIN_PATTERN = re.compile(r"\d{12}")


def public_origin() -> str | None:
    configured_origin = os.environ.get("PUBLIC_ORIGIN", "").strip().rstrip("/")
    if configured_origin:
        parsed = urlsplit(configured_origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
            raise RuntimeError("PUBLIC_ORIGIN must be an HTTPS origin without a path.")
        return f"https://{parsed.netloc.lower()}"

    hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip().lower()
    if hostname:
        return f"https://{hostname}"
    return None


def public_hostname() -> str | None:
    origin = public_origin()
    return urlsplit(origin).hostname if origin else None


@dataclass
class DashboardSession:
    data: dict[str, Any]
    expires_at: float


sessions: dict[str, DashboardSession] = {}
sessions_lock = threading.Lock()


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def load_dashboard_data() -> dict[str, Any]:
    journals_by_id: dict[str, dict[str, Any]] = {}
    journal_list_path = ROOT / "journals_list_raw.json"
    if journal_list_path.exists():
        raw_journals = read_json(journal_list_path)
        for journal in raw_journals.get("data", []):
            if isinstance(journal, dict) and journal.get("id") is not None:
                journals_by_id[str(journal["id"])] = {
                    "id": str(journal["id"]),
                    "name": str(journal.get("name") or "Предмет без названия"),
                }

    names: dict[str, str] = {}
    for path in ROOT.glob("journal_*_contingent_raw.json"):
        raw = read_json(path)
        for student in raw.get("students", []):
            if isinstance(student, dict) and student.get("id") is not None:
                name = student.get("fullName") or student.get("shortName")
                if name:
                    names[str(student["id"])] = str(name)

    if len(names) > 1:
        raise ValueError("В сохранённых данных найдено несколько имён; для защиты оценок других учеников выберите свой профиль.")

    journals: list[dict[str, Any]] = []
    for path in ROOT.glob("journal_*_table_raw.json"):
        match = JOURNAL_ID.fullmatch(path.name)
        if not match:
            continue
        journal_id = match.group(1)
        raw = read_json(path)
        listed = journals_by_id.get(journal_id, {})
        days: list[dict[str, Any]] = []
        raw_days = raw.get("days", [])
        current_month: int | None = None
        for index, day in enumerate(raw_days):
            if not isinstance(day, dict):
                continue
            date_label = str(day.get("label") or "")
            date_match = re.fullmatch(r"\d{1,2}\.(\d{1,2})", date_label)
            is_attestation = date_label.strip().casefold() == "а"
            if date_match:
                current_month = int(date_match.group(1))
            attestation_month = current_month
            if is_attestation and attestation_month is None:
                for later_day in raw_days[index + 1:]:
                    later_label = str(later_day.get("label") or "")
                    later_match = re.fullmatch(r"\d{1,2}\.(\d{1,2})", later_label)
                    if later_match:
                        attestation_month = int(later_match.group(1))
                        break
            scores = day.get("points")
            points: list[dict[str, Any]] = []
            if isinstance(scores, dict):
                for student_id, score in scores.items():
                    student_id = str(student_id)
                    if student_id not in names or not isinstance(score, dict):
                        continue
                    value = score.get("point")
                    points.append({
                        "studentId": student_id,
                        "value": value if isinstance(value, (int, float)) else None,
                        "label": str(score.get("label") or ""),
                        "absent": bool(score.get("absented")),
                        "failed": bool(score.get("failed")),
                        "uncertified": bool(score.get("uncertified")),
                    })
            days.append({
                "date": date_label,
                "isAttestation": is_attestation,
                "attestationMonth": attestation_month if is_attestation else None,
                "theme": str(day.get("theme") or "Занятие"),
                "points": points,
            })
        journals.append({
            "id": journal_id,
            "name": listed.get("name", f"Предмет {journal_id}"),
            "days": days,
        })

    journals.sort(key=lambda journal: journal["name"].casefold())
    return {"journals": journals, "students": names}


def _extract_student_names(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    students = raw.get("students", [])
    if not isinstance(students, list):
        return {}
    result: dict[str, str] = {}
    for student in students:
        if not isinstance(student, dict):
            continue
        student_id = student.get("id") or student.get("student_id") or student.get("contingent_id")
        name = student.get("fullName") or student.get("full_name") or student.get("name") or student.get("shortName")
        if student_id is not None and name:
            result[str(student_id)] = str(name)
    return result


def load_live_dashboard_data(client: SmartNationClient) -> dict[str, Any]:
    journals = client.list_open_journals(save_raw=False)
    if not journals:
        raise LoginError("Вход выполнен, но SmartNation не вернул список журналов.")

    names: dict[str, str] = {}
    for journal in journals:
        names.update(_extract_student_names(
            client.get_contingent_raw(journal.journal_id, save_raw=False)
        ))
    if len(names) != 1:
        raise LoginError(
            "Не удалось однозначно определить ваш профиль в журналах. "
            "Оценки не загружены, чтобы не показать данные одногруппников."
        )
    student_id = next(iter(names))

    live_journals: list[dict[str, Any]] = []
    for journal in journals:
        raw = client.get_journal_table_raw(journal.journal_id, save_raw=False)
        if not isinstance(raw, dict) or not isinstance(raw.get("days"), list):
            raise LoginError(f"Не удалось загрузить журнал «{journal.name}».")

        days: list[dict[str, Any]] = []
        raw_days = raw["days"]
        current_month: int | None = None
        for index, day in enumerate(raw_days):
            if not isinstance(day, dict):
                continue
            date_label = str(day.get("label") or "")
            date_match = re.fullmatch(r"\d{1,2}\.(\d{1,2})", date_label)
            is_attestation = date_label.strip().casefold() == "а"
            if date_match:
                current_month = int(date_match.group(1))
            attestation_month = current_month
            if is_attestation and attestation_month is None:
                for later_day in raw_days[index + 1:]:
                    if not isinstance(later_day, dict):
                        continue
                    later_match = re.fullmatch(
                        r"\d{1,2}\.(\d{1,2})", str(later_day.get("label") or "")
                    )
                    if later_match:
                        attestation_month = int(later_match.group(1))
                        break

            source_points = day.get("points")
            score = source_points.get(student_id) if isinstance(source_points, dict) else None
            points: list[dict[str, Any]] = []
            if isinstance(score, dict):
                value = score.get("point")
                points.append({
                    "studentId": student_id,
                    "value": value if isinstance(value, (int, float)) else None,
                    "label": str(score.get("label") or ""),
                    "absent": bool(score.get("absented")),
                    "failed": bool(score.get("failed")),
                    "uncertified": bool(score.get("uncertified")),
                })
            days.append({
                "date": date_label,
                "isAttestation": is_attestation,
                "attestationMonth": attestation_month if is_attestation else None,
                "theme": str(day.get("theme") or "Занятие"),
                "points": points,
            })

        live_journals.append({
            "id": str(journal.journal_id),
            "name": journal.name,
            "days": days,
        })

    return {"journals": live_journals, "students": names}


class DashboardHandler(BaseHTTPRequestHandler):
    def _send(
        self, status: int, content_type: str, body: bytes, headers: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if headers:
            for name, value in headers.items():
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(
        self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body, headers)

    def _request_host_is_allowed(self) -> bool:
        host_header = self.headers.get("Host", "")
        host = urlsplit(f"//{host_header}").hostname
        if not host:
            return False
        host = host.lower()
        if IS_PRODUCTION:
            return host == public_hostname()
        return (
            self.client_address[0] in ("127.0.0.1", "::1")
            and host in ("127.0.0.1", "localhost")
        )

    def _origin_is_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if IS_PRODUCTION:
            return origin is not None and origin.rstrip("/").lower() == public_origin()
        if origin is None:
            return True
        return origin in {
            f"http://127.0.0.1:{PORT}",
            f"http://localhost:{PORT}",
        }

    def _session_token(self) -> str | None:
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        morsel = cookie.get("grades_session")
        return morsel.value if morsel else None

    def _get_session(self) -> DashboardSession | None:
        token = self._session_token()
        if not token:
            return None
        with sessions_lock:
            session = sessions.get(token)
            if session and session.expires_at > time.time():
                session.expires_at = time.time() + SESSION_TTL_SECONDS
                return session
            sessions.pop(token, None)
        return None

    def _read_json_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0 or length > MAX_REQUEST_BYTES:
            return None
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def do_GET(self) -> None:
        if not self._request_host_is_allowed():
            self._send_json(403, {"error": "Запрос отклонён."})
            return
        path = unquote(urlsplit(self.path).path)
        if path == "/api/session":
            session = self._get_session()
            if session is None:
                self._send_json(200, {"authenticated": False})
            else:
                self._send_json(200, {
                    "authenticated": True,
                    "name": next(iter(session.data["students"].values())),
                })
            return
        if path == "/api/grades":
            session = self._get_session()
            if session is None:
                self._send_json(401, {"error": "Сначала войди в SmartNation."})
                return
            self._send_json(200, session.data)
            return

        static_file = STATIC_FILES.get(path)
        if static_file is None:
            self._send(404, "text/plain; charset=utf-8", "Not found".encode())
            return

        file_name, content_type = static_file
        try:
            body = (SITE_ROOT / file_name).read_bytes()
        except OSError as error:
            print(f"Не удалось прочитать файл сайта {file_name}: {error}")
            self._send(500, "text/plain; charset=utf-8", "Не удалось открыть файл сайта.".encode())
            return
        self._send(200, content_type, body)

    def do_POST(self) -> None:
        if not self._request_host_is_allowed() or not self._origin_is_allowed():
            self._send_json(403, {"error": "Запрос отклонён."})
            return
        path = unquote(urlsplit(self.path).path)
        if path == "/api/logout":
            token = self._session_token()
            if token:
                with sessions_lock:
                    sessions.pop(token, None)
            self._send_json(200, {"authenticated": False}, {
                "Set-Cookie": (
                    "grades_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
                    + ("; Secure" if IS_PRODUCTION else "")
                ),
            })
            return
        if path != "/api/login":
            self._send_json(404, {"error": "Not found"})
            return

        payload = self._read_json_body()
        if payload is None:
            self._send_json(400, {"error": "Проверь поля ИИН и пароль."})
            return
        iin = str(payload.get("iin") or "").strip()
        password = payload.get("password")
        if not IIN_PATTERN.fullmatch(iin) or not isinstance(password, str) or not password or len(password) > 1024:
            self._send_json(400, {"error": "Введи ИИН из 12 цифр и пароль SmartNation."})
            return

        try:
            client = SmartNationClient(iin, password)
            client.login()
            data = load_live_dashboard_data(client)
        except LoginError as error:
            self._send_json(401, {"error": str(error)})
            return
        except requests.RequestException:
            self._send_json(502, {
                "error": "Не удалось связаться со SmartNation. Проверь интернет и попробуй позже.",
            })
            return
        except ValueError:
            self._send_json(502, {
                "error": "SmartNation вернул данные в неожиданном формате. Попробуй позже.",
            })
            return
        finally:
            password = ""

        token = secrets.token_urlsafe(32)
        with sessions_lock:
            sessions[token] = DashboardSession(data=data, expires_at=time.time() + SESSION_TTL_SECONDS)
        self._send_json(200, {
            "authenticated": True,
            "name": next(iter(data["students"].values())),
        }, {
            "Set-Cookie": (
                f"grades_session={token}; HttpOnly; SameSite=Strict; Path=/; "
                f"Max-Age={SESSION_TTL_SECONDS}"
                + ("; Secure" if IS_PRODUCTION else "")
            ),
        })

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[сайт] {self.address_string()} - {format % args}")


def main() -> None:
    if not SITE_ROOT.is_dir():
        raise SystemExit(f"Папка сайта не найдена: {SITE_ROOT}")
    if IS_PRODUCTION and public_hostname() is None:
        raise SystemExit(
            "Для production нужен RENDER_EXTERNAL_HOSTNAME или PUBLIC_ORIGIN с HTTPS."
        )

    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    if IS_PRODUCTION:
        print(f"Сайт запущен на порту {PORT}; Render обслуживает HTTPS.")
    else:
        url = f"http://{HOST}:{PORT}/"
        print(f"Сайт открыт: {url}")
        print("Сервер доступен только на этом компьютере. Для остановки нажмите Ctrl+C.")
        threading.Timer(0.7, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

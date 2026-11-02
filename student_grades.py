#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
student_grades.py

Клиент для college.smartnation.kz (SmartNation College) — журналы/оценки.
Логинится ИИН+паролем, даёт список журналов, таблицу оценок по журналу
(с фильтром по месяцу), сырые данные сохраняются на диск для отладки.

Пароль нигде не хардкодится и не пишется на диск.
"""

from __future__ import annotations

import os
import sys
import getpass
import json
import re
from dataclasses import dataclass, field
from typing import Any

import requests

try:
    import matplotlib.pyplot as plt
except ImportError:
    plt = None


BASE_URL = "https://college.smartnation.kz"
LOGIN_PAGE_URL = f"{BASE_URL}/ru/sko/login"
LOGIN_POST_URL = f"{BASE_URL}/ru/sko/login"
JOURNALS_LIST_URL = f"{BASE_URL}/ru/sko/control/journals"

HEADERS_COMMON = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
}

MONTHS_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


class LoginError(RuntimeError):
    pass


@dataclass
class JournalRef:
    journal_id: int
    name: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DayPoint:
    date_label: str   # "02.10"
    theme: str
    point: float
    failed: bool
    absented: bool


@dataclass
class StudentSummary:
    student_id: str
    name: str
    points: list[DayPoint] = field(default_factory=list)
    days_total: int = 0          # все занятия в выборке (с оценкой и без)
    days_with_point: int = 0
    days_without_point: int = 0  # занятия, где поле "points" пустое (ещё не оценено)

    @property
    def average(self) -> float | None:
        if not self.points:
            return None
        return sum(p.point for p in self.points) / len(self.points)

    def needed_average_on_remaining(self, passing_percent: float = 50.0) -> float | None:
        """
        Сколько в среднем нужно набирать на оставшихся (ещё не оценённых)
        занятиях, чтобы итоговый средний процент по ВСЕМ занятиям дошёл
        до passing_percent. None, если оставшихся занятий нет (считать нечего)
        или цель уже недостижима/не нужна.
        """
        if self.days_without_point <= 0:
            return None
        current_sum = sum(p.point for p in self.points)
        target_sum = passing_percent * self.days_total
        need = (target_sum - current_sum) / self.days_without_point
        return max(0.0, min(100.0, need))

    @property
    def is_passing(self) -> bool:
        avg = self.average
        return avg is not None and avg >= 50.0


class SmartNationClient:
    def __init__(self, iin: str, password: str):
        self.iin = iin
        self.password = password
        self.session = requests.Session()
        self.session.headers.update(HEADERS_COMMON)
        self.csrf_token: str | None = None

    # ---------- логин ----------

    def _fetch_csrf_token(self, url: str = LOGIN_PAGE_URL) -> str:
        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()
        marker = 'name="csrf-token" content="'
        idx = resp.text.find(marker)
        if idx == -1:
            raise LoginError("Не нашёл csrf-token — верстка могла поменяться.")
        start = idx + len(marker)
        end = resp.text.find('"', start)
        return resp.text[start:end]

    def login(self) -> None:
        iin, password = self.iin, self.password
        try:
            self.csrf_token = self._fetch_csrf_token()
            resp = self.session.post(
                LOGIN_POST_URL,
                json={"iin": iin, "password": password, "type": "iin"},
                headers={
                    "X-CSRF-TOKEN": self.csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json",
                    "Referer": LOGIN_PAGE_URL,
                },
                timeout=20,
            )
            if resp.status_code in (401, 403, 422):
                raise LoginError("ИИН или пароль не подошли. Проверь данные и попробуй ещё раз.")
            if not resp.ok:
                raise LoginError(f"SmartNation временно не принимает вход (код {resp.status_code}).")

            check = self.session.get(
                JOURNALS_LIST_URL,
                params={"status": "open"},
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json",
                },
                timeout=20,
            )
            check.raise_for_status()
            if (
                check.url.rstrip("/").endswith("/login")
                or re.search(r'"isAuth"\s*:\s*false', check.text)
            ):
                raise LoginError("SmartNation не подтвердил вход. Проверь ИИН и пароль.")
            try:
                result = resp.json()
            except ValueError:
                result = None
            if isinstance(result, dict) and result.get("error"):
                raise LoginError("SmartNation отклонил вход. Проверь ИИН и пароль.")
        finally:
            self.iin = ""
            self.password = ""

    # ---------- список журналов ----------

    def get_journals_list_raw(self, status: str = "open", save_raw: bool = True) -> Any:
        """
        Тянет HTML страницы списка журналов и достаёт journal-id/name
        из атрибутов <v-journal ...> ПРЯМО НЕТ — список журналов рисует
        vue-table через отдельный XHR. Пробуем JSON сначала, если не
        выйдет — откатываемся на разбор HTML.
        """
        resp = self.session.get(
            JOURNALS_LIST_URL,
            params={"status": status},
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()

        if save_raw:
            _dump("journals_list_raw.json", resp)

        try:
            return resp.json()
        except ValueError:
            return {"_html": resp.text}

    def list_open_journals(self, status: str = "open", save_raw: bool = True) -> list[JournalRef]:
        raw = self.get_journals_list_raw(status, save_raw=save_raw)
        items = _extract_items(raw)

        journals: list[JournalRef] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            jid = item.get("id") or item.get("journal_id")
            name = (
                item.get("name")
                or item.get("journal_name")
                or item.get("discipline_name")
                or item.get("title")
            )
            if jid is None or name is None:
                continue
            try:
                journals.append(JournalRef(journal_id=int(jid), name=str(name), raw=item))
            except (TypeError, ValueError):
                continue

        return journals

    # ---------- таблица оценок по журналу ----------

    def get_journal_table_raw(
        self, journal_id: int, month: int | None = None, save_raw: bool = True
    ) -> Any:
        """
        TODO: подтвердить реальные query-параметры фильтра по месяцу
        (кандидаты: month, date_from/date_to, period). Сейчас отправляю
        несколько вариантов сразу — сервер должен проигнорировать лишние.
        """
        url = f"{BASE_URL}/ru/sko/control/journals/{journal_id}/load-table"

        params: dict[str, Any] = {}
        if month:
            params.update({
                "month": month,
                "period": month,
            })

        resp = self.session.get(
            url,
            params=params,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
            timeout=20,
        )
        resp.raise_for_status()

        if save_raw:
            _dump(f"journal_{journal_id}_table_raw.json", resp)

        try:
            return resp.json()
        except ValueError:
            return {"_html": resp.text}

    def get_contingent_raw(self, journal_id: int, save_raw: bool = True) -> Any:
        url = f"{BASE_URL}/ru/sko/control/journals/{journal_id}/contingent"
        resp = self.session.get(
            url,
            headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        if save_raw:
            _dump(f"journal_{journal_id}_contingent_raw.json", resp)
        try:
            return resp.json()
        except ValueError:
            return {"_html": resp.text}

    def get_student_names(self, journal_id: int) -> dict[str, str]:
        """
        Пытается достать {student_id: ФИО} из /contingent. Формат этого
        эндпоинта ещё не подтверждён (TODO), поэтому разбор максимально
        гибкий по именам ключей; если не получится — вернёт {}, и в
        таблице просто останутся ID вместо имён.
        """
        raw = self.get_contingent_raw(journal_id)
        items = _extract_items(raw)
        names: dict[str, str] = {}

        for item in items:
            if not isinstance(item, dict):
                continue
            sid = (
                item.get("id")
                or item.get("contingent_id")
                or item.get("student_id")
            )
            name = (
                item.get("name")
                or item.get("full_name")
                or item.get("fio")
                or item.get("student_name")
            )
            if sid is None and isinstance(item.get("contingent"), dict):
                sid = item["contingent"].get("id")
                name = name or item["contingent"].get("name")
            if sid is not None and name:
                names[str(sid)] = str(name)

        return names

    def get_calc_result_raw(self, journal_id: int) -> Any:
        """Сайт сам считает итог — пробуем забрать готовый расчёт."""
        url = f"{BASE_URL}/ru/sko/control/journals/{journal_id}/calc-result/data"
        resp = self.session.get(
            url,
            headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        _dump(f"journal_{journal_id}_calc_result_raw.json", resp)
        try:
            return resp.json()
        except ValueError:
            return {"_html": resp.text}


# ---------- generic-разбор, пока точная схема не подтверждена ----------

def _dump(filename: str, resp: requests.Response) -> None:
    """Сохраняет сырой ответ на диск — для отладки формата."""
    try:
        data = resp.json()
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except ValueError:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(resp.text)


def _extract_items(raw: Any) -> list[Any]:
    """Достаёт список записей из ответа независимо от обёртки
    (data / items / rows / сам список)."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("data", "items", "rows", "journals", "result"):
            if key in raw and isinstance(raw[key], list):
                return raw[key]
    return []


def parse_journal_days(raw: Any, month: int | None = None) -> list[dict[str, Any]]:
    """
    Достаёт список занятий из ответа load-table (ключ "days").
    Фильтр по месяцу — на своей стороне, по "label" вида "dd.mm".
    """
    days = raw.get("days", []) if isinstance(raw, dict) else []
    if not month:
        return days

    suffix = f".{month:02d}"
    return [d for d in days if isinstance(d.get("label"), str) and d["label"].endswith(suffix)]


def build_student_summaries(
    days: list[dict[str, Any]],
    names: dict[str, str] | None = None,
) -> list[StudentSummary]:
    """
    Строит сводку по студентам из списка занятий (как вернул
    parse_journal_days). points в каждом дне — dict {student_id: {...}}
    или [] (пустой список), если в этот день ещё никого не оценили.
    """
    names = names or {}
    summaries: dict[str, StudentSummary] = {}

    def _get(sid: str) -> StudentSummary:
        if sid not in summaries:
            summaries[sid] = StudentSummary(student_id=sid, name=names.get(sid, sid))
        return summaries[sid]

    # Сначала учтём всех студентов, которые хоть раз встретились (из names
    # и из любых points), чтобы не терять тех, у кого пока 0 оценок.
    for sid in names:
        _get(sid)

    for day in days:
        points = day.get("points")
        label = day.get("label", "")
        theme = day.get("theme", "")

        if isinstance(points, dict) and points:
            graded_ids = set(points.keys())
            for sid, p in points.items():
                s = _get(sid)
                s.days_total += 1
                if p.get("absented"):
                    # отсутствие не считаем как оценённое занятие ни в плюс, ни в минус к среднему
                    continue
                point_val = p.get("point")
                if point_val is None:
                    s.days_without_point += 1
                    continue
                s.days_with_point += 1
                s.points.append(DayPoint(
                    date_label=label,
                    theme=theme,
                    point=float(point_val),
                    failed=bool(p.get("failed")),
                    absented=bool(p.get("absented")),
                ))
            # Студенты, которые есть в names, но не оценены в этот конкретный день —
            # тоже засчитываем день как "ещё не оценённый" для них, если known.
            for sid in names:
                if sid not in graded_ids:
                    s = _get(sid)
                    s.days_total += 1
                    s.days_without_point += 1
        else:
            # points == [] — занятие прошло/запланировано, но оценок нет вообще ни у кого
            for sid in names:
                s = _get(sid)
                s.days_total += 1
                s.days_without_point += 1

    return list(summaries.values())


def generic_table_from_raw(raw: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """
    Фолбэк-парсер: берёт что угодно похожее на список записей и строит
    (колонки, строки) для отображения, даже если точная схема не известна.
    Это чтобы GUI показывал ХОТЬ ЧТО-ТО, а не пустой экран.
    """
    items = _extract_items(raw)
    rows: list[dict[str, Any]] = [i for i in items if isinstance(i, dict)]

    columns: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in columns and not isinstance(row[key], (dict, list)):
                columns.append(key)

    return columns, rows


# ---------- точка входа (CLI-режим, для быстрой проверки без GUI) ----------

def main() -> int:
    iin = os.environ.get("SN_IIN") or input("ИИН: ").strip()
    password = os.environ.get("SN_PASSWORD") or getpass.getpass("Пароль: ")

    client = SmartNationClient(iin, password)
    try:
        client.login()
    except LoginError as e:
        print(f"[error] {e}")
        return 1

    journals = client.list_open_journals()
    if not journals:
        print("[!] Список журналов пуст или не распознан — смотри journals_list_raw.json")
        return 1

    print("\nЖурналы:")
    for j in journals:
        print(f"  [{j.journal_id}] {j.name}")

    jid = journals[0].journal_id
    raw_table = client.get_journal_table_raw(jid)

    if isinstance(raw_table, dict) and "days" in raw_table:
        names = client.get_student_names(jid)
        days = parse_journal_days(raw_table)
        summaries = build_student_summaries(days, names)

        print(f"\nЖурнал {jid}: {len(days)} занятий, {len(summaries)} студентов")
        print(f"{'Студент':<30} {'Ср. %':>8} {'Нужно на ост.':>14}")
        print("-" * 56)
        for s in sorted(summaries, key=lambda s: (s.average is None, s.average or 0)):
            avg = f"{s.average:.1f}" if s.average is not None else "—"
            need = s.needed_average_on_remaining()
            need_s = f"{need:.1f}" if need is not None else "—"
            print(f"{s.name:<30} {avg:>8} {need_s:>14}")
    else:
        columns, rows = generic_table_from_raw(raw_table)
        print(f"\nТаблица журнала {jid}: {len(rows)} строк, колонки: {columns}")
        print(f"(полный ответ сохранён в journal_{jid}_table_raw.json)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

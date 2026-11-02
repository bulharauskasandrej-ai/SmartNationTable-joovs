#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
student_grades_gui.py

GUI (tkinter) для student_grades.py.
Вход -> список журналов -> выбор журнала и месяца -> таблица оценок.

Запуск:
    pip install requests matplotlib
    python student_grades_gui.py
"""

from __future__ import annotations

import threading
import traceback
import tkinter as tk
from tkinter import ttk, messagebox

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from student_grades import (
    SmartNationClient,
    LoginError,
    JournalRef,
    StudentSummary,
    generic_table_from_raw,
    parse_journal_days,
    build_student_summaries,
    MONTHS_RU,
)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Сборщик оценок — SmartNation College")
        self.geometry("1040x680")
        self.minsize(820, 560)

        self.client: SmartNationClient | None = None
        self.journals: list[JournalRef] = []

        self._build_login_bar()
        self._build_journal_bar()
        self._build_body()
        self._build_statusbar()

        self._set_journal_controls_enabled(False)

    # ---------- UI: логин ----------

    def _build_login_bar(self) -> None:
        bar = ttk.Frame(self, padding=10)
        bar.pack(fill="x")

        ttk.Label(bar, text="ИИН:").grid(row=0, column=0, padx=(0, 6))
        self.iin_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.iin_var, width=18).grid(row=0, column=1, padx=(0, 16))

        ttk.Label(bar, text="Пароль:").grid(row=0, column=2, padx=(0, 6))
        self.pwd_var = tk.StringVar()
        self.pwd_entry = ttk.Entry(bar, textvariable=self.pwd_var, show="*", width=18)
        self.pwd_entry.grid(row=0, column=3, padx=(0, 6))

        self.show_pwd_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bar, text="показать", variable=self.show_pwd_var, command=self._toggle_pwd
        ).grid(row=0, column=4, padx=(0, 16))

        self.login_btn = ttk.Button(bar, text="Войти", command=self._on_login_click)
        self.login_btn.grid(row=0, column=5, padx=(0, 6))

        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=120)
        self.progress.grid(row=0, column=6, padx=(10, 0))

        self.pwd_entry.bind("<Return>", lambda e: self._on_login_click())

    def _toggle_pwd(self) -> None:
        self.pwd_entry.config(show="" if self.show_pwd_var.get() else "*")

    # ---------- UI: выбор журнала/месяца ----------

    def _build_journal_bar(self) -> None:
        bar = ttk.Frame(self, padding=(10, 0, 10, 10))
        bar.pack(fill="x")

        ttk.Label(bar, text="Журнал:").grid(row=0, column=0, padx=(0, 6))
        self.journal_var = tk.StringVar()
        self.journal_combo = ttk.Combobox(bar, textvariable=self.journal_var, width=55, state="readonly")
        self.journal_combo.grid(row=0, column=1, padx=(0, 16))

        ttk.Label(bar, text="Месяц:").grid(row=0, column=2, padx=(0, 6))
        self.month_var = tk.StringVar(value="Все")
        self.month_combo = ttk.Combobox(
            bar, textvariable=self.month_var, width=12, state="readonly",
            values=["Все"] + MONTHS_RU,
        )
        self.month_combo.grid(row=0, column=3, padx=(0, 16))

        self.load_btn = ttk.Button(bar, text="Загрузить оценки", command=self._on_load_click)
        self.load_btn.grid(row=0, column=4)

    def _set_journal_controls_enabled(self, enabled: bool) -> None:
        state = "readonly" if enabled else "disabled"
        self.journal_combo.config(state=state)
        self.month_combo.config(state=state)
        self.load_btn.config(state="normal" if enabled else "disabled")

    # ---------- UI: тело (таблица + график) ----------

    def _build_body(self) -> None:
        body = ttk.Frame(self, padding=(10, 0, 10, 10))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(left, show="headings")
        self.tree.grid(row=0, column=0, sticky="nsew")

        vscroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        hscroll = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll.grid(row=1, column=0, sticky="ew")

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self.figure = Figure(figsize=(5, 4), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=right)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

    def _build_statusbar(self) -> None:
        self.status_var = tk.StringVar(value="Введи ИИН и пароль, нажми «Войти».")
        bar = ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w", padding=4)
        bar.pack(fill="x", side="bottom")

    # ---------- busy state ----------

    def _set_busy(self, busy: bool) -> None:
        self.login_btn.config(state="disabled" if busy else "normal")
        self.load_btn.config(state="disabled" if busy else "normal")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    # ---------- логин ----------

    def _on_login_click(self) -> None:
        iin = self.iin_var.get().strip()
        password = self.pwd_var.get()
        if not iin or not password:
            messagebox.showwarning("Не хватает данных", "Введи ИИН и пароль.")
            return

        self._set_busy(True)
        self.status_var.set("Вхожу и загружаю список журналов...")
        threading.Thread(target=self._login_worker, args=(iin, password), daemon=True).start()

    def _login_worker(self, iin: str, password: str) -> None:
        try:
            client = SmartNationClient(iin, password)
            client.login()
            journals = client.list_open_journals()
        except LoginError as e:
            self.after(0, lambda: self._on_error("Ошибка входа", str(e)))
            return
        except Exception as e:
            self.after(0, lambda: self._on_error("Ошибка", f"{e}\n\n{traceback.format_exc(limit=3)}"))
            return

        self.client = client
        self.journals = journals
        self.after(0, self._on_logged_in)

    def _on_logged_in(self) -> None:
        self._set_busy(False)
        if not self.journals:
            self.status_var.set("Вошёл, но список журналов пуст/не распознан — смотри journals_list_raw.json")
            messagebox.showinfo(
                "Нужна донастройка",
                "Вход прошёл, но не смог разобрать список журналов.\n"
                "Файл journals_list_raw.json сохранён рядом со скриптом — "
                "пришли его содержимое, чтобы доделать парсер.",
            )
            return

        self.journal_combo["values"] = [f"{j.journal_id} — {j.name}" for j in self.journals]
        self.journal_combo.current(0)
        self._set_journal_controls_enabled(True)
        self.status_var.set(f"Вошёл. Журналов найдено: {len(self.journals)}. Выбери журнал и нажми «Загрузить».")

    # ---------- загрузка таблицы ----------

    def _on_load_click(self) -> None:
        idx = self.journal_combo.current()
        if idx < 0 or not self.client:
            return
        journal = self.journals[idx]

        month_name = self.month_var.get()
        month_num = None
        if month_name and month_name != "Все":
            month_num = MONTHS_RU.index(month_name) + 1

        self._set_busy(True)
        self.status_var.set(f"Загружаю «{journal.name}»...")
        threading.Thread(target=self._load_worker, args=(journal, month_num), daemon=True).start()

    def _load_worker(self, journal: JournalRef, month_num: int | None) -> None:
        try:
            raw = self.client.get_journal_table_raw(journal.journal_id)
        except Exception as e:
            self.after(0, lambda: self._on_error("Ошибка загрузки", f"{e}\n\n{traceback.format_exc(limit=3)}"))
            return

        if isinstance(raw, dict) and "days" in raw:
            try:
                names = self.client.get_student_names(journal.journal_id)
            except Exception:
                names = {}
            days = parse_journal_days(raw, month_num)
            summaries = build_student_summaries(days, names)
            self.after(0, lambda: self._on_summaries_loaded(journal, summaries))
        else:
            columns, rows = generic_table_from_raw(raw)
            self.after(0, lambda: self._on_table_loaded(journal, columns, rows))

    # ---- отображение по студентам (реальная схема load-table) ----

    def _on_summaries_loaded(self, journal: JournalRef, summaries: list[StudentSummary]) -> None:
        self._set_busy(False)

        if not summaries:
            self.status_var.set(f"Журнал «{journal.name}»: занятий/студентов в выборке не нашлось (месяц пустой?).")
            self._clear_table()
            self.ax.clear()
            self.canvas.draw()
            return

        summaries = sorted(summaries, key=lambda s: (s.average is None, s.average or 0))
        self.status_var.set(f"Журнал «{journal.name}»: {len(summaries)} студентов.")

        columns = ["Студент", "Ср. %", "Дней с оценкой", "Нужно на ост.", "Статус"]
        self._clear_table()
        self.tree["columns"] = columns
        for col in columns:
            self.tree.heading(col, text=col)
            width = 220 if col == "Студент" else 110
            self.tree.column(col, width=width, anchor="center" if col != "Студент" else "w")

        for s in summaries:
            avg = f"{s.average:.1f}" if s.average is not None else "—"
            need = s.needed_average_on_remaining()
            need_s = f"{need:.1f}" if need is not None else "—"
            status = "OK" if s.is_passing else ("риск" if s.average is not None else "нет оценок")
            self.tree.insert("", "end", values=(s.name, avg, s.days_with_point, need_s, status))

        self._draw_student_chart(summaries)

    def _draw_student_chart(self, summaries: list[StudentSummary]) -> None:
        self.ax.clear()
        with_avg = [s for s in summaries if s.average is not None]
        if not with_avg:
            self.ax.text(0.5, 0.5, "Нет оценок для графика", ha="center", va="center")
            self.canvas.draw()
            return

        names = [s.name for s in with_avg]
        avgs = [s.average for s in with_avg]
        colors = ["#4CAF50" if a >= 50 else "#FF7043" for a in avgs]

        x = range(len(names))
        self.ax.bar(x, avgs, color=colors)
        self.ax.axhline(y=50, color="grey", linestyle="--", linewidth=1, label="Порог (50%)")
        self.ax.set_xticks(list(x))
        self.ax.set_xticklabels(names, rotation=45, ha="right", fontsize=7)
        self.ax.set_ylabel("Средний %")
        self.ax.legend(fontsize=8)
        self.figure.tight_layout()
        self.canvas.draw()

    # ---- фолбэк: сырая таблица, когда схема не "days" ----

    def _on_table_loaded(self, journal: JournalRef, columns: list[str], rows: list[dict]) -> None:
        self._set_busy(False)

        if not rows:
            self.status_var.set(
                f"Журнал «{journal.name}»: сервер ответил, но строк не нашлось — "
                f"смотри journal_{journal.journal_id}_table_raw.json рядом со скриптом."
            )
            self._clear_table()
            self.ax.clear()
            self.canvas.draw()
            return

        self.status_var.set(f"Журнал «{journal.name}»: {len(rows)} строк, {len(columns)} колонок.")
        self._fill_table(columns, rows)
        self._draw_chart(columns, rows)

    def _clear_table(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.tree["columns"] = []

    def _fill_table(self, columns: list[str], rows: list[dict]) -> None:
        self._clear_table()
        self.tree["columns"] = columns
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=max(80, min(220, 10 + 8 * len(col))), anchor="center")

        for row in rows:
            values = [row.get(c, "") for c in columns]
            self.tree.insert("", "end", values=values)

    def _draw_chart(self, columns: list[str], rows: list[dict]) -> None:
        self.ax.clear()

        # Пытаемся найти числовые колонки — строим простой средний балл по строкам.
        numeric_cols = []
        for c in columns:
            sample = [r.get(c) for r in rows if r.get(c) not in (None, "")]
            if sample and all(_is_number(v) for v in sample):
                numeric_cols.append(c)

        if not numeric_cols:
            self.ax.text(0.5, 0.5, "Нет числовых колонок для графика", ha="center", va="center")
            self.canvas.draw()
            return

        # Берём первую текстовую колонку как подпись строки (обычно ФИО студента).
        label_col = next((c for c in columns if c not in numeric_cols), None)
        labels = [str(r.get(label_col, i)) for i, r in enumerate(rows)] if label_col else [str(i) for i in range(len(rows))]

        main_col = numeric_cols[0]
        values = [float(r.get(main_col, 0) or 0) for r in rows]

        x = range(len(labels))
        self.ax.bar(x, values, color="#4CAF50")
        self.ax.set_xticks(list(x))
        self.ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
        self.ax.set_ylabel(main_col)
        self.ax.set_title(f"{main_col} по строкам")
        self.figure.tight_layout()
        self.canvas.draw()

    def _on_error(self, title: str, text: str) -> None:
        self._set_busy(False)
        self.status_var.set("Ошибка — подробности в окне.")
        messagebox.showerror(title, text)


def _is_number(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()

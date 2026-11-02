const MONTHS = [
  "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
  "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
];
const PASSING_SCORE = 50;
const state = { journals: [], students: {}, months: [], subjects: [] };
const elements = {
  studentName: document.querySelector("#student-name"),
  search: document.querySelector("#search-filter"),
  gradesMonth: document.querySelector("#grades-month-filter"),
  tableScroll: document.querySelector("#table-scroll"),
  table: document.querySelector("#table-content"),
  empty: document.querySelector("#empty-state"),
  error: document.querySelector("#error-state"),
  passed: document.querySelector("#passed-value"),
  warning: document.querySelector("#warning-value"),
  warningCaption: document.querySelector("#warning-caption"),
  average: document.querySelector("#average-value"),
  subjectCount: document.querySelector("#subject-count"),
  warningBanner: document.querySelector("#warning-banner"),
  loginScreen: document.querySelector("#login-screen"),
  dashboard: document.querySelector("#dashboard"),
  footer: document.querySelector("#footer"),
  loginForm: document.querySelector("#login-form"),
  iin: document.querySelector("#iin-input"),
  password: document.querySelector("#password-input"),
  loginSubmit: document.querySelector("#login-submit"),
  loginError: document.querySelector("#login-error"),
  logout: document.querySelector("#logout-button"),
};

function parseDate(date) {
  const match = /^(\d{1,2})\.(\d{1,2})$/.exec(date);
  if (!match) return null;
  const day = Number(match[1]);
  const month = Number(match[2]);
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  return { day, month, order: month * 100 + day };
}

function escapeHTML(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function numericValue(score) {
  if (score.absent || score.uncertified || score.value === null || score.value === undefined) return null;
  const value = Number(score.value);
  return Number.isFinite(value) ? value : null;
}

function buildSubjects() {
  const studentId = Object.keys(state.students)[0];
  const subjects = [];

  for (const journal of state.journals) {
    const months = new Map(state.months.map((month) => [month, { entries: [], attestations: [] }]));
    for (const day of journal.days) {
      const studentScore = day.points.find((point) => point.studentId === studentId);
      if (!studentScore) continue;

      if (day.isAttestation && months.has(day.attestationMonth)) {
        months.get(day.attestationMonth).attestations.push({
          score: numericValue(studentScore),
          label: studentScore.label,
          uncertified: studentScore.uncertified,
        });
        continue;
      }

      const date = parseDate(day.date);
      if (!date || !months.has(date.month)) continue;
      months.get(date.month).entries.push({
        date: day.date,
        dateParts: date,
        theme: day.theme,
        score: numericValue(studentScore),
        label: studentScore.label,
        absent: studentScore.absent,
      });
    }

    const attestations = [...months.entries()].map(([month, data]) => ({
      month,
      entries: data.entries,
      attestation: data.attestations[data.attestations.length - 1] || null,
    }));
    subjects.push({ id: journal.id, name: journal.name, attestations });
  }

  return subjects;
}

function getWarnings(subjects) {
  const warnings = [];
  for (const subject of subjects) {
    for (const month of subject.attestations) {
      if (!month.attestation) {
        warnings.push({ subject: subject.name, month: MONTHS[month.month], message: "нет аттестации" });
      } else if (month.attestation.score === null) {
        warnings.push({ subject: subject.name, month: MONTHS[month.month], message: "нет балла аттестации" });
      } else if (month.attestation.score < PASSING_SCORE) {
        warnings.push({
          subject: subject.name,
          month: MONTHS[month.month],
          message: `${formatScore(month.attestation.score)} баллов — меньше ${PASSING_SCORE}`,
        });
      }
    }
  }
  return warnings;
}

function formatScore(value) {
  return Number.isInteger(value) ? String(value) : value.toFixed(1).replace(".", ",");
}

function attestationCard(attestation) {
  if (!attestation) {
    return `<div class="attestation-card attestation-missing">
      <div class="attestation-letter">А</div><div class="attestation-rule"></div>
      <div class="attestation-bottom"><span class="attestation-score">—</span><span class="attestation-badge badge-missing">Нет аттестации</span></div>
    </div>`;
  }
  if (attestation.score === null) {
    return `<div class="attestation-card attestation-missing">
      <div class="attestation-letter">А</div><div class="attestation-rule"></div>
      <div class="attestation-bottom"><span class="attestation-score">—</span><span class="attestation-badge badge-missing">Нет балла</span></div>
    </div>`;
  }
  const passed = attestation.score >= PASSING_SCORE;
  return `<div class="attestation-card ${passed ? "attestation-passed" : "attestation-failed"}">
    <div class="attestation-letter">А</div><div class="attestation-rule"></div>
    <div class="attestation-bottom"><span class="attestation-score">${formatScore(attestation.score)}</span><span class="attestation-badge ${passed ? "badge-passed" : "badge-failed"}">${passed ? "Зачёт есть" : "Зачёта нет"}</span></div>
  </div>`;
}

function gradeWord(count) {
  const lastTwo = count % 100;
  const last = count % 10;
  if (lastTwo >= 11 && lastTwo <= 14) return "оценок";
  if (last === 1) return "оценка";
  if (last >= 2 && last <= 4) return "оценки";
  return "оценок";
}

function attestationWord(count) {
  const lastTwo = count % 100;
  const last = count % 10;
  if (lastTwo >= 11 && lastTwo <= 14) return "аттестаций";
  if (last === 1) return "аттестация";
  if (last >= 2 && last <= 4) return "аттестации";
  return "аттестаций";
}

function renderGradeDetails(subject) {
  const selectedMonth = elements.gradesMonth.value;
  const grades = subject.attestations.flatMap((month) => month.entries
    .map((entry) => ({ ...entry, month: month.month })))
    .filter((grade) => selectedMonth === "all" || grade.month === Number(selectedMonth))
    .sort((left, right) => left.dateParts.order - right.dateParts.order);
  if (!grades.length) {
    const period = selectedMonth === "all" ? "за все месяцы" : `за ${MONTHS[Number(selectedMonth)]}`;
    return `<div class="no-grades">Оценок ${period} пока нет.</div>`;
  }
  return `<div class="grade-details-list">${grades.map((grade) => {
    let score = grade.score === null
      ? grade.absent ? "Н" : grade.label || "—"
      : `${formatScore(grade.score)} б.`;
    const isLow = grade.score !== null && grade.score < PASSING_SCORE;
    return `<div class="grade-detail">
      <span class="detail-date">${grade.date}</span>
      <span class="detail-month">${MONTHS[grade.month]}</span>
      <span class="detail-theme" title="${escapeHTML(grade.theme)}">${escapeHTML(grade.theme)}</span>
      <span class="detail-score ${isLow ? "detail-score-low" : ""}">${escapeHTML(score)}</span>
    </div>`;
  }).join("")}</div>`;
}

function render() {
  const query = elements.search.value.trim().toLocaleLowerCase("ru");
  const visibleSubjects = state.subjects.filter((subject) => subject.name.toLocaleLowerCase("ru").includes(query));
  const warnings = getWarnings(state.subjects);
  const attestations = state.subjects.flatMap((subject) => subject.attestations);
  const passedCount = attestations.filter((item) => item.attestation?.score >= PASSING_SCORE).length;
  const recordedAttestations = attestations
    .map((item) => item.attestation?.score)
    .filter((score) => score !== null && score !== undefined);
  const average = recordedAttestations.length
    ? recordedAttestations.reduce((sum, score) => sum + score, 0) / recordedAttestations.length
    : null;

  elements.passed.textContent = `${passedCount} / ${attestations.length}`;
  elements.warning.textContent = String(warnings.length);
  elements.warningCaption.textContent = warnings.length
    ? "нет аттестации, нет балла или меньше 50"
    : "все аттестации от 50 баллов";
  elements.average.textContent = average === null ? "—" : formatScore(average);
  elements.subjectCount.textContent = `(${visibleSubjects.length})`;
  elements.table.style.setProperty("--month-count", state.months.length);
  elements.table.innerHTML = `
    <div class="table-header">
      <div class="subject-heading">Предмет</div>
      ${state.months.map((month) => `<div>${MONTHS[month]}</div>`).join("")}
      <div>Просмотр</div>
    </div>
    ${visibleSubjects.map((subject, index) => {
      const monthlyCells = subject.attestations.map((month) =>
        `<div class="month-cell">${attestationCard(month.attestation)}</div>`).join("");
      const matchingMonths = elements.gradesMonth.value === "all"
        ? subject.attestations
        : subject.attestations.filter((month) => month.month === Number(elements.gradesMonth.value));
      const grades = matchingMonths.reduce((count, month) => count + month.entries.length, 0);
      return `<article class="subject-row" style="animation-delay:${Math.min(index, 10) * 35}ms">
        <div class="subject-name" title="${escapeHTML(subject.name)}"><span>${escapeHTML(subject.name)}</span></div>
        ${monthlyCells}
        <details class="subject-details">
          <summary><span>Оценки</span><span class="grade-count">${grades}</span></summary>
          <div class="details-panel"><div class="details-heading">${escapeHTML(subject.name)} <span>· ${grades} ${gradeWord(grades)}</span></div>${renderGradeDetails(subject)}</div>
        </details>
      </article>`;
    }).join("")}`;
  elements.tableScroll.hidden = visibleSubjects.length === 0;
  elements.empty.hidden = visibleSubjects.length !== 0;

  elements.warningBanner.hidden = warnings.length === 0;
  elements.warningBanner.innerHTML = warnings.length
    ? `<strong>Проверьте ${warnings.length} ${attestationWord(warnings.length)}.</strong><details class="warning-details"><summary>Показать список</summary><ul>${warnings.map((item) => `<li>${escapeHTML(item.subject)} · ${item.month}: ${escapeHTML(item.message)}</li>`).join("")}</ul></details>`
    : "";
}

function populateMonths() {
  const months = new Set();
  for (const journal of state.journals) {
    for (const day of journal.days) {
      const date = parseDate(day.date);
      if (date) months.add(date.month);
    }
  }
  state.months = [...months].sort((left, right) => left - right);
  elements.gradesMonth.innerHTML = `<option value="all">Все месяцы</option>${state.months
    .map((month) => `<option value="${month}">${MONTHS[month]}</option>`).join("")}`;
}

function setAuthenticatedView(authenticated) {
  elements.loginScreen.hidden = authenticated;
  elements.dashboard.hidden = !authenticated;
  elements.footer.hidden = !authenticated;
  elements.logout.hidden = !authenticated;
}

async function loadGrades() {
  try {
    const response = await fetch("/api/grades");
    const result = await response.json().catch(() => ({}));
    if (response.status === 401) {
      setAuthenticatedView(false);
      return;
    }
    if (!response.ok) throw new Error(result.error || `Ошибка загрузки данных (${response.status}).`);
    state.journals = result.journals || [];
    state.students = result.students || {};
    const studentIds = Object.keys(state.students);
    if (studentIds.length !== 1) {
      throw new Error("Не удалось однозначно определить твой профиль. Чужие оценки не показаны.");
    }
    if (!state.journals.length) throw new Error("SmartNation не вернул журналы с оценками.");
    elements.studentName.textContent = state.students[studentIds[0]];
    populateMonths();
    state.subjects = buildSubjects();
    render();
  } catch (error) {
    elements.error.textContent = error.message || "Не удалось загрузить оценки.";
    elements.error.hidden = false;
  }
}

elements.search.addEventListener("input", render);
elements.gradesMonth.addEventListener("change", render);

elements.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  elements.loginError.hidden = true;
  elements.loginSubmit.disabled = true;
  elements.loginSubmit.classList.add("is-loading");
  const credentials = {
    iin: elements.iin.value.trim(),
    password: elements.password.value,
  };
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(credentials),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "Не удалось войти в SmartNation.");
    elements.iin.value = "";
    elements.studentName.textContent = result.name;
    setAuthenticatedView(true);
    await loadGrades();
  } catch (error) {
    elements.loginError.textContent = error.message || "Не удалось войти в SmartNation.";
    elements.loginError.hidden = false;
  } finally {
    credentials.password = "";
    elements.password.value = "";
    elements.loginSubmit.disabled = false;
    elements.loginSubmit.classList.remove("is-loading");
  }
});

elements.logout.addEventListener("click", async () => {
  elements.logout.disabled = true;
  try {
    const response = await fetch("/api/logout", { method: "POST" });
    if (!response.ok) throw new Error("Не удалось завершить сеанс.");
    state.journals = [];
    state.subjects = [];
    state.students = {};
    elements.table.innerHTML = "";
    setAuthenticatedView(false);
    elements.iin.focus();
  } catch (error) {
    elements.error.textContent = error.message || "Не удалось завершить сеанс.";
    elements.error.hidden = false;
  } finally {
    elements.logout.disabled = false;
  }
});

const themeButton = document.querySelector("#theme-button");
const savedTheme = localStorage.getItem("grades-theme");
document.documentElement.dataset.theme = savedTheme === "light" ? "light" : "dark";
function updateThemeButton() {
  const isDark = document.documentElement.dataset.theme === "dark";
  themeButton.textContent = isDark ? "☼" : "☾";
  themeButton.setAttribute("aria-label", isDark ? "Включить светлую тему" : "Включить тёмную тему");
}
themeButton.addEventListener("click", () => {
  const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = nextTheme;
  localStorage.setItem("grades-theme", nextTheme);
  updateThemeButton();
});
updateThemeButton();

async function restoreSession() {
  setAuthenticatedView(false);
  try {
    const response = await fetch("/api/session");
    const result = await response.json();
    if (!response.ok || !result.authenticated) return;
    setAuthenticatedView(true);
    elements.studentName.textContent = result.name;
    await loadGrades();
  } catch (error) {
    elements.loginError.textContent = error.message || "Не удалось проверить вход.";
    elements.loginError.hidden = false;
  }
}

restoreSession();

(function () {
  'use strict';
  const page = document.querySelector('.student-search-page');
  if (!page) return;

  const input = document.getElementById('student-search-input');
  const results = document.getElementById('student-search-results');
  const status = document.getElementById('student-search-status');
  const spinner = document.getElementById('student-search-spinner');
  const selection = document.getElementById('student-selection');
  const csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
  let timer;
  let controller;

  const escapeHtml = (value) => {
    const node = document.createElement('div');
    node.textContent = value || 'Not available';
    return node.innerHTML;
  };

  function renderResults(items) {
    results.innerHTML = items.map((student) => `
      <div class="student-result" role="option">
        <button type="button" class="student-result-select" data-student-id="${student.id}">
          <span class="student-result-name">${escapeHtml(student.name)}</span>
          <span class="student-result-meta">
            <span><strong>Email:</strong> ${escapeHtml(student.email)}</span>
            <span><strong>Application:</strong> ${escapeHtml(student.application_number)}</span>
            <span><strong>Matric:</strong> ${escapeHtml(student.matriculation_number)}</span>
            <span><strong>Programme:</strong> ${escapeHtml(student.programme)}</span>
            <span><strong>Level:</strong> ${escapeHtml(student.level)}</span>
          </span>
        </button>
        <a class="student-result-action" href="${escapeHtml(student.correction_url)}">Change Level / Admitted Session</a>
      </div>`).join('');
    input.setAttribute('aria-expanded', items.length ? 'true' : 'false');
  }

  async function search() {
    const query = input.value.trim();
    if (controller) controller.abort();
    if (!query) {
      renderResults([]);
      status.textContent = 'Enter a name, email, application number, or matriculation number.';
      spinner.hidden = true;
      return;
    }
    controller = new AbortController();
    spinner.hidden = false;
    status.textContent = 'Searching…';
    try {
      const response = await fetch(`${page.dataset.resultsUrl}?q=${encodeURIComponent(query)}`, {
        headers: {'X-Requested-With': 'XMLHttpRequest'}, signal: controller.signal
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Search could not be completed.');
      renderResults(data.results);
      status.textContent = data.message || `${data.results.length} student${data.results.length === 1 ? '' : 's'} found. Select one to continue.`;
    } catch (error) {
      if (error.name !== 'AbortError') { renderResults([]); status.textContent = error.message; }
    } finally {
      if (!controller || !controller.signal.aborted) spinner.hidden = true;
    }
  }

  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(search, 250); });
  document.getElementById('student-search-form').addEventListener('submit', (event) => { event.preventDefault(); clearTimeout(timer); search(); });

  results.addEventListener('click', async (event) => {
    const button = event.target.closest('.student-result-select');
    if (!button) return;
    button.disabled = true;
    const body = new URLSearchParams({student_id: button.dataset.studentId});
    try {
      const response = await fetch(page.dataset.selectionUrl, {
        method: 'POST', body,
        headers: {'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest'}
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Student selection failed.');
      const student = data.student;
      selection.innerHTML = `<h2>Selected student</h2><div class="student-selection-grid">
        <div>Name<strong>${escapeHtml(student.name)}</strong></div>
        <div>Email<strong>${escapeHtml(student.email)}</strong></div>
        <div>Application number<strong>${escapeHtml(student.application_number)}</strong></div>
        <div>Matriculation number<strong>${escapeHtml(student.matriculation_number)}</strong></div>
        <div>Programme<strong>${escapeHtml(student.programme)}</strong></div>
        <div>Level / Session<strong>${escapeHtml(student.level)} · ${escapeHtml(student.academic_session)}</strong></div>
      </div><a class="btn btn-outline-primary mt-3" href="${escapeHtml(student.correction_url)}">Change Level / Admitted Session</a>`;
      selection.hidden = false;
      status.textContent = `${student.name} selected.`;
      selection.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    } catch (error) {
      status.textContent = error.message;
    } finally { button.disabled = false; }
  });
})();

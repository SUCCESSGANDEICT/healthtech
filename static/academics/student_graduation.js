(function () {
  'use strict';
  const page = document.querySelector('.graduation-page');
  if (!page) return;

  const form = document.getElementById('graduation-filter-form');
  const resultsCard = document.getElementById('graduation-results-card');
  const results = document.getElementById('graduation-results');
  const loading = document.getElementById('graduation-loading');
  const feedback = document.getElementById('graduation-feedback');
  const search = document.getElementById('graduation-search');
  const queryButton = document.getElementById('graduation-query-button');
  const graduateSelected = document.getElementById('graduate-selected');
  const confirmButton = document.getElementById('confirm-graduation');
  const selectionCount = document.getElementById('graduation-selection-count');
  const csrf = form.querySelector('[name=csrfmiddlewaretoken]').value;
  let selectedIds = [];
  let searchTimer;
  let controller;
  let submitting = false;

  function field(id) { return form.querySelector(`#id_${id}`); }
  function selectedLabel(id) {
    const element = field(id);
    return element.options[element.selectedIndex] ? element.options[element.selectedIndex].text : '';
  }
  function showFeedback(message, kind) {
    feedback.innerHTML = '';
    if (!message) return;
    const alert = document.createElement('div');
    alert.className = `alert alert-${kind || 'info'}`;
    alert.textContent = message;
    feedback.appendChild(alert);
  }
  function resetSelection() {
    selectedIds = [];
    selectionCount.textContent = '0 selected';
    graduateSelected.disabled = true;
  }
  function syncSelection() {
    selectedIds = Array.from(results.querySelectorAll('.student-select:checked')).map((box) => box.value);
    selectionCount.textContent = `${selectedIds.length} selected`;
    graduateSelected.disabled = selectedIds.length === 0 || submitting;
    const selectAll = document.getElementById('select-current-page');
    const eligible = Array.from(results.querySelectorAll('.student-select:not(:disabled)'));
    if (selectAll) {
      selectAll.checked = eligible.length > 0 && eligible.every((box) => box.checked);
      selectAll.indeterminate = eligible.some((box) => box.checked) && !selectAll.checked;
    }
  }
  function contextParams() {
    return new URLSearchParams({
      course: field('course').value,
      level: field('level').value,
      session: field('session').value,
      search: search.value.trim()
    });
  }
  async function loadResults(pageNumber) {
    if (!form.reportValidity()) return;
    if (controller) controller.abort();
    controller = new AbortController();
    const params = contextParams();
    params.set('page', pageNumber || '1');
    resultsCard.hidden = false;
    loading.hidden = false;
    results.hidden = true;
    queryButton.disabled = true;
    resetSelection();
    showFeedback('', 'info');
    try {
      const response = await fetch(`${page.dataset.resultsUrl}?${params.toString()}`, {
        headers: {'X-Requested-With': 'XMLHttpRequest'}, signal: controller.signal
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Students could not be loaded.');
      results.innerHTML = data.html;
      results.hidden = false;
    } catch (error) {
      if (error.name !== 'AbortError') showFeedback(error.message, 'danger');
    } finally {
      if (!controller || !controller.signal.aborted) {
        loading.hidden = true;
        queryButton.disabled = false;
      }
    }
  }
  function openConfirmation(ids, individualName) {
    selectedIds = ids;
    document.getElementById('graduation-modal-count').textContent = individualName
      ? `Graduate ${individualName}`
      : `${ids.length} student${ids.length === 1 ? '' : 's'} selected`;
    document.getElementById('graduation-modal-course').textContent = selectedLabel('course');
    document.getElementById('graduation-modal-level').textContent = selectedLabel('level');
    document.getElementById('graduation-modal-session').textContent = selectedLabel('session');
    window.jQuery('#graduation-confirm-modal').modal('show');
  }
  async function submitGraduation() {
    if (submitting || selectedIds.length === 0) return;
    submitting = true;
    confirmButton.disabled = true;
    graduateSelected.disabled = true;
    const originalText = confirmButton.textContent;
    confirmButton.textContent = 'Processing…';
    const body = contextParams();
    selectedIds.forEach((id) => body.append('student_ids', id));
    try {
      const response = await fetch(page.dataset.graduateUrl, {
        method: 'POST', body,
        headers: {'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest'}
      });
      const data = await response.json();
      if (!response.ok && response.status !== 409) throw new Error(data.error || 'Graduation failed.');
      window.jQuery('#graduation-confirm-modal').modal('hide');
      await loadResults(1);
      const reasons = Array.from(new Set((data.skipped || []).map((item) => item.reason))).join(' ');
      const detail = data.skipped_count ? ` ${data.skipped_count} selection(s) were skipped safely. ${reasons}` : '';
      showFeedback(`${data.message}${detail}`, data.graduated_count ? 'success' : 'warning');
    } catch (error) {
      showFeedback(error.message, 'danger');
    } finally {
      submitting = false;
      confirmButton.disabled = false;
      confirmButton.textContent = originalText;
      syncSelection();
    }
  }

  form.addEventListener('submit', (event) => { event.preventDefault(); loadResults(1); });
  search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadResults(1), 300);
  });
  results.addEventListener('change', (event) => {
    if (event.target.id === 'select-current-page') {
      results.querySelectorAll('.student-select:not(:disabled)').forEach((box) => { box.checked = event.target.checked; });
    }
    if (event.target.matches('.student-select, #select-current-page')) syncSelection();
  });
  results.addEventListener('click', (event) => {
    const pageLink = event.target.closest('.graduation-page-link');
    if (pageLink && !pageLink.closest('.disabled')) loadResults(pageLink.dataset.page);
    const one = event.target.closest('.graduate-one');
    if (one && !one.disabled) openConfirmation([one.dataset.studentId], one.dataset.studentName);
  });
  graduateSelected.addEventListener('click', () => openConfirmation(selectedIds));
  confirmButton.addEventListener('click', submitGraduation);
})();

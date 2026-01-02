document.addEventListener('DOMContentLoaded', () => {
  const salespersonIdInput = document.getElementById('salesPersonId');
  if (!salespersonIdInput) return;

  const salespersonId = salespersonIdInput.value;
  const container = document.getElementById('suggestionsContainer');
  const summary = document.getElementById('suggestionsSummary');
  const loadingState = document.getElementById('suggestionsLoading');
  const refreshBtn = document.getElementById('refreshSuggestions');

  refreshBtn?.addEventListener('click', () => {
    refreshBtn.disabled = true;
    refreshBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Refreshing';
    loadSuggestions().finally(() => {
      refreshBtn.disabled = false;
      refreshBtn.innerHTML = '<i class="bi bi-stars"></i> Refresh Suggestions';
    });
  });

  const formatCurrency = (value) => {
    const num = Number(value || 0);
    if (!Number.isFinite(num)) return '£0';
    return `£${num.toLocaleString('en-GB', { maximumFractionDigits: 0 })}`;
  };

  const escapeHtml = (value) => {
    if (value === undefined || value === null) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  };

  const formatNumber = (value) => {
    const num = Number(value || 0);
    if (!Number.isFinite(num)) return '0';
    return num.toLocaleString('en-GB');
  };

  const relativeDate = (iso) => {
    if (!iso) return 'No contact recorded';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    const diffMs = Date.now() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    if (diffDays < 1) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    return `${diffDays} days ago`;
  };

  const copyToClipboard = async (text, trigger) => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      if (trigger) {
        const original = trigger.innerHTML;
        trigger.innerHTML = '<i class="bi bi-clipboard-check"></i> Copied';
        trigger.classList.remove('btn-outline-secondary');
        trigger.classList.add('btn-success');
        setTimeout(() => {
          trigger.innerHTML = original;
          trigger.classList.remove('btn-success');
          trigger.classList.add('btn-outline-secondary');
        }, 1200);
      }
    } catch (err) {
      console.error('Clipboard error', err);
    }
  };

  const renderSummary = (data) => {
    const suggestions = data?.suggestions || [];
    if (!suggestions.length) {
      summary.innerHTML = '';
      return;
    }
    const generatedAt = data.generated_at
      ? new Date(data.generated_at).toLocaleString()
      : 'just now';
    summary.innerHTML = `
      <div class="alert alert-secondary d-flex justify-content-between align-items-center">
        <div>
          <strong>${suggestions.length}</strong> suggestion${suggestions.length === 1 ? '' : 's'} ranked.
          ${data.cached_news_used ? 'Using cached news where available.' : 'Fetching fresh news for missing customers.'}
        </div>
        <small class="text-muted">Generated at ${generatedAt}</small>
      </div>
    `;
  };

  const renderNews = (newsItems) => {
    if (!newsItems || !newsItems.length) return '<span class="text-muted">No recent news found</span>';
    return newsItems.slice(0, 2).map((item) => `
      <div class="news-chip mb-1">
        <i class="bi bi-newspaper"></i>
        <span>${escapeHtml(item.headline || 'News item')}</span>
      </div>
    `).join('');
  };

  const renderSuggestionCard = (suggestion) => {
    const lastContact = suggestion.last_contact;
    const lastEmail = suggestion.last_email;
    const suggestedEmail = suggestion.suggested_email || {};
    const newsItems = suggestion.news_items || [];
    const contactAge = lastContact?.date ? relativeDate(lastContact.date) : 'No contact recorded';

    const scoreBadgeClass = suggestion.score >= 95
      ? 'bg-danger'
      : suggestion.score >= 80
        ? 'bg-warning text-dark'
        : 'bg-info text-dark';

    return `
      <div class="col-12 col-lg-6">
        <div class="card shadow-sm suggestion-card">
          <div class="card-body d-flex flex-column h-100">
            <div class="d-flex justify-content-between align-items-start mb-2">
              <div>
                <h5 class="mb-0">${escapeHtml(suggestion.customer_name || 'Customer')}</h5>
                <div class="text-muted small">${escapeHtml(suggestion.status || 'Target')} • Last touch: ${escapeHtml(contactAge)}</div>
              </div>
              <span class="badge ${scoreBadgeClass} suggestion-score">${suggestion.score}</span>
            </div>

            <div class="d-flex flex-wrap gap-2 mb-2">
              <span class="metric-pill"><i class="bi bi-cash-stack"></i> ${formatCurrency(suggestion.estimated_revenue)}</span>
              <span class="metric-pill"><i class="bi bi-truck"></i> Fleet ${formatNumber(suggestion.fleet_size)}</span>
              <span class="metric-pill"><i class="bi bi-clock-history"></i> ${
                Number.isFinite(suggestion.score_breakdown?.days_since_contact)
                  ? `${suggestion.score_breakdown.days_since_contact} days stale`
                  : 'No communications yet'
              }</span>
            </div>

            <div class="mb-2">${renderNews(newsItems)}</div>

            <div class="mb-2">
              <div class="fw-semibold">Latest email</div>
              ${lastEmail
                ? `<div class="small text-muted">${escapeHtml(lastEmail.subject || 'Subject unknown')}</div>
                   <div class="small">${escapeHtml(lastEmail.preview || 'No preview available')}</div>`
                : '<div class="text-muted small">No email captured yet</div>'}
            </div>

            <div class="mb-2">
              <div class="fw-semibold d-flex justify-content-between align-items-center">
                <span>Suggested subject</span>
                <button class="btn btn-sm btn-outline-secondary" data-copy-subject>Copy</button>
              </div>
              <div class="form-control bg-white">${escapeHtml(suggestedEmail.subject || 'Subject not available')}</div>
            </div>

            <div class="mb-3">
              <div class="fw-semibold d-flex justify-content-between align-items-center">
                <span>Suggested body</span>
                <button class="btn btn-sm btn-outline-secondary" data-copy-body>Copy</button>
              </div>
              <div class="suggestion-body">${escapeHtml(suggestedEmail.body || 'No draft available').replace(/\n/g, '<br>')}</div>
            </div>

            <div class="mt-auto d-flex justify-content-between align-items-center text-muted small">
              <span>Draft source: ${escapeHtml(suggestedEmail.source || 'openai')}</span>
              <span>Score drivers: rev ${escapeHtml(suggestion.score_breakdown?.revenue_component ?? '-')}, fleet ${escapeHtml(suggestion.score_breakdown?.fleet_component ?? '-')}, recency ${escapeHtml(suggestion.score_breakdown?.recency_component ?? '-')}</span>
            </div>
          </div>
        </div>
      </div>
    `;
  };

  const renderSuggestions = (suggestions) => {
    container.innerHTML = '';
    if (!suggestions || !suggestions.length) {
      container.innerHTML = `
        <div class="col-12">
          <div class="alert alert-light text-center mb-0">
            No eligible target customers with zero spend were found.
          </div>
        </div>
      `;
      return;
    }

    container.innerHTML = suggestions.map(renderSuggestionCard).join('');

    container.querySelectorAll('[data-copy-subject]').forEach((button) => {
      button.addEventListener('click', () => {
        const subject = button.closest('.card-body').querySelector('.form-control')?.textContent || '';
        copyToClipboard(subject.trim(), button);
      });
    });

    container.querySelectorAll('[data-copy-body]').forEach((button) => {
      button.addEventListener('click', () => {
        const bodyHtml = button.closest('.card-body').querySelector('.suggestion-body')?.innerHTML || '';
        const text = bodyHtml.replace(/<br>/g, '\n');
        copyToClipboard(text.trim(), button);
      });
    });
  };

  const loadSuggestions = async () => {
    summary.innerHTML = '';
    if (loadingState) {
      container.innerHTML = '';
      container.appendChild(loadingState);
      loadingState.classList.remove('d-none');
    } else {
      container.innerHTML = '';
    }
    try {
      const response = await fetch(`/salespeople/${salespersonId}/contact-suggestions/data`);
      if (!response.ok) {
        throw new Error('Failed to load suggestions');
      }
      const data = await response.json();
      renderSummary(data);
      renderSuggestions(data.suggestions || []);
    } catch (error) {
      console.error(error);
      container.innerHTML = `
        <div class="col-12">
          <div class="alert alert-danger">Unable to load suggestions. ${error?.message || ''}</div>
        </div>
      `;
    } finally {
      if (loadingState) loadingState.classList.add('d-none');
    }
  };

  loadSuggestions();
});

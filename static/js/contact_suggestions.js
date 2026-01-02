document.addEventListener('DOMContentLoaded', () => {
  const salespersonIdInput = document.getElementById('salesPersonId');
  if (!salespersonIdInput) return;

  const salespersonId = salespersonIdInput.value;
  const container = document.getElementById('suggestionsContainer');
  const summary = document.getElementById('suggestionsSummary');
  const loadingState = document.getElementById('suggestionsLoading');
  const refreshBtn = document.getElementById('refreshSuggestions');

  let currentSuggestions = [];
  let templates = [];
  let templatesLoaded = false;

  const countryTemplateKey = 'contactSuggestionsTemplateByCountry';
  let countryTemplateDefaults = {};
  try {
    countryTemplateDefaults = JSON.parse(localStorage.getItem(countryTemplateKey) || '{}');
  } catch (err) {
    countryTemplateDefaults = {};
  }

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
    if (!Number.isFinite(num)) return 'GBP 0';
    return `GBP ${num.toLocaleString('en-GB', { maximumFractionDigits: 0 })}`;
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

  const getTemplateIdForSuggestion = (suggestion) => {
    const explicit = suggestion.template_id || '';
    if (explicit) return explicit;
    const country = (suggestion.country || '').trim();
    if (!country) return '';
    return countryTemplateDefaults[country] || '';
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

  const renderTemplateOptions = (selectedId) => {
    const options = [`<option value="">No template</option>`];
    if (!templatesLoaded) {
      options.push('<option value="" disabled>Loading templates...</option>');
    } else if (!templates.length) {
      options.push('<option value="" disabled>No templates available</option>');
    } else {
      templates.forEach((template) => {
        const id = String(template.id || '');
        const selected = id && id === String(selectedId) ? 'selected' : '';
        options.push(`<option value="${escapeHtml(id)}" ${selected}>${escapeHtml(template.name || 'Unnamed template')}</option>`);
      });
    }
    return options.join('');
  };

  const renderSuggestionCard = (suggestion) => {
    const lastContact = suggestion.last_contact;
    const lastEmail = suggestion.last_email;
    const graphEmail = suggestion.last_graph_email;
    const suggestedEmail = suggestion.suggested_email || {};
    const newsItems = suggestion.news_items || [];
    const hasSuggestedEmail = Boolean(suggestedEmail?.subject || suggestedEmail?.body);
    const aiGenerated = Boolean(suggestion.ai_generated || hasSuggestedEmail || newsItems.length);
    const aiError = suggestion.ai_error || '';
    const contactAge = lastContact?.date ? relativeDate(lastContact.date) : 'No contact recorded';
    const lastEmailSubject = lastEmail?.subject || lastEmail?.notes || '';
    const lastEmailPreview = lastEmail?.preview || '';
    const latestUpdate = suggestion.latest_update || '';
    const statusLabel = suggestion.status || 'No spend';
    const countryLabel = suggestion.country ? ` | ${suggestion.country}` : '';
    const templateId = getTemplateIdForSuggestion(suggestion);
    const graphEmailAge = graphEmail?.date ? relativeDate(graphEmail.date) : '';

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
                <div class="text-muted small">${escapeHtml(statusLabel)} | Last touch: ${escapeHtml(contactAge)}${escapeHtml(countryLabel)}</div>
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

            <div class="mb-2">
              <label class="form-label small fw-semibold" for="template-${suggestion.customer_id}">AI template</label>
              <select class="form-select form-select-sm" id="template-${suggestion.customer_id}" data-template-select data-customer-id="${suggestion.customer_id}">
                ${renderTemplateOptions(templateId)}
              </select>
            </div>

            <div class="d-flex justify-content-between align-items-center mb-2">
              <button class="btn btn-sm btn-outline-primary" data-generate-ai data-customer-id="${suggestion.customer_id}">
                ${hasSuggestedEmail ? 'Regenerate AI draft' : 'Generate AI draft'}
              </button>
              <span class="text-muted small">${aiGenerated ? 'AI draft ready' : 'AI draft not generated'}</span>
            </div>

            ${aiError ? `<div class="alert alert-warning py-1 mb-2 small">${escapeHtml(aiError)}</div>` : ''}

            <div class="mb-2">
              ${aiGenerated
                ? renderNews(newsItems)
                : '<div class="text-muted small">Generate AI draft to pull news.</div>'}
            </div>

            <div class="mb-2">
              <div class="fw-semibold">Latest update</div>
              ${latestUpdate
                ? `<div class="small">${escapeHtml(latestUpdate)}</div>`
                : '<div class="text-muted small">No updates yet</div>'}
            </div>

            <div class="mb-2">
              <div class="fw-semibold">Latest email</div>
              ${lastEmail
                ? `<div class="small text-muted">${escapeHtml(lastEmailSubject || 'Email captured')}</div>
                   ${lastEmailPreview ? `<div class="small">${escapeHtml(lastEmailPreview)}</div>` : ''}`
                : '<div class="text-muted small">No email captured yet</div>'}
              ${graphEmail ? `
                <div class="small mt-2"><span class="fw-semibold">Graph:</span> ${escapeHtml(graphEmail.subject || 'Email')} ${graphEmailAge ? `<span class="text-muted">(${escapeHtml(graphEmailAge)})</span>` : ''}</div>
                ${graphEmail.preview ? `<div class="small">${escapeHtml(graphEmail.preview)}</div>` : ''}
              ` : ''}
            </div>

            ${hasSuggestedEmail ? `
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
            ` : `
              <div class="text-muted small mb-3">No AI draft yet.</div>
            `}

            <div class="mt-auto d-flex justify-content-between align-items-center text-muted small">
              <span>${hasSuggestedEmail ? `Draft source: ${escapeHtml(suggestedEmail.source || 'openai')}` : ''}</span>
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
            No customers with zero spend were found.
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
        const text = bodyHtml.replace(/<br>/g, '\\n');
        copyToClipboard(text.trim(), button);
      });
    });

    container.querySelectorAll('[data-template-select]').forEach((select) => {
      select.addEventListener('change', () => {
        const customerId = select.getAttribute('data-customer-id');
        const selected = select.value || '';
        const suggestion = currentSuggestions.find((item) => String(item.customer_id) === String(customerId));
        if (suggestion) {
          suggestion.template_id = selected || null;
          const country = (suggestion.country || '').trim();
          if (country && selected) {
            countryTemplateDefaults[country] = selected;
            localStorage.setItem(countryTemplateKey, JSON.stringify(countryTemplateDefaults));
          }
        }
        renderSuggestions(currentSuggestions);
      });
    });

    container.querySelectorAll('[data-generate-ai]').forEach((button) => {
      button.addEventListener('click', () => {
        const customerId = button.getAttribute('data-customer-id');
        generateAiDraft(customerId, button);
      });
    });
  };

  const loadTemplates = async () => {
    try {
      const response = await fetch('/api/email-templates');
      if (!response.ok) {
        throw new Error('Failed to load templates');
      }
      const result = await response.json();
      templates = Array.isArray(result) ? result : (result.templates || result.data || []);
      templatesLoaded = true;
    } catch (error) {
      console.error(error);
      templates = [];
      templatesLoaded = true;
    }
    renderSuggestions(currentSuggestions);
  };

  const generateAiDraft = async (customerId, button) => {
    if (!customerId) return;
    if (button) {
      button.disabled = true;
      button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Generating';
    }

    const suggestion = currentSuggestions.find((item) => String(item.customer_id) === String(customerId));
    const templateId = suggestion ? getTemplateIdForSuggestion(suggestion) : '';

    const payload = { customer_id: customerId };
    if (templateId) payload.template_id = templateId;

    try {
      const response = await fetch(`/salespeople/${salespersonId}/contact-suggestions/ai`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!response.ok) {
        throw new Error('Failed to generate AI draft');
      }
      const data = await response.json();
      const index = currentSuggestions.findIndex((item) => String(item.customer_id) === String(customerId));
      if (index > -1) {
        currentSuggestions[index] = {
          ...currentSuggestions[index],
          suggested_email: data.suggested_email || null,
          news_items: data.news_items || [],
          ai_generated: true,
          ai_error: null
        };
      }
      renderSuggestions(currentSuggestions);
    } catch (error) {
      console.error(error);
      const index = currentSuggestions.findIndex((item) => String(item.customer_id) === String(customerId));
      if (index > -1) {
        currentSuggestions[index] = {
          ...currentSuggestions[index],
          ai_error: error?.message || 'Failed to generate AI draft'
        };
      }
      renderSuggestions(currentSuggestions);
    }
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
      currentSuggestions = data.suggestions || [];
      renderSummary(data);
      renderSuggestions(currentSuggestions);
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

  loadTemplates();
  loadSuggestions();
});

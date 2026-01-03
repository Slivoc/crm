document.addEventListener('DOMContentLoaded', () => {
  const salespersonIdInput = document.getElementById('salesPersonId');
  if (!salespersonIdInput) return;

  const salespersonId = salespersonIdInput.value;
  const container = document.getElementById('suggestionsContainer');
  const summary = document.getElementById('suggestionsSummary');
  const loadingState = document.getElementById('suggestionsLoading');
  const refreshBtn = document.getElementById('refreshSuggestions');
  const loadMoreBtn = document.getElementById('loadMoreSuggestions');

  let currentSuggestions = [];
  let templates = [];
  let templatesLoaded = false;
  let totalAvailable = null;
  const pageSize = 8;

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

  const stripHtml = (value) => {
    if (!value) return '';
    const holder = document.createElement('div');
    holder.innerHTML = value;
    return holder.textContent || holder.innerText || '';
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
    if (!currentSuggestions.length) {
      summary.innerHTML = '';
      return;
    }
    const total = Number.isFinite(data?.total_available) ? data.total_available : totalAvailable;
    const shownCount = currentSuggestions.length;
    const generatedAt = data.generated_at
      ? new Date(data.generated_at).toLocaleString()
      : 'just now';
    summary.innerHTML = `
      <div class="alert alert-secondary d-flex justify-content-between align-items-center">
        <div>
          <strong>${shownCount}</strong>${Number.isFinite(total) ? ` of ${total}` : ''} suggestion${shownCount === 1 ? '' : 's'} shown.
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

  const getSelectedContact = (suggestion) => {
    const contacts = suggestion.contacts || [];
    if (!contacts.length) return null;
    const selectedId = suggestion.selected_contact_id;
    if (selectedId) {
      const match = contacts.find((contact) => String(contact.id) === String(selectedId));
      if (match) return match;
    }
    return contacts[0];
  };

  const renderSuggestionCard = (suggestion) => {
    const lastContact = suggestion.last_contact;
    const lastEmail = suggestion.last_email;
    const graphEmail = suggestion.last_graph_email;
    const suggestedEmail = suggestion.suggested_email || {};
    const newsItems = suggestion.news_items || [];
    const contacts = suggestion.contacts || [];
    const selectedContact = getSelectedContact(suggestion);
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
    const graphMessageId = graphEmail?.message_id;

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
              <label class="form-label small fw-semibold" for="contact-${suggestion.customer_id}">Contact to email</label>
              ${contacts.length ? `
                <select class="form-select form-select-sm" id="contact-${suggestion.customer_id}" data-contact-select data-customer-id="${suggestion.customer_id}">
                  ${contacts.map((contact) => {
                    const id = String(contact.id || '');
                    const selected = selectedContact && String(selectedContact.id) === id ? 'selected' : '';
                    const label = [contact.name, contact.email].filter(Boolean).join(' • ');
                    return `<option value="${escapeHtml(id)}" ${selected}>${escapeHtml(label)}</option>`;
                  }).join('')}
                </select>
                ${selectedContact?.email ? `
                  <div class="small text-muted mt-1">
                    ${escapeHtml(selectedContact.email)}
                    <a class="ms-2" href="mailto:${escapeHtml(selectedContact.email)}">Open email</a>
                    <button class="btn btn-sm btn-link p-0 ms-2" data-copy-email="${escapeHtml(selectedContact.email)}">Copy</button>
                  </div>
                ` : ''}
              ` : '<div class="text-muted small">No contacts with email</div>'}
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
              ${selectedContact?.email ? `
                <div class="mt-2">
                  <button class="btn btn-sm btn-outline-secondary" data-load-graph-latest data-email="${escapeHtml(selectedContact.email)}" data-customer-id="${suggestion.customer_id}">
                    Load latest Graph email
                  </button>
                  ${graphMessageId ? `
                    <button class="btn btn-sm btn-outline-secondary ms-2" data-load-graph data-message-id="${escapeHtml(graphMessageId)}">
                      Load cached Graph email
                    </button>
                  ` : ''}
                  <div class="graph-email-content small mt-2 d-none" data-graph-content></div>
                </div>
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
            No zero-spend customers with email contacts were found.
          </div>
        </div>
      `;
      if (loadMoreBtn) loadMoreBtn.classList.add('d-none');
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

    container.querySelectorAll('[data-contact-select]').forEach((select) => {
      select.addEventListener('change', () => {
        const customerId = select.getAttribute('data-customer-id');
        const selected = select.value || '';
        const suggestion = currentSuggestions.find((item) => String(item.customer_id) === String(customerId));
        if (suggestion) {
          suggestion.selected_contact_id = selected || null;
        }
        renderSuggestions(currentSuggestions);
      });
    });

    container.querySelectorAll('[data-copy-email]').forEach((button) => {
      button.addEventListener('click', () => {
        const email = button.getAttribute('data-copy-email') || '';
        copyToClipboard(email, button);
      });
    });

    container.querySelectorAll('[data-load-graph]').forEach((button) => {
      button.addEventListener('click', async () => {
        const messageId = button.getAttribute('data-message-id');
        const wrapper = button.closest('.card-body');
        const output = wrapper ? wrapper.querySelector('[data-graph-content]') : null;
        if (!messageId || !output) return;

        const isLoaded = output.getAttribute('data-loaded') === 'true';
        if (isLoaded) {
          const isHidden = output.classList.toggle('d-none');
          button.textContent = isHidden ? 'Load full Graph email' : 'Hide full Graph email';
          return;
        }

        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading';

        try {
          const response = await fetch(`/emails/graph/message/${encodeURIComponent(messageId)}`);
          if (!response.ok) {
            throw new Error('Failed to load Graph email');
          }
          const data = await response.json();
          if (!data?.success || !data?.message) {
            throw new Error(data?.error?.message || 'Graph email unavailable');
          }
          const message = data.message;
          const subject = message.subject || '';
          const fromEmail = message.from?.emailAddress?.address || '';
          const received = message.receivedDateTime
            ? new Date(message.receivedDateTime).toLocaleString()
            : '';
          const body = stripHtml(message.body?.content || message.bodyPreview || '');
          const lines = [];
          if (subject) lines.push(`Subject: ${subject}`);
          if (fromEmail) lines.push(`From: ${fromEmail}`);
          if (received) lines.push(`Date: ${received}`);
          if (lines.length) lines.push('');
          lines.push(body || '(No body content)');
          output.textContent = lines.join('\n');
          output.setAttribute('data-loaded', 'true');
          output.classList.remove('d-none');
          button.textContent = 'Hide full Graph email';
        } catch (error) {
          output.textContent = error?.message || 'Unable to load Graph email';
          output.classList.remove('d-none');
          button.textContent = 'Retry Graph email';
        } finally {
          button.disabled = false;
        }
      });
    });

    container.querySelectorAll('[data-load-graph-latest]').forEach((button) => {
      button.addEventListener('click', async () => {
        const email = button.getAttribute('data-email');
        const wrapper = button.closest('.card-body');
        const output = wrapper ? wrapper.querySelector('[data-graph-content]') : null;
        if (!email || !output) return;

        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading';

        try {
          const response = await fetch(`/emails/graph/latest?email=${encodeURIComponent(email)}`);
          if (!response.ok) {
            throw new Error('Failed to load latest Graph email');
          }
          const data = await response.json();
          if (!data?.success || !data?.message) {
            throw new Error(data?.error?.message || 'Graph email unavailable');
          }
          const message = data.message;
          const subject = message.subject || '';
          const fromEmail = message.from?.emailAddress?.address || '';
          const received = message.receivedDateTime
            ? new Date(message.receivedDateTime).toLocaleString()
            : '';
          const body = stripHtml(message.body?.content || message.bodyPreview || '');
          const lines = [];
          if (subject) lines.push(`Subject: ${subject}`);
          if (fromEmail) lines.push(`From: ${fromEmail}`);
          if (received) lines.push(`Date: ${received}`);
          if (lines.length) lines.push('');
          lines.push(body || '(No body content)');
          output.textContent = lines.join('\n');
          output.classList.remove('d-none');
          button.textContent = 'Refresh latest Graph email';

          const customerId = button.getAttribute('data-customer-id');
          const suggestion = currentSuggestions.find((item) => String(item.customer_id) === String(customerId));
          if (suggestion) {
            suggestion.last_graph_email = {
              message_id: message.id,
              subject: message.subject || '',
              preview: message.bodyPreview || '',
              sender_email: fromEmail,
              date: message.receivedDateTime || message.sentDateTime || null
            };
          }
        } catch (error) {
          output.textContent = error?.message || 'Unable to load Graph email';
          output.classList.remove('d-none');
          button.textContent = 'Retry latest Graph email';
        } finally {
          button.disabled = false;
        }
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
      const response = await fetch(`/salespeople/${salespersonId}/contact-suggestions/data?limit=${pageSize}&offset=0`);
      if (!response.ok) {
        throw new Error('Failed to load suggestions');
      }
      const data = await response.json();
      currentSuggestions = data.suggestions || [];
      totalAvailable = Number.isFinite(data.total_available) ? data.total_available : null;
      renderSummary(data);
      renderSuggestions(currentSuggestions);
      if (loadMoreBtn) {
        if (totalAvailable !== null && currentSuggestions.length < totalAvailable) {
          loadMoreBtn.classList.remove('d-none');
        } else {
          loadMoreBtn.classList.add('d-none');
        }
      }
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

  const loadMoreSuggestions = async () => {
    if (!loadMoreBtn) return;
    loadMoreBtn.disabled = true;
    loadMoreBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading';

    try {
      const offset = currentSuggestions.length;
      const response = await fetch(`/salespeople/${salespersonId}/contact-suggestions/data?limit=${pageSize}&offset=${offset}`);
      if (!response.ok) {
        throw new Error('Failed to load more suggestions');
      }
      const data = await response.json();
      const newSuggestions = data.suggestions || [];
      totalAvailable = Number.isFinite(data.total_available) ? data.total_available : totalAvailable;
      currentSuggestions = currentSuggestions.concat(newSuggestions);
      renderSummary(data);
      renderSuggestions(currentSuggestions);
      if (!newSuggestions.length || (totalAvailable !== null && currentSuggestions.length >= totalAvailable)) {
        loadMoreBtn.classList.add('d-none');
      }
    } catch (error) {
      console.error(error);
    } finally {
      loadMoreBtn.disabled = false;
      loadMoreBtn.textContent = 'Load more suggestions';
    }
  };

  loadTemplates();
  loadSuggestions();

  loadMoreBtn?.addEventListener('click', loadMoreSuggestions);
});

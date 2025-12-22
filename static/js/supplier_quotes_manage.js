let currentQuoteId = null;
let quotesCache = [];

function showAlert(message, type) {
    const container = document.getElementById('supplier-quotes-alerts');
    if (!container) return;

    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    container.appendChild(alertDiv);
    setTimeout(() => alertDiv.remove(), 4000);
}

function fetchQuotes() {
    const container = document.getElementById('quotes-list-container');
    if (container) {
        container.innerHTML = '<div class="text-muted">Loading quotes...</div>';
    }

    return fetch(`/parts_list/parts-lists/${window.PARTS_LIST_ID}/supplier-quotes`)
        .then(response => response.json())
        .then(data => {
            if (!data.success) {
                throw new Error(data.message || 'Unable to load quotes');
            }
            quotesCache = data.quotes || [];
            renderQuotesList(quotesCache);
        })
        .catch(error => {
            console.error('Error loading quotes:', error);
            if (container) {
                container.innerHTML = '<div class="text-danger">Error loading quotes.</div>';
            }
            showAlert('Error loading quotes list', 'danger');
        });
}

function renderQuotesList(quotes) {
    const container = document.getElementById('quotes-list-container');
    if (!container) return;

    if (!quotes.length) {
        container.innerHTML = '<div class="text-muted">No supplier quotes found.</div>';
        return;
    }

    const list = document.createElement('div');
    list.className = 'list-group';

    quotes.forEach(quote => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = `list-group-item list-group-item-action quote-list-item ${quote.id === currentQuoteId ? 'active' : ''}`;
        item.dataset.quoteId = quote.id;
        item.innerHTML = `
            <div class="d-flex justify-content-between align-items-start">
                <div>
                    <div class="fw-semibold">${quote.supplier_name}</div>
                    <div class="small text-muted">${quote.quote_reference || 'No reference'} | ${quote.quote_date || 'No date'}</div>
                </div>
                <span class="badge bg-secondary">${quote.line_count} lines</span>
            </div>
        `;

        item.addEventListener('click', () => loadQuoteDetails(quote.id));
        list.appendChild(item);
    });

    container.innerHTML = '';
    container.appendChild(list);
}

function loadQuoteDetails(quoteId) {
    const placeholder = document.getElementById('quote-detail-placeholder');
    const form = document.getElementById('quote-detail-form');
    const title = document.getElementById('quote-detail-title');

    if (placeholder) placeholder.textContent = 'Loading quote details...';
    if (form) form.style.display = 'none';
    if (title) title.textContent = 'Quote Details';

    fetch(`/parts_list/parts-lists/${window.PARTS_LIST_ID}/supplier-quotes/${quoteId}`)
        .then(response => response.json())
        .then(data => {
            if (!data.success) {
                throw new Error(data.message || 'Unable to load quote');
            }

            currentQuoteId = quoteId;
            renderQuotesList(quotesCache);
            renderQuoteDetails(data.quote, data.lines || []);
        })
        .catch(error => {
            console.error('Error loading quote:', error);
            showAlert('Error loading quote details', 'danger');
            if (placeholder) placeholder.textContent = 'Select a quote to view lines and update the supplier.';
        });
}

function renderQuoteDetails(quote, lines) {
    const placeholder = document.getElementById('quote-detail-placeholder');
    const form = document.getElementById('quote-detail-form');
    const title = document.getElementById('quote-detail-title');

    if (placeholder) placeholder.style.display = 'none';
    if (form) form.style.display = 'block';
    if (title) title.textContent = `Quote #${quote.id} - ${quote.supplier_name}`;

    document.getElementById('quote-reference-input').value = quote.quote_reference || '';
    document.getElementById('quote-date-input').value = quote.quote_date || '';
    document.getElementById('quote-currency-input').value = quote.currency_code || '';
    document.getElementById('quote-notes-input').value = quote.notes || '';

    initializeSupplierSelect(quote);
    renderQuoteLines(quote, lines);
}

function initializeSupplierSelect(quote) {
    const $select = $('#quote-supplier-select');

    if (!$select.hasClass('select2-hidden-accessible')) {
        $select.select2({
            ajax: {
                url: '/ils/suppliers/search',
                dataType: 'json',
                delay: 250,
                data: function (params) {
                    return {
                        q: params.term || '',
                        limit: params.term ? 20 : 100
                    };
                },
                processResults: function (data) {
                    if (!data.success) {
                        return { results: [] };
                    }
                    return {
                        results: data.suppliers.map(function (item) {
                            return {
                                id: item.id.toString(),
                                text: item.name
                            };
                        })
                    };
                },
                cache: true
            },
            placeholder: 'Search for supplier...',
            minimumInputLength: 0,
            allowClear: false,
            width: '100%'
        });
    }

    $select.find('option').remove();
    const option = new Option(quote.supplier_name, quote.supplier_id, true, true);
    $select.append(option).trigger('change');
}

function renderQuoteLines(quote, lines) {
    const tbody = document.getElementById('quote-lines-body');
    const deleteBtn = document.getElementById('delete-lines-btn');
    const countLabel = document.getElementById('quote-lines-count');
    const selectAll = document.getElementById('select-all-lines');

    if (!tbody) return;

    const quoteLines = (lines || []).filter(line => line.id);
    tbody.innerHTML = '';

    if (quoteLines.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="text-muted text-center py-3">No quote lines found for this quote.</td>
            </tr>
        `;
        if (deleteBtn) deleteBtn.disabled = true;
        if (selectAll) selectAll.checked = false;
        if (countLabel) countLabel.textContent = 'Showing 0 quote lines.';
        return;
    }

    quoteLines.forEach(line => {
        const tr = document.createElement('tr');
        const unitPrice = formatPrice(line.unit_price, quote.currency_code);
        const noBid = line.is_no_bid ? 'Yes' : 'No';

        tr.innerHTML = `
            <td>
                <input type="checkbox" class="quote-line-checkbox" data-line-id="${line.id}">
            </td>
            <td>${line.line_number || ''}</td>
            <td>${line.customer_part_number || ''}</td>
            <td>${line.quoted_part_number || ''}</td>
            <td>${line.quantity_quoted || ''}</td>
            <td>${unitPrice}</td>
            <td>${noBid}</td>
            <td>${line.line_notes || ''}</td>
        `;
        tbody.appendChild(tr);
    });

    if (deleteBtn) deleteBtn.disabled = false;
    if (selectAll) {
        selectAll.checked = false;
    }
    if (countLabel) {
        countLabel.textContent = `Showing ${quoteLines.length} quote lines.`;
    }
}

function formatPrice(value, currencyCode) {
    if (value === null || value === undefined || value === '') {
        return '';
    }
    const num = Number(value);
    if (!Number.isFinite(num)) {
        return '';
    }
    const formatted = num.toFixed(2);
    return currencyCode ? `${currencyCode} ${formatted}` : formatted;
}

function deleteSelectedLines() {
    if (!currentQuoteId) return;

    const checkboxes = Array.from(document.querySelectorAll('.quote-line-checkbox:checked'));
    if (!checkboxes.length) {
        showAlert('Select at least one line to delete.', 'warning');
        return;
    }

    if (!confirm('Delete selected quote lines? This cannot be undone.')) {
        return;
    }

    const lineIds = checkboxes.map(cb => cb.dataset.lineId);

    fetch(`/parts_list/parts-lists/${window.PARTS_LIST_ID}/supplier-quotes/${currentQuoteId}/lines/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ line_ids: lineIds })
    })
    .then(response => response.json())
    .then(data => {
        if (!data.success) {
            throw new Error(data.message || 'Delete failed');
        }
        showAlert(`Deleted ${data.deleted_count || lineIds.length} lines.`, 'success');
        loadQuoteDetails(currentQuoteId);
        fetchQuotes();
    })
    .catch(error => {
        console.error('Delete error:', error);
        showAlert('Error deleting lines', 'danger');
    });
}

function saveSupplierChange() {
    if (!currentQuoteId) return;

    const supplierId = $('#quote-supplier-select').val();
    if (!supplierId) {
        showAlert('Please select a supplier.', 'warning');
        return;
    }

    fetch(`/parts_list/parts-lists/${window.PARTS_LIST_ID}/supplier-quotes/${currentQuoteId}/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ supplier_id: parseInt(supplierId, 10) })
    })
    .then(response => response.json())
    .then(data => {
        if (!data.success) {
            throw new Error(data.message || 'Update failed');
        }
        showAlert('Supplier updated.', 'success');
        fetchQuotes().then(() => loadQuoteDetails(currentQuoteId));
    })
    .catch(error => {
        console.error('Update error:', error);
        showAlert('Error updating supplier', 'danger');
    });
}

function toggleSelectAllLines(event) {
    const checked = event.target.checked;
    document.querySelectorAll('.quote-line-checkbox').forEach(cb => {
        cb.checked = checked;
    });
}

document.addEventListener('DOMContentLoaded', function() {
    document.getElementById('refresh-quotes-btn')?.addEventListener('click', fetchQuotes);
    document.getElementById('delete-lines-btn')?.addEventListener('click', deleteSelectedLines);
    document.getElementById('save-supplier-btn')?.addEventListener('click', saveSupplierChange);
    document.getElementById('select-all-lines')?.addEventListener('change', toggleSelectAllLines);

    fetchQuotes();
});

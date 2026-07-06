let currentLines = [];
let editLineModal = null;

function showAlert(message, type) {
    const container = document.getElementById('supplier-quote-lines-alerts');
    if (!container) return;

    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
    alertDiv.innerHTML = `
        ${escapeHtml(message)}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    container.appendChild(alertDiv);
    setTimeout(() => alertDiv.remove(), 4000);
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function getFilters() {
    return {
        list_id: document.getElementById('filter-list-id')?.value || '',
        quote_id: document.getElementById('filter-quote-id')?.value || '',
        supplier_id: $('#filter-supplier-id').val() || '',
        part_number: document.getElementById('filter-part-number')?.value || ''
    };
}

function buildQuery(params) {
    const searchParams = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
        if (value !== null && value !== undefined && value !== '') {
            searchParams.append(key, value);
        }
    });
    return searchParams.toString();
}

function loadLines() {
    const params = getFilters();
    const query = buildQuery(params);
    const url = query ? `/parts_list/supplier-quotes/lines/data?${query}` : '/parts_list/supplier-quotes/lines/data';

    const tbody = document.getElementById('quote-lines-body');
    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="10" class="text-muted text-center py-3">Loading...</td>
            </tr>
        `;
    }

    fetch(url)
        .then(response => response.json())
        .then(data => {
            if (!data.success) {
                throw new Error(data.message || 'Unable to load lines');
            }
            currentLines = data.lines || [];
            renderLines(currentLines, data.total_count || 0);
        })
        .catch(error => {
            console.error('Error loading lines:', error);
            showAlert('Error loading supplier quote lines', 'danger');
            if (tbody) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="10" class="text-danger text-center py-3">Failed to load lines.</td>
                    </tr>
                `;
            }
        });
}

function renderLines(lines, totalCount) {
    const tbody = document.getElementById('quote-lines-body');
    const countLabel = document.getElementById('quote-lines-count');
    const selectAll = document.getElementById('select-all-lines');

    if (!tbody) return;

    tbody.innerHTML = '';

    if (!lines.length) {
        tbody.innerHTML = `
            <tr>
                <td colspan="10" class="text-muted text-center py-3">No lines found.</td>
            </tr>
        `;
        if (countLabel) countLabel.textContent = 'Showing 0 lines.';
        if (selectAll) selectAll.checked = false;
        updateActionButtons();
        return;
    }

    lines.forEach(line => {
        const tr = document.createElement('tr');
        const quoteDate = formatDate(line.quote_date || line.date_modified || line.date_created);
        const price = formatMoney(line.unit_price, line.currency_symbol, line.currency_code, 4);
        const lbInfo = line.price_entered_as_lb
            ? `<div class="small text-muted">LB ${formatMoney(line.lb_unit_price, line.currency_symbol, line.currency_code, 4)} / PPP ${formatNumber(line.pieces_per_pound_used, 4)}</div>`
            : '';
        const noBid = line.is_no_bid ? '<span class="badge bg-secondary">No bid</span>' : '';
        const customerQuoteContext = renderCustomerQuoteContext(line);
        const salesOrderContext = renderSalesOrderContext(line);

        tr.innerHTML = `
            <td>
                <input type="checkbox" class="quote-line-checkbox" data-line-id="${line.id}">
            </td>
            <td>
                <button type="button" class="btn btn-sm btn-outline-primary edit-line-btn" data-line-id="${line.id}" title="Edit quote line">
                    <i class="bi bi-pencil-square"></i>
                </button>
            </td>
            <td class="text-nowrap">${escapeHtml(quoteDate)}</td>
            <td>
                <div>${escapeHtml(line.supplier_name)}</div>
                ${line.quote_reference ? `<div class="small text-muted">${escapeHtml(line.quote_reference)}</div>` : ''}
            </td>
            <td class="line-part">
                <div><strong>${escapeHtml(line.quoted_part_number || line.customer_part_number || line.base_part_number)}</strong></div>
                ${line.quoted_part_number && line.quoted_part_number !== line.customer_part_number ? `<div class="small text-muted">Requested ${escapeHtml(line.customer_part_number || '')}</div>` : ''}
                ${line.base_part_number ? `<div class="small text-muted">Base ${escapeHtml(line.base_part_number)}</div>` : ''}
                ${line.manufacturer ? `<div class="small text-muted">${escapeHtml(line.manufacturer)}</div>` : ''}
            </td>
            <td>${escapeHtml(line.quantity_quoted ?? '')}</td>
            <td class="line-money">
                <div>${price}</div>
                ${lbInfo}
                ${noBid}
            </td>
            <td class="line-context">${customerQuoteContext}</td>
            <td class="line-context">${salesOrderContext}</td>
            <td>
                <div><a href="/parts_list/parts-lists/${line.parts_list_id}/supplier-quotes/manage?quote_id=${line.supplier_quote_id}">Quote #${escapeHtml(line.supplier_quote_id)}</a></div>
                <div class="small text-muted">List #${escapeHtml(line.parts_list_id)} · Line ${escapeHtml(line.line_number ?? '')}</div>
                ${line.list_name ? `<div class="small text-muted">${escapeHtml(line.list_name)}</div>` : ''}
            </td>
            <td class="line-notes">${escapeHtml(line.line_notes || '')}</td>
        `;
        tbody.appendChild(tr);
    });

    if (countLabel) {
        const shown = lines.length;
        const total = totalCount || shown;
        countLabel.textContent = `Showing ${shown} of ${total} lines.`;
    }

    if (selectAll) {
        selectAll.checked = false;
    }

    updateActionButtons();
}

function renderCustomerQuoteContext(line) {
    const count = Number(line.customer_quote_count || 0);
    if (!count) return '<span class="text-muted">No customer quotes</span>';

    return `
        <div>Latest ${formatMoney(line.latest_customer_quote_price, 'GBP ', 'GBP', 2)}</div>
        <div>Avg ${formatMoney(line.avg_customer_quote_price, 'GBP ', 'GBP', 2)}</div>
        <div class="small text-muted">${count} quote${count === 1 ? '' : 's'}${line.latest_customer_quote_date ? ` · ${escapeHtml(formatDate(line.latest_customer_quote_date))}` : ''}</div>
    `;
}

function renderSalesOrderContext(line) {
    const count = Number(line.sales_order_count || 0);
    if (!count) return '<span class="text-muted">No sales orders</span>';

    const latestDetails = [
        line.latest_sales_order_ref,
        line.latest_sales_order_qty ? `Qty ${line.latest_sales_order_qty}` : '',
        line.latest_sales_order_date ? formatDate(line.latest_sales_order_date) : ''
    ].filter(Boolean).map(escapeHtml).join(' · ');

    return `
        <div>Latest ${formatMoney(line.latest_sales_order_price, 'GBP ', 'GBP', 2)}</div>
        <div>Avg ${formatMoney(line.avg_sales_order_price, 'GBP ', 'GBP', 2)}</div>
        <div class="small text-muted">${count} line${count === 1 ? '' : 's'}${latestDetails ? ` · ${latestDetails}` : ''}</div>
    `;
}

function formatPrice(value) {
    return formatNumber(value, 2);
}

function formatNumber(value, decimals) {
    if (value === null || value === undefined || value === '') {
        return '';
    }
    const num = Number(value);
    if (!Number.isFinite(num)) {
        return '';
    }
    return num.toFixed(decimals);
}

function formatMoney(value, symbol, code, decimals) {
    const formatted = formatNumber(value, decimals);
    if (!formatted) return '';
    const cleanSymbol = symbol || '';
    if (cleanSymbol) {
        return `${escapeHtml(cleanSymbol)}${formatted}`;
    }
    return `${escapeHtml(code || '')} ${formatted}`.trim();
}

function formatDate(value) {
    if (!value) return '';
    return String(value).slice(0, 10);
}

function updateActionButtons() {
    const selected = getSelectedLineIds();
    const deleteBtn = document.getElementById('delete-lines-btn');
    const reassignBtn = document.getElementById('reassign-lines-btn');

    if (deleteBtn) deleteBtn.disabled = selected.length === 0;
    if (reassignBtn) reassignBtn.disabled = selected.length === 0;
}

function getSelectedLineIds() {
    return Array.from(document.querySelectorAll('.quote-line-checkbox:checked'))
        .map(cb => cb.dataset.lineId);
}

function deleteSelectedLines() {
    const lineIds = getSelectedLineIds();
    if (!lineIds.length) {
        showAlert('Select at least one line to delete.', 'warning');
        return;
    }

    if (!confirm('Delete selected quote lines? This cannot be undone.')) {
        return;
    }

    fetch('/parts_list/supplier-quotes/lines/delete', {
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
        loadLines();
    })
    .catch(error => {
        console.error('Delete error:', error);
        showAlert('Error deleting lines', 'danger');
    });
}

function reassignSelectedLines() {
    const lineIds = getSelectedLineIds();
    const newQuoteId = $('#reassign-quote-select').val();

    if (!lineIds.length) {
        showAlert('Select at least one line to move.', 'warning');
        return;
    }

    if (!newQuoteId) {
        showAlert('Select a target quote.', 'warning');
        return;
    }

    fetch('/parts_list/supplier-quotes/lines/reassign', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            line_ids: lineIds,
            supplier_quote_id: parseInt(newQuoteId, 10)
        })
    })
    .then(response => response.json())
    .then(data => {
        if (!data.success) {
            throw new Error(data.message || 'Reassign failed');
        }
        showAlert(`Moved ${data.updated_count || lineIds.length} lines.`, 'success');
        loadLines();
    })
    .catch(error => {
        console.error('Reassign error:', error);
        showAlert('Error moving lines', 'danger');
    });
}

function initializeQuoteSelect() {
    const $select = $('#reassign-quote-select');

    if ($select.hasClass('select2-hidden-accessible')) {
        $select.off('select2:select');
        $select.select2('destroy');
    }

    $select.select2({
        ajax: {
            url: '/parts_list/supplier-quotes/search',
            dataType: 'json',
            delay: 250,
            data: function (params) {
                return {
                    q: params.term || '',
                    limit: 50
                };
            },
            processResults: function (data) {
                if (!data.success) {
                    return { results: [] };
                }
                return {
                    results: data.quotes.map(function (item) {
                        const label = `#${item.id} - ${item.supplier_name} (${item.quote_reference || 'No ref'}) [List ${item.parts_list_id}]`;
                        return {
                            id: item.id.toString(),
                            text: label
                        };
                    })
                };
            },
            cache: true
        },
        placeholder: 'Search quotes to move lines to...',
        minimumInputLength: 0,
        allowClear: true,
        width: '100%'
    });
}

function initializeSupplierFilter() {
    const $select = $('#filter-supplier-id');

    if ($select.hasClass('select2-hidden-accessible')) {
        $select.select2('destroy');
    }

    $select.select2({
        ajax: {
            url: '/ils/suppliers/search',
            dataType: 'json',
            delay: 250,
            data: function (params) {
                return {
                    q: params.term || '',
                    limit: 20
                };
            },
            processResults: function (data) {
                if (!data.success) {
                    return { results: [] };
                }
                return {
                    results: (data.suppliers || []).map(function (supplier) {
                        return {
                            id: supplier.id.toString(),
                            text: supplier.name
                        };
                    })
                };
            },
            cache: true
        },
        placeholder: 'Type supplier name...',
        minimumInputLength: 2,
        allowClear: true,
        theme: 'bootstrap-5',
        width: '100%'
    });
}

function toggleSelectAllLines(event) {
    const checked = event.target.checked;
    document.querySelectorAll('.quote-line-checkbox').forEach(cb => {
        cb.checked = checked;
    });
    updateActionButtons();
}

function openEditLine(lineId) {
    const line = currentLines.find(item => String(item.id) === String(lineId));
    if (!line) {
        showAlert('Line not found in current results.', 'warning');
        return;
    }

    setInputValue('edit-line-id', line.id);
    setInputValue('edit-quoted-part-number', line.quoted_part_number);
    setInputValue('edit-manufacturer', line.manufacturer);
    setInputValue('edit-quantity-quoted', line.quantity_quoted);
    setInputValue('edit-qty-available', line.qty_available);
    setInputValue('edit-purchase-increment', line.purchase_increment);
    setInputValue('edit-moq', line.moq);
    setInputValue('edit-unit-price', line.unit_price);
    setInputValue('edit-lb-unit-price', line.lb_unit_price);
    setInputValue('edit-pieces-per-pound-used', line.pieces_per_pound_used);
    setInputValue('edit-lead-time-days', line.lead_time_days);
    setInputValue('edit-condition-code', line.condition_code);
    setInputValue('edit-certifications', line.certifications);
    setInputValue('edit-line-notes', line.line_notes);
    setChecked('edit-price-entered-as-lb', line.price_entered_as_lb);
    setChecked('edit-is-no-bid', line.is_no_bid);

    editLineModal?.show();
}

function setInputValue(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value ?? '';
}

function setChecked(id, value) {
    const el = document.getElementById(id);
    if (el) el.checked = Boolean(value);
}

function readInputValue(id) {
    return document.getElementById(id)?.value ?? '';
}

function saveEditedLine() {
    const lineId = readInputValue('edit-line-id');
    if (!lineId) {
        showAlert('No line selected.', 'warning');
        return;
    }

    const payload = {
        quoted_part_number: readInputValue('edit-quoted-part-number'),
        manufacturer: readInputValue('edit-manufacturer'),
        quantity_quoted: readInputValue('edit-quantity-quoted'),
        qty_available: readInputValue('edit-qty-available'),
        purchase_increment: readInputValue('edit-purchase-increment'),
        moq: readInputValue('edit-moq'),
        unit_price: readInputValue('edit-unit-price'),
        price_entered_as_lb: document.getElementById('edit-price-entered-as-lb')?.checked || false,
        lb_unit_price: readInputValue('edit-lb-unit-price'),
        pieces_per_pound_used: readInputValue('edit-pieces-per-pound-used'),
        lead_time_days: readInputValue('edit-lead-time-days'),
        condition_code: readInputValue('edit-condition-code'),
        certifications: readInputValue('edit-certifications'),
        is_no_bid: document.getElementById('edit-is-no-bid')?.checked || false,
        line_notes: readInputValue('edit-line-notes')
    };

    const saveBtn = document.getElementById('save-line-btn');
    if (saveBtn) saveBtn.disabled = true;

    fetch(`/parts_list/supplier-quotes/lines/${lineId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(response => response.json())
    .then(data => {
        if (!data.success) {
            throw new Error(data.message || 'Save failed');
        }
        editLineModal?.hide();
        showAlert('Supplier quote line saved.', 'success');
        loadLines();
    })
    .catch(error => {
        console.error('Save error:', error);
        showAlert(error.message || 'Error saving line', 'danger');
    })
    .finally(() => {
        if (saveBtn) saveBtn.disabled = false;
    });
}

document.addEventListener('DOMContentLoaded', function() {
    const modalEl = document.getElementById('editQuoteLineModal');
    if (modalEl && window.bootstrap) {
        editLineModal = new bootstrap.Modal(modalEl);
    }

    document.getElementById('apply-filters-btn')?.addEventListener('click', loadLines);
    document.getElementById('delete-lines-btn')?.addEventListener('click', deleteSelectedLines);
    document.getElementById('reassign-lines-btn')?.addEventListener('click', reassignSelectedLines);
    document.getElementById('select-all-lines')?.addEventListener('change', toggleSelectAllLines);
    document.getElementById('save-line-btn')?.addEventListener('click', saveEditedLine);
    document.getElementById('filter-part-number')?.addEventListener('keydown', function(event) {
        if (event.key === 'Enter') {
            event.preventDefault();
            loadLines();
        }
    });
    document.getElementById('quote-lines-body')?.addEventListener('change', function(event) {
        if (event.target && event.target.classList.contains('quote-line-checkbox')) {
            updateActionButtons();
        }
    });
    document.getElementById('quote-lines-body')?.addEventListener('click', function(event) {
        const button = event.target.closest('.edit-line-btn');
        if (button) {
            openEditLine(button.dataset.lineId);
        }
    });

    initializeSupplierFilter();
    initializeQuoteSelect();
    loadLines();
});

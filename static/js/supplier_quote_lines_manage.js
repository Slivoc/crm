let currentLines = [];

function showAlert(message, type) {
    const container = document.getElementById('supplier-quote-lines-alerts');
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

function getFilters() {
    return {
        list_id: document.getElementById('filter-list-id')?.value || '',
        quote_id: document.getElementById('filter-quote-id')?.value || '',
        supplier_id: document.getElementById('filter-supplier-id')?.value || '',
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
                <td colspan="11" class="text-muted text-center py-3">Loading...</td>
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
                        <td colspan="11" class="text-danger text-center py-3">Failed to load lines.</td>
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
                <td colspan="11" class="text-muted text-center py-3">No lines found.</td>
            </tr>
        `;
        if (countLabel) countLabel.textContent = 'Showing 0 lines.';
        if (selectAll) selectAll.checked = false;
        updateActionButtons();
        return;
    }

    lines.forEach(line => {
        const tr = document.createElement('tr');
        const unitPrice = formatPrice(line.unit_price);
        const noBid = line.is_no_bid ? 'Yes' : 'No';
        const listLabel = line.parts_list_id ? `${line.parts_list_id}` : '';
        const quoteLabel = line.supplier_quote_id ? `${line.supplier_quote_id}` : '';

        tr.innerHTML = `
            <td>
                <input type="checkbox" class="quote-line-checkbox" data-line-id="${line.id}">
            </td>
            <td>${quoteLabel}</td>
            <td>${listLabel}</td>
            <td>${line.supplier_name || ''}</td>
            <td>${line.customer_part_number || ''}</td>
            <td>${line.quoted_part_number || ''}</td>
            <td>${line.line_number || ''}</td>
            <td>${line.quantity_quoted || ''}</td>
            <td>${unitPrice}</td>
            <td>${noBid}</td>
            <td>${line.line_notes || ''}</td>
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

function formatPrice(value) {
    if (value === null || value === undefined || value === '') {
        return '';
    }
    const num = Number(value);
    if (!Number.isFinite(num)) {
        return '';
    }
    return num.toFixed(2);
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

function toggleSelectAllLines(event) {
    const checked = event.target.checked;
    document.querySelectorAll('.quote-line-checkbox').forEach(cb => {
        cb.checked = checked;
    });
    updateActionButtons();
}

document.addEventListener('DOMContentLoaded', function() {
    document.getElementById('apply-filters-btn')?.addEventListener('click', loadLines);
    document.getElementById('delete-lines-btn')?.addEventListener('click', deleteSelectedLines);
    document.getElementById('reassign-lines-btn')?.addEventListener('click', reassignSelectedLines);
    document.getElementById('select-all-lines')?.addEventListener('change', toggleSelectAllLines);
    document.getElementById('quote-lines-body')?.addEventListener('change', function(event) {
        if (event.target && event.target.classList.contains('quote-line-checkbox')) {
            updateActionButtons();
        }
    });

    initializeQuoteSelect();
    loadLines();
});

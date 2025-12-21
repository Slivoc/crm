// Parts List Sourcing JavaScript - IMPROVED VERSION

document.addEventListener('DOMContentLoaded', function() {
    console.log('Parts List Sourcing loaded');

    // Toggle VQ details
    document.addEventListener('click', function(e) {
        const vqBtn = e.target.closest('.expand-vq-btn');
        if (!vqBtn) return;

        const lineId = vqBtn.dataset.lineId;
        const detailsRows = document.querySelectorAll(`.vq-details-${lineId}`);
        const icon = vqBtn.querySelector('i');

        detailsRows.forEach(row => {
            row.classList.toggle('show');
        });

        if (icon) {
            if (icon.classList.contains('bi-chevron-right')) {
                icon.classList.remove('bi-chevron-right');
                icon.classList.add('bi-chevron-down');
            } else {
                icon.classList.remove('bi-chevron-down');
                icon.classList.add('bi-chevron-right');
            }
        }
    });

    // Toggle PO details
    document.addEventListener('click', function(e) {
        const poBtn = e.target.closest('.expand-po-btn');
        if (!poBtn) return;

        const lineId = poBtn.dataset.lineId;
        const detailsRows = document.querySelectorAll(`.po-details-${lineId}`);
        const icon = poBtn.querySelector('i');

        detailsRows.forEach(row => {
            row.classList.toggle('show');
        });

        if (icon) {
            if (icon.classList.contains('bi-chevron-right')) {
                icon.classList.remove('bi-chevron-right');
                icon.classList.add('bi-chevron-down');
            } else {
                icon.classList.remove('bi-chevron-down');
                icon.classList.add('bi-chevron-right');
            }
        }
    });

    // Toggle Stock details
    document.addEventListener('click', function(e) {
        const stockBtn = e.target.closest('.expand-stock-btn');
        if (!stockBtn) return;

        const lineId = stockBtn.dataset.lineId;
        const detailsRows = document.querySelectorAll(`.stock-details-${lineId}`);
        const icon = stockBtn.querySelector('i');

        detailsRows.forEach(row => {
            row.classList.toggle('show');
        });

        if (icon) {
            if (icon.classList.contains('bi-chevron-right')) {
                icon.classList.remove('bi-chevron-right');
                icon.classList.add('bi-chevron-down');
            } else {
                icon.classList.remove('bi-chevron-down');
                icon.classList.add('bi-chevron-right');
            }
        }
    });

    // Manual supplier add button handler
    document.addEventListener('click', function(e) {
        const addManualBtn = e.target.closest('.add-manual-supplier-btn');
        if (!addManualBtn) return;

        const lineId = addManualBtn.dataset.lineId;
        showSupplierSelectModal(lineId);
    });

    // Function to show supplier selection modal
    function showSupplierSelectModal(lineId) {
        const modalHTML = `
            <div class="modal fade" id="supplierSelectModal" tabindex="-1" aria-hidden="true">
                <div class="modal-dialog">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">Add Supplier</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <div class="mb-3">
                                <label class="form-label">Search Supplier:</label>
                                <input type="text" class="form-control" id="supplier-search" placeholder="Type to search...">
                            </div>
                            <div id="supplier-list" class="list-group" style="max-height: 400px; overflow-y: auto;">
                                <div class="text-center py-3">
                                    <div class="spinner-border spinner-border-sm" role="status"></div>
                                    <span class="ms-2">Loading suppliers...</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;

        const existingModal = document.getElementById('supplierSelectModal');
        if (existingModal) {
            existingModal.remove();
        }

        document.body.insertAdjacentHTML('beforeend', modalHTML);

        const modal = new bootstrap.Modal(document.getElementById('supplierSelectModal'));
        modal.show();

        let allSuppliers = [];

        fetch('/parts_list/suppliers/all')
            .then(r => r.json())
            .then(data => {
                if (!data.success) {
                    throw new Error(data.error || 'Failed to load suppliers');
                }

                allSuppliers = data.suppliers;
                displaySuppliers(allSuppliers);

                const searchInput = document.getElementById('supplier-search');
                searchInput.addEventListener('input', function() {
                    const searchTerm = this.value.toLowerCase();
                    const filtered = allSuppliers.filter(s =>
                        s.name && s.name.toLowerCase().includes(searchTerm)
                    );
                    displaySuppliers(filtered);
                });
            })
            .catch(error => {
                document.getElementById('supplier-list').innerHTML = `
                    <div class="alert alert-danger">Error loading suppliers: ${error.message}</div>
                `;
            });

        document.getElementById('supplier-list').addEventListener('click', function(e) {
            const supplierItem = e.target.closest('.supplier-list-item');
            if (!supplierItem) return;

            const supplierId = supplierItem.dataset.supplierId;
            const supplierName = supplierItem.dataset.supplierName;
            addSuggestedSupplier(lineId, supplierId, supplierName, 'manual');
            modal.hide();
        });

        document.getElementById('supplierSelectModal').addEventListener('hidden.bs.modal', function() {
            this.remove();
        });
    }

    function displaySuppliers(suppliers) {
        const listContainer = document.getElementById('supplier-list');

        if (suppliers.length === 0) {
            listContainer.innerHTML = '<div class="text-muted text-center py-3">No suppliers found</div>';
            return;
        }

        let html = '';
        suppliers.forEach(supplier => {
            const supplierName = supplier.name || 'Unnamed Supplier';
            html += `
                <button type="button"
        class="list-group-item list-group-item-action supplier-list-item"
        data-supplier-id="${supplier.id}"
        data-supplier-name="${supplierName}">
    <div class="d-flex justify-content-between align-items-center">
        <strong>${supplierName}</strong>
        ${supplier.currency_code ? `<span class="badge bg-secondary">${supplier.currency_code}</span>` : ''}
    </div>
</button>
            `;
        });

        listContainer.innerHTML = html;
    }

    // Filter functionality
    const filterType = document.getElementById('filter-type');
    const searchPart = document.getElementById('search-part');

    function applyFilters() {
        const filterValue = filterType.value;
        const searchValue = searchPart.value.toLowerCase();
        const rows = document.querySelectorAll('.sourcing-table tbody tr.main-row');

        rows.forEach(row => {
            let showRow = true;

            if (filterValue !== 'all') {
                switch(filterValue) {
                    case 'no-source':
                        showRow = row.dataset.hasVq === '0' &&
                                 row.dataset.hasPo === '0' &&
                                 row.dataset.hasStock === '0';
                        break;
                    case 'has-vq':
                        showRow = row.dataset.hasVq === '1';
                        break;
                    case 'has-po':
                        showRow = row.dataset.hasPo === '1';
                        break;
                    case 'has-stock':
                        showRow = row.dataset.hasStock === '1';
                        break;
                    case 'has-suggested':
                        showRow = row.dataset.hasSuggested === '1';
                        break;
                    case 'contacted':
                        showRow = row.dataset.contacted === '1';
                        break;
                    case 'not-contacted':
                        showRow = row.dataset.contacted === '0';
                        break;
                    case 'not-costed':
                        showRow = row.dataset.hasCost === '0';
                        break;
                }
            }

            if (showRow && searchValue) {
                const partNumber = row.dataset.partNumber.toLowerCase();
                showRow = partNumber.includes(searchValue);
            }

            row.style.display = showRow ? '' : 'none';

            const lineId = row.dataset.lineId;
            const detailsRows = document.querySelectorAll(
                `.po-details-${lineId}, .vq-details-${lineId}, .stock-details-${lineId}, .suggested-details-${lineId}`
            );

            detailsRows.forEach(detailRow => {
                if (!showRow) {
                    detailRow.style.display = 'none';
                    detailRow.classList.remove('show');
                }
            });
        });
    }

    if (filterType) filterType.addEventListener('change', applyFilters);
    if (searchPart) searchPart.addEventListener('input', applyFilters);

    // Use cost buttons
    document.addEventListener('click', function(e) {
        const costBtn = e.target.closest('.use-vq-cost-btn, .use-po-cost-btn, .use-stock-cost-btn');
        if (!costBtn) return;

        const lineId = costBtn.dataset.lineId;
        const cost = costBtn.dataset.cost;
        const qty = costBtn.dataset.qty;
        const supplierId = costBtn.dataset.supplierId;
        const supplierName = costBtn.dataset.supplierName;
        const currencyId = costBtn.dataset.currencyId;
        const currencyCode = costBtn.dataset.currencyCode;
        const leadDays = costBtn.dataset.leadDays;
        const sourceType = costBtn.dataset.sourceType;

        console.log('Use cost clicked:', { lineId, cost, qty, supplierId, supplierName, currencyId, currencyCode, leadDays, sourceType });

        let source = sourceType;
        if (!source) {
            if (costBtn.classList.contains('use-vq-cost-btn')) source = 'vq';
            else if (costBtn.classList.contains('use-po-cost-btn')) source = 'po';
            else if (costBtn.classList.contains('use-stock-cost-btn')) source = 'stock';
        }

        fetch(`/parts_list/parts-lists/${window.PARTS_LIST_ID}/lines/${lineId}/use-cost`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                cost: parseFloat(cost),
                chosen_qty: qty ? parseInt(qty) : null,
                supplier_id: supplierId ? parseInt(supplierId) : null,
                currency_id: currencyId && currencyId !== 'None' ? parseInt(currencyId) : null,
                currency_code: currencyCode || null,
                lead_days: leadDays ? parseInt(leadDays) : null,
                source_type: source
            })
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast('Cost saved', 'success');
                // Update the UI live instead of reloading
                updateCostBadge(lineId, cost, currencyCode, supplierName);
                
                // Update the data-has-cost attribute
                const mainRow = document.querySelector(`tr.main-row[data-line-id="${lineId}"]`);
                if (mainRow) {
                    mainRow.dataset.hasCost = '1';
                }
            } else {
                showToast('Failed to save cost: ' + (data.message || ''), 'error');
            }
        })
        .catch(err => {
            console.error('Error saving cost:', err);
            showToast('Error: ' + err.message, 'error');
        });
    });

    // NEW: Update cost badge with supplier name
  // NEW: Update cost badge with supplier name
function updateCostBadge(lineId, cost, currencyCode, supplierName) {
    const mainRow = document.querySelector(`tr.main-row[data-line-id="${lineId}"]`);
    if (!mainRow) return;

    const partNumberCell = mainRow.querySelector('td:nth-child(2)');
    if (!partNumberCell) return;

    // Remove existing cost badge and supplier name if present
    const existingBadge = partNumberCell.querySelector('.cost-badge');
    if (existingBadge) {
        existingBadge.remove();
    }
    const existingSupplier = partNumberCell.querySelector('small.text-muted');
    if (existingSupplier) {
        existingSupplier.remove();
    }
    const existingBreaks = partNumberCell.querySelectorAll('br');
    existingBreaks.forEach(br => br.remove());

    // Add new badge on new line
    const br1 = document.createElement('br');
    const badge = document.createElement('span');
    badge.className = 'badge bg-success mt-1 cost-badge';
    badge.title = `Chosen cost${supplierName ? ' from ' + supplierName : ''}`;
    badge.textContent = `${currencyCode || 'GBP'} ${parseFloat(cost).toFixed(2)}`;

    const strongTag = partNumberCell.querySelector('strong');
    if (strongTag) {
        strongTag.after(br1);
        br1.after(badge);

        // Add supplier name below if available
        if (supplierName) {
            const br2 = document.createElement('br');
            const supplierText = document.createElement('small');
            supplierText.className = 'text-muted';
            supplierText.textContent = supplierName;
            badge.after(br2);
            br2.after(supplierText);
        }
    }
}

    // ILS Search Modal
    document.querySelectorAll('.search-ils-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const partNumber = this.dataset.part;
            const lineId = this.dataset.lineId;
            showILSModal(partNumber, lineId);
        });
    });

    function showILSModal(partNumber, lineId) {
        const modal = new bootstrap.Modal(document.getElementById('ilsModal'));
        const container = document.getElementById('ils-results-container');

        container.innerHTML = '<div class="text-center"><div class="spinner-border" role="status"></div></div>';
        modal.show();

        fetch(`/parts_list/api/parts-lists/${window.PARTS_LIST_ID}/lines/${lineId}/ils-data`)
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    displayILSResults(data.results, lineId, container);
                } else {
                    container.innerHTML = `<div class="alert alert-danger">Error: ${data.error || 'Unknown error'}</div>`;
                }
            })
            .catch(error => {
                container.innerHTML = `<div class="alert alert-danger">Error loading ILS data: ${error.message}</div>`;
            });
    }

    function displayILSResults(results, lineId, container) {
        const mappedSuppliers = results.filter(item => item.supplier_id);
        const unmappedSuppliers = results.filter(item => !item.supplier_id);

        let html = '';

        if (mappedSuppliers.length === 0 && unmappedSuppliers.length === 0) {
            html = '<div class="alert alert-info">No ILS results found for this part.</div>';
        } else {
            if (mappedSuppliers.length > 0) {
                html += '<h6 class="mb-3">Mapped Suppliers</h6>';
                html += '<table class="table table-sm ils-results-table table-hover">';
                html += '<thead><tr><th>ILS Company</th><th>Qty</th><th>Condition</th><th>System Supplier</th><th></th></tr></thead>';
                html += '<tbody>';

                mappedSuppliers.forEach(item => {
                    html += `
                        <tr>
                            <td>${item.ils_company_name || '-'}</td>
                            <td>${item.quantity || '-'}</td>
                            <td>${item.condition_code || '-'}</td>
                            <td><span class="badge bg-success">${item.supplier_name}</span></td>
                            <td>
                                <button class="btn btn-xs btn-outline-success add-supplier-from-ils"
                                        data-line-id="${lineId}"
                                        data-supplier-id="${item.supplier_id}"
                                        data-supplier-name="${item.supplier_name}">
                                    <i class="bi bi-plus"></i>
                                </button>
                            </td>
                        </tr>
                    `;
                });

                html += '</tbody></table>';
            }

            if (unmappedSuppliers.length > 0) {
                html += `
                    <div class="mt-3">
                        <div class="unmapped-toggle" style="cursor: pointer;" onclick="this.nextElementSibling.style.display = this.nextElementSibling.style.display === 'none' ? 'block' : 'none'">
                            <i class="bi bi-chevron-down"></i> ${unmappedSuppliers.length} Unmapped Suppliers (click to expand)
                        </div>
                        <div class="unmapped-content" style="display: none;">
                            <table class="table table-sm ils-results-table">
                                <thead><tr><th>ILS Company</th><th>CAGE</th><th>Qty</th><th>Condition</th></tr></thead>
                                <tbody>
                `;

                unmappedSuppliers.forEach(item => {
                    html += `
                        <tr>
                            <td>${item.ils_company_name || '-'}</td>
                            <td>${item.ils_cage_code || '-'}</td>
                            <td>${item.quantity || '-'}</td>
                            <td>${item.condition_code || '-'}</td>
                        </tr>
                    `;
                });

                html += '</tbody></table></div></div>';
            }
        }

        container.innerHTML = html;

        container.querySelectorAll('.add-supplier-from-ils').forEach(btn => {
            btn.addEventListener('click', function() {
                const supplierName = this.dataset.supplierName;
                addSuggestedSupplier(this.dataset.lineId, this.dataset.supplierId, supplierName, 'ils');
            });
        });
    }

    // Add supplier to suggested (using event delegation)
    document.addEventListener('click', function(e) {
        const addBtn = e.target.closest('.add-supplier-btn');
        if (!addBtn) return;

        const lineId = addBtn.dataset.lineId;
        const supplierId = addBtn.dataset.supplierId;
        const supplierName = addBtn.dataset.supplierName;
        const source = addBtn.dataset.source;
        addSuggestedSupplier(lineId, supplierId, supplierName, source);
    });

    // IMPROVED: Add supplier with live UI update
    function addSuggestedSupplier(lineId, supplierId, supplierName, source) {
        fetch(`/parts_list/parts-lists/${window.PARTS_LIST_ID}/lines/${lineId}/suggested-suppliers/add`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                supplier_id: supplierId,
                source_type: source
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast('Supplier added to suggested list', 'success');

                // Update the UI live instead of reloading
                updateSuggestedSuppliersUI(lineId, data.suggested_id, supplierName, supplierId, source);

                // Update the row's data attribute
                const mainRow = document.querySelector(`tr.main-row[data-line-id="${lineId}"]`);
                if (mainRow) {
                    mainRow.dataset.hasSuggested = '1';
                }
            } else {
                showToast(data.message || 'Failed to add supplier', 'error');
            }
        })
        .catch(error => {
            showToast('Error: ' + error.message, 'error');
        });
    }

    // NEW: Update suggested suppliers UI without reload
    function updateSuggestedSuppliersUI(lineId, suggestedId, supplierName, supplierId, sourceType) {
        const mainRow = document.querySelector(`tr.main-row[data-line-id="${lineId}"]`);
        if (!mainRow) return;

        const suggestedCell = mainRow.querySelector('.suggested-suppliers-cell');
        if (!suggestedCell) return;

        let wrapper = suggestedCell.querySelector('.suggested-wrapper');
        if (!wrapper) {
            suggestedCell.innerHTML = `
                <div class="suggested-wrapper">
                    <div class="suggested-suppliers-list"></div>
                </div>
            `;
            wrapper = suggestedCell.querySelector('.suggested-wrapper');
        }

        const list = wrapper.querySelector('.suggested-suppliers-list');

        const badge = document.createElement('span');
        badge.className = 'badge bg-info me-1 mb-1';
        badge.innerHTML = `
            ${supplierName}
            <button class="btn-close btn-close-white btn-sm ms-1 remove-supplier-btn"
                    data-suggested-id="${suggestedId}"
                    data-line-id="${lineId}"
                    style="font-size: 0.6rem; padding: 0.1rem;"
                    aria-label="Remove"></button>
        `;

        list.appendChild(badge);
    }

    // Remove supplier from suggested (using event delegation)
    document.addEventListener('click', function(e) {
        const removeBtn = e.target.closest('.remove-supplier-btn');
        if (!removeBtn) return;

        const suggestedId = removeBtn.dataset.suggestedId;
        const lineId = removeBtn.dataset.lineId;

        if (confirm('Remove this supplier from suggested list?')) {
            fetch(`/parts_list/parts-lists/${window.PARTS_LIST_ID}/lines/${lineId}/suggested-suppliers/${suggestedId}/remove`, {
                method: 'POST'
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showToast('Supplier removed', 'success');
                    // Remove from UI live
                    removeBtn.closest('.badge').remove();

                    // Check if that was the last suggested supplier
                    const mainRow = document.querySelector(`tr.main-row[data-line-id="${lineId}"]`);
                    const suggestedCell = mainRow.querySelector('.suggested-suppliers-cell');
                    const remainingBadges = suggestedCell.querySelectorAll('.badge');

                    if (remainingBadges.length === 0) {
                        suggestedCell.innerHTML = '<span class="text-muted">-</span>';
                        mainRow.dataset.hasSuggested = '0';
                    }
                } else {
                    showToast(data.message || 'Failed to remove supplier', 'error');
                }
            })
            .catch(error => {
                showToast('Error: ' + error.message, 'error');
            });
        }
    });

    // NEW: Quick no-bid X button handler
    document.addEventListener('click', function(e) {
        const noBidBtn = e.target.closest('.quick-no-bid-x');
        if (!noBidBtn) return;

        const supplierId = noBidBtn.dataset.supplierId;
        const lineId = noBidBtn.dataset.lineId;
        const supplierName = noBidBtn.dataset.supplierName;

        if (!confirm(`Mark ${supplierName} as NO BID for this part?`)) return;

        fetch(`/parts_list/api/parts-lists/${window.PARTS_LIST_ID}/quick-no-bid/${supplierId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ line_ids: [parseInt(lineId)] })
        })
        .then(r => r.json())
        .then(data => {
            if (!data.success) {
                showToast(data.message || 'Failed to set no bid', 'error');
                return;
            }

            showToast('No bid recorded', 'success');

            // Update UI to show no-bid status
            const supplierSpan = noBidBtn.closest('.contacted-supplier');
            if (supplierSpan) {
                supplierSpan.classList.add('text-decoration-line-through', 'text-muted');
                noBidBtn.remove(); // Remove the X button

                // Add NO BID badge
                const noBidBadge = document.createElement('span');
                noBidBadge.className = 'badge bg-danger ms-1';
                noBidBadge.textContent = 'NO BID';
                supplierSpan.appendChild(noBidBadge);
            }
        })
        .catch(err => {
            showToast(err.message, 'error');
        });
    });

    // Export functionality
    const exportBtn = document.getElementById('export-sourcing-btn');
    if (exportBtn) {
        exportBtn.addEventListener('click', function() {
            window.location.href = `/parts_list/api/parts-lists/${window.PARTS_LIST_ID}/export-sourcing`;
        });
    }

    // Helper function to show toast messages
    function showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `alert alert-${type === 'error' ? 'danger' : type} position-fixed`;
        toast.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
        toast.textContent = message;

        document.body.appendChild(toast);

        setTimeout(() => {
            toast.remove();
        }, 3000);
    }

    // Quick No Bid modal
    const quickNoBidBtn = document.getElementById('quick-no-bid-btn');
    if (quickNoBidBtn) {
        quickNoBidBtn.addEventListener('click', function() {
            const modalEl = document.getElementById('quickNoBidModal');
            const modal = new bootstrap.Modal(modalEl);
            const container = document.getElementById('quick-no-bid-content');

            container.innerHTML = '<div class="text-center py-3"><div class="spinner-border" role="status"></div></div>';
            modal.show();

            fetch(`/parts_list/api/parts-lists/${window.PARTS_LIST_ID}/quick-no-bid`)
                .then(res => res.json())
                .then(data => {
                    if (!data.success) {
                        container.innerHTML = `<div class="alert alert-danger">${data.message || 'Error loading data'}</div>`;
                        return;
                    }

                    if (!data.suppliers.length) {
                        container.innerHTML = '<div class="alert alert-info">No contacted suppliers found for this parts list.</div>';
                        return;
                    }

                    let html = '';
                    data.suppliers.forEach(sup => {
                        html += `
                            <div class="card mb-3">
                                <div class="card-header d-flex justify-content-between align-items-center">
                                    <div>
                                        <strong>${sup.supplier_name}</strong>
                                        <span class="badge bg-secondary ms-2">${sup.lines.length} lines</span>
                                    </div>
                                    <button class="btn btn-sm btn-outline-danger quick-no-bid-all-btn"
                                            data-supplier-id="${sup.supplier_id}">
                                        No Bid All
                                    </button>
                                </div>
                                <div class="card-body p-2">
                                    <table class="table table-sm mb-0">
                                        <thead>
                                            <tr>
                                                <th style="width:40px;">#</th>
                                                <th>Part Number</th>
                                                <th style="width:80px;" class="text-center">No Bid</th>
                                            </tr>
                                        </thead>
                                        <tbody>`;
                        sup.lines.forEach(line => {
                            html += `
                                            <tr>
                                                <td>${line.line_number}</td>
                                                <td>${line.customer_part_number}</td>
                                                <td class="text-center">
                                                    <input type="checkbox"
                                                           class="form-check-input quick-no-bid-line"
                                                           data-supplier-id="${sup.supplier_id}"
                                                           data-line-id="${line.line_id}"
                                                           ${line.has_no_bid ? 'checked' : ''}>
                                                </td>
                                            </tr>`;
                        });
                        html += `
                                        </tbody>
                                    </table>
                                </div>
                            </div>`;
                    });

                    container.innerHTML = html;
                })
                .catch(err => {
                    container.innerHTML = `<div class="alert alert-danger">${err.message}</div>`;
                });
        });
    }

    // Supplier panel modal
    const supplierPanelBtn = document.getElementById('supplier-panel-btn');
    if (supplierPanelBtn) {
        supplierPanelBtn.addEventListener('click', function() {
            const modalEl = document.getElementById('supplierPanelModal');
            const modal = new bootstrap.Modal(modalEl);
            const container = document.getElementById('supplier-panel-content');

            container.innerHTML = '<div class="text-center py-3"><div class="spinner-border" role="status"></div></div>';
            modal.show();

            fetch(`/parts_list/parts-lists/${window.PARTS_LIST_ID}/supplier-panel-data`)
                .then(res => res.json())
                .then(data => {
                    if (!data.success) {
                        container.innerHTML = `<div class="alert alert-danger">${data.message || 'Error loading supplier data'}</div>`;
                        return;
                    }

                    if (!data.suppliers || data.suppliers.length === 0) {
                        container.innerHTML = '<div class="alert alert-info">No contacted suppliers found for this parts list.</div>';
                        return;
                    }

                    container.innerHTML = renderSupplierPanel(data.suppliers);
                })
                .catch(err => {
                    container.innerHTML = `<div class="alert alert-danger">${err.message}</div>`;
                });
        });
    }

    // Individual line no-bid
    document.addEventListener('change', function(e) {
        const cb = e.target.closest('.quick-no-bid-line');
        if (!cb) return;

        const supplierId = cb.dataset.supplierId;
        const lineId = cb.dataset.lineId;
        const checked = cb.checked;

        if (!checked) {
            return;
        }

        fetch(`/parts_list/api/parts-lists/${window.PARTS_LIST_ID}/quick-no-bid/${supplierId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ line_ids: [lineId] })
        })
        .then(r => r.json())
        .then(data => {
            if (!data.success) {
                showToast(data.message || 'Failed to set no bid', 'error');
                cb.checked = false;
            } else {
                showToast('No bid recorded', 'success');
            }
        })
        .catch(err => {
            showToast(err.message, 'error');
            cb.checked = false;
        });
    });

    // "No Bid All" for a supplier
    document.addEventListener('click', function(e) {
        const btn = e.target.closest('.quick-no-bid-all-btn');
        if (!btn) return;

        const supplierId = btn.dataset.supplierId;

        if (!confirm('Mark all emailed lines for this supplier as NO BID?')) return;

        fetch(`/parts_list/api/parts-lists/${window.PARTS_LIST_ID}/quick-no-bid/${supplierId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ all: true })
        })
        .then(r => r.json())
        .then(data => {
            if (!data.success) {
                showToast(data.message || 'Failed to set no bids', 'error');
                return;
            }

            showToast('No bid recorded for all emailed lines for this supplier', 'success');

            document.querySelectorAll(`.quick-no-bid-line[data-supplier-id="${supplierId}"]`)
                .forEach(cb => cb.checked = true);
        })
        .catch(err => {
            showToast(err.message, 'error');
        });
    });

    console.log('All event listeners attached');
});

function renderSupplierPanel(suppliers) {
    let html = '';
    let matchedSuppliers = 0;

    html += `
        <div class="d-flex justify-content-between align-items-center mb-3">
            <div class="text-muted-sm">Showing awaiting quote lines only</div>
        </div>
    `;

    suppliers.forEach(sup => {
        const lines = sup.lines || [];
        const filteredLines = lines.filter(line => {
            const isNoBid = line.is_no_bid === true || line.is_no_bid === 1 || line.is_no_bid === '1' || line.is_no_bid === 'true';
            const isCosted = line.is_costed === true || line.is_costed === 1 || line.is_costed === '1' || line.is_costed === 'true';
            const hasQuote = line.quoted_price !== null && line.quoted_price !== undefined && line.quoted_price !== '';
            return !isNoBid && !isCosted && !hasQuote;
        });

        if (filteredLines.length === 0) {
            return;
        }

        matchedSuppliers += 1;
        const displayedTotal = filteredLines.length;
        const contactInfo = [sup.contact_name, sup.contact_email].filter(Boolean).join(' | ');

        html += `
            <div class="card mb-3 supplier-panel-card" data-supplier-name="${sup.supplier_name}">
                <div class="card-header d-flex justify-content-between align-items-start flex-wrap gap-2">
                    <div>
                        <strong>${sup.supplier_name}</strong>
                        ${contactInfo ? `<div class="text-muted-sm">${contactInfo}</div>` : ''}
                    </div>
                    <div class="d-flex align-items-center flex-wrap gap-2">
                        <div class="d-flex flex-wrap gap-1">
                            <span class="badge bg-secondary">Total ${displayedTotal}</span>
                            <span class="badge bg-success">Quoted 0</span>
                            <span class="badge bg-danger">No Bid 0</span>
                            <span class="badge bg-warning text-dark">Awaiting ${displayedTotal}</span>
                            <span class="badge bg-info text-dark">Costed 0</span>
                        </div>
                        <button class="btn btn-sm btn-outline-primary supplier-panel-copy-supplier">
                            <i class="bi bi-clipboard me-1"></i>Copy
                        </button>
                    </div>
                </div>
                <div class="card-body p-2">
                    <div class="table-responsive">
                        <table class="table table-sm mb-0">
                            <thead>
                                <tr>
                                    <th style="width:40px;">#</th>
                                    <th>Part Number</th>
                                    <th style="width:70px;">Qty</th>
                                    <th style="width:160px;">Sent</th>
                                    <th style="width:160px;">Recipient</th>
                                    <th style="width:100px;">Status</th>
                                    <th style="width:140px;">Quote</th>
                                </tr>
                            </thead>
                            <tbody>
        `;

        filteredLines.forEach(line => {
            const statusBadge = line.is_no_bid
                ? '<span class="badge bg-danger">No Bid</span>'
                : (line.quoted_price !== null && line.quoted_price !== undefined)
                    ? '<span class="badge bg-success">Quoted</span>'
                    : '<span class="badge bg-warning text-dark">Awaiting</span>';
            const costedBadge = line.is_costed ? ' <span class="badge bg-info text-dark">Costed</span>' : '';
            const quoteText = line.quoted_price !== null && line.quoted_price !== undefined
                ? formatPrice(line.quoted_price, line.currency_code)
                : '-';

            html += `
                <tr class="supplier-panel-row">
                    <td>${line.line_number ?? '-'}</td>
                    <td>${line.customer_part_number || '-'}</td>
                    <td>${line.quantity ?? '-'}</td>
                    <td>${line.date_sent || '-'}</td>
                    <td>${line.recipient_name || '-'}</td>
                    <td>${statusBadge}${costedBadge}</td>
                    <td>${quoteText}</td>
                </tr>
            `;
        });

        html += `
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        `;
    });

    if (matchedSuppliers === 0) {
        return '<div class="alert alert-info">No awaiting-quote lines found (costed and no-bid hidden).</div>';
    }

    setTimeout(bindSupplierPanelCopy, 0);
    return html;
}

function formatPrice(value, currencyCode) {
    const amount = parseFloat(value);
    if (Number.isNaN(amount)) {
        return '-';
    }
    return `${currencyCode || 'GBP'} ${amount.toFixed(2)}`;
}

function bindSupplierPanelCopy() {
    const copyButtons = document.querySelectorAll('.supplier-panel-copy-supplier');
    copyButtons.forEach(btn => {
        if (btn.dataset.bound === '1') {
            return;
        }
        btn.dataset.bound = '1';

        btn.addEventListener('click', function() {
            const card = btn.closest('.supplier-panel-card');
            if (!card) {
                return;
            }

            const rows = card.querySelectorAll('.supplier-panel-row');
            if (!rows.length) {
                return;
            }

            const header = ['Supplier', '#', 'Part Number', 'Qty', 'Sent', 'Recipient', 'Status', 'Quote'];
            const lines = [header.join('\t')];
            const supplierName = card.dataset.supplierName || '';

            rows.forEach(row => {
                if (row.style.display === 'none') {
                    return;
                }
                const cells = row.querySelectorAll('td');
                const statusText = cells[5] ? cells[5].textContent.trim().replace(/\s+/g, ' ') : '';
                const quoteText = cells[6] ? cells[6].textContent.trim() : '';

                lines.push([
                    supplierName,
                    cells[0] ? cells[0].textContent.trim() : '',
                    cells[1] ? cells[1].textContent.trim() : '',
                    cells[2] ? cells[2].textContent.trim() : '',
                    cells[3] ? cells[3].textContent.trim() : '',
                    cells[4] ? cells[4].textContent.trim() : '',
                    statusText,
                    quoteText
                ].join('\t'));
            });

            copyToClipboard(lines.join('\n'), btn);
        });
    });
}

function copyToClipboard(text, buttonEl) {
    const setFeedback = () => {
        if (!buttonEl) return;
        const original = buttonEl.innerHTML;
        buttonEl.innerHTML = '<i class="bi bi-check2 me-1"></i>Copied';
        buttonEl.classList.remove('btn-outline-primary');
        buttonEl.classList.add('btn-success');
        setTimeout(() => {
            buttonEl.innerHTML = original;
            buttonEl.classList.remove('btn-success');
            buttonEl.classList.add('btn-outline-primary');
        }, 1500);
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(setFeedback).catch(() => {});
        return;
    }

    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'absolute';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    try {
        document.execCommand('copy');
        setFeedback();
    } finally {
        document.body.removeChild(textarea);
    }
}

// Email ILS Suppliers button
const emailSuppliersBtn = document.getElementById('email-suppliers-btn');

if (emailSuppliersBtn) {
    const hasILSData = document.querySelectorAll('.sourcing-table .populated').length > 0 ||
                       (window.LINE_DATA && window.LINE_DATA.some(line =>
                           line.ils_data && line.ils_data.length > 0
                       ));

    console.log('Has ILS data:', hasILSData);

    if (hasILSData) {
        emailSuppliersBtn.style.display = 'inline-block';
    }

    emailSuppliersBtn.addEventListener('click', function() {
        console.log('Email ILS Suppliers clicked');

        const lines = document.querySelectorAll('.sourcing-table tbody tr.main-row');
        if (lines.length === 0) {
            alert('No parts found');
            return;
        }

        const partsToAnalyze = [];
        lines.forEach(row => {
            const lineId = row.dataset.lineId;
            const partNumber = row.querySelector('td:nth-child(2) strong').textContent.trim();
            const partText = partNumber.split('\n')[0].trim();
            const qtyBadge = row.querySelector('td:nth-child(3) .badge');
            const quantity = qtyBadge ? parseInt(qtyBadge.textContent.trim()) : 1;

            partsToAnalyze.push({
                part_number: partText,
                quantity: quantity,
                line_id: parseInt(lineId)
            });
        });

        console.log('Parts to analyze:', partsToAnalyze);

        const originalText = this.innerHTML;
        this.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading...';
        this.disabled = true;

        fetch('/parts_list/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                parts: partsToAnalyze
            })
        })
        .then(response => response.json())
        .then(data => {
            console.log('Analyze response:', data);

            if (!data.success || !data.results) {
                alert('Error analyzing parts');
                this.innerHTML = originalText;
                this.disabled = false;
                return;
            }

            return fetch('/parts_list/email-suppliers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    mode: 'ils',
                    results: data.results,
                    list_id: window.PARTS_LIST_ID
                })
            });
        })
        .then(response => response.json())
        .then(data => {
            console.log('Email suppliers response:', data);

            if (data.success && data.redirect) {
                window.location.href = data.redirect;
            } else {
                alert('Error: ' + (data.message || 'Failed to navigate to email page'));
                this.innerHTML = originalText;
                this.disabled = false;
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error navigating to email page: ' + error.message);
            this.innerHTML = originalText;
            this.disabled = false;
        });
    });
}

// Email Suggested Suppliers button
const emailSuggestedBtn = document.getElementById('email-suggested-suppliers-btn');

if (emailSuggestedBtn) {
    const hasSuggestedSuppliers = document.querySelectorAll('.sourcing-table .suggested-wrapper').length > 0;

    console.log('Has suggested suppliers:', hasSuggestedSuppliers);

    if (hasSuggestedSuppliers) {
        emailSuggestedBtn.style.display = 'inline-block';
    }

    emailSuggestedBtn.addEventListener('click', function() {
        console.log('Email Suggested Suppliers clicked');

        const lines = document.querySelectorAll('.sourcing-table tbody tr.main-row');
        console.log('Found lines:', lines.length);

        if (lines.length === 0) {
            alert('No parts found');
            return;
        }

        const partsToSend = [];
        lines.forEach((row, index) => {
            const lineId = row.dataset.lineId;
            const partNumberElement = row.querySelector('td:nth-child(2) strong');

            if (!partNumberElement) {
                console.warn(`Row ${index}: No part number element found`);
                return;
            }

            const partNumber = partNumberElement.textContent.trim();
            const partText = partNumber.split('\n')[0].trim();
            const qtyBadge = row.querySelector('td:nth-child(3) .badge');
            const quantity = qtyBadge ? parseInt(qtyBadge.textContent.trim()) : 1;

            if (!lineId) {
                console.warn(`Row ${index}: Part ${partText} has no line_id`);
                return;
            }

            const partData = {
                input_part_number: partText,
                quantity: quantity,
                line_id: parseInt(lineId)
            };

            console.log(`Adding part ${index}:`, partData);
            partsToSend.push(partData);
        });

        console.log('Parts to send:', partsToSend);
        console.log('Total parts with line_id:', partsToSend.length);

        if (partsToSend.length === 0) {
            alert('No parts with valid line IDs found');
            return;
        }

        const originalText = this.innerHTML;
        this.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading...';
        this.disabled = true;

        fetch('/parts_list/email-suppliers', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                mode: 'suggested',
                results: partsToSend,
                list_id: window.PARTS_LIST_ID
            })
        })
        .then(response => response.json())
        .then(data => {
            console.log('Email suppliers response:', data);

            if (data.success && data.redirect) {
                window.location.href = data.redirect;
            } else {
                alert('Error: ' + (data.message || 'Failed to navigate to email page'));
                this.innerHTML = originalText;
                this.disabled = false;
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error navigating to email page: ' + error.message);
            this.innerHTML = originalText;
            this.disabled = false;
        });
    });
}


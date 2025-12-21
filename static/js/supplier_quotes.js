// Supplier Quotes Management
let currentQuoteId = null;
let quoteLinesTable = null;
let quoteLinesData = [];
let currentSupplierId = window.PRESELECTED_SUPPLIER_ID || null;
let showSentOnly = false;
let partNumberFilterValue = '';
let emailedSuppliersCache = null;

function noBidCheckboxRenderer(instance, td, row, col, prop, value, cellProperties) {
    Handsontable.dom.empty(td);
    const checked = !!value;
    td.textContent = checked ? '✓' : '';
    td.style.textAlign = 'center';
    td.style.cursor = 'pointer';
    td.style.fontWeight = 'bold';

    td.onclick = function (e) {
        e.stopPropagation();
        const current = !!instance.getDataAtCell(row, col);
        instance.setDataAtCell(row, col, !current);
    };

    return td;
}

// Part number normalization
function normalizePN(pn) {
    if (!pn) return '';
    return pn.toString().toUpperCase().replace(/[^A-Z0-9]/g, '');
}

// Levenshtein distance for fuzzy matching
function levenshteinDistance(a, b) {
    const m = a.length;
    const n = b.length;

    if (m === 0) return n;
    if (n === 0) return m;

    const dp = Array.from({ length: m + 1 }, () => new Array(n + 1));

    for (let i = 0; i <= m; i++) dp[i][0] = i;
    for (let j = 0; j <= n; j++) dp[0][j] = j;

    for (let i = 1; i <= m; i++) {
        for (let j = 1; j <= n; j++) {
            const cost = a[i - 1] === b[j - 1] ? 0 : 1;
            dp[i][j] = Math.min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost
            );
        }
    }

    return dp[m][n];
}

function pnSimilarity(a, b) {
    const na = normalizePN(a);
    const nb = normalizePN(b);

    if (!na || !nb) return 0;
    if (na === nb) return 1;

    const dist = levenshteinDistance(na, nb);
    const maxLen = Math.max(na.length, nb.length);

    return 1 - dist / maxLen;
}

// ========== DOM READY ==========
document.addEventListener('DOMContentLoaded', function() {
    // Initialize supplier quotes button
    const manageQuotesBtn = document.getElementById('manage-supplier-quotes-btn');
    if (manageQuotesBtn) {
        manageQuotesBtn.addEventListener('click', openSupplierQuotesModal);
    }

    // Create new quote
    document.getElementById('create-new-quote-btn')?.addEventListener('click', function() {
        showQuoteInputView(null);
    });

    // Back to list
    document.getElementById('back-to-quotes-list')?.addEventListener('click', showQuotesListView);

    // Extract quote data
    document.getElementById('extract-quote-btn')?.addEventListener('click', extractQuoteData);

    // Save quote
    document.getElementById('save-quote-btn')?.addEventListener('click', saveSupplierQuote);

    // Delete quote
    document.getElementById('delete-quote-btn')?.addEventListener('click', deleteSupplierQuote);

    // Initialize PDF drop zone - ALWAYS initialize if element exists
    if (document.getElementById('pdf-drop-zone')) {
        initializePdfDropZone();
    }

    // Initialize for quick quote page
    if (window.IS_QUICK_QUOTE) {
        // Load suppliers for the quick quote page
        if (document.getElementById('quote-supplier-select')) {
            initializeQuickQuoteSuppliers();
        }

        // Initialize empty quote lines table for quick quote page
        initializeEmptyQuoteLines(currentSupplierId);
    }

    initializeEmailedSupplierSelect();

    if (window.OPEN_QUOTE_ID) {
        openSupplierQuotesModal();
        const quoteId = parseInt(window.OPEN_QUOTE_ID);
        if (Number.isFinite(quoteId)) {
            setTimeout(() => loadQuoteForEditing(quoteId), 200);
        }
    }
});

// ========== PDF DROP ZONE ==========
function initializePdfDropZone() {
    const dropZone = document.getElementById('pdf-drop-zone');
    const fileInput = document.getElementById('pdf-upload-input');

    if (!dropZone || !fileInput) return;

    // Drag events
    ['dragover', 'dragenter'].forEach(evt => {
        dropZone.addEventListener(evt, e => {
            e.preventDefault();
            e.stopPropagation();
            dropZone.classList.add('drag-over');
        });
    });

    ['dragleave', 'dragend', 'drop'].forEach(evt => {
        dropZone.addEventListener(evt, e => {
            e.preventDefault();
            e.stopPropagation();
            dropZone.classList.remove('drag-over');
        });
    });

    // Handle drop
    dropZone.addEventListener('drop', e => {
        const file = e.dataTransfer.files[0];
        if (file && file.type === 'application/pdf') {
            uploadAndExtractPdf(file);
        } else {
            showToast('Please drop a PDF file', 'warning');
        }
    });

    // Handle click to browse
    dropZone.addEventListener('click', () => fileInput.click());

    fileInput.addEventListener('change', () => {
        if (fileInput.files[0]) {
            uploadAndExtractPdf(fileInput.files[0]);
        }
    });

    ensureQuoteLinesToolbar();
    applyVisibilityFilters();
}

function ensureQuoteLinesToolbar(container) {
    const target = container || document.getElementById('quote-lines-table-container');
    if (!target) return;
    const parent = target.parentElement;
    if (!parent) return;

    let toolbar = document.getElementById('quote-lines-toolbar');
    if (!toolbar) {
        toolbar = document.createElement('div');
        toolbar.id = 'quote-lines-toolbar';
        toolbar.className = 'd-flex justify-content-between align-items-center px-3 py-2 border-bottom';
        toolbar.innerHTML = `
            <div class="d-flex align-items-center gap-2">
                <button type="button" class="btn btn-sm btn-outline-info" id="toggle-sent-filter-btn">
                    <i class="bi bi-envelope-check me-1"></i>Sent to Supplier
                </button>
                <button type="button" class="btn btn-sm btn-outline-secondary" id="clear-part-filter-btn">
                    <i class="bi bi-eraser me-1"></i>Clear Part Filter
                </button>
            </div>
            <small class="text-muted" id="quote-filter-indicator" style="display:none;">Filters active</small>
        `;
        parent.insertBefore(toolbar, target);

        document.getElementById('toggle-sent-filter-btn').addEventListener('click', function() {
            showSentOnly = !showSentOnly;
            this.classList.toggle('btn-outline-info', !showSentOnly);
            this.classList.toggle('btn-info', showSentOnly);
            applyVisibilityFilters();
        });

        document.getElementById('clear-part-filter-btn').addEventListener('click', function() {
            partNumberFilterValue = '';
            const input = document.querySelector('.part-filter-input');
            if (input) input.value = '';
            applyVisibilityFilters();
        });
    }

    const toggleBtn = document.getElementById('toggle-sent-filter-btn');
    if (toggleBtn) {
        toggleBtn.classList.toggle('btn-info', showSentOnly);
        toggleBtn.classList.toggle('btn-outline-info', !showSentOnly);
    }
}

function applyVisibilityFilters() {
    if (!quoteLinesTable) return;

    const hiddenRowsPlugin = quoteLinesTable.getPlugin('hiddenRows');
    if (!hiddenRowsPlugin) return;

    const rowsToHide = [];
    const filterValue = partNumberFilterValue;

    quoteLinesData.forEach((line, idx) => {
        const matchesPart = filterValue
            ? (line.customer_part_number || '').toLowerCase().includes(filterValue) ||
              (line.quoted_part_number || '').toLowerCase().includes(filterValue)
            : true;
        const matchesSent = showSentOnly ? !!line.quote_requested : true;

        if (!(matchesPart && matchesSent)) {
            rowsToHide.push(idx);
        }
    });

    // Reset then apply new hidden rows
    if (hiddenRowsPlugin.getHiddenRows) {
        const hidden = hiddenRowsPlugin.getHiddenRows();
        if (hidden.length) {
            hiddenRowsPlugin.showRows(hidden);
        }
    }

    if (rowsToHide.length > 0) {
        hiddenRowsPlugin.hideRows(rowsToHide);
    }
    quoteLinesTable.render();

    const indicator = document.getElementById('quote-filter-indicator');
    if (indicator) {
        indicator.style.display = (showSentOnly || filterValue) ? 'block' : 'none';
    }
}

function uploadAndExtractPdf(file) {
    // Show loading state
    const dropZone = document.getElementById('pdf-drop-zone');
    const originalHTML = dropZone.innerHTML;

    dropZone.innerHTML = `
        <div class="card-body text-center py-5">
            <div class="spinner-border text-primary mb-3" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
            <h5 class="mb-2">Processing PDF...</h5>
            <p class="text-muted mb-0">Extracting quote data with AI</p>
        </div>
    `;

    const formData = new FormData();
    formData.append('file', file);

    if (window.PARTS_LIST_ID) {
        formData.append('list_id', window.PARTS_LIST_ID);
    }

    fetch('/parts_list/extract-quote-from-pdf', {
        method: 'POST',
        body: formData
    })
    .then(r => r.json())
    .then(data => {
        console.log('extract-quote-from-pdf response:', data);

        if (!data.success) {
            showToast('Error: ' + (data.message || 'PDF extraction failed'), 'danger');
            dropZone.innerHTML = originalHTML;
            initializePdfDropZone(); // Re-initialize
            return;
        }

        // Show success state
        dropZone.innerHTML = `
            <div class="card-body text-center py-5">
                <i class="bi bi-check-circle display-3 text-success mb-3"></i>
                <h5 class="mb-2">PDF Processed!</h5>
                <p class="text-muted mb-0">Quote data extracted successfully</p>
            </div>
        `;

        // Optional: show raw text in textarea
        if (data.raw_text) {
            document.getElementById('supplier-response-text').value = data.raw_text;
        }

        const extractedLines =
            data.extracted_lines ||
            data.items ||
            data.lines ||
            (Array.isArray(data) ? data : []);

        if (Array.isArray(extractedLines) && extractedLines.length > 0) {
            applyExtractedDataToTable(extractedLines);
            showToast(`AI extracted ${extractedLines.length} lines from PDF!`, 'success');
        } else {
            showToast('PDF processed but no quoted parts were found', 'warning');
        }

        // Reset drop zone after 3 seconds
        setTimeout(() => {
            dropZone.innerHTML = originalHTML;
            initializePdfDropZone();
        }, 3000);
    })
    .catch(err => {
        console.error(err);
        showToast('Upload failed', 'danger');
        dropZone.innerHTML = originalHTML;
        initializePdfDropZone(); // Re-initialize
    });
}

// ========== MODAL MANAGEMENT ==========
function openSupplierQuotesModal() {
    document.getElementById('quotes-list-view').style.display = 'block';
    document.getElementById('quote-input-view').style.display = 'none';
    loadSupplierQuotes();
    const modal = new bootstrap.Modal(document.getElementById('supplierQuotesModal'));
    modal.show();
}

function loadSupplierQuotes() {
    fetch(`/parts_list/parts-lists/${window.PARTS_LIST_ID}/supplier-quotes`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                displayQuotesList(data.quotes);
            }
        })
        .catch(error => console.error('Error loading quotes:', error));
}

function displayQuotesList(quotes) {
    const container = document.getElementById('quotes-list-container');

    if (quotes.length === 0) {
        container.innerHTML = `
            <div class="alert alert-info">
                No supplier quotes yet. Click "New Quote" to add one.
            </div>
        `;
        return;
    }

    let html = '<div class="list-group">';
    quotes.forEach(quote => {
        html += `
            <div class="list-group-item list-group-item-action"
                 style="cursor: pointer;"
                 data-quote-id="${quote.id}">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <h6 class="mb-1">${quote.supplier_name}</h6>
                        <small class="text-muted">
                            ${quote.quote_reference || 'No reference'} |
                            ${quote.quote_date || 'No date'} |
                            ${quote.currency_code}
                        </small>
                    </div>
                    <div class="text-end">
                        <span class="badge bg-primary">${quote.line_count} lines</span>
                        ${quote.no_bid_count > 0 ? `<span class="badge bg-warning ms-1">${quote.no_bid_count} no-bids</span>` : ''}
                    </div>
                </div>
            </div>
        `;
    });
    html += '</div>';

    container.innerHTML = html;

    container.querySelectorAll('.list-group-item').forEach(item => {
        item.addEventListener('click', function() {
            const quoteId = parseInt(this.dataset.quoteId);
            loadQuoteForEditing(quoteId);
        });
    });
}

function showQuoteInputView(quoteId = null) {
    currentQuoteId = quoteId;
    currentSupplierId = window.PRESELECTED_SUPPLIER_ID || null;
    showSentOnly = false;
    partNumberFilterValue = '';

    document.getElementById('quotes-list-view').style.display = 'none';
    document.getElementById('quote-input-view').style.display = 'block';

    const today = new Date().toISOString().split('T')[0];
    document.getElementById('quote-date-input').value = today;

    loadSuppliersForQuote();

    if (quoteId) {
        loadQuoteForEditing(quoteId);
        const deleteBtn = document.getElementById('delete-quote-btn');
        if (deleteBtn) deleteBtn.style.display = 'block';
    } else {
        document.getElementById('quote-supplier-select').value = '';
        document.getElementById('quote-reference-input').value = '';
        document.getElementById('quote-currency-select').value = 3;

        const notesInput = document.getElementById('quote-notes-input');
        if (notesInput) notesInput.value = '';

        const responseText = document.getElementById('supplier-response-text');
        if (responseText) responseText.value = '';

        const deleteBtn = document.getElementById('delete-quote-btn');
        if (deleteBtn) deleteBtn.style.display = 'none';

        initializeEmptyQuoteLines(currentSupplierId);
    }
}

function showQuotesListView() {
    document.getElementById('quote-input-view').style.display = 'none';
    document.getElementById('quotes-list-view').style.display = 'block';

    if (quoteLinesTable) {
        quoteLinesTable.destroy();
        quoteLinesTable = null;
    }

    loadSupplierQuotes();
}

function loadSuppliersForQuote() {
    $('#quote-supplier-select').select2({
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
                            text: item.name,
                            currency_id: item.currency_id,
                            similarity_score: item.similarity_score
                        };
                    })
                };
            },
            cache: true
        },
        placeholder: 'Search for supplier...',
        minimumInputLength: 0,
        allowClear: true,
        width: '100%',
        dropdownParent: $('#supplierQuotesModal')
    }).on('select2:select', function (e) {
        var data = e.params.data;
        if (data.currency_id) {
            document.getElementById('quote-currency-select').value = data.currency_id;
        }
        if (!currentQuoteId) {
            currentSupplierId = parseInt(data.id);
            initializeEmptyQuoteLines(currentSupplierId);
        }
    }).on('select2:clear', function () {
        if (!currentQuoteId) {
            currentSupplierId = null;
            initializeEmptyQuoteLines();
        }
    });

    initializeEmailedSupplierSelect();

    ensureQuoteLinesToolbar();
    applyVisibilityFilters();
}

// For quick quote page (no modal)
function initializeQuickQuoteSuppliers() {
    const $supplierSelect = $('#quote-supplier-select');

    $supplierSelect.select2({
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
                            text: item.name,
                            currency_id: item.currency_id
                        };
                    })
                };
            },
            cache: true
        },
        placeholder: 'Search for supplier...',
        minimumInputLength: 0,
        allowClear: true,
        width: '100%'
    }).on('select2:select', function (e) {
        var data = e.params.data;
        if (data.currency_id) {
            var currencySelect = document.getElementById('quote-currency-select');
            if (currencySelect) {
                currencySelect.value = data.currency_id;
            }
        }
        if (!currentQuoteId) {
            currentSupplierId = parseInt(data.id);
            initializeEmptyQuoteLines(currentSupplierId);
        }
    }).on('select2:clear', function () {
        if (!currentQuoteId) {
            currentSupplierId = null;
            initializeEmptyQuoteLines();
        }
    });

    initializeEmailedSupplierSelect();

    // Pre-select supplier if provided and fetch its currency
    if (window.PRESELECTED_SUPPLIER_ID) {
        fetch(`/suppliers/api/${window.PRESELECTED_SUPPLIER_ID}`)
            .then(response => response.json())
            .then(data => {
                if (data.success && data.supplier) {
                    const supplier = data.supplier;

                    // Create and append the option
                    const newOption = new Option(supplier.name, supplier.id, true, true);
                    $supplierSelect.append(newOption);

                    // Trigger change to update Select2
                    $supplierSelect.trigger('change');
                    currentSupplierId = supplier.id;

                    // Set the currency AFTER Select2 is fully initialized
                    setTimeout(() => {
                        if (supplier.currency_id) {
                            var currencySelect = document.getElementById('quote-currency-select');
                            if (currencySelect) {
                                currencySelect.value = supplier.currency_id;
                                console.log('Set currency to:', supplier.currency_id);
                            }
                        }
                    }, 100);
                }
            })
            .catch(error => {
                console.error('Error loading supplier:', error);
                $supplierSelect.val(window.PRESELECTED_SUPPLIER_ID).trigger('change');
            });
    }
}

function initializeEmailedSupplierSelect() {
    const emailedSelect = document.getElementById('emailed-supplier-select');
    if (!emailedSelect || !window.PARTS_LIST_ID) return;

    fetchEmailedSuppliers()
        .then(suppliers => {
            if (!suppliers.length) {
                emailedSelect.style.display = 'none';
                return;
            }

            emailedSelect.innerHTML = '<option value="">Emailed suppliers...</option>';
            suppliers.forEach(supplier => {
                const option = document.createElement('option');
                option.value = supplier.supplier_id;
                option.textContent = supplier.contact_email
                    ? `${supplier.supplier_name} (${supplier.contact_email})`
                    : supplier.supplier_name;
                option.dataset.currencyId = supplier.currency_id || '';
                emailedSelect.appendChild(option);
            });
            emailedSelect.style.display = '';
        })
        .catch(err => console.error('Error loading emailed suppliers:', err));

    emailedSelect.onchange = function() {
        const supplierId = this.value;
        if (!supplierId) return;

        const selectedOption = this.options[this.selectedIndex];
        const supplierName = selectedOption ? selectedOption.textContent : '';
        const currencyId = selectedOption?.dataset.currencyId;

        const supplierSelect = $('#quote-supplier-select');
        if (supplierSelect.find(`option[value="${supplierId}"]`).length === 0) {
            const newOption = new Option(supplierName || 'Selected Supplier', supplierId, true, true);
            supplierSelect.append(newOption);
        } else {
            supplierSelect.val(supplierId);
        }
        supplierSelect.trigger('change');

        if (currencyId) {
            const currencySelect = document.getElementById('quote-currency-select');
            if (currencySelect) {
                currencySelect.value = currencyId;
            }
        }

        if (!currentQuoteId) {
            currentSupplierId = parseInt(supplierId);
            initializeEmptyQuoteLines(currentSupplierId);
        }

        this.value = '';
    };
}

function fetchEmailedSuppliers() {
    if (emailedSuppliersCache) return Promise.resolve(emailedSuppliersCache);
    return fetch(`/parts_list/parts-lists/${window.PARTS_LIST_ID}/emailed-suppliers`)
        .then(response => response.json())
        .then(data => {
            if (!data.success) return [];
            emailedSuppliersCache = data.suppliers || [];
            return emailedSuppliersCache;
        });
}

function loadQuoteForEditing(quoteId) {
    showSentOnly = false;
    partNumberFilterValue = '';

    document.getElementById('quotes-list-view').style.display = 'none';
    document.getElementById('quote-input-view').style.display = 'block';

    const container = document.getElementById('quote-lines-table-container');
    container.innerHTML = '<div class="text-center p-4"><div class="spinner-border"></div><p class="mt-2">Loading quote...</p></div>';

    fetch(`/parts_list/parts-lists/${window.PARTS_LIST_ID}/supplier-quotes/${quoteId}`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                currentQuoteId = quoteId;
                currentSupplierId = data.quote ? data.quote.supplier_id : null;
                loadSuppliersForQuote();

                setTimeout(() => {
                    populateQuoteForm(data.quote);
                }, 500);

                initializeQuoteLinesTable(data.lines);

                const deleteBtn = document.getElementById('delete-quote-btn');
                if (deleteBtn) {
                    deleteBtn.style.display = 'block';
                }
            } else {
                showToast('Error loading quote: ' + data.message, 'danger');
                showQuotesListView();
            }
        })
        .catch(error => {
            console.error('Error loading quote:', error);
            showToast('Error loading quote', 'danger');
            showQuotesListView();
        });
}

function populateQuoteForm(quote) {
    const supplierSelect = $('#quote-supplier-select');

    if (supplierSelect.find(`option[value="${quote.supplier_id}"]`).length === 0) {
        const newOption = new Option(quote.supplier_name, quote.supplier_id, true, true);
        supplierSelect.append(newOption);
    } else {
        supplierSelect.val(quote.supplier_id);
    }
    supplierSelect.trigger('change');

    document.getElementById('quote-reference-input').value = quote.quote_reference || '';
    document.getElementById('quote-date-input').value = quote.quote_date || '';
    document.getElementById('quote-currency-select').value = quote.currency_id;

    const notesInput = document.getElementById('quote-notes-input');
    if (notesInput) {
        notesInput.value = quote.notes || '';
    }
}

function initializeEmptyQuoteLines(supplierId = null) {
    let url = `/parts_list/parts-lists/${window.PARTS_LIST_ID}/lines`;

    // Add supplier_id to URL if available
    if (supplierId) {
        url += `?supplier_id=${supplierId}`;
    }

    fetch(url)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const lines = data.lines.map(line => ({
                    parts_list_line_id: line.id,
                    line_number: line.line_number,
                    customer_part_number: line.customer_part_number,
                    requested_quantity: line.quantity,
                    quoted_part_number: line.customer_part_number,
                    quantity_quoted: line.quantity,
                    unit_price: null,
                    lead_time_days: null,
                    condition_code: '',
                    certifications: '',
                    is_no_bid: false,
                    line_notes: '',
                    other_quotes_count: 0,
                    quote_requested: line.quote_requested || 0
                }));

                initializeQuoteLinesTable(lines);
            }
        })
        .catch(error => console.error('Error loading parts list lines:', error));
}

function initializeQuoteLinesTable(lines) {
    quoteLinesData = lines.map(line => ({
        ...line,
        quote_requested: line.quote_requested || 0
    }));

    const container = document.getElementById('quote-lines-table-container');
    container.innerHTML = '';

    const tableData = quoteLinesData.map(line => [
        line.line_number,
        line.customer_part_number,
        line.requested_quantity,
        line.quoted_part_number || line.customer_part_number,
        line.quantity_quoted,
        line.unit_price,
        line.lead_time_days,
        line.condition_code,
        line.certifications,
        !!line.is_no_bid,
        line.line_notes,
        line.other_quotes_count || 0,
        !!line.quote_requested
    ]);

    quoteLinesTable = new Handsontable(container, {
        data: tableData,
        colHeaders: [
            '#',
            'Our Part #',
            'Req Qty',
            'Quoted Part #',
            'Qty Quoted',
            'Unit Price',
            'Lead Days',
            'Condition',
            'Certifications',
            'No Bid',
            'Notes',
            'Other Quotes',
            'Sent?'
        ],
        columns: [
            { data: 0, type: 'numeric', readOnly: true, className: 'htCenter htMiddle' },
            { data: 1, type: 'text', readOnly: true },
            { data: 2, type: 'numeric', readOnly: true, className: 'htCenter' },
            { data: 3, type: 'text' },
            { data: 4, type: 'numeric' },
            { data: 5, type: 'numeric', numericFormat: { pattern: '0,0.00' } },
            { data: 6, type: 'numeric' },
            { data: 7, type: 'text' },
            { data: 8, type: 'text' },
            {
                data: 9,
                type: 'text',
                renderer: noBidCheckboxRenderer,
                readOnly: false,
                className: 'htCenter'
            },
            { data: 10, type: 'text' },
            { data: 11, type: 'numeric', readOnly: true, className: 'htCenter' },
            { data: 12, type: 'checkbox', readOnly: true }
        ],
        rowHeaders: true,
        height: 500,
        licenseKey: 'non-commercial-and-evaluation',
        stretchH: 'all',
        contextMenu: true,
        manualColumnResize: true,
        filters: true,
        dropdownMenu: true,
        hiddenColumns: {
            columns: [12],
            indicators: false
        },
        hiddenRows: {
            indicators: false
        },
        afterGetColHeader: function(col, TH) {
            if (col === 1) {
                let wrapper = TH.querySelector('.part-filter-wrapper');
                if (!wrapper) {
                    wrapper = document.createElement('div');
                    wrapper.className = 'part-filter-wrapper mt-1';
                    const input = document.createElement('input');
                    input.type = 'text';
                    input.className = 'form-control form-control-sm part-filter-input';
                    input.placeholder = 'Filter part #';
                    input.value = partNumberFilterValue;
                    input.addEventListener('input', function(e) {
                        partNumberFilterValue = e.target.value.trim().toLowerCase();
                        applyVisibilityFilters();
                    });
                    wrapper.appendChild(input);
                    TH.appendChild(wrapper);
                } else {
                    const input = wrapper.querySelector('input');
                    if (input && input.value !== partNumberFilterValue) {
                        input.value = partNumberFilterValue;
                    }
                }
            }
        },
        cells: function(row, col) {
            const cellProperties = {};

            // Highlight rows that were sent to this supplier
            if (quoteLinesData[row].quote_requested) {
                cellProperties.className = ((cellProperties.className || '') + ' bg-info bg-opacity-25').trim();
            }

            // Other quotes warning
            if (col === 11 && this.instance.getDataAtCell(row, col) > 0) {
                cellProperties.className = ((cellProperties.className || '') + ' bg-warning').trim();
            }

            // No-bid styling (takes precedence)
            if (this.instance.getDataAtCell(row, 9) === true) {
                cellProperties.className = 'bg-secondary text-white';
            }

            return cellProperties;
        }
    });
}

// ========== EXTRACTION ==========
function extractQuoteData() {
    const quoteText = document.getElementById('supplier-response-text').value.trim();

    if (!quoteText) {
        showToast('Please paste supplier response text', 'warning');
        return;
    }

    const extractBtn = document.getElementById('extract-quote-btn');
    const originalText = extractBtn.innerHTML;
    extractBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Extracting...';
    extractBtn.disabled = true;

    fetch('/parts_list/extract_supplier_quote', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            quote_text: quoteText,
            parts_list_lines: quoteLinesData.map(line => ({
                line_number: line.line_number,
                customer_part_number: line.customer_part_number,
                quantity: line.requested_quantity
            }))
        })
    })
    .then(response => response.json())
    .then(data => {
        console.log('extract_supplier_quote response:', data);

        const extractedLines =
            data.extracted_lines ||
            data.items ||
            (Array.isArray(data) ? data : []);

        if (data.success && Array.isArray(extractedLines) && extractedLines.length > 0) {
            applyExtractedDataToTable(extractedLines);
            showToast(`Extracted ${extractedLines.length} line(s) successfully`, 'success');
        } else {
            showToast('No data could be extracted', 'warning');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        showToast('Error extracting quote data', 'danger');
    })
    .finally(() => {
        extractBtn.innerHTML = originalText;
        extractBtn.disabled = false;
    });
}

function applyExtractedDataToTable(extractedLines) {
    if (!quoteLinesTable || !Array.isArray(quoteLinesData) || quoteLinesData.length === 0) {
        console.warn('No parts list lines loaded to map the extracted quote onto.');
        showToast('No parts list lines loaded to map the extracted quote onto.', 'warning');
        return;
    }

    console.log('Applying extracted lines:', extractedLines);

    const AUTO_MATCH_THRESHOLD = 0.80;

    let matchedCount = 0;
    const unmatched = [];

    extractedLines.forEach(extracted => {
        const extractedPN = extracted.part_number || '';
        if (!extractedPN) {
            unmatched.push('(no part number)');
            return;
        }

        let bestIndex = -1;
        let bestScore = 0;

        for (let i = 0; i < quoteLinesData.length; i++) {
            const line = quoteLinesData[i];
            const candidatePN = line.quoted_part_number || line.customer_part_number || '';

            if (!candidatePN) continue;

            const score = pnSimilarity(candidatePN, extractedPN);

            if (score > bestScore) {
                bestScore = score;
                bestIndex = i;
            }
        }

        if (bestIndex !== -1 && bestScore >= AUTO_MATCH_THRESHOLD) {
            matchedCount++;
            console.log(
                `Matched extracted PN "${extractedPN}" to row ${bestIndex} ` +
                `(score=${bestScore.toFixed(2)})`
            );

            quoteLinesTable.setDataAtCell([
                [bestIndex, 3, extracted.part_number],
                [bestIndex, 4, extracted.quantity],
                [bestIndex, 5, extracted.price],
                [bestIndex, 6, extracted.lead_time_days],
                [bestIndex, 7, extracted.condition],
                [bestIndex, 8, extracted.certifications],
                [bestIndex, 9, !!extracted.is_no_bid],
                [bestIndex, 10, extracted.notes]
            ]);
        } else {
            console.warn(`No strong match for extracted PN "${extractedPN}", bestScore=${bestScore.toFixed(2)}`);
            unmatched.push(extractedPN);
        }
    });

    console.log(`Matched ${matchedCount} line(s). Unmatched:`, unmatched);

    if (matchedCount === 0) {
        showToast('No extracted lines could be confidently matched to your parts list.', 'warning');
    } else if (unmatched.length > 0) {
        showToast(`Applied ${matchedCount} line(s). Could not match: ${unmatched.join(', ')}`, 'warning');
    } else {
        showToast(`Applied ${matchedCount} line(s) to the table.`, 'success');
    }
}

// ========== SAVE/DELETE ==========
function saveSupplierQuote() {
    const supplierId = document.getElementById('quote-supplier-select').value;

    if (!supplierId) {
        showToast('Please select a supplier', 'warning');
        return;
    }

    const saveBtn = document.getElementById('save-quote-btn');
    const originalText = saveBtn.innerHTML;
    saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving...';
    saveBtn.disabled = true;

    const notesInput = document.getElementById('quote-notes-input');

    const quoteData = {
        supplier_id: parseInt(supplierId),
        quote_reference: document.getElementById('quote-reference-input').value,
        quote_date: document.getElementById('quote-date-input').value,
        currency_id: parseInt(document.getElementById('quote-currency-select').value),
        notes: notesInput ? notesInput.value : ''
    };

    const headerPromise = currentQuoteId
        ? updateQuoteHeader(currentQuoteId, quoteData)
        : createQuoteHeader(quoteData);

    headerPromise
        .then(quoteId => {
            currentQuoteId = quoteId;
            return saveQuoteLines(quoteId);
        })
        .then(() => {
            showToast('Quote saved successfully', 'success');

            const deleteBtn = document.getElementById('delete-quote-btn');
            if (deleteBtn) {
                deleteBtn.style.display = 'block';
            }

            // If on quick quote page, redirect back to costing
            if (window.IS_QUICK_QUOTE) {
                setTimeout(() => {
                    window.location.href = `/parts_list/parts-lists/${window.PARTS_LIST_ID}/costing`;
                }, 1000);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showToast('Error saving quote', 'danger');
        })
        .finally(() => {
            saveBtn.innerHTML = originalText;
            saveBtn.disabled = false;
        });
}

function createQuoteHeader(quoteData) {
    return fetch(`/parts_list/parts-lists/${window.PARTS_LIST_ID}/supplier-quotes/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(quoteData)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            return data.quote_id;
        } else {
            throw new Error(data.message);
        }
    });
}

function updateQuoteHeader(quoteId, quoteData) {
    return fetch(`/parts_list/parts-lists/${window.PARTS_LIST_ID}/supplier-quotes/${quoteId}/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(quoteData)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            return quoteId;
        } else {
            throw new Error(data.message);
        }
    });
}

function saveQuoteLines(quoteId) {
    const tableData = quoteLinesTable.getData();

    const lines = quoteLinesData.map((line, index) => ({
        parts_list_line_id: line.parts_list_line_id,
        quoted_part_number: tableData[index][3],
        quantity_quoted: tableData[index][4],
        unit_price: tableData[index][5],
        lead_time_days: tableData[index][6],
        condition_code: tableData[index][7],
        certifications: tableData[index][8],
        is_no_bid: !!tableData[index][9],
        line_notes: tableData[index][10]
    }));

    return fetch(`/parts_list/parts-lists/${window.PARTS_LIST_ID}/supplier-quotes/${quoteId}/lines/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lines })
    })
    .then(response => response.json())
    .then(data => {
        if (!data.success) {
            throw new Error(data.message);
        }
    });
}

function deleteSupplierQuote() {
    if (!currentQuoteId) return;

    if (!confirm('Are you sure you want to delete this quote? This cannot be undone.')) {
        return;
    }

    fetch(`/parts_list/parts-lists/${window.PARTS_LIST_ID}/supplier-quotes/${currentQuoteId}/delete`, {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showToast('Quote deleted successfully', 'success');
            showQuotesListView();
        } else {
            showToast('Error deleting quote: ' + data.message, 'danger');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        showToast('Error deleting quote', 'danger');
    });
}

// ========== HELPER ==========
function showToast(message, type) {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show position-fixed`;
    alertDiv.style.cssText = 'top: 20px; right: 20px; z-index: 10000; min-width: 300px;';
    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    document.body.appendChild(alertDiv);
    setTimeout(() => alertDiv.remove(), 3000);
}

// Make function global
window.loadQuoteForEditing = loadQuoteForEditing;

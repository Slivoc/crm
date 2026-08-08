(function () {
    'use strict';

    const root = document.getElementById('geoDeepDiveWorkspace');
    if (!root) return;

    document.body.classList.add('geo-deepdive-page');

    const endpoints = {
        penetration: root.dataset.penetrationUrl,
        customerSearch: root.dataset.customerSearchUrl,
        curated: root.dataset.curatedUrl,
        links: root.dataset.customerLinkUrl,
        improve: root.dataset.improveUrl,
        apply: root.dataset.applyUrl,
    };

    const state = {
        marketFilter: 'all',
        marketQuery: '',
        focusCustomer: null,
        textCustomer: null,
        manualCustomer: null,
        selectedText: '',
        improvedContent: null,
    };

    const debounce = (fn, wait = 250) => {
        let timer;
        return (...args) => {
            window.clearTimeout(timer);
            timer = window.setTimeout(() => fn(...args), wait);
        };
    };

    async function readJson(response) {
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
        return payload;
    }

    function showToast(message, type = 'success') {
        document.querySelectorAll('.geo-toast').forEach((toast) => toast.remove());
        const toast = document.createElement('div');
        toast.className = `geo-toast ${type}`;
        const icon = document.createElement('i');
        icon.className = type === 'success' ? 'bi bi-check2-circle' : 'bi bi-exclamation-circle';
        const text = document.createElement('span');
        text.textContent = message;
        toast.append(icon, text);
        document.body.appendChild(toast);
        window.setTimeout(() => toast.remove(), 3600);
    }

    function getModal(id) {
        return bootstrap.Modal.getOrCreateInstance(document.getElementById(id));
    }

    function initialiseSectionNavigation() {
        const tabs = [...root.querySelectorAll('[data-bs-toggle="tab"][data-section]')];
        const requestedSection = window.location.hash.replace('#', '');
        const requestedTab = tabs.find((tab) => tab.dataset.section === requestedSection);
        if (requestedTab) bootstrap.Tab.getOrCreateInstance(requestedTab).show();

        tabs.forEach((tab) => {
            tab.addEventListener('shown.bs.tab', () => {
                history.replaceState(null, '', `#${tab.dataset.section}`);
            });
        });
    }

    function showSection(name) {
        const tab = root.querySelector(`[data-section="${name}"]`);
        if (tab) bootstrap.Tab.getOrCreateInstance(tab).show();
    }

    function initialiseMarketMap() {
        const search = document.getElementById('marketOrganisationSearch');
        const filters = [...root.querySelectorAll('[data-market-filter]')];

        const applyFilter = () => {
            let visibleCount = 0;
            root.querySelectorAll('[data-market-organisation]').forEach((card) => {
                const relationshipMatch = state.marketFilter === 'all'
                    || (state.marketFilter === 'main' && card.dataset.main === 'true')
                    || card.dataset.relationship === state.marketFilter;
                const searchMatch = !state.marketQuery || card.dataset.search.includes(state.marketQuery);
                card.hidden = !(relationshipMatch && searchMatch);
                if (!card.hidden) visibleCount += 1;
            });
            const empty = document.getElementById('marketEmptyFilter');
            if (empty) empty.hidden = visibleCount !== 0;
        };

        filters.forEach((button) => button.addEventListener('click', () => {
            state.marketFilter = button.dataset.marketFilter;
            filters.forEach((item) => item.classList.toggle('active', item === button));
            applyFilter();
        }));

        search?.addEventListener('input', () => {
            state.marketQuery = search.value.trim().toLowerCase();
            applyFilter();
        });

        root.querySelectorAll('[data-open-market-search]').forEach((button) => {
            button.addEventListener('click', () => {
                showSection('market-map');
                const allButton = root.querySelector('[data-market-filter="all"]');
                allButton?.click();
                if (search) {
                    search.value = button.dataset.openMarketSearch;
                    search.dispatchEvent(new Event('input'));
                    window.setTimeout(() => search.focus(), 160);
                }
            });
        });

        root.querySelector('[data-open-market-gaps]')?.addEventListener('click', () => {
            showSection('market-map');
            window.setTimeout(() => root.querySelector('[data-market-filter="gap"]')?.click(), 100);
        });
    }

    function initialiseSegmentFilter() {
        const input = document.getElementById('segmentCustomerSearch');
        const rows = [...root.querySelectorAll('[data-segment-customer]')];
        const empty = document.getElementById('segmentFilterEmpty');
        input?.addEventListener('input', () => {
            const query = input.value.trim().toLowerCase();
            let visible = 0;
            rows.forEach((row) => {
                row.hidden = Boolean(query && !row.dataset.search.includes(query));
                if (!row.hidden) visible += 1;
            });
            if (empty) empty.hidden = visible !== 0;
        });
    }

    async function searchCustomers(query) {
        if (query.trim().length < 2) return [];
        const url = new URL(endpoints.customerSearch, window.location.origin);
        url.searchParams.set('q', query.trim());
        url.searchParams.set('limit', '10');
        const data = await readJson(await fetch(url, { headers: { Accept: 'application/json' } }));
        return Array.isArray(data) ? data : (data.customers || []);
    }

    function renderCustomerResults(container, customers, onSelect) {
        container.replaceChildren();
        if (!customers.length) {
            const message = document.createElement('div');
            message.className = 'search-message';
            message.textContent = 'No matching CRM customers found.';
            container.appendChild(message);
            return;
        }

        customers.forEach((customer) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'customer-result';
            button.dataset.customerId = customer.id;

            const details = document.createElement('span');
            const name = document.createElement('strong');
            name.textContent = customer.name || 'Unnamed customer';
            const meta = document.createElement('small');
            const metaParts = [customer.country, customer.status_name || customer.status, customer.assigned_salesperson_name].filter(Boolean);
            meta.textContent = metaParts.join(' · ') || 'No additional details';
            details.append(name, meta);

            const check = document.createElement('i');
            check.className = 'bi bi-check2-circle';
            button.append(details, check);
            button.addEventListener('click', () => {
                container.querySelectorAll('.customer-result').forEach((item) => item.classList.remove('selected'));
                button.classList.add('selected');
                onSelect(customer);
            });
            container.appendChild(button);
        });
    }

    function bindCustomerSearch(input, results, onResults, onError) {
        const run = debounce(async () => {
            const query = input.value.trim();
            if (query.length < 2) {
                results.replaceChildren();
                onResults([]);
                return;
            }
            results.innerHTML = '<div class="search-message">Searching CRM…</div>';
            try {
                const customers = await searchCustomers(query);
                onResults(customers);
            } catch (error) {
                results.innerHTML = '<div class="search-message">Customer search is unavailable.</div>';
                onError?.(error);
            }
        });
        input.addEventListener('input', run);
        return run;
    }

    function initialiseFocusAccounts() {
        const modalElement = document.getElementById('geoDeepDiveCustomerModal');
        const input = document.getElementById('geoCustomerSearch');
        const results = document.getElementById('geoSearchResults');
        const notes = document.getElementById('geoCustomerNotes');
        const confirm = document.getElementById('geoConfirmAddCustomer');

        root.querySelector('[data-action="add-focus-account"]')?.addEventListener('click', () => {
            state.focusCustomer = null;
            input.value = '';
            notes.value = '';
            results.replaceChildren();
            confirm.disabled = true;
            getModal('geoDeepDiveCustomerModal').show();
            modalElement.addEventListener('shown.bs.modal', () => input.focus(), { once: true });
        });

        bindCustomerSearch(input, results, (customers) => {
            state.focusCustomer = null;
            confirm.disabled = true;
            renderCustomerResults(results, customers, (customer) => {
                state.focusCustomer = customer;
                confirm.disabled = false;
            });
        });

        confirm.addEventListener('click', async () => {
            if (!state.focusCustomer) return;
            confirm.disabled = true;
            try {
                await readJson(await fetch(endpoints.curated, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ customer_id: state.focusCustomer.id, notes: notes.value.trim() }),
                }));
                window.location.hash = 'crm-coverage';
                window.location.reload();
            } catch (error) {
                showToast(error.message, 'danger');
                confirm.disabled = false;
            }
        });

        root.addEventListener('click', async (event) => {
            const button = event.target.closest('[data-remove-focus-account]');
            if (!button) return;
            if (!window.confirm('Remove this account from the deep-dive focus list? The CRM customer will not be deleted.')) return;
            button.disabled = true;
            try {
                await readJson(await fetch(`${endpoints.curated}/${button.dataset.removeFocusAccount}`, { method: 'DELETE' }));
                button.closest('tr')?.remove();
                showToast('Account removed from this focus list.');
            } catch (error) {
                showToast(error.message, 'danger');
                button.disabled = false;
            }
        });
    }

    function initialiseInlineEditing() {
        root.querySelectorAll('.editable-field').forEach((field) => {
            const value = field.querySelector('.field-value');
            const input = field.querySelector('.field-input');
            const edit = field.querySelector('.edit-field');
            if (!value || !input || !edit) return;

            const finish = () => {
                value.style.display = '';
                edit.style.display = '';
                input.style.display = 'none';
                field.classList.remove('editing');
            };

            const save = async () => {
                if (!field.classList.contains('editing')) return;
                if (input.dataset.cancelled === 'true') {
                    input.dataset.cancelled = 'false';
                    input.value = input.dataset.original || '';
                    finish();
                    return;
                }
                const nextValue = input.value.trim();
                input.disabled = true;
                try {
                    await readJson(await fetch(`${endpoints.curated}/${field.dataset.customerId}/quick-update`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ field: field.dataset.field, value: nextValue }),
                    }));
                    if (field.dataset.field === 'estimated_revenue') {
                        value.textContent = nextValue ? formatCurrency(Number(nextValue), true) : '—';
                    } else if (field.dataset.field === 'notes') {
                        value.textContent = nextValue || 'Add context…';
                    } else {
                        value.textContent = nextValue || '—';
                    }
                    input.dataset.original = nextValue;
                    showToast('Account detail updated.');
                } catch (error) {
                    input.value = input.dataset.original || '';
                    showToast(error.message, 'danger');
                } finally {
                    input.disabled = false;
                    finish();
                }
            };

            edit.addEventListener('click', () => {
                input.dataset.original = input.value;
                value.style.display = 'none';
                edit.style.display = 'none';
                input.style.display = 'block';
                field.classList.add('editing');
                input.focus();
                input.select();
            });
            input.addEventListener('blur', save);
            input.addEventListener('keydown', (event) => {
                if (event.key === 'Escape') {
                    input.dataset.cancelled = 'true';
                    input.blur();
                } else if (event.key === 'Enter' && input.tagName !== 'TEXTAREA') {
                    event.preventDefault();
                    input.blur();
                } else if (event.key === 'Enter' && input.tagName === 'TEXTAREA' && (event.ctrlKey || event.metaKey)) {
                    event.preventDefault();
                    input.blur();
                }
            });
        });
    }

    function initialiseStatusUpdates() {
        root.addEventListener('click', async (event) => {
            const badge = event.target.closest('.status-badge.clickable');
            if (!badge || badge.classList.contains('updating')) return;
            event.preventDefault();
            event.stopPropagation();
            const customerId = badge.dataset.customerId;
            const matchingBadges = root.querySelectorAll(`.status-badge[data-customer-id="${customerId}"]`);
            matchingBadges.forEach((item) => item.classList.add('updating'));
            try {
                const data = await readJson(await fetch(`/customers/${customerId}/bump_status`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                }));
                if (!data.success || !data.new_status) throw new Error('The status response was incomplete.');
                matchingBadges.forEach((item) => {
                    const dot = item.querySelector('.status-dot') || document.createElement('span');
                    dot.classList.add('status-dot');
                    item.replaceChildren(dot, document.createTextNode(data.new_status.name));
                    item.dataset.status = data.new_status.name.toLowerCase();
                    item.dataset.statusId = data.new_status.id || '';
                });
                showToast(`Status advanced to ${data.new_status.name}.`);
                loadPenetrationData();
            } catch (error) {
                showToast(error.message, 'danger');
            } finally {
                matchingBadges.forEach((item) => item.classList.remove('updating'));
            }
        });
    }

    function openCustomerLink(text) {
        const cleanText = String(text || '').trim().slice(0, 240);
        if (cleanText.length < 2) return;
        state.selectedText = cleanText;
        state.textCustomer = null;
        const selectedText = document.getElementById('selectedText');
        const search = document.getElementById('textSelectionSearch');
        const results = document.getElementById('textSelectionResults');
        const confirm = document.getElementById('confirmTextSelection');
        selectedText.textContent = cleanText;
        search.value = cleanText;
        results.replaceChildren();
        confirm.disabled = true;
        getModal('textSelectionModal').show();
        window.setTimeout(() => search.dispatchEvent(new Event('input')), 120);
    }

    function initialiseNarrativeConnections() {
        const content = document.getElementById('contentDisplay');
        if (!content) return;

        content.querySelectorAll('strong, b').forEach((element) => {
            if (element.closest('a, button, .status-badge')) return;
            element.classList.add('clickable-text');
            element.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                openCustomerLink(element.textContent);
            });
        });

        content.addEventListener('mouseup', (event) => {
            window.setTimeout(() => {
                const selection = window.getSelection();
                const text = selection?.toString().trim();
                if (!text || text.length < 3 || text.length > 240) return;
                document.querySelectorAll('.selection-tooltip').forEach((item) => item.remove());
                const tooltip = document.createElement('div');
                tooltip.className = 'selection-tooltip';
                const button = document.createElement('button');
                button.type = 'button';
                button.innerHTML = '<i class="bi bi-link-45deg"></i> Connect to CRM';
                button.addEventListener('click', () => {
                    tooltip.remove();
                    selection.removeAllRanges();
                    openCustomerLink(text);
                });
                tooltip.appendChild(button);
                tooltip.style.left = `${event.pageX}px`;
                tooltip.style.top = `${Math.max(10, event.pageY - 42)}px`;
                document.body.appendChild(tooltip);
                window.setTimeout(() => tooltip.remove(), 4500);
            }, 0);
        });

        document.addEventListener('mousedown', (event) => {
            if (!event.target.closest('.selection-tooltip')) document.querySelectorAll('.selection-tooltip').forEach((item) => item.remove());
        });

        root.querySelectorAll('[data-link-organisation]').forEach((button) => {
            button.addEventListener('click', () => openCustomerLink(button.dataset.linkOrganisation));
        });

        const search = document.getElementById('textSelectionSearch');
        const results = document.getElementById('textSelectionResults');
        const confirm = document.getElementById('confirmTextSelection');
        bindCustomerSearch(search, results, (customers) => {
            state.textCustomer = null;
            confirm.disabled = true;
            renderCustomerResults(results, customers, (customer) => {
                state.textCustomer = customer;
                confirm.disabled = false;
            });
        });

        confirm.addEventListener('click', () => {
            if (state.textCustomer) createCustomerLink(state.textCustomer.id, state.selectedText, confirm);
        });

        document.getElementById('createNewCustomerFromText').addEventListener('click', () => {
            getModal('textSelectionModal').hide();
            window.setTimeout(() => {
                if (typeof window.openAddCustomerModal === 'function') {
                    window.openAddCustomerModal(state.selectedText);
                } else {
                    showToast('The customer creation form is unavailable.', 'danger');
                }
            }, 250);
        });
    }

    async function createCustomerLink(customerId, linkedText, button) {
        if (button) button.disabled = true;
        try {
            await readJson(await fetch(endpoints.links, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ customer_id: customerId, linked_text: linkedText }),
            }));
            window.location.hash = 'connections';
            window.location.reload();
        } catch (error) {
            showToast(error.message, 'danger');
            if (button) button.disabled = false;
        }
    }

    function initialiseManualConnections() {
        const text = document.getElementById('manualLinkedText');
        const search = document.getElementById('manualCustomerSearch');
        const results = document.getElementById('manualCustomerResults');
        const selected = document.getElementById('manualSelectedCustomer');
        const add = document.getElementById('manualAddLink');

        const updateState = () => { add.disabled = !(state.manualCustomer && text.value.trim().length >= 2); };
        text.addEventListener('input', updateState);
        bindCustomerSearch(search, results, (customers) => {
            state.manualCustomer = null;
            selected.hidden = true;
            updateState();
            renderCustomerResults(results, customers, (customer) => {
                state.manualCustomer = customer;
                selected.textContent = `Selected CRM account: ${customer.name}`;
                selected.hidden = false;
                updateState();
            });
        });
        add.addEventListener('click', () => {
            if (state.manualCustomer) createCustomerLink(state.manualCustomer.id, text.value.trim(), add);
        });

        root.addEventListener('click', async (event) => {
            const button = event.target.closest('.remove-linked-text');
            if (!button) return;
            if (!window.confirm(`Remove the link for “${button.dataset.linkedText}”?`)) return;
            button.disabled = true;
            try {
                await readJson(await fetch(endpoints.links, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ customer_id: Number(button.dataset.customerId), linked_text: button.dataset.linkedText }),
                }));
                button.closest('.connection-row')?.remove();
                showToast('Narrative connection removed.');
            } catch (error) {
                showToast(error.message, 'danger');
                button.disabled = false;
            }
        });
    }

    function formatCurrency(amount, exact = false) {
        if (!Number.isFinite(amount)) return '—';
        if (!exact && Math.abs(amount) >= 1000000) return `£${(amount / 1000000).toFixed(1)}m`;
        if (!exact && Math.abs(amount) >= 1000) return `£${Math.round(amount / 1000)}k`;
        return new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP', maximumFractionDigits: 0 }).format(amount);
    }

    const statusColours = {
        1: '#168291',
        2: '#d87820',
        3: '#c99b16',
        4: '#2672c9',
        5: '#7855b6',
        6: '#278657',
    };

    async function loadPenetrationData() {
        const chart = document.getElementById('penetrationChart');
        if (!chart) return;
        try {
            const data = await readJson(await fetch(endpoints.penetration, { headers: { Accept: 'application/json' } }));
            chart.replaceChildren();

            const total = document.createElement('div');
            total.className = 'penetration-total';
            const totalLabel = document.createElement('span');
            totalLabel.textContent = 'Estimated focus-account value';
            const totalValue = document.createElement('strong');
            totalValue.textContent = formatCurrency(Number(data.total_revenue) || 0);
            total.append(totalLabel, totalValue);
            chart.appendChild(total);

            if (!data.penetration_data?.length) {
                const empty = document.createElement('div');
                empty.className = 'empty-compact';
                empty.innerHTML = '<i class="bi bi-info-circle"></i><span>Add status and estimated value to focus accounts to see the pipeline mix.</span>';
                chart.appendChild(empty);
                return;
            }

            data.penetration_data.forEach((item) => {
                const row = document.createElement('div');
                row.className = 'penetration-row';
                const header = document.createElement('div');
                header.className = 'penetration-row-header';
                const label = document.createElement('span');
                label.textContent = item.status_name || item.status || `Status ${item.status_id}`;
                const amount = document.createElement('span');
                amount.textContent = `${formatCurrency(Number(item.revenue) || 0)} · ${Number(item.percentage || 0).toFixed(0)}%`;
                header.append(label, amount);
                const track = document.createElement('div');
                track.className = 'penetration-track';
                const fill = document.createElement('span');
                fill.style.width = `${Math.max(0, Math.min(100, Number(item.percentage) || 0))}%`;
                fill.style.backgroundColor = statusColours[item.status_id] || '#7e8c92';
                track.appendChild(fill);
                row.append(header, track);
                chart.appendChild(row);
            });
        } catch (error) {
            chart.innerHTML = '<div class="empty-compact"><i class="bi bi-exclamation-circle"></i><span>CRM value data could not be loaded.</span></div>';
        }
    }

    function initialiseImprovementFlow() {
        const modalElement = document.getElementById('improveContentModal');
        const request = document.getElementById('improvementRequest');
        const generate = document.getElementById('generateImprovedContent');
        const result = document.getElementById('improvedContentSection');
        const preview = document.getElementById('improvedContentPreview');
        const compare = document.getElementById('contentDiffSection');
        const toggleCompare = document.getElementById('toggleContentDiff');
        const apply = document.getElementById('applyImprovedContent');

        root.querySelector('[data-action="improve-content"]')?.addEventListener('click', () => {
            getModal('improveContentModal').show();
            modalElement.addEventListener('shown.bs.modal', () => request.focus(), { once: true });
        });

        generate.addEventListener('click', async () => {
            if (!request.value.trim()) {
                showToast('Describe what you want the research assistant to investigate.', 'danger');
                request.focus();
                return;
            }
            const original = generate.innerHTML;
            generate.disabled = true;
            generate.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Researching…';
            try {
                const data = await readJson(await fetch(endpoints.improve, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ request: request.value.trim() }),
                }));
                state.improvedContent = data;
                preview.textContent = data.improved_content || '';
                result.hidden = false;
                compare.hidden = true;
                result.scrollIntoView({ behavior: 'smooth', block: 'start' });
            } catch (error) {
                showToast(error.message, 'danger');
            } finally {
                generate.disabled = false;
                generate.innerHTML = original;
            }
        });

        toggleCompare.addEventListener('click', () => {
            if (!state.improvedContent) return;
            document.getElementById('originalContentDiff').textContent = state.improvedContent.original_content || '';
            document.getElementById('enhancedContentDiff').textContent = state.improvedContent.improved_content || '';
            compare.hidden = !compare.hidden;
        });

        apply.addEventListener('click', async () => {
            if (!state.improvedContent || !window.confirm('Replace the current briefing with this proposed update?')) return;
            apply.disabled = true;
            const form = new FormData();
            form.append('improved_content', state.improvedContent.improved_content);
            try {
                const response = await fetch(endpoints.apply, { method: 'POST', body: form });
                if (!response.ok) throw new Error('The updated briefing could not be applied.');
                window.location.hash = 'briefing';
                window.location.reload();
            } catch (error) {
                showToast(error.message, 'danger');
                apply.disabled = false;
            }
        });
    }

    initialiseSectionNavigation();
    initialiseMarketMap();
    initialiseSegmentFilter();
    initialiseFocusAccounts();
    initialiseInlineEditing();
    initialiseStatusUpdates();
    initialiseNarrativeConnections();
    initialiseManualConnections();
    initialiseImprovementFlow();
    loadPenetrationData();
})();

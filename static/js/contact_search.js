// Contact Search functionality with quick action buttons
$(document).ready(function() {
    function getActiveSalespersonId() {
        const hiddenSalespersonInput = document.getElementById('salesPersonId');
        if (hiddenSalespersonInput && hiddenSalespersonInput.value) {
            return hiddenSalespersonInput.value;
        }

        const searchInput = document.querySelector('input[name="contact_search"]');
        if (searchInput && searchInput.dataset.defaultSalespersonId) {
            return searchInput.dataset.defaultSalespersonId;
        }

        const bodySalespersonId = document.body?.dataset?.defaultSalespersonId;
        if (bodySalespersonId) {
            return bodySalespersonId;
        }

        const urlParams = new URLSearchParams(window.location.search);
        const querySalespersonId = urlParams.get('salesperson_id');
        if (querySalespersonId) {
            return querySalespersonId;
        }

        return window.location.pathname.match(/\/salespeople\/(\d+)/)?.[1] || '';
    }

    function escapeHtml(value) {
        return $('<div>').text(value || '').html();
    }

    function showContactSearchToast(message, type = 'primary') {
        const container = document.getElementById('notification-toast-container');
        if (!container) {
            return;
        }

        const toast = document.createElement('div');
        toast.className = 'toast align-items-center border-0 show mb-2';
        toast.setAttribute('role', 'alert');
        toast.setAttribute('aria-live', 'assertive');
        toast.setAttribute('aria-atomic', 'true');

        const colorClassMap = {
            success: 'text-bg-success',
            danger: 'text-bg-danger',
            warning: 'text-bg-warning',
            info: 'text-bg-info',
            primary: 'text-bg-primary'
        };

        toast.classList.add(colorClassMap[type] || colorClassMap.primary);
        toast.innerHTML = `
            <div class="d-flex">
                <div class="toast-body">${escapeHtml(message)}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
        `;

        container.appendChild(toast);
        const bsToast = new bootstrap.Toast(toast, { delay: 3500 });
        toast.addEventListener('hidden.bs.toast', () => toast.remove());
        bsToast.show();
    }

    function updateCallListButton(button, isOnCallList, title) {
        const icon = button.querySelector('i');
        if (!icon) {
            return;
        }

        button.classList.toggle('is-on-call-list', Boolean(isOnCallList));
        button.classList.toggle('add-to-call-list-btn', !isOnCallList);
        button.classList.toggle('remove-from-call-list-btn', Boolean(isOnCallList));
        icon.className = isOnCallList ? 'bi bi-check-circle-fill' : 'bi bi-list-check';
        button.title = title;
        button.setAttribute('aria-label', title);
    }

    function renderCallListButton(contact, salespersonId) {
        const hasSalesperson = Boolean(salespersonId);
        const isOnCallList = Boolean(contact.is_on_call_list);
        const title = !hasSalesperson
            ? 'Select a salesperson to use the call list'
            : (isOnCallList ? 'Remove from call list' : 'Add to call list');

        const classes = [
            'contact-quick-action-btn',
            'call-list-action',
            hasSalesperson ? '' : 'is-disabled',
            isOnCallList ? 'is-on-call-list remove-from-call-list-btn' : 'add-to-call-list-btn'
        ].filter(Boolean).join(' ');

        const icon = isOnCallList ? 'bi-check-circle-fill' : 'bi-list-check';
        const disabledAttr = hasSalesperson ? '' : 'disabled';

        return `
            <button class="${classes}"
                    data-action="call-list"
                    data-contact-id="${contact.id}"
                    data-contact-name="${escapeHtml(contact.full_name)}"
                    title="${escapeHtml(title)}"
                    aria-label="${escapeHtml(title)}"
                    ${disabledAttr}>
                <i class="bi ${icon}"></i>
            </button>
        `;
    }

    let debounceTimer = null;
    let activeRequest = null;
    let requestSeq = 0;

    $('input[name="contact_search"]').on('input', function() {
        const query = ($(this).val() || '').trim();

        if (debounceTimer) {
            clearTimeout(debounceTimer);
        }

        debounceTimer = setTimeout(function() {
            const resultDropdown = $('#contact-results');

            if (query.length > 1) {
                if (activeRequest && activeRequest.readyState !== 4) {
                    activeRequest.abort();
                }

                const mySeq = ++requestSeq;
                const salespersonId = getActiveSalespersonId();
                activeRequest = $.ajax({
                    url: "/customers/search_contact",
                    type: 'GET',
                    data: {
                        query: query,
                        salesperson_id: salespersonId || undefined
                    },
                    success: function(data) {
                        if (mySeq !== requestSeq) {
                            return;
                        }

                        resultDropdown.empty();

                        if (data.length > 0) {
                            data.forEach(function(contact) {
                                var contactHtml = `
                                <div class="contact-search-item" data-contact-id="${contact.id}">
                                    <div class="contact-info">
                                        <div class="contact-name">${escapeHtml(contact.full_name)}</div>
                                        ${contact.email ? `
                                            <div class="contact-detail">
                                                <i class="bi bi-envelope"></i>
                                                <span>${escapeHtml(contact.email)}</span>
                                            </div>
                                        ` : ''}
                                        ${contact.phone ? `
                                            <div class="contact-detail">
                                                <i class="bi bi-telephone"></i>
                                                <span>${escapeHtml(contact.phone)}</span>
                                            </div>
                                        ` : ''}
                                        ${contact.job_title ? `
                                            <div class="contact-detail">
                                                <i class="bi bi-briefcase"></i>
                                                <span>${escapeHtml(contact.job_title)}</span>
                                            </div>
                                        ` : ''}
                                        <div class="contact-customer">
                                            <i class="bi bi-building me-1"></i>${escapeHtml(contact.customer_name || 'No Customer')}
                                        </div>
                                        ${contact.status_name ? `
                                            <div class="contact-status" style="background-color: ${contact.status_color || '#6c757d'}">
                                                ${escapeHtml(contact.status_name)}
                                            </div>
                                        ` : ''}
                                    </div>
                                    <div class="contact-quick-actions">
                                        <button class="contact-quick-action-btn" data-action="phone" title="Log Phone Call">
                                            <i class="bi bi-telephone"></i>
                                        </button>
                                        <button class="contact-quick-action-btn" data-action="email" title="Log Email">
                                            <i class="bi bi-envelope"></i>
                                        </button>
                                        ${renderCallListButton(contact, salespersonId)}
                                    </div>
                                </div>
                            `;
                                resultDropdown.append(contactHtml);
                            });

                            // Attach click handlers to the contact items and quick action buttons
                            $('.contact-search-item').each(function() {
                                const $item = $(this);
                                const contactId = $item.data('contact-id');
                                const contact = data.find(c => c.id === contactId);

                                // Click on the contact info (not the buttons) opens the modal
                                $item.find('.contact-info').on('click', function(e) {
                                    e.preventDefault();
                                    e.stopPropagation();

                                    const salespersonId = getActiveSalespersonId();

                                    UniversalContactPreview.open(contact, {
                                        salesperson_id: salespersonId
                                    });

                                    // Clear search
                                    $('input[name="contact_search"]').val('');
                                    $('#contact-results').empty();
                                });

                                // Click on quick action buttons
                                $item.find('.contact-quick-action-btn').not('.call-list-action').on('click', function(e) {
                                    e.preventDefault();
                                    e.stopPropagation();

                                    const action = $(this).data('action');
                                    const communicationType = action === 'phone' ? 'Phone' : 'Email';
                                    const salespersonId = getActiveSalespersonId();

                                    UniversalContactPreview.open(contact, {
                                        salesperson_id: salespersonId,
                                        communication_type: communicationType
                                    });

                                    // Clear search
                                    $('input[name="contact_search"]').val('');
                                    $('#contact-results').empty();
                                });
                            });
                        } else {
                            resultDropdown.append('<div class="contact-search-item disabled">No contacts found</div>');
                        }
                    },
                    error: function(xhr, status) {
                        if (status === 'abort') {
                            return;
                        }
                        if (mySeq !== requestSeq) {
                            return;
                        }
                        resultDropdown.empty();
                    }
                });
            } else {
                if (activeRequest && activeRequest.readyState !== 4) {
                    activeRequest.abort();
                }
                requestSeq++;
                resultDropdown.empty();
            }
        }, 150);
    });

    $(document).on('click', '.contact-quick-action-btn.call-list-action', function(e) {
        e.preventDefault();
        e.stopPropagation();

        const button = e.currentTarget;
        if (button.disabled || button.classList.contains('is-disabled')) {
            showContactSearchToast('Select a salesperson before using the call list.', 'warning');
            return;
        }

        const salespersonId = getActiveSalespersonId();
        if (!salespersonId) {
            showContactSearchToast('Select a salesperson before using the call list.', 'warning');
            return;
        }

        const contactId = button.getAttribute('data-contact-id');
        const contactName = button.getAttribute('data-contact-name') || 'Contact';
        const isRemoving = button.classList.contains('remove-from-call-list-btn');
        const icon = button.querySelector('i');
        const previousIconClass = icon ? icon.className : '';

        if (icon) {
            icon.className = 'bi bi-hourglass-split';
        }
        button.disabled = true;

        fetch(`/salespeople/${salespersonId}/${isRemoving ? 'remove-from-call-list' : 'add-to-call-list'}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify(isRemoving ? {
                contact_id: contactId
            } : {
                contact_id: contactId,
                notes: '',
                priority: 0
            })
        })
            .then(response => response.json())
            .then(data => {
                if (!data.success) {
                    throw new Error(data.error || 'Unable to update call list');
                }

                updateCallListButton(
                    button,
                    !isRemoving,
                    isRemoving ? 'Add to call list' : 'Remove from call list'
                );
                showContactSearchToast(
                    `${contactName} ${isRemoving ? 'removed from' : 'added to'} call list`,
                    'success'
                );
            })
            .catch(error => {
                console.error('Error updating call list:', error);
                if (icon) {
                    icon.className = previousIconClass;
                }
                showContactSearchToast(error.message || 'An error occurred while updating the call list.', 'danger');
            })
            .finally(() => {
                button.disabled = false;
            });
    });

    // Clear contact search results when clicking outside
    $(document).on('click', function(e) {
        if (!$(e.target).closest('input[name="contact_search"], #contact-results-container').length) {
            $('#contact-results').empty();
        }
    });
});

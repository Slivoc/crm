// Contact Search functionality with quick action buttons
$(document).ready(function() {
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
                activeRequest = $.ajax({
                    url: "/customers/search_contact",
                    type: 'GET',
                    data: { query: query },
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
                                        <div class="contact-name">${contact.full_name}</div>
                                        ${contact.email ? `
                                            <div class="contact-detail">
                                                <i class="bi bi-envelope"></i>
                                                <span>${contact.email}</span>
                                            </div>
                                        ` : ''}
                                        ${contact.phone ? `
                                            <div class="contact-detail">
                                                <i class="bi bi-telephone"></i>
                                                <span>${contact.phone}</span>
                                            </div>
                                        ` : ''}
                                        ${contact.job_title ? `
                                            <div class="contact-detail">
                                                <i class="bi bi-briefcase"></i>
                                                <span>${contact.job_title}</span>
                                            </div>
                                        ` : ''}
                                        <div class="contact-customer">
                                            <i class="bi bi-building me-1"></i>${contact.customer_name || 'No Customer'}
                                        </div>
                                        ${contact.status_name ? `
                                            <div class="contact-status" style="background-color: ${contact.status_color || '#6c757d'}">
                                                ${contact.status_name}
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

                                    // Get current salesperson ID from URL or session
                                    const urlParams = new URLSearchParams(window.location.search);
                                    const salespersonId = urlParams.get('salesperson_id') ||
                                                         window.location.pathname.match(/\/salespeople\/(\d+)/)?.[1];

                                    UniversalContactPreview.open(contact, {
                                        salesperson_id: salespersonId
                                    });

                                    // Clear search
                                    $('input[name="contact_search"]').val('');
                                    $('#contact-results').empty();
                                });

                                // Click on quick action buttons
                                $item.find('.contact-quick-action-btn').on('click', function(e) {
                                    e.preventDefault();
                                    e.stopPropagation();

                                    const action = $(this).data('action');
                                    const communicationType = action === 'phone' ? 'Phone' : 'Email';

                                    // Get current salesperson ID
                                    const urlParams = new URLSearchParams(window.location.search);
                                    const salespersonId = urlParams.get('salesperson_id') ||
                                                         window.location.pathname.match(/\/salespeople\/(\d+)/)?.[1];

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

    // Clear contact search results when clicking outside
    $(document).on('click', function(e) {
        if (!$(e.target).closest('input[name="contact_search"], #contact-results-container').length) {
            $('#contact-results').empty();
        }
    });
});

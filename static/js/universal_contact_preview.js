// Universal Contact Preview Module - Enhanced with Edit Functionality
const UniversalContactPreview = {
    currentContact: null,
    currentOptions: {},
    timezoneInterval: null,
    allTimezones: [],
    contactStatuses: [],
    timezoneCacheKey: 'crm_timezones_cache_v1',
    timezoneCacheTtlMs: 24 * 60 * 60 * 1000,
    notesSaveTimer: null,
    notesSaveDelayMs: 600,
    lastNotesValue: '',

    /**
     * Initialize the module - call this on page load
     */
    initialize: function() {
        this.loadContactStatuses();
        this.attachEventHandlers();
    },

    /**
     * Load all timezones
     */
    loadTimezones: async function() {
        try {
            const response = await fetch('/customers/timezones');
            const result = await response.json();
            if (result.success) {
                this.allTimezones = result.timezones;
                this.cacheTimezones(result.timezones);
            }
        } catch (error) {
            console.error('Failed to load timezones:', error);
        }
    },

    cacheTimezones: function(timezones) {
        try {
            const payload = {
                ts: Date.now(),
                timezones: timezones || []
            };
            localStorage.setItem(this.timezoneCacheKey, JSON.stringify(payload));
        } catch (error) {
            console.warn('Failed to cache timezones:', error);
        }
    },

    getCachedTimezones: function() {
        try {
            const raw = localStorage.getItem(this.timezoneCacheKey);
            if (!raw) return null;
            const payload = JSON.parse(raw);
            if (!payload || !payload.ts || !Array.isArray(payload.timezones)) {
                return null;
            }
            const age = Date.now() - payload.ts;
            if (age > this.timezoneCacheTtlMs) {
                return null;
            }
            return payload.timezones;
        } catch (error) {
            return null;
        }
    },

    ensureTimezonesLoaded: async function() {
        if (this.allTimezones.length > 0) return;
        const cached = this.getCachedTimezones();
        if (cached && cached.length > 0) {
            this.allTimezones = cached;
            return;
        }
        await this.loadTimezones();
    },

    /**
     * Load contact statuses
     */
    loadContactStatuses: async function() {
        try {
            const response = await fetch('/customers/contact_statuses');
            const result = await response.json();
            if (result.success) {
                this.contactStatuses = result.statuses;
                this.populateStatusSelect();
            } else {
                // Fallback statuses
                this.contactStatuses = [
                    {id: 1, name: 'Active', color: '#28a745'},
                    {id: 2, name: 'Inactive', color: '#ffc107'}
                ];
                this.populateStatusSelect();
            }
        } catch (error) {
            console.error('Failed to load contact statuses:', error);
            this.contactStatuses = [
                {id: 1, name: 'Active', color: '#28a745'},
                {id: 2, name: 'Inactive', color: '#ffc107'}
            ];
            this.populateStatusSelect();
        }
    },

    /**
     * Populate status select dropdown
     */
    populateStatusSelect: function() {
        const select = $('#edit_preview_status');
        if (!select.length) return;

        select.empty();
        this.contactStatuses.forEach(status => {
            select.append(`<option value="${status.id}">${status.name}</option>`);
        });
    },

    /**
     * Populate timezone select
     */
    populateTimezoneSelect: function(selectedTimezone = null) {
        const select = $('#edit_preview_timezone');
        if (!select.length) return;

        select.html('<option value="">Select timezone...</option>');

        // Group by region
        const grouped = {};
        this.allTimezones.forEach(tz => {
            const region = tz.value.split('/')[0];
            if (!grouped[region]) grouped[region] = [];
            grouped[region].push(tz);
        });

        // Add optgroups
        Object.keys(grouped).sort().forEach(region => {
            const optgroup = $('<optgroup>').attr('label', region);
            grouped[region].forEach(tz => {
                const option = $('<option>')
                    .val(tz.value)
                    .text(tz.label);
                if (selectedTimezone && tz.value === selectedTimezone) {
                    option.prop('selected', true);
                }
                optgroup.append(option);
            });
            select.append(optgroup);
        });
    },

    /**
 * Suggest timezone based on country
 */
suggestTimezoneForCountry: async function(countryCode) {
    if (!countryCode) return;

    try {
        const response = await fetch(`/customers/timezone/suggest/${countryCode}`);
        const result = await response.json();

        if (result.success) {
            const select = $('#edit_preview_timezone');
            const hintEl = $('#preview-timezone-hint');

            if (select.length) {
                select.val(result.timezone);
            }

            if (hintEl.length && result.hint) {
                hintEl.text(result.hint).addClass('text-info');
            }

            // If multiple timezones, log the options
            if (result.multiple && result.options) {
                console.log('Multiple timezone options available:', result.options);
            }
        }
    } catch (error) {
        console.error('Failed to suggest timezone:', error);
    }
},

    /**
     * Attach all event handlers
     */
    attachEventHandlers: function() {
        // Edit button in preview modal
        $(document).on('click', '#edit-from-preview-btn', () => {
            this.openEditModal();
        });

        // Email button in preview modal
        $(document).on('click', '#contact-modal-email-btn', (e) => {
            e.preventDefault();
            const contact = this.currentContact || {};
            if (!contact.email) {
                alert('No email address available for this contact.');
                return;
            }
            const showEmailModal = () => {
                const modalEl = document.getElementById('emailModal');
                if (!modalEl || !window.emailModalInstance) {
                    alert('Email modal not available. Please refresh and try again.');
                    return;
                }
                modalEl.dataset.customerId = contact.customer_id || '';
                const contactName = contact.full_name || [contact.name, contact.second_name].filter(Boolean).join(' ').trim();
                const recipients = [{
                    id: contact.id || '',
                    name: contactName || contact.email,
                    email: contact.email,
                    title: contact.job_title || '',
                    company: contact.customer_name || '',
                    customerId: contact.customer_id || ''
                }];
                window.emailModalInstance.clearRecipients();
                window.emailModalInstance.addMultipleRecipients(recipients);
                const emailModal = new bootstrap.Modal(modalEl);
                emailModal.show();
            };

            const previewEl = document.getElementById('contactPreviewModal');
            if (previewEl && previewEl.classList.contains('show')) {
                const handleHidden = () => {
                    previewEl.removeEventListener('hidden.bs.modal', handleHidden);
                    showEmailModal();
                };
                previewEl.addEventListener('hidden.bs.modal', handleHidden);
                const instance = bootstrap.Modal.getInstance(previewEl);
                if (instance) {
                    instance.hide();
                } else {
                    handleHidden();
                }
            } else {
                showEmailModal();
            }
        });

        // Save button in edit modal
        $(document).on('click', '#save-preview-contact-btn', () => {
            this.saveContact();
        });

        // Preset response buttons
        $(document).on('click', '.preset-response-btn', function(e) {
            e.preventDefault();
            const response = $(this).data('response');
            const notesField = $('#comm-notes');
            $('.preset-response-btn').removeClass('active');
            $(this).addClass('active');
            notesField.val(response).focus();
        });

        // Cancel/Clear communication button
        $(document).on('click', '#cancel-communication-btn', (e) => {
            e.preventDefault();
            this.clearCommunicationForm();
        });

        // Communication form submission
        $(document).on('submit', '#communication-form', (e) => {
            e.preventDefault();
            const formData = $(e.target).serialize();
            this.submitCommunication(formData);
        });

        // Reset when preview modal closes
        $('#contactPreviewModal').on('hidden.bs.modal', () => {
            this.clearCommunicationForm();
            if (this.timezoneInterval) {
                clearInterval(this.timezoneInterval);
                this.timezoneInterval = null;
            }
            this.clearNotesSaveTimer();
        });

        // Refresh preview modal after edit modal closes
        $('#editContactFromPreviewModal').on('hidden.bs.modal', () => {
            // If preview modal is still open, refresh the contact data
            if ($('#contactPreviewModal').hasClass('show')) {
                this.refreshContactData();
            }
        });

        $(document).on('input', '#contact-notes-input', () => {
            this.queueNotesSave();
        });

        $(document).on('blur', '#contact-notes-input', () => {
            this.flushNotesSave();
        });
    },

    /**
     * Open the contact preview modal
     */
    open: function(contact, options = {}) {
        this.currentContact = contact;
        this.currentOptions = options;

        this.populateContactInfo(contact);
        this.initializeTimezoneClock(contact);
        this.loadCommunicationHistory(contact.id);
        this.initializeCommunicationForm(options.communication_type);

        const modal = new bootstrap.Modal(document.getElementById('contactPreviewModal'));
        modal.show();

        $('#contactPreviewModal').one('shown.bs.modal', function() {
            $('#comm-notes').focus();
        });
    },

/**
 * Open the edit modal
 */
openEditModal: async function() {
    if (!this.currentContact) return;

    try {
        // Fetch the full contact details with proper headers
        const response = await fetch(`/customers/contacts/${this.currentContact.id}`, {
            headers: {
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            }
        });

        const result = await response.json();

        if (!result.success) {
            this.showMessage('edit-preview-messages', 'Failed to load contact details');
            return;
        }

        const contact = result.contact;

        // Populate edit form with fetched data
        $('#edit_preview_contact_id').val(contact.id);
        $('#edit_preview_first_name').val(contact.name || '');
        $('#edit_preview_second_name').val(contact.second_name || '');
        $('#edit_preview_email').val(contact.email || '');
        $('#edit_preview_phone').val(contact.phone || '');
        $('#edit_preview_job_title').val(contact.job_title || '');

        // Ensure statuses are loaded before setting the value
        if (this.contactStatuses.length === 0) {
            await this.loadContactStatuses();
        }

        // Set status with the fetched value
        $('#edit_preview_status').val(contact.status_id || 1);

        // Ensure timezones are loaded before populating the select
        await this.ensureTimezonesLoaded();

        // Populate timezone select and set current value
        this.populateTimezoneSelect(contact.timezone || 'UTC');

        // Suggest timezone if not set and customer has a country
        if (contact.customer_id &&
            (!contact.timezone || contact.timezone === 'UTC')) {
            try {
                const customerResponse = await fetch(`/customers/${contact.customer_id}`, {
                    headers: {
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                });
                const customerResult = await customerResponse.json();
                if (customerResult.success && customerResult.customer.country) {
                    await this.suggestTimezoneForCountry(customerResult.customer.country);
                }
            } catch (error) {
                console.error('Failed to fetch customer for timezone suggestion:', error);
            }
        }

        // Clear any previous messages
        $('#edit-preview-messages').addClass('d-none');

        // Show edit modal
        const editModal = new bootstrap.Modal(document.getElementById('editContactFromPreviewModal'));
        editModal.show();

    } catch (error) {
        console.error('Failed to load contact details:', error);
        alert('Failed to load contact details. Please try again.');
    }
},


    /**
     * Save contact changes
     */
    saveContact: async function() {
        const form = $('#contact-edit-from-preview-form')[0];
        const contactId = $('#edit_preview_contact_id').val();

        const formData = {
            name: $('#edit_preview_first_name').val().trim(),
            second_name: $('#edit_preview_second_name').val().trim(),
            email: $('#edit_preview_email').val().trim(),
            phone: $('#edit_preview_phone').val().trim(),
            job_title: $('#edit_preview_job_title').val().trim(),
            status_id: $('#edit_preview_status').val(),
            timezone: $('#edit_preview_timezone').val() || 'UTC'
        };

        // Disable save button
        const saveBtn = $('#save-preview-contact-btn');
        saveBtn.prop('disabled', true).html('<span class="spinner-border spinner-border-sm me-1"></span> Saving...');

        try {
            const response = await fetch(`/customers/contacts/${contactId}/update`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(formData)
            });

            const result = await response.json();

            if (result.success) {
                this.showMessage('edit-preview-messages', 'Contact updated successfully!', 'success');

                // Update currentContact with new data
                this.currentContact = {
                    ...this.currentContact,
                    name: formData.name,
                    second_name: formData.second_name,
                    full_name: `${formData.name} ${formData.second_name}`.trim(),
                    email: formData.email,
                    phone: formData.phone,
                    job_title: formData.job_title,
                    status_id: formData.status_id,
                    timezone: formData.timezone
                };

                // Close edit modal after short delay
                setTimeout(() => {
                    const modal = bootstrap.Modal.getInstance(document.getElementById('editContactFromPreviewModal'));
                    modal.hide();
                }, 1000);

            } else {
                this.showMessage('edit-preview-messages', result.error || 'Failed to update contact');
            }
        } catch (error) {
            this.showMessage('edit-preview-messages', 'Failed to update contact. Please try again.');
        } finally {
            saveBtn.prop('disabled', false).html('Save Changes');
        }
    },

  /**
 * Refresh contact data in preview modal
 */
    refreshContactData: async function() {
    if (!this.currentContact) return;

    try {
        const response = await fetch(`/customers/contacts/${this.currentContact.id}`, {
            headers: {
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            }
        });
        const result = await response.json();

        if (result.success) {
            this.currentContact = result.contact;
            this.populateContactInfo(result.contact);
            this.initializeTimezoneClock(result.contact);
        }
    } catch (error) {
        console.error('Failed to refresh contact data:', error);
    }
},

    clearNotesSaveTimer: function() {
        if (this.notesSaveTimer) {
            clearTimeout(this.notesSaveTimer);
            this.notesSaveTimer = null;
        }
    },

    setNotesStatus: function(message, isError = false) {
        const statusEl = $('#contact-notes-status');
        if (!statusEl.length) return;

        statusEl
            .toggleClass('text-danger', isError)
            .toggleClass('text-muted', !isError)
            .text(message || '');
    },

    setNotesInputValue: function(notes) {
        const notesValue = notes || '';
        $('#contact-notes-input').val(notesValue);
        this.lastNotesValue = notesValue;
        this.setNotesStatus('');
    },

    queueNotesSave: function() {
        this.clearNotesSaveTimer();
        this.notesSaveTimer = setTimeout(() => {
            this.saveNotes();
        }, this.notesSaveDelayMs);
    },

    flushNotesSave: function() {
        this.clearNotesSaveTimer();
        this.saveNotes();
    },

    saveNotes: async function() {
        if (!this.currentContact) return;
        const notes = $('#contact-notes-input').val() || '';

        if (notes === this.lastNotesValue) {
            return;
        }

        this.setNotesStatus('Saving...');

        try {
            const response = await fetch(`/customers/contacts/${this.currentContact.id}/update-notes`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ notes })
            });
            const result = await response.json();

            if (result.success) {
                this.lastNotesValue = notes;
                this.currentContact = {
                    ...this.currentContact,
                    notes: notes
                };
                this.setNotesStatus('Saved');
                setTimeout(() => {
                    this.setNotesStatus('');
                }, 2000);
            } else {
                this.setNotesStatus('Failed', true);
            }
        } catch (error) {
            this.setNotesStatus('Failed', true);
        }
    },

    /**
     * Show message in specified element
     */
    showMessage: function(elementId, message, type = 'danger') {
        const msgEl = $(`#${elementId}`);
        if (msgEl.length) {
            msgEl.removeClass('d-none alert-danger alert-success alert-info')
               .addClass(`alert-${type}`)
               .text(message);
            setTimeout(() => msgEl.addClass('d-none'), 5000);
        }
    },

    /**
     * Initialize and start the timezone clock
     */
    initializeTimezoneClock: function(contact) {
        if (this.timezoneInterval) {
            clearInterval(this.timezoneInterval);
            this.timezoneInterval = null;
        }

        if (contact.timezone && contact.timezone !== 'UTC' && contact.timezone !== '') {
            $('#contact-timezone-container').show();
            this.updateTimezoneClock(contact.timezone);
            this.timezoneInterval = setInterval(() => {
                this.updateTimezoneClock(contact.timezone);
            }, 1000);
        } else {
            $('#contact-timezone-container').hide();
        }
    },

    /**
     * Update the timezone clock display
     */
    updateTimezoneClock: function(timezone) {
        try {
            const now = new Date();
            const options = {
                timeZone: timezone,
                hour12: true,
                hour: 'numeric',
                minute: '2-digit',
                second: '2-digit'
            };

            const timeString = now.toLocaleTimeString('en-US', options);
            const tzAbbr = now.toLocaleTimeString('en-US', {
                timeZone: timezone,
                timeZoneName: 'short'
            }).split(' ').pop();

            $('#contact-timezone-display').text(`${timeString} ${tzAbbr}`);
        } catch (error) {
            console.error('Error updating timezone clock:', error);
            $('#contact-timezone-display').text('Invalid timezone');
        }
    },

    /**
     * Populate the modal with contact information
     */
    populateContactInfo: function(contact) {
        $('#contact-modal-name').text(contact.full_name || 'Unknown Contact');

        if (contact.customer_name) {
            $('#contact-modal-customer').html(`<i class="bi bi-building me-1"></i>${contact.customer_name}`).show();
        } else {
            $('#contact-modal-customer').hide();
        }

        if (contact.status_name) {
            $('#contact-modal-status')
                .text(contact.status_name)
                .css('background-color', contact.status_color || '#6c757d')
                .show();
        } else {
            $('#contact-modal-status').hide();
        }

        if (contact.email) {
            $('#contact-modal-email').text(contact.email).attr('href', `mailto:${contact.email}`);
            $('#contact-email-container').show();
            $('#contact-modal-email-btn')
                .removeClass('d-none')
                .attr('data-contact-id', contact.id)
                .attr('data-contact-email', contact.email);
        } else {
            $('#contact-email-container').hide();
            $('#contact-modal-email-btn').addClass('d-none');
        }

        if (contact.phone) {
            $('#contact-modal-phone').text(contact.phone).attr('href', `tel:${contact.phone}`);
            $('#contact-phone-container').show();
        } else {
            $('#contact-phone-container').hide();
        }

        if (contact.job_title) {
            $('#contact-modal-job-title').text(contact.job_title);
            $('#contact-job-title-container').show();
        } else {
            $('#contact-job-title-container').hide();
        }

        // Build URL for full contact page with optional salesperson_id
        let contactUrl = `/salespeople/contact/${contact.id}`;
        if (this.currentOptions.salesperson_id) {
            contactUrl += `?salesperson_id=${this.currentOptions.salesperson_id}`;
        }
        $('#view-full-contact-btn').attr('href', contactUrl);
        this.setNotesInputValue(contact.notes);
    },

    /**
     * Load communication history for the contact
     */
    loadCommunicationHistory: function(contactId) {
        const historyContainer = $('#communication-history-list');

        historyContainer.html(`
            <div class="text-center text-muted py-4">
                <div class="spinner-border spinner-border-sm" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
                <p class="mt-2 mb-0">Loading communication history...</p>
            </div>
        `);

        $.ajax({
            url: `/customers/contacts/${contactId}/communications`,
            type: 'GET',
            dataType: 'json',
            success: (data) => {
                if (data.length > 0) {
                    historyContainer.empty();
                    data.forEach((comm) => {
                        const commHtml = this.renderCommunicationItem(comm);
                        historyContainer.append(commHtml);
                    });
                } else {
                    historyContainer.html(`
                        <div class="text-center text-muted py-4">
                            <i class="bi bi-chat-left-text" style="font-size: 2rem;"></i>
                            <p class="mt-2 mb-0">No communication history found.</p>
                        </div>
                    `);
                }
            },
            error: () => {
                historyContainer.html(`
                    <div class="text-center text-danger py-4">
                        <i class="bi bi-exclamation-triangle" style="font-size: 2rem;"></i>
                        <p class="mt-2 mb-0">Failed to load communication history.</p>
                    </div>
                `);
            }
        });
    },

    /**
     * Render a single communication item
     */
    renderCommunicationItem: function(comm) {
        const typeClass = `type-${comm.communication_type.toLowerCase()}`;
        const iconClass = this.getCommunicationIcon(comm.communication_type);
        const formattedDate = this.formatDate(comm.communication_date);

        return `
            <div class="communication-item ${typeClass}">
                <div class="communication-header">
                    <div class="communication-type">
                        <i class="bi bi-${iconClass} me-1"></i>
                        ${comm.communication_type}
                    </div>
                    <div class="communication-date">${formattedDate}</div>
                </div>
                <div class="communication-notes">${comm.notes}</div>
                ${comm.salesperson_name ? `
                    <div class="communication-salesperson">
                        <i class="bi bi-person me-1"></i>
                        Logged by: ${comm.salesperson_name}
                    </div>
                ` : ''}
            </div>
        `;
    },

    /**
     * Get icon for communication type
     */
    getCommunicationIcon: function(type) {
        const icons = {
            'Phone': 'telephone',
            'Email': 'envelope',
            'Meeting': 'calendar-event',
            'Other': 'chat-left-text'
        };
        return icons[type] || 'chat-left-text';
    },

    /**
     * Format date string
     */
    formatDate: function(dateString) {
        const date = new Date(dateString);
        const now = new Date();
        const diffTime = Math.abs(now - date);
        const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));

        if (diffDays === 0) {
            return 'Today at ' + date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
        } else if (diffDays === 1) {
            return 'Yesterday at ' + date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
        } else if (diffDays < 7) {
            return date.toLocaleDateString('en-US', { weekday: 'long', hour: '2-digit', minute: '2-digit' });
        } else {
            return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        }
    },

    /**
     * Initialize the communication form
     */
    initializeCommunicationForm: function(communicationType = '') {
        $('#comm-contact-id').val(this.currentContact.id);
        $('#comm-customer-id').val(this.currentContact.customer_id);
        $('#comm-salesperson-id').val(this.currentOptions.salesperson_id || '');

        if (communicationType) {
            $('#comm-type').val(communicationType.toLowerCase());
        }

        const now = new Date();
        const dateString = now.toISOString().slice(0, 10);
        const timeString = now.toTimeString().slice(0, 5);
        $('#comm-date').val(dateString);
        $('#comm-time').val(timeString);
    },

    /**
     * Clear the communication form
     */
    clearCommunicationForm: function() {
        $('#communication-form')[0].reset();
        $('.preset-response-btn').removeClass('active');

        $('#comm-contact-id').val(this.currentContact.id);
        $('#comm-customer-id').val(this.currentContact.customer_id);
        $('#comm-salesperson-id').val(this.currentOptions.salesperson_id || '');

        const now = new Date();
        const dateString = now.toISOString().slice(0, 10);
        const timeString = now.toTimeString().slice(0, 5);
        $('#comm-date').val(dateString);
        $('#comm-time').val(timeString);

        $('#comm-notes').focus();
    },

    /**
     * Submit communication form
     */
    submitCommunication: function(formData) {
        const submitBtn = $('#communication-form button[type="submit"]');
        submitBtn.prop('disabled', true).html('<span class="spinner-border spinner-border-sm me-1"></span> Saving...');

        const customerId = this.currentContact.customer_id;

        $.ajax({
            url: `/customers/${customerId}/add_update`,
            type: 'POST',
            data: formData,
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            success: (response) => {
                this.showToast('Communication logged successfully!', 'success');
                this.clearCommunicationForm();
                this.loadCommunicationHistory(this.currentContact.id);
            },
            error: () => {
                this.showToast('Failed to log communication. Please try again.', 'danger');
            },
            complete: () => {
                submitBtn.prop('disabled', false).html('<i class="bi bi-check-circle me-1"></i> Save Communication');
            }
        });
    },

    /**
     * Show toast notification
     */
    showToast: function(message, type = 'info') {
        if ($('#contact-toast-container').length === 0) {
            $('body').append(`
                <div id="contact-toast-container" class="position-fixed bottom-0 end-0 p-3" style="z-index: 11000;">
                </div>
            `);
        }

        const toastId = 'toast-' + Date.now();
        const toast = $(`
            <div id="${toastId}" class="toast align-items-center text-white bg-${type} border-0" role="alert">
                <div class="d-flex">
                    <div class="toast-body">${message}</div>
                    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
                </div>
            </div>
        `);

        $('#contact-toast-container').append(toast);
        const bsToast = new bootstrap.Toast(document.getElementById(toastId));
        bsToast.show();

        toast.on('hidden.bs.toast', function() {
            $(this).remove();
        });
    }
};

// Initialize when document is ready
$(document).ready(function() {
    UniversalContactPreview.initialize();
});

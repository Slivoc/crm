class EmailModal {
    constructor() {
        console.log('EmailModal constructor called');

        // Initialize flags
        this.isSending = false;
        this.templatesLoaded = false;
        this.isCustomEmail = true; // Set to true by default

        // Initialize recipients array for multiple recipients
        this.recipients = [];

        // Find the modal
        this.modal = document.getElementById('emailModal');
        if (!this.modal) {
            console.warn('EmailModal: modal element not found');
            return;
        }

        // Get elements with null checks
        this.templateSelect = this.modal.querySelector('#template_id');
        this.customEmailCheckbox = this.modal.querySelector('#custom_email');
        this.customSubjectInput = this.modal.querySelector('#custom_subject');
        this.customBodyEditor = this.modal.querySelector('#custom_body');
        this.templateSection = this.modal.querySelector('.template-selection-section');
        this.customSection = this.modal.querySelector('.custom-email-section');
        this.previewSection = this.modal.querySelector('.preview-section');
        this.previewBtn = this.modal.querySelector('.preview-btn');
        this.sendBtn = this.modal.querySelector('.send-btn');
        this.outlookBtn = this.modal.querySelector('.outlook-btn');
        this.sendSystemBtn = this.modal.querySelector('.send-system-btn');
        this.loadingSpinner = this.modal.querySelector('.loading-spinner');

        // Reply context elements (optional)
        this.replySection = this.modal.querySelector('.reply-section');
        this.replySelect = this.modal.querySelector('#reply_message_id');
        this.replyPreview = this.modal.querySelector('#replyPreview');
        this.replyPreviewSubject = this.modal.querySelector('#replyPreviewSubject');
        this.replyPreviewMeta = this.modal.querySelector('#replyPreviewMeta');
        this.replyPreviewBody = this.modal.querySelector('#replyPreviewBody');
        this.replyPreviewText = '';
        this.replyPreviewSubjectText = '';
        this.replyMessageId = '';
        this.replySubject = '';
        this.replyOptionsEmail = '';
        this.manualSubjectValue = '';
        this.context = this.modal.dataset.context || '';
        this.aiSection = this.modal.querySelector('#aiDraftSection');
        this.aiGenerateBtn = this.modal.querySelector('#ai_generate_btn');
        this.aiStatus = this.modal.querySelector('#aiDraftStatus');
        this.aiNews = this.modal.querySelector('#aiDraftNews');
        this.aiUseNews = this.modal.querySelector('#ai_use_news');

        // Recipients elements
        this.recipientsList = this.modal.querySelector('.recipients-list');
        this.recipientCount = this.modal.querySelector('.recipient-count');
        this.noRecipientsDiv = this.modal.querySelector('.no-recipients');
        this.addRecipientBtn = this.modal.querySelector('.add-recipient-btn');

        // Initialize data storage with empty values
        this.contactData = { id: "", name: "", email: "" };
        this.customerData = { id: "" };

        // Create hidden fields
        this.createHiddenFields();

        // Initialize and bind events
        this.initializeModal();
        this.bindEvents();

        // Apply initial state for custom email as default (with safety check)
        if (this.previewSection && this.templateSection && this.customSection) {
            this.toggleEmailMode();
        } else {
            console.warn('Some modal sections not found, skipping initial toggle');
        }

        this.originalSubject = '';
        this.originalBody = '';
        this.isContentPersonalized = false;
    }

    // FIXED: insertPlaceholderAtCursor method
    insertPlaceholderAtCursor(placeholder) {
        console.log('Inserting placeholder:', placeholder);

        if (!this.isCustomEmail) {
            alert('Placeholders can only be used in custom email mode');
            return;
        }

        // Determine which field should receive the placeholder
        let targetField = null;
        const activeElement = document.activeElement;

        console.log('Active element:', activeElement.id, activeElement.tagName);

        if (activeElement === this.customSubjectInput) {
            targetField = 'subject';
        } else if (activeElement === this.customBodyEditor) {
            targetField = 'body';
        } else if (window.tinymce && tinymce.get('custom_body') && tinymce.get('custom_body').hasFocus()) {
            targetField = 'body';
        } else {
            // Default to body if no specific field is focused
            targetField = 'body';
            console.log('No specific field focused, defaulting to body');
        }

        console.log('Target field:', targetField);

        if (targetField === 'body') {
            // Handle TinyMCE editor
            if (window.tinymce && tinymce.get('custom_body')) {
                console.log('Inserting into TinyMCE editor');
                const editor = tinymce.get('custom_body');
                editor.focus();
                editor.execCommand('mceInsertContent', false, placeholder);
            } else {
                // Handle regular textarea
                console.log('Inserting into regular textarea');
                const textarea = this.customBodyEditor;
                if (textarea) {
                    const start = textarea.selectionStart || textarea.value.length;
                    const end = textarea.selectionEnd || textarea.value.length;
                    const text = textarea.value;

                    textarea.value = text.substring(0, start) + placeholder + text.substring(end);

                    const newPosition = start + placeholder.length;
                    textarea.setSelectionRange(newPosition, newPosition);
                    textarea.focus();

                    console.log('Placeholder inserted at position:', newPosition);
                }
            }
        } else if (targetField === 'subject') {
            console.log('Inserting into subject field');
            const start = this.customSubjectInput.selectionStart || this.customSubjectInput.value.length;
            const end = this.customSubjectInput.selectionEnd || this.customSubjectInput.value.length;
            const text = this.customSubjectInput.value;

            this.customSubjectInput.value = text.substring(0, start) + placeholder + text.substring(end);

            const newPosition = start + placeholder.length;
            this.customSubjectInput.setSelectionRange(newPosition, newPosition);
            this.customSubjectInput.focus();

            console.log('Placeholder inserted into subject at position:', newPosition);
        }
    }

    // SIMPLE: Just create clickable placeholder text
    createClickablePlaceholders() {
        console.log('Creating clickable placeholders...');

        // Find the container where placeholders should go
        const placeholderTags = this.modal.querySelector('.placeholder-tags');
        if (!placeholderTags) {
            console.error('placeholder-tags container not found');
            return;
        }

        // Define the placeholders we want
        const placeholders = [
            '{{contact_name}}',
            '{{contact_first_name}}',
            '{{contact_title}}',
            '{{company_name}}',
            '{{today_date}}',
            '{{sender_name}}',
            '{{sender_title}}'
        ];

        // Clear existing content and create new clickable elements
        placeholderTags.innerHTML = '';

        placeholders.forEach((placeholder, index) => {
            // Create clickable span
            const span = document.createElement('span');
            span.textContent = placeholder;
            span.style.cssText = `
                cursor: pointer;
                padding: 3px 6px;
                border-radius: 4px;
                background-color: #f8f9fa;
                border: 1px solid #dee2e6;
                display: inline-block;
                margin: 2px;
                font-family: monospace;
                font-size: 0.875rem;
                user-select: none;
                transition: all 0.2s ease;
            `;

            // Add hover effect
            span.addEventListener('mouseenter', () => {
                span.style.backgroundColor = '#007bff';
                span.style.color = 'white';
                span.style.borderColor = '#007bff';
                span.style.transform = 'translateY(-1px)';
            });

            span.addEventListener('mouseleave', () => {
                span.style.backgroundColor = '#f8f9fa';
                span.style.color = '';
                span.style.borderColor = '#dee2e6';
                span.style.transform = '';
            });

            // Add click handler
            span.addEventListener('click', (e) => {
                e.preventDefault();
                console.log('Clicked placeholder:', placeholder);
                this.insertPlaceholder(placeholder);

                // Visual feedback
                span.style.backgroundColor = '#28a745';
                span.style.borderColor = '#28a745';
                setTimeout(() => {
                    span.style.backgroundColor = '#f8f9fa';
                    span.style.borderColor = '#dee2e6';
                }, 200);
            });

            placeholderTags.appendChild(span);
        });

        console.log(`Created ${placeholders.length} clickable placeholders`);
    }

    // SIMPLE: Insert placeholder into the right field
    insertPlaceholder(placeholder) {
        if (!this.isCustomEmail) {
            alert('Placeholders can only be used in custom email mode');
            return;
        }

        // Check what field is focused
        const activeElement = document.activeElement;
        let targetField = null;

        if (activeElement === this.customSubjectInput) {
            targetField = this.customSubjectInput;
        } else if (activeElement === this.customBodyEditor) {
            targetField = this.customBodyEditor;
        } else {
            // Default to body field
            targetField = this.customBodyEditor;
            if (targetField) targetField.focus();
        }

        if (!targetField) {
            console.warn('No target field found for placeholder insertion');
            return;
        }

        // Insert at cursor position
        const start = targetField.selectionStart || targetField.value.length;
        const end = targetField.selectionEnd || targetField.value.length;
        const currentValue = targetField.value;

        const newValue = currentValue.substring(0, start) + placeholder + currentValue.substring(end);
        targetField.value = newValue;

        // Move cursor to end of inserted text
        const newPosition = start + placeholder.length;
        targetField.setSelectionRange(newPosition, newPosition);
        targetField.focus();

        console.log(`Inserted "${placeholder}" at position ${start}`);
    }

    isReplyEnabled() {
        return this.context === 'contact_suggestions';
    }

    sanitizeHtml(html) {
        if (!html) return '';
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        ['script', 'style', 'meta', 'link', 'head', 'title'].forEach((tag) => {
            doc.querySelectorAll(tag).forEach((node) => node.remove());
        });
        return doc.body ? doc.body.innerHTML : '';
    }

    escapeHtml(value) {
        if (value === undefined || value === null) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    getCustomBodyValue() {
        if (window.tinymce && tinymce.get('custom_body')) {
            return tinymce.get('custom_body').getContent();
        }
        return this.customBodyEditor ? this.customBodyEditor.value : '';
    }

    setCustomBodyValue(value) {
        if (window.tinymce && tinymce.get('custom_body')) {
            tinymce.get('custom_body').setContent(value || '');
            return;
        }
        if (this.customBodyEditor) {
            this.customBodyEditor.value = value || '';
        }
    }

    applyContactSuggestionsDefaults() {
        if (!this.isReplyEnabled() || !this.isCustomEmail) {
            return;
        }
        const currentBody = (this.getCustomBodyValue() || '').trim();
        if (currentBody) {
            return;
        }
        const defaultBody = 'Hi {{contact_first_name}}\\n\\nplease see our quote below\\n\\nThanks\\n{{sender_name}}';
        this.setCustomBodyValue(defaultBody);
    }

    updateAiPanelVisibility() {
        if (!this.aiSection) {
            return;
        }
        const customerId = this.modal.dataset.customerId || '';
        const isSingleRecipient = this.recipients.length === 1;
        const shouldShow = this.context === 'contact_suggestions' && this.isCustomEmail && isSingleRecipient && customerId;
        if (!shouldShow) {
            this.aiSection.classList.add('d-none');
            return;
        }
        this.aiSection.classList.remove('d-none');
        this.applyAiPrefillFromDataset();
    }

    applyAiPrefillFromDataset() {
        const aiSubject = this.modal.dataset.aiSubject || '';
        const aiBody = this.modal.dataset.aiBody || '';
        const aiNewsRaw = this.modal.dataset.aiNews || '';
        let aiNews = [];
        if (aiNewsRaw) {
            try {
                aiNews = JSON.parse(aiNewsRaw);
            } catch (err) {
                aiNews = [];
            }
        }
        if (aiSubject && this.customSubjectInput && !this.customSubjectInput.value) {
            this.customSubjectInput.value = aiSubject;
        }
        if (aiBody && !this.getCustomBodyValue().trim()) {
            this.setCustomBodyValue(aiBody);
        }
        this.renderAiNews(aiNews);
    }

    renderAiNews(items) {
        if (!this.aiNews) {
            return;
        }
        if (!items || !items.length) {
            this.aiNews.innerHTML = '';
            return;
        }
        const formatDate = (value) => {
            if (!value) return '';
            const raw = String(value).trim();
            return raw ? ` (${this.escapeHtml(raw)})` : '';
        };
        const html = items.slice(0, 3).map((item) => {
            const headline = item?.headline || 'News item';
            const date = formatDate(item?.published_date || item?.published_at || item?.date);
            return `<div class="badge bg-light text-dark border me-2 mb-1">${this.escapeHtml(headline)}${date}</div>`;
        }).join('');
        this.aiNews.innerHTML = html;
    }

    async generateAiDraftFromModal() {
        const salespersonId = this.modal.dataset.salespersonId || '';
        const customerId = this.modal.dataset.customerId || '';
        if (!salespersonId || !customerId) {
            this.showToast('Customer context is required for AI drafts.', 'warning');
            return;
        }
        const payload = { customer_id: customerId };
        const recipient = this.recipients.length ? this.recipients[0] : null;
        const contactId = recipient && recipient.id ? String(recipient.id) : '';
        const includeNews = this.aiUseNews ? this.aiUseNews.checked : true;
        payload.include_news = includeNews;
        const replySubject = this.replyMessageId ? (this.replyPreviewSubjectText || '') : '';
        const replyBody = this.replyMessageId ? (this.replyPreviewText || '') : '';
        const graphSubject = replySubject || this.modal.dataset.graphEmailSubject || '';
        const graphBody = replyBody || this.modal.dataset.graphEmailBody || '';
        if (graphSubject) {
            payload.graph_email_subject = graphSubject;
        }
        if (graphBody) {
            payload.graph_email_body = graphBody;
        }

        if (this.aiStatus) {
            this.aiStatus.textContent = 'Generating...';
        }
        if (this.aiGenerateBtn) {
            this.aiGenerateBtn.disabled = true;
        }

        try {
            const endpoint = contactId
                ? `/salespeople/contact_details/${encodeURIComponent(contactId)}/next-email`
                : `/salespeople/${salespersonId}/contact-suggestions/ai`;
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json();
            if (!response.ok || !data.suggested_email) {
                throw new Error(data.error || 'AI draft failed');
            }
            const suggested = data.suggested_email || {};
            if (!this.replyMessageId && suggested.subject && this.customSubjectInput) {
                this.customSubjectInput.value = suggested.subject;
            }
            if (suggested.body) {
                this.setCustomBodyValue(suggested.body);
            }
            if (includeNews) {
                this.renderAiNews(data.news_items || []);
            } else {
                this.renderAiNews([]);
            }
            if (this.aiStatus) {
                this.aiStatus.textContent = 'Draft ready';
            }
        } catch (error) {
            if (this.aiStatus) {
                this.aiStatus.textContent = 'AI draft failed';
            }
            this.showToast(error.message || 'AI draft failed', 'error');
        } finally {
            if (this.aiGenerateBtn) {
                this.aiGenerateBtn.disabled = false;
            }
        }
    }

    async loadReplyOptionsForRecipient(email) {
        if (!this.replySelect || !email) {
            return;
        }
        if (this.replyOptionsEmail === email) {
            return;
        }
        this.replyOptionsEmail = email;
        this.replySelect.innerHTML = '<option value="">Send new email (no reply)</option>';
        this.replySelect.value = '';
        this.replyMessageId = '';
        this.loadReplyPreview('');
        try {
            const response = await fetch(`/emails/graph/contact-timeline?email=${encodeURIComponent(email)}&limit=8`);
            const result = await response.json();
            if (!response.ok || !result.success) {
                return;
            }
            const emails = result.emails || [];
            emails.forEach((item) => {
                if (!item.id) {
                    return;
                }
                const direction = item.direction === 'sent' ? 'Sent' : 'Received';
                const dateLabel = item.timestamp ? new Date(item.timestamp).toLocaleDateString() : '';
                const subjectLabel = item.subject || '(No subject)';
                const option = document.createElement('option');
                option.value = item.id;
                option.textContent = `${direction}: ${subjectLabel}${dateLabel ? ` (${dateLabel})` : ''}`;
                this.replySelect.appendChild(option);
            });
        } catch (error) {
            console.warn('Failed to load reply options', error);
        }
    }

    async loadReplyPreview(messageId) {
        if (!this.replyPreview || !this.replyPreviewSubject || !this.replyPreviewMeta || !this.replyPreviewBody) {
            return;
        }
        if (!messageId) {
            this.replyPreview.classList.add('d-none');
            this.replyPreviewSubject.textContent = '';
            this.replyPreviewMeta.textContent = '';
            this.replyPreviewBody.textContent = '';
            this.replyPreviewSubjectText = '';
            this.replyPreviewText = '';
            return;
        }
        this.replyPreview.classList.remove('d-none');
        this.replyPreviewSubject.textContent = 'Loading preview...';
        this.replyPreviewMeta.textContent = '';
        this.replyPreviewBody.textContent = '';
        try {
            const response = await fetch(`/emails/graph/message/${encodeURIComponent(messageId)}`);
            const result = await response.json();
            if (!response.ok || !result.success || !result.message) {
                throw new Error('Unable to load message');
            }
            const msg = result.message || {};
            const fromAddr = msg.from?.emailAddress?.address || 'Unknown';
            const fromName = msg.from?.emailAddress?.name || '';
            const received = msg.receivedDateTime ? new Date(msg.receivedDateTime).toLocaleString() : 'Unknown date';
            const subject = msg.subject || '(No subject)';
            const bodyHtml = this.sanitizeHtml(msg.body?.content || msg.bodyPreview || '');

            this.replyPreviewSubject.textContent = subject;
            this.replyPreviewMeta.textContent = `From: ${fromName ? `${fromName} <${fromAddr}>` : fromAddr} | Received: ${received}`;
            this.replyPreviewBody.innerHTML = bodyHtml || '';
            this.replySubject = subject;
            this.replyPreviewSubjectText = subject;
            this.replyPreviewText = this.htmlToPlainText(bodyHtml || msg.bodyPreview || '');
            if (this.customSubjectInput) {
                this.customSubjectInput.value = subject;
            }
        } catch (error) {
            this.replyPreviewSubject.textContent = 'Unable to load preview';
            this.replyPreviewSubjectText = '';
            this.replyPreviewText = '';
        }
    }

    updateReplyModeState() {
        if (!this.isReplyEnabled() || !this.replySection || !this.isCustomEmail) {
            if (this.replySection) {
                this.replySection.classList.add('d-none');
            }
            return;
        }
        if (this.recipients.length !== 1) {
            this.replySection.classList.add('d-none');
            this.replyMessageId = '';
            return;
        }
        this.replySection.classList.remove('d-none');
        const recipient = this.recipients[0];
        this.loadReplyOptionsForRecipient(recipient.email);

        const isReply = this.replySelect && this.replySelect.value;
        if (isReply && !this.isCustomEmail && this.customEmailCheckbox) {
            this.customEmailCheckbox.checked = true;
            this.isCustomEmail = true;
            this.toggleEmailMode();
        }
        if (this.customSubjectInput) {
            if (isReply) {
                if (!this.customSubjectInput.disabled) {
                    this.manualSubjectValue = this.customSubjectInput.value;
                }
                this.customSubjectInput.disabled = true;
            } else {
                this.customSubjectInput.disabled = false;
                if (!this.customSubjectInput.value) {
                    this.customSubjectInput.value = this.manualSubjectValue || '';
                }
            }
        }
        this.replyMessageId = isReply || '';
        this.loadReplyPreview(this.replyMessageId);
    }

    // Enhanced methods for recipient management
    updateRecipientsDisplay() {
        if (!this.recipientsList || !this.recipientCount) return;

        const recipientCount = this.recipients.length;

        // Update count
        this.recipientCount.textContent = `${recipientCount} recipient${recipientCount !== 1 ? 's' : ''}`;

        // Clear current display
        this.recipientsList.innerHTML = '';

        if (recipientCount === 0) {
            // Show no recipients message
            const noRecipientsDiv = document.createElement('div');
            noRecipientsDiv.className = 'no-recipients text-center text-muted fst-italic';
            noRecipientsDiv.style.padding = '1rem';
            noRecipientsDiv.textContent = 'No recipients selected';
            this.recipientsList.appendChild(noRecipientsDiv);

            // Hide add recipient button when no recipients
            if (this.addRecipientBtn) {
                this.addRecipientBtn.classList.add('d-none');
            }
        } else {
            // Show recipients
            this.recipients.forEach((recipient, index) => {
                const recipientItem = this.createRecipientItem(recipient, index);
                this.recipientsList.appendChild(recipientItem);
            });

            // Show add recipient button when we have recipients
            if (this.addRecipientBtn) {
                this.addRecipientBtn.classList.remove('d-none');
            }
        }

        // Update button states based on recipient count
        this.updateButtonVisibility();

        this.applyContactSuggestionsDefaults();
        this.updateReplyModeState();
        this.updateAiPanelVisibility();
    }

    createRecipientItem(recipient, index) {
        const item = document.createElement('div');
        item.className = 'recipient-item';
        item.style.cssText = `
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.5rem;
            margin-bottom: 0.5rem;
            background-color: white;
            border: 1px solid #e9ecef;
            border-radius: 0.25rem;
        `;

        const recipientInfo = document.createElement('div');
        recipientInfo.className = 'recipient-info-item';
        recipientInfo.style.cssText = 'display: flex; flex-direction: column;';

        const nameDiv = document.createElement('div');
        nameDiv.className = 'recipient-name';
        nameDiv.style.cssText = 'font-weight: 500; margin-bottom: 0.125rem;';
        nameDiv.textContent = recipient.name || 'Unknown Name';

        const emailDiv = document.createElement('div');
        emailDiv.className = 'recipient-email';
        emailDiv.style.cssText = 'font-size: 0.875rem; color: #6c757d;';
        emailDiv.textContent = recipient.email || 'No email';

        recipientInfo.appendChild(nameDiv);
        recipientInfo.appendChild(emailDiv);

        // Add company badge if available
        const rightSection = document.createElement('div');
        rightSection.style.cssText = 'display: flex; align-items: center;';

        if (recipient.company) {
            const companyBadge = document.createElement('span');
            companyBadge.className = 'recipient-badge';
            companyBadge.style.cssText = `
                background-color: #e3f2fd;
                color: #1976d2;
                font-size: 0.75rem;
                padding: 0.25rem 0.5rem;
                border-radius: 0.25rem;
                margin-right: 0.5rem;
            `;
            companyBadge.textContent = recipient.company;
            rightSection.appendChild(companyBadge);
        }

        // Always add remove button for multiple recipient support
        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'btn btn-sm btn-outline-danger';
        removeBtn.innerHTML = '<i class="fas fa-times"></i>';
        removeBtn.title = 'Remove recipient';
        removeBtn.onclick = () => this.removeRecipient(index);
        rightSection.appendChild(removeBtn);

        item.appendChild(recipientInfo);
        item.appendChild(rightSection);

        return item;
    }

    // Enhanced recipient management methods
    addRecipient(recipientData) {
        // Check if recipient already exists (by email)
        const existingIndex = this.recipients.findIndex(r => r.email === recipientData.email);
        if (existingIndex !== -1) {
            console.log('Recipient already exists:', recipientData.email);
            return false; // Don't add duplicate
        }

        // Add the new recipient
        this.recipients.push(recipientData);
        this.updateRecipientsDisplay();
        console.log('Added recipient:', recipientData);
        return true;
    }

    removeRecipient(index) {
        if (index >= 0 && index < this.recipients.length) {
            const removed = this.recipients.splice(index, 1)[0];
            this.updateRecipientsDisplay();
            console.log('Removed recipient:', removed);
        }
    }

    // New method to add multiple recipients at once
    addMultipleRecipients(recipientDataArray) {
        let addedCount = 0;
        recipientDataArray.forEach(recipientData => {
            if (this.addRecipient(recipientData)) {
                addedCount++;
            }
        });
        console.log(`Added ${addedCount} new recipients out of ${recipientDataArray.length} provided`);
        return addedCount;
    }

    // Clear all recipients
    clearRecipients() {
        this.recipients = [];
        this.updateRecipientsDisplay();
    }

    // Method to populate recipients from various data sources
    populateRecipientsFromData(data) {
         this.clearRecipients();

    if (Array.isArray(data)) {
        // Handle array of recipient objects
        this.addMultipleRecipients(data);

        // Store customer data from first recipient for compatibility
        if (data.length > 0 && data[0].customerId) {
            this.customerData = {
                id: data[0].customerId,
                name: data[0].company || ''
            };
        }
    } else if (data.recipients && Array.isArray(data.recipients)) {
        // Handle object with recipients array
        this.addMultipleRecipients(data.recipients);

        if (data.recipients.length > 0 && data.recipients[0].customerId) {
            this.customerData = {
                id: data.recipients[0].customerId,
                name: data.recipients[0].company || ''
            };
        }
    } else if (data.email) {
        // Handle single recipient object
        this.addRecipient(data);

        if (data.customerId) {
            this.customerData = {
                id: data.customerId,
                name: data.company || ''
            };
        }
    } else if (typeof data === 'string') {
        // Handle comma-separated email string
        const emails = data.split(',').map(email => email.trim()).filter(email => email);
        const recipients = emails.map(email => ({
            id: '',
            name: email.split('@')[0], // Use part before @ as name fallback
            email: email,
            title: '',
            company: '',
            customerId: '' // No customer ID available from string
        }));
        this.addMultipleRecipients(recipients);
    }
}
    showLoading() {
        if (this.loadingSpinner) {
            this.loadingSpinner.classList.remove('d-none');
        }
        if (this.previewBtn) {
            this.previewBtn.disabled = true;
        }
    }

    hideLoading() {
        if (this.loadingSpinner) {
            this.loadingSpinner.classList.add('d-none');
        }
        if (this.previewBtn) {
            this.previewBtn.disabled = false;
        }
    }

    initializeModal() {
        // Initialize select2 if available
        if (this.templateSelect && window.$ && $.fn.select2) {
            try {
                $(this.templateSelect).select2({
                    theme: 'bootstrap-5',
                    dropdownParent: $('#emailModal'),
                    placeholder: 'Select a template',
                    width: '100%'
                });
            } catch (e) {
                console.warn('Select2 initialization failed:', e);
            }
        }

        this.ensurePlaceholderStyles();

        // Initialize rich text editor if available
        if (this.customBodyEditor && window.tinymce) {
            try {
                tinymce.init({
                    selector: '#custom_body',
                    height: 300,
                    menubar: false,
                    plugins: 'link lists image code table',
                    toolbar: 'undo redo | formatselect | bold italic | alignleft aligncenter alignright | bullist numlist | link image | table',
                    content_style: 'body { font-family: Arial, sans-serif; font-size: 14px; }'
                });
            } catch (e) {
                console.warn('TinyMCE initialization failed:', e);
            }
        }

        // Check the custom email checkbox by default
        if (this.customEmailCheckbox) {
            this.customEmailCheckbox.checked = true;
        }
    }

    // Add this new method to ensure styles are loaded:
    ensurePlaceholderStyles() {
        if (!document.querySelector('#placeholder-styles')) {
            const style = document.createElement('style');
            style.id = 'placeholder-styles';
            style.textContent = `
                .clickable-placeholder {
                    cursor: pointer !important;
                    padding: 3px 6px !important;
                    border-radius: 4px !important;
                    background-color: #f8f9fa !important;
                    border: 1px solid #dee2e6 !important;
                    transition: all 0.2s ease !important;
                    display: inline-block !important;
                    margin: 2px !important;
                    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace !important;
                    font-size: 0.875rem !important;
                    user-select: none !important;
                }

                .clickable-placeholder:hover {
                    background-color: #007bff !important;
                    color: white !important;
                    border-color: #007bff !important;
                    transform: translateY(-1px) !important;
                    box-shadow: 0 2px 4px rgba(0,123,255,0.3) !important;
                }

                .clickable-placeholder:active {
                    transform: translateY(0) !important;
                    box-shadow: 0 1px 2px rgba(0,123,255,0.3) !important;
                    background-color: #28a745 !important;
                    border-color: #28a745 !important;
                }

                @keyframes placeholderInserted {
                    0% {
                        background-color: #28a745;
                        color: white;
                        transform: scale(1.1);
                    }
                    100% {
                        background-color: #f8f9fa;
                        color: inherit;
                        transform: scale(1);
                    }
                }

                .placeholder-inserted {
                    animation: placeholderInserted 0.3s ease;
                }
            `;
            document.head.appendChild(style);
        }
    }

    bindEvents() {
        if (!this.modal) return;

        console.log("EmailModal bindEvents called");

        // Save reference to this for event handlers
        const self = this;

        // Custom email checkbox change
        if (this.customEmailCheckbox) {
            this.customEmailCheckbox.addEventListener('change', function() {
                self.isCustomEmail = this.checked;
                self.toggleEmailMode();
            });
        }

        // Template selection change - Select2 compatible
        if (this.templateSelect) {
            if (window.$ && $.fn.select2) {
                $(this.templateSelect).on('change', function() {
                    console.log("Template selected:", this.value);
                    self.updateButtonVisibility();
                });
            } else {
                // Fallback for regular select
                this.templateSelect.addEventListener('change', function() {
                    console.log("Template selected:", this.value);
                    self.updateButtonVisibility();
                });
            }
        }

        // Preview button click
        if (this.previewBtn) {
            this.previewBtn.addEventListener('click', function() {
                console.log("Preview button clicked");
                self.previewEmail();
            });
        }

        // Send button click
        if (this.sendBtn) {
            this.sendBtn.addEventListener('click', function() {
                console.log("Send button clicked");
                self.sendEmail();
            });
        }
        if (this.sendSystemBtn) {
            this.sendSystemBtn.addEventListener('click', function() {
                console.log("Send via system button clicked");
                self.sendAllViaSystem();
            });
        }

        // Outlook button click
        if (this.outlookBtn) {
            this.outlookBtn.addEventListener('click', function() {
                console.log("Outlook button clicked");
                self.openInOutlook();
            });
        }

        // Add recipient button click
        if (this.addRecipientBtn) {
            this.addRecipientBtn.addEventListener('click', function() {
                self.showAddRecipientDialog();
            });
        }

        if (this.replySelect) {
            this.replySelect.addEventListener('change', () => {
                this.updateReplyModeState();
            });
        }
        if (this.aiGenerateBtn) {
            this.aiGenerateBtn.addEventListener('click', () => {
                this.generateAiDraftFromModal();
            });
        }

        this.createClickablePlaceholders();

        // Modal show event - UPDATED
        this.modal.addEventListener('show.bs.modal', (event) => {
            console.log('Modal show event triggered');

            // Try to get data from button or modal attributes
            this.loadDataFromRelatedTarget(event.relatedTarget);

            // Always load templates on modal open
            this.loadTemplates();

            // Ensure correct mode is shown based on checkbox
            this.isCustomEmail = this.customEmailCheckbox ? this.customEmailCheckbox.checked : true;
            this.toggleEmailMode();
            this.updateButtonVisibility();
            this.applyContactSuggestionsDefaults();
            this.updateReplyModeState();
            this.updateAiPanelVisibility();
        });

        // Modal shown event - NEW - this fires after the modal is fully displayed
        this.modal.addEventListener('shown.bs.modal', () => {
            console.log('Modal shown event triggered - creating placeholders');

            // IMPORTANT: Re-create clickable placeholders after modal content is fully loaded
            setTimeout(() => {
                this.createClickablePlaceholders();
            }, 100);
        });
    }

    showAddRecipientDialog() {
        // Create the contact search modal
        const searchModalHtml = `
            <div class="modal fade" id="addRecipientModal" tabindex="-1" data-bs-backdrop="static">
                <div class="modal-dialog modal-lg">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">
                                <i class="fas fa-user-plus me-2"></i>
                                Add Email Recipient
                            </h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <!-- Search Section -->
                            <div class="search-section mb-4">
                                <label for="contactSearch" class="form-label fw-bold">
                                    <i class="fas fa-search me-2"></i>Search Existing Contacts
                                </label>
                                <div class="input-group">
                                    <input type="text"
                                           class="form-control"
                                           id="contactSearch"
                                           placeholder="Type contact name, email, or company..."
                                           autocomplete="off">
                                    <button class="btn btn-outline-secondary" type="button" id="clearSearch">
                                        <i class="fas fa-times"></i>
                                    </button>
                                </div>

                                <!-- Search Results -->
                                <div class="search-results mt-3" id="searchResults" style="max-height: 300px; overflow-y: auto;">
                                    <div class="no-results text-center text-muted py-3 d-none">
                                        <i class="fas fa-search-minus fa-2x mb-2"></i>
                                        <p>No contacts found matching your search.</p>
                                    </div>
                                    <div class="search-help text-center text-muted py-3">
                                        <i class="fas fa-keyboard fa-2x mb-2"></i>
                                        <p>Start typing to search for contacts...</p>
                                    </div>
                                </div>
                            </div>

                            <div class="divider my-4">
                                <hr>
                                <div class="text-center">
                                    <span class="bg-white px-3 text-muted">OR</span>
                                </div>
                            </div>

                            <!-- Manual Entry Section -->
                            <div class="manual-entry-section">
                                <label class="form-label fw-bold">
                                    <i class="fas fa-edit me-2"></i>Add New Contact Manually
                                </label>

                                <div class="row">
                                    <div class="col-md-6">
                                        <div class="mb-3">
                                            <label for="manualName" class="form-label">Name <span class="text-danger">*</span></label>
                                            <input type="text" class="form-control" id="manualName" placeholder="Contact name">
                                        </div>
                                    </div>
                                    <div class="col-md-6">
                                        <div class="mb-3">
                                            <label for="manualEmail" class="form-label">Email <span class="text-danger">*</span></label>
                                            <input type="email" class="form-control" id="manualEmail" placeholder="contact@company.com">
                                        </div>
                                    </div>
                                </div>

                                <div class="row">
                                    <div class="col-md-6">
                                        <div class="mb-3">
                                            <label for="manualTitle" class="form-label">Job Title</label>
                                            <input type="text" class="form-control" id="manualTitle" placeholder="Job title (optional)">
                                        </div>
                                    </div>
                                    <div class="col-md-6">
                                        <div class="mb-3">
                                            <label for="manualCompany" class="form-label">Company</label>
                                            <input type="text" class="form-control" id="manualCompany" placeholder="Company name (optional)">
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                            <button type="button" class="btn btn-primary" id="addManualRecipient" disabled>
                                <i class="fas fa-plus me-1"></i>Add Recipient
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // Remove existing modal if present
        const existingModal = document.getElementById('addRecipientModal');
        if (existingModal) {
            existingModal.remove();
        }

        // Add new modal to DOM
        document.body.insertAdjacentHTML('beforeend', searchModalHtml);
        const modal = document.getElementById('addRecipientModal');

        // Initialize the modal functionality
        this.initializeAddRecipientModal(modal);

        // Show the modal
        if (window.bootstrap && bootstrap.Modal) {
            const modalInstance = new bootstrap.Modal(modal);
            modalInstance.show();
        }
    }

    // Initialize the add recipient modal functionality
    initializeAddRecipientModal(modal) {
        const contactSearch = modal.querySelector('#contactSearch');
        const searchResults = modal.querySelector('#searchResults');
        const clearSearchBtn = modal.querySelector('#clearSearch');
        const manualName = modal.querySelector('#manualName');
        const manualEmail = modal.querySelector('#manualEmail');
        const manualTitle = modal.querySelector('#manualTitle');
        const manualCompany = modal.querySelector('#manualCompany');
        const addManualBtn = modal.querySelector('#addManualRecipient');

        let searchTimeout = null;
        let currentSearchTerm = '';

        // Search functionality
        const performSearch = async (query) => {
            if (!query || query.length < 2) {
                this.showSearchHelp(searchResults);
                return;
            }

            try {
                this.showSearchLoading(searchResults);

                const response = await fetch(`/customers/search_contact?query=${encodeURIComponent(query)}`, {
                    headers: {
                        'X-API-Key': 'dingleberry'
                    }
                });

                if (!response.ok) {
                    throw new Error(`Search failed: ${response.status}`);
                }

                const contacts = await response.json();
                this.displaySearchResults(searchResults, contacts, query);

            } catch (error) {
                console.error('Contact search error:', error);
                this.showSearchError(searchResults, error.message);
            }
        };

        // Search input handler with debouncing
        contactSearch.addEventListener('input', (e) => {
            const query = e.target.value.trim();
            currentSearchTerm = query;

            // Clear previous timeout
            if (searchTimeout) {
                clearTimeout(searchTimeout);
            }

            // Debounce search
            searchTimeout = setTimeout(() => {
                if (currentSearchTerm === query) { // Only search if query hasn't changed
                    performSearch(query);
                }
            }, 300);
        });

        // Clear search
        clearSearchBtn.addEventListener('click', () => {
            contactSearch.value = '';
            currentSearchTerm = '';
            this.showSearchHelp(searchResults);
            contactSearch.focus();
        });

        // Manual entry validation
        const validateManualEntry = () => {
            const name = manualName.value.trim();
            const email = manualEmail.value.trim();
            const isValid = name && email && this.isValidEmail(email);

            addManualBtn.disabled = !isValid;

            if (isValid) {
                addManualBtn.innerHTML = '<i class="fas fa-plus me-1"></i>Add Recipient';
                addManualBtn.className = 'btn btn-primary';
            } else {
                addManualBtn.innerHTML = '<i class="fas fa-plus me-1"></i>Add Recipient';
                addManualBtn.className = 'btn btn-primary';
            }
        };

        // Manual entry field listeners
        [manualName, manualEmail].forEach(field => {
            field.addEventListener('input', validateManualEntry);
            field.addEventListener('blur', validateManualEntry);
        });

        // Add manual recipient
        addManualBtn.addEventListener('click', () => {
            const recipientData = {
                id: '', // No ID for manual entries
                name: manualName.value.trim(),
                email: manualEmail.value.trim(),
                title: manualTitle.value.trim(),
                company: manualCompany.value.trim()
            };

            if (this.addRecipient(recipientData)) {
                this.showAddRecipientSuccess(`Added ${recipientData.name}`);
                this.closeAddRecipientModal(modal);
            } else {
                this.showAddRecipientError('This contact is already in the recipient list');
            }
        });

        // Initialize with search help
        this.showSearchHelp(searchResults);
    }

    // Display search results
    displaySearchResults(container, contacts, query) {
        if (contacts.length === 0) {
            container.innerHTML = `
                <div class="no-results text-center text-muted py-3">
                    <i class="fas fa-search-minus fa-2x mb-2"></i>
                    <p>No contacts found for "${query}"</p>
                    <small>Try searching by name, email, or company</small>
                </div>
            `;
            return;
        }

        let html = `<div class="contacts-list">`;

        contacts.forEach(contact => {
            const isAlreadyAdded = this.recipients.some(r => r.email === contact.email);

            html += `
                <div class="contact-result-item ${isAlreadyAdded ? 'disabled' : ''}"
                     data-contact='${JSON.stringify(contact)}'>
                    <div class="d-flex align-items-center justify-content-between p-3 border rounded mb-2 ${isAlreadyAdded ? 'bg-light' : 'bg-white'}"
                         style="cursor: ${isAlreadyAdded ? 'default' : 'pointer'}; border-color: ${isAlreadyAdded ? '#e9ecef' : '#dee2e6'} !important;">

                        <div class="contact-info flex-grow-1">
                            <div class="contact-name fw-bold ${isAlreadyAdded ? 'text-muted' : ''}">
                                ${contact.full_name || contact.name}
                            </div>
                            <div class="contact-email small ${isAlreadyAdded ? 'text-muted' : 'text-secondary'}">
                                <i class="fas fa-envelope me-1"></i>${contact.email}
                            </div>
                            ${contact.job_title ? `
                                <div class="contact-title small ${isAlreadyAdded ? 'text-muted' : 'text-secondary'}">
                                    <i class="fas fa-briefcase me-1"></i>${contact.job_title}
                                </div>
                            ` : ''}
                            ${contact.customer_name ? `
                                <div class="contact-company small ${isAlreadyAdded ? 'text-muted' : 'text-primary'}">
                                    <i class="fas fa-building me-1"></i>${contact.customer_name}
                                </div>
                            ` : ''}
                        </div>

                        <div class="contact-actions">
                            ${isAlreadyAdded ? `
                                <span class="badge bg-secondary">
                                    <i class="fas fa-check me-1"></i>Added
                                </span>
                            ` : `
                                <button type="button" class="btn btn-primary btn-sm add-contact-btn">
                                    <i class="fas fa-plus"></i>
                                </button>
                            `}
                        </div>
                    </div>
                </div>
            `;
        });

        html += `</div>`;
        container.innerHTML = html;

        // Bind click events for non-disabled items
        container.querySelectorAll('.contact-result-item:not(.disabled)').forEach(item => {
            item.addEventListener('click', (e) => {
                if (e.target.closest('.add-contact-btn') || !item.classList.contains('disabled')) {
                    const contact = JSON.parse(item.getAttribute('data-contact'));
                    this.addContactFromSearch(contact);
                }
            });
        });
    }

    // Add contact from search results
    addContactFromSearch(contact) {
        const recipientData = {
            id: contact.id || '',
            name: contact.full_name || contact.name,
            email: contact.email,
            title: contact.job_title || '',
            company: contact.customer_name || ''
        };

        if (this.addRecipient(recipientData)) {
            this.showAddRecipientSuccess(`Added ${recipientData.name}`);

            // Find the modal and close it
            const modal = document.getElementById('addRecipientModal');
            if (modal) {
                this.closeAddRecipientModal(modal);
            }
        } else {
            this.showAddRecipientError('This contact is already in the recipient list');
        }
    }

    // Helper methods for the add recipient modal
    showSearchHelp(container) {
        container.innerHTML = `
            <div class="search-help text-center text-muted py-3">
                <i class="fas fa-keyboard fa-2x mb-2"></i>
                <p>Start typing to search for contacts...</p>
                <small>Search by name, email, job title, or company</small>
            </div>
        `;
    }

    showSearchLoading(container) {
        container.innerHTML = `
            <div class="search-loading text-center py-3">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Searching...</span>
                </div>
                <p class="mt-2 text-muted">Searching contacts...</p>
            </div>
        `;
    }

    showSearchError(container, error) {
        container.innerHTML = `
            <div class="search-error text-center text-danger py-3">
                <i class="fas fa-exclamation-triangle fa-2x mb-2"></i>
                <p>Search failed: ${error}</p>
                <small>Please try again or add the contact manually</small>
            </div>
        `;
    }

    showAddRecipientSuccess(message) {
        this.showToast(message, 'success');
    }

    showAddRecipientError(message) {
        this.showToast(message, 'error');
    }

    closeAddRecipientModal(modal) {
        if (window.bootstrap && bootstrap.Modal) {
            const modalInstance = bootstrap.Modal.getInstance(modal);
            if (modalInstance) {
                modalInstance.hide();
            }
        }

        // Clean up after a short delay
        setTimeout(() => {
            if (modal && modal.parentElement) {
                modal.remove();
            }
        }, 500);
    }

    // Email validation helper
    isValidEmail(email) {
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        return emailRegex.test(email);
    }

    // FIXED: updateButtonVisibility method with null checks
    updateButtonVisibility() {
        // Safety check - if required elements don't exist, return early
        if (!this.previewSection || !this.previewBtn || !this.outlookBtn) {
            console.warn('Required button elements not found, skipping visibility update');
            return;
        }

        // Only show preview/send buttons if we have recipients
        const hasRecipients = this.recipients.length > 0;
        const hasPreview = !this.previewSection.classList.contains('d-none');

        // For template mode, also check if template is selected
        let canPreview = hasRecipients;
        if (!this.isCustomEmail && this.templateSelect) {
            canPreview = hasRecipients && this.templateSelect.value;
        }

        // Show/hide preview button
        if (canPreview) {
            this.previewBtn.classList.remove('d-none');
        } else {
            this.previewBtn.classList.add('d-none');
        }

        // Always use Outlook button after preview (since direct send is disabled)
        if (hasPreview && hasRecipients) {
            if (this.sendBtn) this.sendBtn.classList.add('d-none');
            this.outlookBtn.classList.remove('d-none');
            if (this.sendSystemBtn) {
                if (this.recipients.length > 1) {
                    this.sendSystemBtn.classList.remove('d-none');
                } else {
                    this.sendSystemBtn.classList.add('d-none');
                }
            }
        } else {
            if (this.sendBtn) this.sendBtn.classList.add('d-none');
            this.outlookBtn.classList.add('d-none');
            if (this.sendSystemBtn) this.sendSystemBtn.classList.add('d-none');
        }
    }

    loadDataFromRelatedTarget(button) {
        if (!button) {
            console.log('No related target, using modal attributes');
            this.loadDataFromModal();
            return;
        }

        console.log('Getting data from button:', button);

        // Check for multiple recipients data
        const recipientsData = button.getAttribute('data-recipients');
        if (recipientsData) {
            try {
                const recipients = JSON.parse(recipientsData);
                this.populateRecipientsFromData(recipients);
                console.log('Multiple recipients loaded:', recipients);
                return;
            } catch (e) {
                console.warn('Error parsing recipients data:', e);
            }
        }

        // Fallback to single recipient data
        const contactId = button.getAttribute('data-contact-id') || '';
        const contactName = button.getAttribute('data-contact-name') || '';
        const contactEmail = button.getAttribute('data-contact-email') || '';
        const contactTitle = button.getAttribute('data-contact-title') || '';
        const customerId = button.getAttribute('data-customer-id') || '';
        const customerName = button.getAttribute('data-customer-name') || '';

        if (contactId || contactEmail) {
            const recipientData = {
                id: contactId,
                name: contactName,
                email: contactEmail,
                title: contactTitle,
                company: customerName
            };

            this.populateRecipientsFromData(recipientData);

            // Store for compatibility
            this.contactData = {
                id: contactId,
                name: contactName,
                email: contactEmail,
                title: contactTitle
            };

            this.customerData = {
                id: customerId,
                name: customerName
            };

            this.updateHiddenFields();
            console.log('Single recipient loaded:', recipientData);
        } else {
            console.log('No contact data found on button');
            this.loadDataFromModal();
        }
    }

    // UPDATED: toggleEmailMode method with null checks
    toggleEmailMode() {
        console.log("Toggling email mode. isCustomEmail:", this.isCustomEmail);

        // Safety checks
        if (!this.templateSection || !this.customSection || !this.previewSection) {
            console.warn('Required sections not found, skipping toggle');
            return;
        }

        if (this.isCustomEmail) {
            // Switch to custom email
            this.templateSection.classList.add('d-none');
            this.customSection.classList.remove('d-none');

            // Re-create clickable placeholders when switching to custom mode
            setTimeout(() => {
                this.createClickablePlaceholders();
            }, 100);
            this.updateAiPanelVisibility();
        } else {
            // Switch to template mode
            this.templateSection.classList.remove('d-none');
            this.customSection.classList.add('d-none');
            this.updateAiPanelVisibility();
        }

        // Reset preview section and update buttons
        this.previewSection.classList.add('d-none');
        this.updateButtonVisibility();
    }

    createHiddenFields() {
        let hiddenDiv = this.modal.querySelector('.hidden-email-data');
        if (!hiddenDiv) {
            hiddenDiv = document.createElement('div');
            hiddenDiv.className = 'hidden-email-data d-none';
            hiddenDiv.innerHTML = `
                <input type="hidden" id="email-contact-id" value="">
                <input type="hidden" id="email-contact-name" value="">
                <input type="hidden" id="email-contact-email" value="">
                <input type="hidden" id="email-contact-title" value="">
                <input type="hidden" id="email-customer-id" value="">
                <input type="hidden" id="email-customer-name" value="">
                <input type="hidden" id="current_user_name" value="${window.current_user_name || ''}">
                <input type="hidden" id="current_user_title" value="${window.current_user_title || ''}">
            `;
            this.modal.querySelector('.modal-body').appendChild(hiddenDiv);
        }
    }

    loadDataFromModal() {
        // Check for multiple recipients in modal attributes
        const recipientsData = this.modal.getAttribute('data-recipients');
        if (recipientsData) {
            try {
                const recipients = JSON.parse(recipientsData);
                this.populateRecipientsFromData(recipients);
                console.log('Multiple recipients loaded from modal:', recipients);
                return true;
            } catch (e) {
                console.warn('Error parsing modal recipients data:', e);
            }
        }

        // Fallback to single recipient from modal attributes
        const contactId = this.modal.getAttribute('data-contact-id') || '';
        const contactName = this.modal.getAttribute('data-contact-name') || '';
        const contactEmail = this.modal.getAttribute('data-contact-email') || '';
        const contactTitle = this.modal.getAttribute('data-contact-title') || '';
        const customerId = this.modal.getAttribute('data-customer-id') || '';
        const customerName = this.modal.getAttribute('data-customer-name') || '';

        if (contactId || contactEmail) {
            const recipientData = {
                id: contactId,
                name: contactName,
                email: contactEmail,
                title: contactTitle,
                company: customerName
            };

            this.populateRecipientsFromData(recipientData);

            // Store for compatibility
            this.contactData = {
                id: contactId,
                name: contactName,
                email: contactEmail,
                title: contactTitle
            };

            this.customerData = {
                id: customerId,
                name: customerName
            };

            this.updateHiddenFields();
            console.log('Single recipient loaded from modal:', recipientData);
            return true;
        }
        return false;
    }

    updateHiddenFields() {
        // Update hidden fields with primary contact data (first recipient for compatibility)
        if (!this.modal || this.recipients.length === 0) return;

        const primaryRecipient = this.recipients[0];

        const contactIdField = this.modal.querySelector('#email-contact-id');
        const contactNameField = this.modal.querySelector('#email-contact-name');
        const contactEmailField = this.modal.querySelector('#email-contact-email');
        const contactTitleField = this.modal.querySelector('#email-contact-title');

        if (contactIdField) contactIdField.value = primaryRecipient.id || '';
        if (contactNameField) contactNameField.value = primaryRecipient.name || '';
        if (contactEmailField) contactEmailField.value = primaryRecipient.email || '';
        if (contactTitleField) contactTitleField.value = primaryRecipient.title || '';

        // Customer data from first recipient's company info
        const customerIdField = this.modal.querySelector('#email-customer-id');
        const customerNameField = this.modal.querySelector('#email-customer-name');

        if (customerIdField && this.customerData) customerIdField.value = this.customerData.id || '';
        if (customerNameField && this.customerData) customerNameField.value = this.customerData.name || primaryRecipient.company || '';
    }

    async loadTemplates() {
        console.log('Loading templates...');

        // Show loading in the select
        if (this.templateSelect) {
            this.templateSelect.innerHTML = '<option value="">Loading templates...</option>';
            this.templateSelect.disabled = true;
        } else {
            console.error('Template select element not found');
            return;
        }

        try {
            const response = await fetch('/api/email-templates', {
                headers: {
                    'X-API-Key': 'dingleberry',
                    'Cache-Control': 'no-cache'
                }
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const result = await response.json();
            console.log('Templates API response:', result);

            // Extract templates from various possible response formats
            const templates = Array.isArray(result) ? result :
                            (result.data && Array.isArray(result.data)) ? result.data : [];

            // Reset the select with the new templates
            this.templateSelect.innerHTML = '<option value="">Choose a template...</option>';

            if (templates.length === 0) {
                console.log('No templates found');
                this.templateSelect.innerHTML += '<option value="" disabled>No templates available</option>';
            } else {
                templates.forEach(template => {
                    const option = document.createElement('option');
                    option.value = template.id || '';
                    option.textContent = template.name || 'Unnamed Template';
                    this.templateSelect.appendChild(option);
                });
                console.log(`${templates.length} templates added to select`);
            }

            this.templatesLoaded = true;
        } catch (error) {
            console.error('Error loading templates:', error);
            this.templateSelect.innerHTML = '<option value="">Error loading templates</option>';
        } finally {
            this.templateSelect.disabled = false;
        }
    }

    // Modify the previewEmail method to store original content
    async previewEmail() {
        if (this.recipients.length === 0) {
            alert('Please add at least one recipient before previewing.');
            return;
        }

        this.showLoading();

        try {
            console.log('Previewing email');

            // Use first recipient as primary for preview (maintains compatibility)
            const primaryRecipient = this.recipients[0];

            // Prepare request data
            const requestData = {
                contact_id: primaryRecipient.id,
                customer_id: this.customerData?.id || '',
                recipients: this.recipients
            };

            if (this.isCustomEmail) {
                // Get custom email data
                const customSubject = this.customSubjectInput ? this.customSubjectInput.value.trim() : '';
                let customBody = '';

                // Get content from TinyMCE if it's initialized
                if (window.tinymce && tinymce.get('custom_body')) {
                    customBody = tinymce.get('custom_body').getContent();
                } else if (this.customBodyEditor) {
                    customBody = this.customBodyEditor.value.trim();
                }

                if (!customSubject || !customBody) {
                    alert('Please provide both subject and body for the custom email');
                    this.hideLoading();
                    return;
                }

                // STORE THE ORIGINAL CONTENT BEFORE PERSONALIZATION
                this.originalSubject = customSubject;
                this.originalBody = customBody;
                this.isContentPersonalized = false;

                // Add custom email data to request
                requestData.is_custom = true;
                requestData.custom_subject = customSubject;
                requestData.custom_body = customBody;

                // Send any placeholder replacements needed
                requestData.placeholders = {
                    contact_name: primaryRecipient.name || '',
                    contact_first_name: primaryRecipient.name ? primaryRecipient.name.split(' ')[0] : '',
                    contact_title: primaryRecipient.title || '',
                    company_name: primaryRecipient.company || this.customerData?.name || '',
                    today_date: new Date().toLocaleDateString(),
                    sender_name: document.getElementById('current_user_name')?.value || 'User',
                    sender_title: document.getElementById('current_user_title')?.value || 'Sales Representative'
                };
            } else {
                // Handle template email preview
                const templateId = this.templateSelect ? this.templateSelect.value : '';
                if (!templateId) {
                    alert('Please select a template first');
                    this.hideLoading();
                    return;
                }
                requestData.template_id = templateId;

                // For templates, we'll store the original after we get the response
                this.isContentPersonalized = false;
            }

            console.log('Preview request data:', requestData);

            const response = await fetch('/api/preview-email', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': 'dingleberry'
                },
                body: JSON.stringify(requestData)
            });

            if (!response.ok) {
                throw new Error(`Server error: ${response.status}`);
            }

            const result = await response.json();
            console.log('Preview result:', result);

            if (result.success || (result.data && (result.data.subject || result.data.body))) {
                const previewData = result.data || result;

                // For template emails, store the original template content if we can get it
                if (!this.isCustomEmail && !this.originalSubject && !this.originalBody) {
                    // Try to get the unpersonalized template content
                    // This might require a separate API call to get the raw template
                    // For now, we'll use the personalized content and try to reverse-engineer it
                    this.originalSubject = previewData.subject || '';
                    this.originalBody = previewData.body_without_signature || previewData.body || '';
                }

                this.updatePreview(previewData);
                this.updateButtonVisibility();
                this.isContentPersonalized = true;
            } else {
                this.showPreviewError(result.error || 'Error generating preview');
            }
        } catch (error) {
            console.error('Preview error:', error);
            this.showPreviewError(`Error: ${error.message}`);
        } finally {
            this.hideLoading();
        }
    }

    // Enhanced openInOutlook method with individual recipient processing
    async openInOutlook() {
        try {
            // Get the preview data
            const emailSubject = this.modal.querySelector('.email-subject');
            const emailBody = this.modal.querySelector('.email-body');

            if (!emailSubject || !emailBody) {
                alert('Please preview the email first');
                return;
            }

            const baseSubject = emailSubject.textContent;
            const baseBodyHtml = emailBody.innerHTML;

            if (this.recipients.length === 1) {
                // Single recipient - use simple approach
                await this.openSingleRecipientOutlook(this.recipients[0], baseSubject, baseBodyHtml);
            } else {
                // Multiple recipients - show individual processing modal
                await this.showMultipleRecipientsModal(this.recipients, baseSubject, baseBodyHtml);
            }

        } catch (error) {
            console.error('Error opening Outlook:', error);
            this.showToast('Error opening Outlook', 'error');
        }
    }

    // Show modal for processing multiple recipients individually
    async showMultipleRecipientsModal(recipients, baseSubject, baseBodyHtml) {
        // Create the multiple recipients modal
        const modalHtml = `
            <div class="modal fade" id="multipleRecipientsModal" tabindex="-1" data-bs-backdrop="static">
                <div class="modal-dialog modal-xl">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">
                                <i class="fas fa-envelope-open-text me-2"></i>
                                Send to Multiple Recipients
                            </h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <div class="row">
                                <!-- Progress Panel -->
                                <div class="col-md-4">
                                    <div class="card h-100">
                                        <div class="card-header">
                                            <h6 class="mb-0">
                                                <i class="fas fa-tasks me-2"></i>Progress
                                            </h6>
                                        </div>
                                        <div class="card-body">
                                            <div class="progress-summary mb-3">
                                                <div class="d-flex justify-content-between align-items-center mb-2">
                                                    <span>Total Recipients:</span>
                                                    <span class="badge bg-primary">${recipients.length}</span>
                                                </div>
                                                <div class="d-flex justify-content-between align-items-center mb-2">
                                                    <span>Completed:</span>
                                                    <span class="badge bg-success completed-count">0</span>
                                                </div>
                                                <div class="d-flex justify-content-between align-items-center mb-2">
                                                    <span>Remaining:</span>
                                                    <span class="badge bg-secondary remaining-count">${recipients.length}</span>
                                                </div>
                                            </div>
                                            <div class="progress mb-3">
                                                <div class="progress-bar" role="progressbar" style="width: 0%"></div>
                                            </div>
                                            <div class="recipient-checklist">
                                                <!-- Recipient checklist will be populated here -->
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                <!-- Current Recipient Panel -->
                                <div class="col-md-8">
                                    <div class="card h-100">
                                        <div class="card-header d-flex justify-content-between align-items-center">
                                            <h6 class="mb-0">
                                                <i class="fas fa-user me-2"></i>
                                                <span class="current-recipient-title">Current Recipient</span>
                                            </h6>
                                            <span class="recipient-counter badge bg-info">1 of ${recipients.length}</span>
                                        </div>
                                        <div class="card-body current-recipient-content">
                                            <!-- Current recipient content will be populated here -->
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                            <button type="button" class="btn btn-success finish-btn d-none" data-bs-dismiss="modal">
                                <i class="fas fa-check me-1"></i>All Done!
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // Remove existing modal if present
        const existingModal = document.getElementById('multipleRecipientsModal');
        if (existingModal) {
            existingModal.remove();
        }

        // Add new modal to DOM
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        const modal = document.getElementById('multipleRecipientsModal');

        // Initialize the modal with recipient data
        await this.initializeMultipleRecipientsModal(modal, recipients, baseSubject, baseBodyHtml);

        // Show the modal
        if (window.bootstrap && bootstrap.Modal) {
            const modalInstance = new bootstrap.Modal(modal);
            modalInstance.show();

            // Close the original email modal when this one opens
            this.closeModal();
        }
    }

    // Initialize the multiple recipients modal with data and functionality
    async initializeMultipleRecipientsModal(modal, recipients, baseSubject, baseBodyHtml) {
        let currentIndex = 0;
        let completedCount = 0;
        const personalizedContents = new Map(); // Cache personalized content

        // Initialize progress checklist
        const checklist = modal.querySelector('.recipient-checklist');
        recipients.forEach((recipient, index) => {
            const checkItem = document.createElement('div');
            checkItem.className = 'recipient-check-item d-flex align-items-center mb-2';
            checkItem.innerHTML = `
                <i class="fas fa-circle text-muted me-2 status-icon" data-index="${index}"></i>
                <small class="text-truncate" title="${recipient.name || recipient.email} (${recipient.email})">
                    ${recipient.name || recipient.email}
                </small>
            `;
            checklist.appendChild(checkItem);
        });


        // Function to update progress
        const updateProgress = () => {
            const progressBar = modal.querySelector('.progress-bar');
            const completedSpan = modal.querySelector('.completed-count');
            const remainingSpan = modal.querySelector('.remaining-count');
            const counterBadge = modal.querySelector('.recipient-counter');
            const finishBtn = modal.querySelector('.finish-btn');

            const percentage = (completedCount / recipients.length) * 100;
            progressBar.style.width = `${percentage}%`;
            progressBar.textContent = `${Math.round(percentage)}%`;

            completedSpan.textContent = completedCount;
            remainingSpan.textContent = recipients.length - completedCount;
            counterBadge.textContent = `${currentIndex + 1} of ${recipients.length}`;

            // Show finish button when all done
            if (completedCount === recipients.length) {
                finishBtn.classList.remove('d-none');
            }
        };

        // Function to mark recipient as completed
        const markCompleted = (index) => {
            const statusIcon = modal.querySelector(`[data-index="${index}"]`);
            if (statusIcon) {
                statusIcon.className = 'fas fa-check-circle text-success me-2 status-icon';
            }
            completedCount++;
            updateProgress();
        };

        // Function to show current recipient
        const showCurrentRecipient = async (index) => {
            if (index >= recipients.length) {
                // All done
                const content = modal.querySelector('.current-recipient-content');
                content.innerHTML = `
                    <div class="text-center py-5">
                        <i class="fas fa-check-circle text-success" style="font-size: 3rem;"></i>
                        <h4 class="mt-3 text-success">All Emails Processed!</h4>
                        <p class="text-muted">You have successfully processed all ${recipients.length} recipients.</p>
                    </div>
                `;
                return;
            }

            const recipient = recipients[index];
            const content = modal.querySelector('.current-recipient-content');

            // Show loading
            content.innerHTML = `
                <div class="text-center py-4">
                    <div class="spinner-border text-primary" role="status">
                        <span class="visually-hidden">Loading...</span>
                    </div>
                    <p class="mt-2 text-muted">Personalizing content for ${recipient.name || recipient.email}...</p>
                </div>
            `;

            try {
                // Get or create personalized content
                let personalizedContent;
                if (personalizedContents.has(index)) {
                    personalizedContent = personalizedContents.get(index);
                } else {
                    personalizedContent = await this.personalizeContentForRecipient(recipient, baseSubject, baseBodyHtml);
                    personalizedContents.set(index, personalizedContent);
                }

                // Show recipient details and content
                content.innerHTML = `
                    <div class="recipient-details mb-4">
                        <div class="row">
                            <div class="col-md-6">
                                <h6><i class="fas fa-user me-2"></i>Recipient Details</h6>
                                <div class="mb-2"><strong>Name:</strong> ${recipient.name || 'N/A'}</div>
                                <div class="mb-2"><strong>Email:</strong> ${recipient.email}</div>
                                ${recipient.title ? `<div class="mb-2"><strong>Title:</strong> ${recipient.title}</div>` : ''}
                                ${recipient.company ? `<div class="mb-2"><strong>Company:</strong> ${recipient.company}</div>` : ''}
                            </div>
                            <div class="col-md-6">
                                <h6><i class="fas fa-envelope me-2"></i>Email Details</h6>
                                <div class="mb-2"><strong>Subject:</strong> ${personalizedContent.subject}</div>
                                <div class="mb-2"><strong>Status:</strong>
                                    <span class="badge bg-warning">Ready to Send</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="email-preview mb-4">
                        <h6><i class="fas fa-eye me-2"></i>Email Preview</h6>
                        <div class="border rounded p-3 bg-light" style="max-height: 300px; overflow-y: auto;">
                            <div class="mb-2"><strong>To:</strong> ${recipient.email}</div>
                            <div class="mb-2"><strong>Subject:</strong> ${personalizedContent.subject}</div>
                            <hr>
                            <div class="email-body-preview">${personalizedContent.bodyHtml}</div>
                        </div>
                    </div>

                    <div class="action-buttons text-center">
                        <button type="button" class="btn btn-lg btn-primary copy-and-open-btn me-3">
                            <i class="fas fa-copy me-2"></i>
                            <i class="fas fa-external-link-alt me-2"></i>
                            Copy Content & Open Outlook
                        </button>
                        <button type="button" class="btn btn-outline-secondary skip-btn">
                            <i class="fas fa-forward me-2"></i>Skip This Recipient
                        </button>
                    </div>

                    <div class="mt-3">
                        <small class="text-muted">
                            <i class="fas fa-info-circle me-1"></i>
                            Clicking "Copy Content & Open Outlook" will copy the personalized email content to your clipboard
                            and open Outlook with the recipient and subject pre-filled. Simply paste the content into the email body.
                        </small>
                    </div>
                `;

                // Bind button events
                const copyOpenBtn = content.querySelector('.copy-and-open-btn');
                const skipBtn = content.querySelector('.skip-btn');

                copyOpenBtn.addEventListener('click', async () => {
    try {
        copyOpenBtn.disabled = true;
        copyOpenBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Copying...';

        // Copy to clipboard
        await this.copyToClipboard(personalizedContent.bodyHtml);

        // LOG THE CUSTOMER UPDATE
        await this.logCustomerUpdate(recipient, personalizedContent.subject);

        // Open Outlook
        const mailtoUrl = `mailto:${encodeURIComponent(recipient.email)}?subject=${encodeURIComponent(personalizedContent.subject)}`;
        window.open(mailtoUrl, '_blank');

        // Mark as completed and move to next
        markCompleted(index);
        currentIndex++;

        // Show success message briefly
        copyOpenBtn.innerHTML = '<i class="fas fa-check me-2"></i>Opened!';
        copyOpenBtn.className = 'btn btn-lg btn-success me-3';

        setTimeout(() => {
            showCurrentRecipient(currentIndex);
        }, 1000);

    } catch (error) {
        console.error('Error copying and opening:', error);
        copyOpenBtn.disabled = false;
        copyOpenBtn.innerHTML = '<i class="fas fa-copy me-2"></i><i class="fas fa-external-link-alt me-2"></i>Copy Content & Open Outlook';
        this.showToast('Error copying content. Please try again.', 'error');
    }
});

            } catch (error) {
                console.error('Error showing recipient:', error);
                content.innerHTML = `
                    <div class="alert alert-danger">
                        <h6><i class="fas fa-exclamation-triangle me-2"></i>Error</h6>
                        <p>Error loading content for ${recipient.name || recipient.email}: ${error.message}</p>
                        <button type="button" class="btn btn-outline-danger btn-sm retry-btn">Retry</button>
                        <button type="button" class="btn btn-outline-secondary btn-sm ms-2 skip-error-btn">Skip</button>
                    </div>
                `;

                // Bind error buttons
                content.querySelector('.retry-btn').addEventListener('click', () => {
                    showCurrentRecipient(index);
                });

                content.querySelector('.skip-error-btn').addEventListener('click', () => {
                    markCompleted(index);
                    currentIndex++;
                    showCurrentRecipient(currentIndex);
                });
            }
        };

        // Start with the first recipient
        await showCurrentRecipient(currentIndex);
        updateProgress();


    }
 // FIXED: Enhanced logCustomerUpdate method with better error handling
async logCustomerUpdate(recipient, emailSubject) {
    try {
        // Get customer ID from recipient data or fallback to stored customerData
        let customerId = recipient.customerId || this.customerData?.id;

        if (!customerId) {
            console.warn('No customer ID available for recipient:', recipient);
            return; // Don't block Outlook opening
        }

        const updateText = `Emailed ${recipient.name} (${recipient.email})`;

        console.log('Logging customer update:', {
            customerId,
            updateText,
            contactId: recipient.id
        });

        const response = await fetch(`/customers/${customerId}/add_update`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-Requested-With': 'XMLHttpRequest', // Important: This tells the server it's an AJAX request
                'X-API-Key': 'dingleberry'
            },
            body: new URLSearchParams({
                update_type: 'email',
                update_text: updateText,
                contact_id: recipient.id || ''
            })
        });

        console.log('Response status:', response.status);
        console.log('Response headers:', Object.fromEntries(response.headers.entries()));

        if (!response.ok) {
            // Log the actual response text for debugging
            const responseText = await response.text();
            console.error('Server response:', responseText);
            throw new Error(`HTTP ${response.status}: ${responseText.substring(0, 200)}`);
        }

        // Check if the response is JSON
        const contentType = response.headers.get('content-type');
        if (!contentType || !contentType.includes('application/json')) {
            const responseText = await response.text();
            console.error('Expected JSON but got:', contentType, responseText.substring(0, 200));

            // If the response contains success indicators, treat it as success
            if (responseText.includes('Update added successfully') || responseText.includes('success')) {
                console.log('Update appears to have succeeded despite non-JSON response');
                return;
            }

            throw new Error(`Expected JSON response but got ${contentType}`);
        }

        const result = await response.json();

        if (!result.success) {
            throw new Error(result.error || 'Server reported failure');
        }

        console.log('Customer update logged successfully for:', recipient.name);

    } catch (error) {
        console.error('Failed to log customer update:', error);

        // More specific error messages
        let errorMessage = 'Could not log email communication';
        if (error.message.includes('Unexpected token')) {
            errorMessage += ' (server returned HTML instead of JSON)';
        } else if (error.message.includes('HTTP 4')) {
            errorMessage += ' (client error)';
        } else if (error.message.includes('HTTP 5')) {
            errorMessage += ' (server error)';
        }

        this.showToast(`Warning: ${errorMessage} for ${recipient.name}`, 'warning');
        // Don't throw - let Outlook opening continue
    }
}

// Handle single recipient Outlook opening with logging
async openSingleRecipientOutlook(recipient, subject, bodyHtml) {
    try {
        // Personalize content for this recipient
        const personalizedContent = await this.personalizeContentForRecipient(recipient, subject, bodyHtml);

        // Copy styled content to clipboard
        await this.copyToClipboard(personalizedContent.bodyHtml);

        // LOG THE CUSTOMER UPDATE
        await this.logCustomerUpdate(recipient, personalizedContent.subject);

        // Open Outlook with recipient and subject
        const mailtoUrl = `mailto:${encodeURIComponent(recipient.email)}?subject=${encodeURIComponent(personalizedContent.subject)}`;
        window.open(mailtoUrl, '_self');

        this.showToast('Styled content copied to clipboard! Paste into Outlook.', 'success');
        this.closeModal();
    } catch (error) {
        console.error('Error opening single recipient Outlook:', error);
        throw error;
    }
}




    // UPDATE: Simplified personalizeContentForRecipient
    async personalizeContentForRecipient(recipient, baseSubject, baseBodyHtml) {
        try {
            // Use original content if available, otherwise fall back to base content
            const sourceSubject = this.originalSubject || baseSubject;
            const sourceBody = this.originalBody || baseBodyHtml;

            if (this.isCustomEmail) {
                return this.personalizeCustomContent(recipient, sourceSubject, sourceBody);
            } else {
                // For template emails, make a fresh API call
                return await this.getPersonalizedTemplateContent(recipient, sourceSubject, sourceBody);
            }
        } catch (error) {
            console.error('Error personalizing content:', error);
            // Fallback to basic personalization with original content
            const sourceSubject = this.originalSubject || baseSubject;
            const sourceBody = this.originalBody || baseBodyHtml;
            return this.personalizeCustomContent(recipient, sourceSubject, sourceBody);
        }
    }

   personalizeCustomContent(recipient, sourceSubject, sourceBody) {
    const placeholders = {
        '{{contact_name}}': recipient.name || recipient.email.split('@')[0],
        '{{contact_first_name}}': recipient.name ? recipient.name.split(' ')[0] : recipient.email.split('@')[0],
        '{{contact_title}}': recipient.title || '',
        '{{company_name}}': recipient.company || '',
        '{{today_date}}': new Date().toLocaleDateString(),
        '{{sender_name}}': document.getElementById('current_user_name')?.value || 'User',
        '{{sender_title}}': document.getElementById('current_user_title')?.value || 'Sales Representative'
    };

    let personalizedSubject = sourceSubject;
    let personalizedBody = sourceBody;

    // Replace placeholders in both subject and body
    Object.entries(placeholders).forEach(([placeholder, value]) => {
        try {
            // Properly escape the placeholder for regex (e.g., {{contact_name}} becomes \{\{contact_name\}\})
            const escapedPlaceholder = placeholder.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            const regex = new RegExp(escapedPlaceholder, 'g');
            personalizedSubject = personalizedSubject.replace(regex, value);
            personalizedBody = personalizedBody.replace(regex, value);
        } catch (error) {
            console.error(`Error processing placeholder "${placeholder}":`, error);
            this.showToast(`Failed to process placeholder ${placeholder}`, 'error');
        }
    });

    personalizedBody = this.stripSignatureHtml(personalizedBody);

    // Ensure line breaks are preserved in personalized content
    personalizedBody = this.preserveLineBreaksInHtml(personalizedBody);

    return {
        subject: personalizedSubject,
        bodyHtml: personalizedBody
    };
}

    // For template emails, make a fresh API call for each recipient
    async getPersonalizedTemplateContent(recipient, baseSubject, baseBodyHtml) {
        try {
            const requestData = {
                contact_id: recipient.id,
                template_id: this.templateSelect.value,
                customer_id: this.customerData?.id || '',
                recipient: recipient // Send specific recipient data
            };

            const response = await fetch('/api/preview-email', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': 'dingleberry'
                },
                body: JSON.stringify(requestData)
            });

            if (!response.ok) {
                throw new Error(`Server error: ${response.status}`);
            }

            const result = await response.json();

            if (result.success || (result.data && (result.data.subject || result.data.body))) {
                const previewData = result.data || result;
                return {
                    subject: previewData.subject || baseSubject,
                    bodyHtml: this.stripSignatureHtml(previewData.body_without_signature || previewData.body || baseBodyHtml)
                };
            } else {
                throw new Error('Invalid response from server');
            }
        } catch (error) {
            console.error('Error getting personalized template content:', error);
            // Fallback to custom personalization with original content
            const sourceSubject = this.originalSubject || baseSubject;
            const sourceBody = this.originalBody || baseBodyHtml;
            return this.personalizeCustomContent(recipient, sourceSubject, sourceBody);
        }
    }

    // Enhanced copyToClipboard method that preserves line breaks
    async copyToClipboard(htmlContent) {
        try {
            // First, let's ensure the HTML has proper line break formatting
            const normalizedHtml = this.normalizeHtmlLineBreaks(this.stripSignatureHtml(htmlContent));

            // Try modern clipboard API with both HTML and plain text
            if (navigator.clipboard && window.ClipboardItem) {
                // Create both HTML and plain text versions
                const plainText = this.htmlToPlainText(normalizedHtml);

                await navigator.clipboard.write([
                    new ClipboardItem({
                        'text/html': new Blob([normalizedHtml], { type: 'text/html' }),
                        'text/plain': new Blob([plainText], { type: 'text/plain' })
                    })
                ]);
                console.log('Multi-format clipboard copy successful');
                return;
            }

            // Fallback method with better line break preservation
            const tempDiv = document.createElement('div');
            tempDiv.innerHTML = normalizedHtml;

            // Apply styles that help preserve formatting when copying
            tempDiv.style.cssText = `
                position: fixed;
                left: -9999px;
                top: -9999px;
                opacity: 0;
                white-space: pre-wrap;
                font-family: Arial, sans-serif;
                line-height: 1.4;
            `;
            tempDiv.contentEditable = true;

            document.body.appendChild(tempDiv);

            // Focus and select all content
            tempDiv.focus();
            const range = document.createRange();
            range.selectNodeContents(tempDiv);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);

            // Copy with execCommand
            const success = document.execCommand('copy');

            // Clean up
            selection.removeAllRanges();
            document.body.removeChild(tempDiv);

            if (!success) {
                throw new Error('Copy failed');
            }

            console.log('HTML clipboard copy successful (fallback method)');

        } catch (error) {
            console.error('HTML clipboard copy failed:', error);
            // Show manual copy modal with properly formatted HTML
            this.showManualCopyModalWithHTML(this.normalizeHtmlLineBreaks(htmlContent));
            throw new Error('Automatic copying failed - manual copy required');
        }
    }

    // Normalize HTML for Outlook copy without inflating line spacing.
    normalizeHtmlLineBreaks(htmlContent) {
        let normalized = htmlContent || '';

        normalized = normalized.replace(/\r\n|\r/g, '\n');
        normalized = normalized.replace(/\n/g, '<br>');

        // Clean up any triple+ breaks that might have been created.
        normalized = normalized.replace(/(<br\s*\/?>\s*){3,}/gi, '<br><br>');

        return normalized;
    }

    // NEW: Convert HTML to plain text while preserving line structure
    htmlToPlainText(htmlContent) {
        // Create a temporary div to parse HTML
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = htmlContent;

        // Replace block elements with line breaks
        const blockElements = tempDiv.querySelectorAll('div, p, h1, h2, h3, h4, h5, h6, li, blockquote');
        blockElements.forEach(element => {
            element.innerHTML = element.innerHTML + '\n\n';
        });

        // Replace <br> tags with newlines
        tempDiv.innerHTML = tempDiv.innerHTML.replace(/<br\s*\/?>/gi, '\n');

        // Get text content and clean up extra whitespace
        let plainText = tempDiv.textContent || tempDiv.innerText || '';

        // Normalize line breaks
        plainText = plainText.replace(/\n\s*\n\s*\n/g, '\n\n'); // Max 2 consecutive line breaks
        plainText = plainText.trim();

        return plainText;
    }

    // UPDATED: Manual copy modal with better formatting preservation
    showManualCopyModalWithHTML(htmlContent) {
        const normalizedHtml = this.normalizeHtmlLineBreaks(htmlContent);
        const plainText = this.htmlToPlainText(normalizedHtml);

        const modalHtml = `
            <div class="modal fade" id="manualCopyModal" tabindex="-1">
                <div class="modal-dialog modal-xl">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">
                                <i class="fas fa-copy me-2"></i>Manual Copy Required
                            </h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <div class="alert alert-info">
                                <i class="fas fa-info-circle me-2"></i>
                                Choose the format that works best for your email client:
                            </div>

                            <!-- Tabs for different formats -->
                            <ul class="nav nav-tabs" id="copyTabs" role="tablist">
                                <li class="nav-item" role="presentation">
                                    <button class="nav-link active" id="html-tab" data-bs-toggle="tab"
                                            data-bs-target="#html-content" type="button" role="tab">
                                        <i class="fas fa-code me-1"></i>Rich Format (Outlook)
                                    </button>
                                </li>
                                <li class="nav-item" role="presentation">
                                    <button class="nav-link" id="plain-tab" data-bs-toggle="tab"
                                            data-bs-target="#plain-content" type="button" role="tab">
                                        <i class="fas fa-align-left me-1"></i>Plain Text
                                    </button>
                                </li>
                            </ul>

                            <div class="tab-content mt-3" id="copyTabsContent">
                                <!-- HTML Format Tab -->
                                <div class="tab-pane fade show active" id="html-content" role="tabpanel">
                                    <div class="mb-3">
                                        <label class="form-label fw-bold">Rich Format (preserves formatting):</label>
                                        <div
                                            id="htmlContentDiv"
                                            class="border rounded p-3"
                                            style="max-height: 400px; overflow-y: auto; background: white; cursor: text; white-space: pre-wrap; line-height: 1.5;"
                                            contenteditable="true"
                                        >${normalizedHtml}</div>
                                    </div>
                                    <button type="button" class="btn btn-primary" id="selectHtmlBtn">
                                        <i class="fas fa-mouse-pointer me-1"></i>Select Rich Content
                                    </button>
                                </div>

                                <!-- Plain Text Tab -->
                                <div class="tab-pane fade" id="plain-content" role="tabpanel">
                                    <div class="mb-3">
                                        <label class="form-label fw-bold">Plain Text (line breaks preserved):</label>
                                        <textarea
                                            id="plainContentTextarea"
                                            class="form-control"
                                            rows="15"
                                            style="white-space: pre-wrap; font-family: monospace;"
                                            readonly
                                        >${plainText}</textarea>
                                    </div>
                                    <button type="button" class="btn btn-secondary" id="selectPlainBtn">
                                        <i class="fas fa-mouse-pointer me-1"></i>Select Plain Text
                                    </button>
                                </div>
                            </div>

                            <div class="alert alert-success mt-3">
                                <i class="fas fa-lightbulb me-1"></i>
                                <strong>For Outlook:</strong> Use the Rich Format tab for best results.
                                For other email clients, try Plain Text if formatting issues occur.
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // Remove existing modal if present
        const existingModal = document.getElementById('manualCopyModal');
        if (existingModal) {
            existingModal.remove();
        }

        // Add new modal
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        const newModal = document.getElementById('manualCopyModal');

        // Bind select buttons
        const selectHtmlBtn = newModal.querySelector('#selectHtmlBtn');
        const selectPlainBtn = newModal.querySelector('#selectPlainBtn');
        const htmlContentDiv = newModal.querySelector('#htmlContentDiv');
        const plainContentTextarea = newModal.querySelector('#plainContentTextarea');

        selectHtmlBtn.addEventListener('click', function() {
            const range = document.createRange();
            range.selectNodeContents(htmlContentDiv);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);

            // Try to copy
            try {
                const success = document.execCommand('copy');
                if (success) {
                    this.innerHTML = '<i class="fas fa-check me-1"></i>Rich Content Copied!';
                    this.className = 'btn btn-success';
                } else {
                    this.innerHTML = '<i class="fas fa-hand-pointer me-1"></i>Selected - Copy with Ctrl+C';
                    this.className = 'btn btn-warning';
                }
            } catch (err) {
                this.innerHTML = '<i class="fas fa-hand-pointer me-1"></i>Selected - Copy with Ctrl+C';
                this.className = 'btn btn-warning';
            }
        });

        selectPlainBtn.addEventListener('click', function() {
            plainContentTextarea.select();
            plainContentTextarea.setSelectionRange(0, plainContentTextarea.value.length);

            try {
                const success = document.execCommand('copy');
                if (success) {
                    this.innerHTML = '<i class="fas fa-check me-1"></i>Plain Text Copied!';
                    this.className = 'btn btn-success';
                } else {
                    this.innerHTML = '<i class="fas fa-hand-pointer me-1"></i>Selected - Copy with Ctrl+C';
                    this.className = 'btn btn-warning';
                }
            } catch (err) {
                this.innerHTML = '<i class="fas fa-hand-pointer me-1"></i>Selected - Copy with Ctrl+C';
                this.className = 'btn btn-warning';
            }
        });

        // Show modal
        if (window.bootstrap && bootstrap.Modal) {
            const modalInstance = new bootstrap.Modal(newModal);
            modalInstance.show();
        }
    }

    // Enhanced showToast method
    showToast(message, type = 'info') {
        // If you have a global toast function, use it
        if (window.showToast && typeof window.showToast === 'function') {
            window.showToast(message, type);
            return;
        }

        // Simple fallback toast
        const toast = document.createElement('div');
        toast.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 12px 20px;
            background: ${type === 'success' ? '#28a745' : type === 'error' ? '#dc3545' : type === 'warning' ? '#ffc107' : '#007bff'};
            color: ${type === 'warning' ? '#000' : '#fff'};
            border-radius: 4px;
            z-index: 9999;
            max-width: 300px;
            font-size: 14px;
            box-shadow: 0 4px 8px rgba(0,0,0,0.2);
        `;
        toast.textContent = message;

        document.body.appendChild(toast);

        setTimeout(() => {
            if (toast.parentElement) {
                toast.style.opacity = '0';
                toast.style.transition = 'opacity 0.3s';
                setTimeout(() => toast.remove(), 300);
            }
        }, 4000);
    }

    async sendEmail() {
        alert('Direct send is not available. Please use "Open in Outlook" instead.');
        return;
    }

    async sendAllViaSystem() {
        try {
            if (this.recipients.length === 0) {
                this.showToast('No recipients selected', 'warning');
                return;
            }

            const emailSubject = this.modal.querySelector('.email-subject');
            const emailBody = this.modal.querySelector('.email-body');

            if (!emailSubject || !emailBody || this.previewSection.classList.contains('d-none')) {
                this.showToast('Please preview the email before sending', 'warning');
                return;
            }

            const baseSubject = emailSubject.textContent;
            const baseBodyHtml = emailBody.innerHTML;
            const templateId = this.templateSelect ? this.templateSelect.value : '';
            const replyMessageId = this.replyMessageId || '';

            if (replyMessageId && this.recipients.length !== 1) {
                this.showToast('Replying is only available for a single recipient', 'warning');
                return;
            }
            if (replyMessageId && !this.isCustomEmail) {
                this.showToast('Replying requires a custom email body', 'warning');
                return;
            }

            if (!this.isCustomEmail && !templateId) {
                this.showToast('Please select a template first', 'warning');
                return;
            }

            const confirmSend = confirm(`Send ${this.recipients.length} emails via the system now?`);
            if (!confirmSend) {
                return;
            }

            if (this.sendSystemBtn) {
                this.sendSystemBtn.disabled = true;
                this.sendSystemBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Sending...';
            }

            let successCount = 0;
            let failureCount = 0;

            for (const recipient of this.recipients) {
                if (!recipient.email) {
                    failureCount += 1;
                    continue;
                }

                try {
                    const personalizedContent = await this.personalizeContentForRecipient(
                        recipient,
                        baseSubject,
                        baseBodyHtml
                    );

                    let endpoint = '';
                    let payload = {};

                    if (replyMessageId) {
                        endpoint = '/emails/graph/reply';
                        payload = {
                            message_id: replyMessageId,
                            html_body: personalizedContent.bodyHtml
                        };
                    } else if (this.isCustomEmail) {
                        endpoint = '/api/send-custom-email';
                        payload = {
                            contact_id: recipient.id,
                            customer_id: recipient.customerId || this.customerData?.id || '',
                            subject: personalizedContent.subject,
                            body: personalizedContent.bodyHtml
                        };
                    } else {
                        endpoint = '/api/send-email';
                        payload = {
                            contact_id: recipient.id,
                            customer_id: recipient.customerId || this.customerData?.id || '',
                            template_id: templateId
                        };
                    }

                    const response = await fetch(endpoint, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(payload)
                    });

                    const result = await response.json();
                    if (!response.ok || !result.success) {
                        throw new Error(result.error || result.message || `Send failed (${response.status})`);
                    }

                    await this.logCustomerUpdate(recipient, personalizedContent.subject);
                    successCount += 1;
                } catch (error) {
                    console.error('System send failed for recipient:', recipient, error);
                    failureCount += 1;
                }
            }

            if (failureCount === 0) {
                this.showToast(`Sent ${successCount} emails via system`, 'success');
            } else {
                this.showToast(`Sent ${successCount} emails, ${failureCount} failed`, 'warning');
            }

            this.closeModal();
        } catch (error) {
            console.error('Send all via system failed:', error);
            this.showToast(error.message || 'Failed to send via system', 'error');
        } finally {
            if (this.sendSystemBtn) {
                this.sendSystemBtn.disabled = false;
                this.sendSystemBtn.innerHTML = '<i class="fas fa-paper-plane me-2"></i>Send All via System';
            }
        }
    }

    closeModal() {
        try {
            if (window.bootstrap && bootstrap.Modal) {
                const modalInstance = bootstrap.Modal.getInstance(this.modal);
                if (modalInstance) modalInstance.hide();
            } else if (window.$ && $.fn.modal) {
                $(this.modal).modal('hide');
            } else {
                this.modal.classList.remove('show');
                this.modal.style.display = 'none';
                document.body.classList.remove('modal-open');
                const backdrop = document.querySelector('.modal-backdrop');
                if (backdrop) backdrop.remove();
            }
        } catch (e) {
            console.warn('Error closing modal:', e);
        }
    }

    updatePreview(preview) {
        // For multiple recipients, show all in preview
        let recipientDisplay = '';
        if (this.recipients.length === 1) {
            const recipient = this.recipients[0];
            recipientDisplay = recipient.name && recipient.email
                ? `${recipient.name} <${recipient.email}>`
                : recipient.email || 'Unknown recipient';
        } else {
            recipientDisplay = `${this.recipients.length} recipients: ${this.recipients.map(r => r.email).join(', ')}`;
        }

        const recipientInfo = this.modal.querySelector('.recipient-info');
        const emailSubject = this.modal.querySelector('.email-subject');
        const emailBody = this.modal.querySelector('.email-body');

        if (recipientInfo) recipientInfo.textContent = recipientDisplay;
        if (emailSubject) emailSubject.textContent = preview.subject || 'No subject';

        // Process body content (signature removal logic if needed)
        let bodyContent = preview.body || 'No content';

        // Optional: Remove signature from the body if it's included in the template
        // You can customize this logic based on how your signatures are structured
        bodyContent = this.stripSignatureHtml(bodyContent);

        // FIXED: Preserve line breaks in the preview display
        if (emailBody) {
            // Apply CSS styles that preserve line breaks and formatting
            emailBody.style.whiteSpace = 'pre-wrap';
            emailBody.style.lineHeight = '1.5';
            emailBody.style.fontFamily = 'Arial, sans-serif';

            // Set the HTML content with preserved formatting
            emailBody.innerHTML = this.preserveLineBreaksInHtml(bodyContent);
        }

        // Show the preview section (no signature section anymore)
        if (this.previewSection) {
            this.previewSection.classList.remove('d-none');
        }
    }

    // NEW: Method to preserve line breaks in HTML content
    preserveLineBreaksInHtml(htmlContent) {
        if (!htmlContent) return htmlContent;

        let processed = htmlContent;

        // Convert various line break patterns to consistent format
        // Handle \n characters that might be in the content
        processed = processed.replace(/\n/g, '<br>');

        // Handle \r\n (Windows line endings)
        processed = processed.replace(/\r\n/g, '<br>');

        // Ensure paragraph endings create proper breaks
        processed = processed.replace(/<\/p>/gi, '</p><br>');

        // Ensure div endings create proper breaks
        processed = processed.replace(/<\/div>/gi, '</div><br>');

        // Handle cases where there might be escaped line breaks
        processed = processed.replace(/\\n/g, '<br>');

        // Clean up any multiple consecutive <br> tags (but keep double for paragraph spacing)
        processed = processed.replace(/(<br\s*\/?>){3,}/gi, '<br><br>');

        // Ensure there's spacing after headings
        processed = processed.replace(/<\/(h[1-6])>/gi, '</$1><br>');

        return processed;
    }

    stripSignatureHtml(htmlContent) {
        let content = htmlContent || '';
        const signaturePatterns = [
            /<br><br>.*?<img.*?signature.*?>/gis,
            /<br><br>.*linkedin.*?<\/a>/gis,
            /<div.*?signature.*?<\/div>/gis,
            /<p.*?signature.*?<\/p>/gis
        ];

        signaturePatterns.forEach(pattern => {
            content = content.replace(pattern, '');
        });

        return content;
    }

    showPreviewError(errorMessage) {
        const recipientInfo = this.modal.querySelector('.recipient-info');
        const emailSubject = this.modal.querySelector('.email-subject');
        const emailBody = this.modal.querySelector('.email-body');

        if (recipientInfo) recipientInfo.textContent = 'Error';
        if (emailSubject) emailSubject.textContent = 'Preview Error';
        if (emailBody) emailBody.innerHTML = `<div class="text-danger">${errorMessage}</div>`;

        if (this.previewSection) {
            this.previewSection.classList.remove('d-none');
        }
    }
}

// Initialize on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
    const t0 = performance.now();
    if (console.time) {
        console.time('init.email_modal');
    }
    console.log("Initializing email modal on DOMContentLoaded");

    // Only initialize if the modal exists on this page
    const emailModal = document.getElementById('emailModal');
    if (emailModal) {
        try {
            // Create the instance and store it globally
            window.emailModalInstance = new EmailModal();
            console.log("Email modal instance created successfully");
        } catch (error) {
            console.error('Error initializing email modal:', error);
        }
    } else {
        console.log("Email modal not found on page");
    }
    if (console.timeEnd) {
        console.timeEnd('init.email_modal');
    }
    console.log(`init.email_modal ${Math.round(performance.now() - t0)}ms`);
});

// Global helper functions
window.debugEmailModal = function() {
    console.log("=== Email Modal Debug ===");

    const modal = document.getElementById('emailModal');
    if (!modal) {
        console.log('Email modal not found in DOM');
        return;
    }

    console.log('Modal element exists in DOM');

    if (window.emailModalInstance) {
        console.log('EmailModal instance found in window');
        console.log('Current state:');
        console.log('- isCustomEmail:', window.emailModalInstance.isCustomEmail);
        console.log('- templatesLoaded:', window.emailModalInstance.templatesLoaded);
        console.log('- recipients:', window.emailModalInstance.recipients);
    } else {
        console.log("No EmailModal instance found in window");
        console.log("Creating new instance now...");
        window.emailModalInstance = new EmailModal();
    }

    console.log("=== End Email Modal Debug ===");
};

// Global helper function to add recipients programmatically
window.addEmailRecipient = function(recipientData) {
    if (window.emailModalInstance) {
        return window.emailModalInstance.addRecipient(recipientData);
    } else {
        console.error('EmailModal instance not found');
        return false;
    }
};

// Global helper function to add multiple recipients
window.addMultipleEmailRecipients = function(recipientDataArray) {
    if (window.emailModalInstance) {
        return window.emailModalInstance.addMultipleRecipients(recipientDataArray);
    } else {
        console.error('EmailModal instance not found');
        return 0;
    }
};

// Global helper function to clear recipients
window.clearEmailRecipients = function() {
    if (window.emailModalInstance) {
        window.emailModalInstance.clearRecipients();
    } else {
        console.error('EmailModal instance not found');
    }
};

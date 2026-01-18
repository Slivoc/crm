// static/js/customer_preview.js

// Ensure modal is available globally
let customerPreviewInstance = null;

class CustomerPreviewModal {
    constructor() {
        // Initialize modal references
        this.modal = new bootstrap.Modal(document.getElementById('customerPreviewModal'));
        this.currentCustomerId = null;

        // Cache DOM elements
        this.loadingElement = document.getElementById('customerPreviewLoading');
        this.contentElement = document.getElementById('customerPreviewContent');
        this.errorElement = document.getElementById('customerPreviewError');

        // Initialize pagination variables
        this.contactsPage = 1;
        this.emailsPage = 1;

        // Initialize modals
        this.editTagsModal = new bootstrap.Modal(document.getElementById('editTagsModal'));
        this.apolloSearchModal = new bootstrap.Modal(document.getElementById('apolloSearchModal'));

        // Initialize collections
        this.selectedTags = new Set();

        // Store modal element reference
        this.modalElement = document.getElementById('customerPreviewModal');

        // Initialize all event listeners
        this.initializeEventListeners();

        // Add event listener for modal hidden
        this.modalElement.addEventListener('hidden.bs.modal', () => {
            this.currentCustomerId = null;
            // Destroy any tooltips
            const tooltips = bootstrap.Tooltip.getInstance(document.getElementById('apolloMatchStatus'));
            if (tooltips) tooltips.dispose();
        });
    }

    initializeEventListeners() {
        const loadMoreContactsBtn = document.getElementById('loadMoreContacts');
        if (loadMoreContactsBtn) {
            loadMoreContactsBtn.addEventListener('click', () => this.loadMoreContacts());
        }

        // Event delegation for email buttons
        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('send-email-btn')) {
                const contactId = e.target.dataset.contactId;
                openEmailModal(this.currentCustomerId, contactId);
            }
        });

        document.addEventListener('click', (e) => {
            const addButton = e.target.closest('.add-to-call-list-btn');
            if (addButton) {
                e.preventDefault();
                e.stopPropagation();
                this.addContactToCallList(addButton);
                return;
            }

            const removeButton = e.target.closest('.remove-from-call-list-btn');
            if (removeButton) {
                e.preventDefault();
                e.stopPropagation();
                this.removeContactFromCallList(removeButton);
            }
        });

        const editTagsBtn = document.getElementById('editTagsBtn');
        if (editTagsBtn) {
            editTagsBtn.addEventListener('click', () => this.openTagsModal());
        }

        const saveTagsBtn = document.getElementById('saveTagsBtn');
        if (saveTagsBtn) {
            saveTagsBtn.addEventListener('click', () => this.saveTags());
        }

        const searchApolloBtn = document.getElementById('searchApolloBtn');
        if (searchApolloBtn) {
            searchApolloBtn.addEventListener('click', () => this.openApolloSearchModal());
        }

        const apolloSearchInput = document.getElementById('apolloSearchInput');
        if (apolloSearchInput) {
            apolloSearchInput.addEventListener('input', this.debounce(() => this.searchApollo(), 500));
        }

        const loadMoreEmailsBtn = document.getElementById('loadMoreEmails');
        if (loadMoreEmailsBtn) {
            loadMoreEmailsBtn.addEventListener('click', () => this.loadMoreEmails());
        }

        // REMOVED: Individual lead search button handlers since they're now handled via onclick in HTML
        // The buttons now call customerPreview.searchLeads() directly
    }

    debounce(func, wait) {
        let timeout;
        return (...args) => {
            clearTimeout(timeout);
            timeout = setTimeout(() => func(...args), wait);
        };
    }

    getSalespersonId() {
        if (window.salespersonManager && typeof window.salespersonManager.getCurrentSalesperson === 'function') {
            return window.salespersonManager.getCurrentSalesperson();
        }
        return localStorage.getItem('salesperson_id');
    }

    show(customerId) {
        this.currentCustomerId = customerId;
        this.contactsPage = 1;
        this.showLoading();
        this.modal.show();

        // Wait for modal to be fully shown
        this.modalElement.addEventListener('shown.bs.modal', () => {
            console.log("Modal is fully shown");
            this.loadCustomerData();
        }, { once: true });
    }

    async loadCustomerData() {
        try {
            console.log("Starting loadCustomerData for ID:", this.currentCustomerId);
            console.log("API Key:", API_KEY);

            const salespersonId = this.getSalespersonId();
            const query = salespersonId ? `?salesperson_id=${encodeURIComponent(salespersonId)}` : '';
            const response = await fetch(`/api/customer-preview/${this.currentCustomerId}${query}`, {
                headers: { 'X-API-Key': API_KEY }
            });

            console.log("Response status:", response.status);

            if (!response.ok) {
                const errorText = await response.text();
                console.error("Error response:", errorText);
                throw new Error('Failed to load customer preview');
            }

            const data = await response.json();
            console.log("Received data:", JSON.stringify(data, null, 2));

            if (!data || !data.data) {
                console.error("Invalid data format:", data);
                throw new Error('Invalid data format received');
            }

            this.updateModalContent(data.data);
        } catch (error) {
            console.error("Full error:", error);
            this.showError(error.message);
        }
    }

    updateModalContent(data) {
        const { customer, contacts, tags, apollo_match, recent_emails } = data;

        console.log("Customer object:", customer);
        console.log("Customer name:", customer.name);

        document.getElementById('previewCustomerName').textContent = customer.name;
        document.getElementById('previewCustomerCountry').textContent = customer.country || '';

        if (contacts?.items) {
            this.updateContacts(contacts.items);
        }

        if (tags) {
            this.updateTags(tags);
        }

        const loadMoreBtn = document.getElementById('loadMoreContacts');
        if (loadMoreBtn) {
            loadMoreBtn.style.display = contacts?.items?.length === 10 ? 'inline-block' : 'none';
        }

        this.updateApolloMatch(apollo_match);
        this.updateEmails(recent_emails);

        const loadMoreEmailsBtn = document.getElementById('loadMoreEmails');
        if (loadMoreEmailsBtn) {
            loadMoreEmailsBtn.style.display = recent_emails?.length === 5 ? 'inline-block' : 'none';
        }

        this.hideLoading();
    }

    updateContacts(contacts) {
        const contactsList = document.getElementById('contactsList');
        const contactsHtml = contacts.map(contact => `
            <div class="card mb-2">
                <div class="card-body py-2">
                    <div class="row align-items-center">
                        <div class="col">
                            <h6 class="mb-0">${contact.name}</h6>
                            <small class="text-muted">
                                ${contact.job_title || ''}<br>
                                ${contact.email || ''}
                            </small>
                        </div>
                        <div class="col-auto d-flex gap-2">
                            <button class="btn btn-sm btn-outline-primary send-email-btn"
                                    data-contact-id="${contact.id}">
                                Send Email
                            </button>
                            ${this.renderCallListButton(contact)}
                        </div>
                    </div>
                </div>
            </div>
        `).join('');

        if (this.contactsPage === 1) {
            contactsList.innerHTML = contactsHtml;
        } else {
            contactsList.insertAdjacentHTML('beforeend', contactsHtml);
        }
    }

    async loadMoreContacts() {
        try {
            this.contactsPage++;
            const salespersonId = this.getSalespersonId();
            const params = new URLSearchParams({ page: this.contactsPage });
            if (salespersonId) {
                params.append('salesperson_id', salespersonId);
            }
            const response = await fetch(`/api/customer-preview/${this.currentCustomerId}/contacts?${params.toString()}`, {
                headers: { 'X-API-Key': API_KEY }
            });

            if (!response.ok) throw new Error('Failed to load more contacts');
            const data = await response.json();
            this.updateContacts(data.data.items);

            const loadMoreBtn = document.getElementById('loadMoreContacts');
            loadMoreBtn.style.display = data.data.items.length === 10 ? 'inline-block' : 'none';
        } catch (error) {
            console.error('Error loading more contacts:', error);
        }
    }

    updateTags(tags) {
        const tagsList = document.getElementById('previewTagsList');
        if (!tagsList) {
            console.error("tagsList element not found");
            return;
        }

        const tagsHtml = tags.map(tag => `
            <div class="tag-item">
                <span class="badge bg-info me-1 mb-1">
                    ${tag.name} (${tag.customer_count || 0})
                </span>
            </div>
        `).join('');

        tagsList.innerHTML = tagsHtml || '<p class="text-muted">No tags assigned.</p>';
    }

    renderCallListButton(contact) {
        const salespersonId = this.getSalespersonId();
        const hasSalesperson = Boolean(salespersonId);
        const isOnCallList = Boolean(contact.is_on_call_list);
        const buttonClass = isOnCallList ? 'btn-success remove-from-call-list-btn' : 'btn-warning add-to-call-list-btn';
        const iconClass = isOnCallList ? 'bi bi-check-circle-fill' : 'bi bi-list-check';
        const title = hasSalesperson
            ? (isOnCallList ? 'Click to remove from call list' : 'Add to Call List')
            : 'Select salesperson to use call list';
        const disabledAttr = hasSalesperson ? '' : 'disabled';

        return `
            <button class="btn btn-sm ${buttonClass}"
                    data-contact-id="${contact.id}"
                    data-contact-name="${contact.name}"
                    title="${title}"
                    ${disabledAttr}>
                <i class="${iconClass}"></i>
            </button>
        `;
    }

    async addContactToCallList(button) {
        const salespersonId = this.getSalespersonId();
        if (!salespersonId) {
            const toast = new bootstrap.Toast(createToast('Select a salesperson before adding to the call list.'));
            toast.show();
            return;
        }

        const contactId = button.getAttribute('data-contact-id');
        const contactName = button.getAttribute('data-contact-name');

        const icon = button.querySelector('i');
        const originalIcon = icon.className;
        icon.className = 'bi bi-hourglass-split';
        button.disabled = true;

        try {
            const response = await fetch(`/salespeople/${salespersonId}/add-to-call-list`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({
                    contact_id: contactId,
                    notes: '',
                    priority: 0
                })
            });

            const data = await response.json();
            if (data.success) {
                icon.className = 'bi bi-check-circle-fill';
                button.classList.remove('btn-warning', 'add-to-call-list-btn');
                button.classList.add('btn-success', 'remove-from-call-list-btn');
                button.disabled = false;
                button.title = 'Click to remove from call list';
                if (data.call_list_id) {
                    button.setAttribute('data-call-list-id', data.call_list_id);
                }

                const toast = new bootstrap.Toast(createToast(`${contactName} added to call list`));
                toast.show();
            } else {
                icon.className = originalIcon;
                button.disabled = false;
                const toast = new bootstrap.Toast(createToast(data.error || 'Failed to add to call list'));
                toast.show();
            }
        } catch (error) {
            console.error('Error adding to call list:', error);
            icon.className = originalIcon;
            button.disabled = false;
            const toast = new bootstrap.Toast(createToast('An error occurred while adding to call list'));
            toast.show();
        }
    }

    async removeContactFromCallList(button) {
        const salespersonId = this.getSalespersonId();
        if (!salespersonId) {
            const toast = new bootstrap.Toast(createToast('Select a salesperson before removing from the call list.'));
            toast.show();
            return;
        }

        const contactId = button.getAttribute('data-contact-id');
        const contactName = button.getAttribute('data-contact-name');
        const callListId = button.getAttribute('data-call-list-id');

        const icon = button.querySelector('i');
        icon.className = 'bi bi-hourglass-split';
        button.disabled = true;

        try {
            const response = await fetch(`/salespeople/${salespersonId}/remove-from-call-list`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({
                    call_list_id: callListId,
                    contact_id: contactId
                })
            });

            const data = await response.json();
            if (data.success) {
                icon.className = 'bi bi-list-check';
                button.classList.remove('btn-success', 'remove-from-call-list-btn');
                button.classList.add('btn-warning', 'add-to-call-list-btn');
                button.disabled = false;
                button.title = 'Add to Call List';
                button.removeAttribute('data-call-list-id');

                const toast = new bootstrap.Toast(createToast(`${contactName} removed from call list`));
                toast.show();
            } else {
                icon.className = 'bi bi-check-circle-fill';
                button.disabled = false;
                const toast = new bootstrap.Toast(createToast(data.error || 'Failed to remove from call list'));
                toast.show();
            }
        } catch (error) {
            console.error('Error removing from call list:', error);
            icon.className = 'bi bi-check-circle-fill';
            button.disabled = false;
            const toast = new bootstrap.Toast(createToast('An error occurred while removing from call list'));
            toast.show();
        }
    }

    async openTagsModal() {
        if (!this.currentCustomerId) {
            console.error("Cannot open tags modal without a valid customer ID.");
            return;
        }

        console.log("Opening tags modal for customer ID:", this.currentCustomerId);

        try {
            const response = await fetch(`/api/customer-preview/${this.currentCustomerId}/tags`, {
                headers: { 'X-API-Key': API_KEY }
            });

            if (!response.ok) throw new Error('Failed to load tags');
            const data = await response.json();

            console.log("Fetched tags data:", data);

            if (!data || !data.data) {
                console.error("No tags data found:", data);
                return;
            }

            this.renderTagsTree(data.data);
            this.editTagsModal.show();
        } catch (error) {
            console.error("Error loading tags:", error);
        }
    }

    renderTagsTree(tags) {
        const tagsTree = document.getElementById('tagsTree');
        const renderNode = (node) => {
            const isChecked = this.selectedTags.has(node.id);
            const marginLeft = node.level * 1.5;

            const html = `
                <div class="form-check" style="margin-left: ${marginLeft}rem">
                    <input class="form-check-input tag-checkbox"
                           type="checkbox"
                           id="tag-${node.id}"
                           value="${node.id}"
                           ${isChecked ? 'checked' : ''}>
                    <label class="form-check-label" for="tag-${node.id}">
                        ${node.name} (${node.customer_count})
                    </label>
                </div>
            `;

            let childrenHtml = '';
            if (node.children && node.children.length > 0) {
                childrenHtml = node.children.map(child => renderNode(child)).join('');
            }

            return html + childrenHtml;
        };

        tagsTree.innerHTML = tags.map(tag => renderNode(tag)).join('');
    }

    async saveTags() {
        try {
            const checkedTags = Array.from(document.querySelectorAll('#tagsTree .tag-checkbox:checked')).map(cb => cb.value);
            const response = await fetch(`/api/customer-preview/${this.currentCustomerId}/tags`, {
                method: 'POST',
                headers: {
                    'X-API-Key': API_KEY,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ tag_ids: checkedTags })
            });

            if (!response.ok) throw new Error('Failed to save tags');
            const data = await response.json();
            this.updateTags(data.data.tags);
            this.editTagsModal.hide();
        } catch (error) {
            console.error('Error saving tags:', error);
        }
    }

    updateApolloMatch(apolloData) {
        const badge = document.getElementById('apolloMatchStatus');
        const searchBtn = document.getElementById('searchApolloBtn');
        const leadSearchSection = document.getElementById('leadSearchSection');
        const initialMessage = document.getElementById('initialLeadMessage');

        // Remove any existing tooltips
        const existingTooltip = bootstrap.Tooltip.getInstance(badge);
        if (existingTooltip) {
            existingTooltip.dispose();
        }

        if (!apolloData) {
            // Unmatched state
            badge.className = 'badge rounded-pill bg-secondary';
            badge.textContent = 'Unmatched';
            searchBtn.textContent = 'Search';

            // Update lead search section
            if (leadSearchSection) {
                if (initialMessage) {
                    initialMessage.innerHTML = `
                        <p class="text-muted mb-2">Match with Apollo to start finding leads</p>
                    `;
                }

                // Disable lead search buttons
                const procurementBtn = document.getElementById('procurementLeadsBtn');
                const generalBtn = document.getElementById('generalLeadsBtn');
                if (procurementBtn) procurementBtn.disabled = true;
                if (generalBtn) generalBtn.disabled = true;
            }
            return;
        }

        // Matched state
        badge.className = 'badge rounded-pill bg-success';
        badge.textContent = 'Matched';
        searchBtn.textContent = 'Change';

        // Add tooltip with Apollo company details
        badge.setAttribute('data-bs-toggle', 'tooltip');
        badge.setAttribute('data-bs-placement', 'top');
        badge.setAttribute('title', `
            ${apolloData.name}
            ${apolloData.website ? ` • ${apolloData.website}` : ''}
            ${apolloData.employee_count ? ` • ${apolloData.employee_count} employees` : ''}
            ${apolloData.primary_industry ? ` • ${apolloData.primary_industry}` : ''}
        `.trim());

        // Initialize tooltip
        new bootstrap.Tooltip(badge);

        // Enable lead search section
        if (leadSearchSection) {
            // Enable lead search buttons
            const procurementBtn = document.getElementById('procurementLeadsBtn');
            const generalBtn = document.getElementById('generalLeadsBtn');
            if (procurementBtn) procurementBtn.disabled = false;
            if (generalBtn) generalBtn.disabled = false;

            // Update initial message
            if (initialMessage) {
                initialMessage.innerHTML = `
                    <div class="d-flex flex-column align-items-center">
                        <p class="text-success mb-2">
                            <i class="bi bi-check-circle"></i>
                            Matched with Apollo
                        </p>
                        <button class="btn btn-primary" onclick="customerPreview.searchLeads('procurement')">
                            <i class="bi bi-search"></i> Find Procurement Leads
                        </button>
                    </div>
                `;
            }

            // Clear any existing leads results
            const leadsSection = document.getElementById('leadsSection');
            if (leadsSection && leadsSection.querySelector('.list-group')) {
                leadsSection.querySelector('.list-group').remove();
            }
        }
    }

    async openApolloSearchModal() {
        if (!this.currentCustomerId) {
            console.error('No customer ID set');
            return;
        }
        this.apolloSearchModal.show();
        document.getElementById('apolloSearchInput').value = '';
        document.getElementById('apolloSearchResults').innerHTML = '';
    }

    async searchApollo() {
        if (!this.currentCustomerId) {
            console.error('No customer ID set');
            return;
        }

        const searchTerm = document.getElementById('apolloSearchInput').value;
        if (!searchTerm) return;

        try {
            const response = await fetch(`/api/customer-preview/${this.currentCustomerId}/apollo-search`, {
                method: 'POST',
                headers: {
                    'X-API-Key': API_KEY,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ q_organization_name: searchTerm })
            });

            if (!response.ok) throw new Error('Apollo search failed');
            const data = await response.json();
            this.updateApolloSearchResults(data.data.organizations);
        } catch (error) {
            console.error('Error searching Apollo:', error);
        }
    }

    updateApolloSearchResults(organizations) {
        const resultsContainer = document.getElementById('apolloSearchResults');
        const resultsHtml = organizations.map(org => `
            <div class="card mb-2">
                <div class="card-body">
                    <h6 class="card-title">${org.name}</h6>
                    <p class="card-text">
                        <small class="text-muted">
                            ${org.website ? `<a href="${org.website}" target="_blank">${org.website}</a><br>` : ''}
                            ${org.employee_count ? `Employees: ${org.employee_count}<br>` : ''}
                            ${org.country || ''}
                        </small>
                    </p>
                    <button class="btn btn-sm btn-primary match-apollo-btn"
                            data-apollo-id="${org.id}">
                        Select Match
                    </button>
                </div>
            </div>
        `).join('');

        resultsContainer.innerHTML = resultsHtml;

        resultsContainer.querySelectorAll('.match-apollo-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                const apolloId = e.target.dataset.apolloId;
                await this.matchApolloOrganization(apolloId);
            });
        });
    }

    async matchApolloOrganization(apolloId) {
        if (!this.currentCustomerId) {
            console.error('No customer ID set');
            return;
        }
        try {
            const response = await fetch(`/api/customer-preview/${this.currentCustomerId}/apollo_match`, {
                method: 'POST',
                headers: {
                    'X-API-Key': API_KEY,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ apollo_id: apolloId })
            });

            if (!response.ok) throw new Error('Failed to match Apollo organization');

            // Show success toast only when newly matched
            const toast = new bootstrap.Toast(createToast('Successfully matched with Apollo!'));
            toast.show();

            await this.loadCustomerData();
            this.apolloSearchModal.hide();
        } catch (error) {
            console.error('Error matching Apollo organization:', error);
        }
    }

    updateEmails(emails) {
        const emailsList = document.getElementById('emailsList');
        const emailsHtml = emails.map(email => `
            <div class="card mb-2">
                <div class="card-body py-2">
                    <h6 class="mb-1">${email.template_name}</h6>
                    <p class="mb-1"><small>${email.subject}</small></p>
                    <small class="text-muted">
                        Sent to ${email.contact_name} on
                        ${new Date(email.sent_at).toLocaleDateString()}
                    </small>
                </div>
            </div>
        `).join('');

        if (this.emailsPage === 1) {
            emailsList.innerHTML = emailsHtml;
        } else {
            emailsList.insertAdjacentHTML('beforeend', emailsHtml);
        }
    }

    async loadMoreEmails() {
        try {
            this.emailsPage++;
            const response = await fetch(`/api/customer-preview/${this.currentCustomerId}/emails?page=${this.emailsPage}`, {
                headers: { 'X-API-Key': API_KEY }
            });

            if (!response.ok) throw new Error('Failed to load more emails');
            const data = await response.json();
            this.updateEmails(data.data.items);

            const loadMoreBtn = document.getElementById('loadMoreEmails');
            loadMoreBtn.style.display = data.data.items.length === 10 ? 'inline-block' : 'none';
        } catch (error) {
            console.error('Error loading more emails:', error);
        }
    }

    showLoading() {
        console.log("Showing loading state");
        console.log("Loading element:", this.loadingElement);
        console.log("Content element:", this.contentElement);

        if (!this.loadingElement || !this.contentElement) {
            console.error("Required elements not found!");
            console.log("Current DOM:", document.body.innerHTML);
            return;
        }

        this.loadingElement.classList.remove('d-none');
        this.contentElement.classList.add('d-none');
        this.errorElement.classList.add('d-none');
    }

    hideLoading() {
        console.log("Hiding loading state");

        if (!this.loadingElement || !this.contentElement) {
            console.error("Required elements not found!");
            return;
        }

        this.loadingElement.classList.add('d-none');
        this.contentElement.classList.remove('d-none');

        console.log("Content element display style:", window.getComputedStyle(this.contentElement).display);
        console.log("Content element HTML:", this.contentElement.innerHTML);
    }

    showError(message) {
        this.errorElement.textContent = message;
        this.errorElement.classList.remove('d-none');
        this.loadingElement.classList.add('d-none');
        this.contentElement.classList.add('d-none');
    }

    async searchLeads(type = 'procurement') {
        // FIXED: Added validation to ensure currentCustomerId is set
        if (!this.currentCustomerId) {
            console.error('No customer ID set - cannot search leads');
            return;
        }

        console.log('Searching leads for customer:', this.currentCustomerId, 'type:', type);

        const leadsSection = document.getElementById('leadsSection');

        // Show loading state
        leadsSection.innerHTML = `
            <div class="text-center py-3">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
            </div>
        `;

        try {
            const response = await fetch(`/customers/${this.currentCustomerId}/leads?type=${type}`, {
                headers: { 'X-API-Key': API_KEY }
            });

            if (!response.ok) throw new Error('Failed to fetch leads');
            const data = await response.json();

            if (data.error) {
                throw new Error(data.error);
            }

            this.updateLeadsSection(data.leads, type);
        } catch (error) {
            console.error('Error searching leads:', error);
            leadsSection.innerHTML = `
                <div class="alert alert-danger">
                    Failed to load leads: ${error.message}
                </div>
            `;
        }
    }

    async enrichAndAddContact(leadId) {
        try {
            // First enrich the contact using existing endpoint
            const enrichResponse = await fetch('/customers/enrich_person', {
                method: 'POST',
                headers: {
                    'X-API-Key': API_KEY,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ apollo_id: leadId })
            });

            if (!enrichResponse.ok) throw new Error('Failed to enrich contact');
            const enrichData = await enrichResponse.json();

            if (!enrichData.success) {
                throw new Error(enrichData.error || 'Failed to enrich contact');
            }

            if (!enrichData.data.email) {
                throw new Error('No email address available for this contact');
            }

            // Then add the contact using your existing endpoint
            const addResponse = await fetch(`/customers/${this.currentCustomerId}/add_contact`, {
                method: 'POST',
                headers: {
                    'X-API-Key': API_KEY,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    name: enrichData.data.name,
                    email: enrichData.data.email,
                    job_title: enrichData.data.title
                })
            });

            if (!addResponse.ok) throw new Error('Failed to add contact');
            const addData = await addResponse.json();

            if (!addData.success) {
                throw new Error(addData.error || 'Failed to add contact');
            }

            // Refresh contacts list
            await this.loadCustomerData();

            // Show success message
            const toast = new bootstrap.Toast(createToast('Contact added successfully!'));
            toast.show();

        } catch (error) {
            console.error('Error adding contact:', error);
            alert(error.message);
        }
    }

    updateLeadsSection(leads, searchType) {
        const leadsSection = document.getElementById('leadsSection');

        if (!leads || leads.length === 0) {
            leadsSection.innerHTML = `
                <div class="alert alert-info">
                    <h6 class="alert-heading">No ${searchType} leads found</h6>
                    <p>${searchType === 'procurement' ?
                        'No procurement professionals found. Try searching for senior contacts instead.' :
                        'No senior contacts found. Try searching for procurement professionals instead.'}
                    </p>
                </div>
            `;
            return;
        }

        leadsSection.innerHTML = `
            <div class="list-group">
                ${leads.map(lead => `
                    <div class="list-group-item">
                        <div class="d-flex justify-content-between align-items-start">
                            <div class="flex-grow-1">
                                <h6 class="mb-1">${lead.name}</h6>
                                <p class="mb-1">
                                    ${lead.title || 'No title'}
                                    ${lead.seniority ?
                                        `<span class="badge bg-secondary ms-2">${lead.seniority}</span>` :
                                        ''}
                                </p>
                                <div class="small text-muted">
                                    ${lead.department ?
                                        `<span class="me-3"><i class="bi bi-diagram-2"></i> ${lead.department}</span>` :
                                        ''}
                                    ${lead.email_status ?
                                        `<span class="me-3"><i class="bi bi-envelope"></i> ${lead.email_status}</span>` :
                                        ''}
                                    ${lead.city ?
                                        `<span><i class="bi bi-geo-alt"></i> ${lead.city}${lead.state ? `, ${lead.state}` : ''}</span>` :
                                        ''}
                                </div>
                                ${lead.linkedin_url ? `
                                    <a href="${lead.linkedin_url}" target="_blank" class="btn btn-link btn-sm px-0">
                                        <i class="bi bi-linkedin"></i> LinkedIn Profile
                                    </a>
                                ` : ''}
                            </div>
                            <button class="btn btn-sm btn-success ms-2"
                                    onclick="customerPreview.enrichAndAddContact('${lead.id}')">
                                <i class="bi bi-plus-circle"></i> Add Contact
                            </button>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
    }

    // ADDED: Missing removeAssignedTag method
    async removeAssignedTag(tagId) {
        const tagElement = document.getElementById(`assignedTag-${tagId}`);
        if (tagElement) {
            tagElement.remove();
        }

        // Also remove from database
        try {
            const response = await fetch(`/api/customer/${this.currentCustomerId}/remove-tag`, {
                method: 'POST',
                headers: {
                    'X-API-Key': API_KEY,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ tag_id: tagId })
            });

            const data = await response.json();
            if (!response.ok || !data.success) {
                throw new Error(data.error || 'Failed to remove tag');
            }

            console.log('Tag removed successfully');
        } catch (error) {
            console.error('Error removing tag:', error);
            // Optionally reload the tags if removal fails
            this.loadCustomerData();
        }
    }
}

// Initialize the modal when DOM is fully loaded
document.addEventListener('DOMContentLoaded', () => {
    try {
        customerPreviewInstance = new CustomerPreviewModal();

        // FIXED: Make it globally available with consistent naming
        window.customerPreview = customerPreviewInstance;
        window.customerPreviewInstance = customerPreviewInstance;

        console.log('CustomerPreviewModal initialized successfully');
    } catch (error) {
        console.error('Error initializing CustomerPreviewModal:', error);
    }
});

// Provide a global method to show the modal
window.showCustomerPreview = (customerId) => {
    if (customerPreviewInstance) {
        customerPreviewInstance.show(customerId);
    } else {
        console.error('CustomerPreviewModal not initialized');
    }
};

// FIXED: Update the tag search event listener
document.addEventListener('DOMContentLoaded', () => {
    const previewTagsSearch = document.getElementById('previewTagsSearch');
    if (previewTagsSearch) {
        previewTagsSearch.addEventListener('input', async (e) => {
            const searchTerm = e.target.value.trim();
            const customerId = customerPreviewInstance?.currentCustomerId;

            if (!searchTerm) {
                document.getElementById('previewTagsSearchResults').innerHTML = '<p class="text-muted">Start typing to find tags...</p>';
                return;
            }

            try {
                const response = await fetch(`/api/tags?search=${searchTerm}`, {
                    headers: { 'X-API-Key': API_KEY }
                });

                if (!response.ok) throw new Error('Failed to fetch tags');

                const data = await response.json();
                displaySearchedTags(data.data || []);
            } catch (error) {
                console.error("Error fetching tags:", error);
                document.getElementById('previewTagsSearchResults').innerHTML = '<p class="text-danger">Error loading tags.</p>';
            }
        });
    }
});

// FIXED: Update the displaySearchedTags function
function displaySearchedTags(tags) {
    const searchResultsContainer = document.getElementById('previewTagsSearchResults');
    searchResultsContainer.innerHTML = tags.length
        ? tags.map(tag => `
            <button class="btn btn-outline-primary btn-sm mb-1 tag-select-btn" data-tag-id="${tag.id}" data-tag-name="${tag.name}">
                ${tag.name} (${tag.customer_count || 0})
            </button>
        `).join('')
        : '<p class="text-muted">No tags found.</p>';

    // Add click event listeners for the tag buttons
    document.querySelectorAll('.tag-select-btn').forEach(button => {
        button.addEventListener('click', (e) => {
            const tagId = e.target.dataset.tagId;
            const tagName = e.target.dataset.tagName;
            addTagToAssignedList(tagId, tagName);
        });
    });
}

// FIXED: Update addTagToAssignedList function
async function addTagToAssignedList(tagId, tagName) {
    const assignedTagsContainer = document.getElementById('previewTagsList');
    if (!document.querySelector(`#assignedTag-${tagId}`)) {
        // Add the tag visually
        assignedTagsContainer.insertAdjacentHTML('beforeend', `
            <span id="assignedTag-${tagId}" class="badge bg-success me-1 mb-1">
                ${tagName}
                <button class="btn-close btn-sm ms-1" aria-label="Remove" onclick="customerPreview.removeAssignedTag(${tagId})"></button>
            </span>
        `);

        // Save the tag in the database
        try {
            const response = await fetch(`/api/customer/${customerPreviewInstance.currentCustomerId}/add-tag`, {
                method: 'POST',
                headers: {
                    'X-API-Key': API_KEY,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ tag_id: tagId })
            });

            const data = await response.json();
            if (!response.ok || !data.success) {
                throw new Error(data.error || 'Failed to add tag');
            }

            console.log('Tag added successfully:', tagName);
        } catch (error) {
            console.error('Error adding tag:', error);
            // Optionally, remove the tag visually if the API call fails
            const tagElement = document.getElementById(`assignedTag-${tagId}`);
            if (tagElement) {
                tagElement.remove();
            }
        }
    }
}

function createToast(message) {
    const toastContainer = document.querySelector('.toast-container') || (() => {
        const container = document.createElement('div');
        container.className = 'toast-container position-fixed bottom-0 end-0 p-3';
        document.body.appendChild(container);
        return container;
    })();

    const toastElement = document.createElement('div');
    toastElement.className = 'toast';
    toastElement.innerHTML = `
        <div class="toast-header">
            <strong class="me-auto">Notification</strong>
            <button type="button" class="btn-close" data-bs-dismiss="toast"></button>
        </div>
        <div class="toast-body">
            ${message}
        </div>
    `;

    toastContainer.appendChild(toastElement);
    return toastElement;
}

// Function to open the email modal
function openEmailModal(customerId, contactId) {
    // Get the existing modal instance or create it if needed
    const modalElement = document.getElementById('emailModal');
    const modal = bootstrap.Modal.getInstance(modalElement) || new bootstrap.Modal(modalElement);

    // Get the contact data from the page
    const contactElement = document.querySelector(`[data-contact-id="${contactId}"]`);
    if (!contactElement) {
        console.error('Contact element not found');
        return;
    }

    // Set up the data attributes for the modal
    modalElement.querySelector('.modal-dialog').dataset.customerId = customerId;
    modalElement.querySelector('.modal-dialog').dataset.contactId = contactId;

    // Reset the form state
    const templateSelect = modalElement.querySelector('#template_id');
    const previewSection = modalElement.querySelector('.preview-section');
    const previewBtn = modalElement.querySelector('.preview-btn');
    const sendBtn = modalElement.querySelector('.send-btn');

    // Reset selections and hide sections
    if (templateSelect) {
        templateSelect.value = '';
        if ($(templateSelect).data('select2')) {
            $(templateSelect).val(null).trigger('change');
        }
    }
    previewSection.classList.add('d-none');
    previewBtn.classList.add('d-none');
    sendBtn.classList.add('d-none');

    // Show the modal
    modal.show();

    // Initialize Select2 if it hasn't been already
    if ($(templateSelect).data('select2') === undefined) {
        $(templateSelect).select2({
            theme: 'bootstrap-5',
            dropdownParent: $('#emailModal'),
            width: '100%'
        });
    }
}

// Make the function globally available
window.openEmailModal = openEmailModal;

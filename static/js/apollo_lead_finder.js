// static/js/apollo_lead_finder.js

let apolloLeadFinderInstance = null;

class ApolloLeadFinderModal {
    constructor() {
        // Initialize modal references
        this.modal = new bootstrap.Modal(document.getElementById('apolloLeadFinderModal'));
        this.currentCustomerId = null;

        // Cache DOM elements
        this.errorElement = document.getElementById('apolloLeadFinderError');

        // Initialize modals
        this.apolloSearchModal = new bootstrap.Modal(document.getElementById('apolloCompanySearchModal'));
        this.manualEmailModal = new bootstrap.Modal(document.getElementById('manualEmailModal'));

        // Store modal element reference
        this.modalElement = document.getElementById('apolloLeadFinderModal');

        // Initialize all event listeners
        this.initializeEventListeners();

        // Add event listener for modal hidden
        this.modalElement.addEventListener('hidden.bs.modal', () => {
            this.currentCustomerId = null;
            // Clear data
            this.resetModal();
            // Destroy any tooltips
            const tooltips = bootstrap.Tooltip.getInstance(document.getElementById('apolloCompanyStatus'));
            if (tooltips) tooltips.dispose();
        });
    }

    initializeEventListeners() {
        // Apollo search button
        const searchApolloCompanyBtn = document.getElementById('searchApolloCompanyBtn');
        if (searchApolloCompanyBtn) {
            searchApolloCompanyBtn.addEventListener('click', () => this.openApolloSearchModal());
        }

        // Apollo search input
        const apolloCompanySearchInput = document.getElementById('apolloCompanySearchInput');
        if (apolloCompanySearchInput) {
            apolloCompanySearchInput.addEventListener('input', this.debounce(() => this.searchApolloCompanies(), 500));
        }

        // Manual email button
        const addManualEmailBtn = document.getElementById('addManualEmailBtn');
        if (addManualEmailBtn) {
            addManualEmailBtn.addEventListener('click', () => this.openManualEmailModal());
        }

        // Save manual email button
        const saveManualEmailBtn = document.getElementById('saveManualEmailBtn');
        if (saveManualEmailBtn) {
            saveManualEmailBtn.addEventListener('click', () => this.saveManualEmail());
        }

        // Event delegation for email buttons
        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('send-lead-email-btn')) {
                const contactId = e.target.dataset.contactId;
                openEmailModal(this.currentCustomerId, contactId);
            }
        });
    }

    debounce(func, wait) {
        let timeout;
        return (...args) => {
            clearTimeout(timeout);
            timeout = setTimeout(() => func(...args), wait);
        };
    }

    resetModal() {
        // Reset customer info
        document.getElementById('leadFinderCustomerName').textContent = '-';
        document.getElementById('leadFinderCustomerCountry').textContent = '-';

        // Reset Apollo status
        const badge = document.getElementById('apolloCompanyStatus');
        badge.className = 'badge rounded-pill bg-secondary';
        badge.textContent = 'Checking...';

        // Clear contacts
        document.getElementById('leadFinderContactsList').innerHTML = `
            <div class="text-center text-muted py-3">
                <i class="bi bi-inbox" style="font-size: 2rem;"></i>
                <p class="mb-0">No contacts yet</p>
            </div>
        `;
        document.getElementById('contactsCount').textContent = '0';

        // Clear leads
        document.getElementById('apolloLeadsSection').innerHTML = `
            <div class="text-center text-muted py-5">
                <i class="bi bi-search" style="font-size: 3rem;"></i>
                <p class="mb-3">Click a button above to search for leads</p>
                <small class="text-muted">Match with Apollo first to enable lead search</small>
            </div>
        `;

        // Hide error
        this.errorElement.classList.add('d-none');
    }

    show(customerId) {
        this.currentCustomerId = customerId;
        this.resetModal();
        this.modal.show();

        // Wait for modal to be fully shown
        this.modalElement.addEventListener('shown.bs.modal', () => {
            console.log("Apollo Lead Finder Modal is fully shown");
            this.loadCustomerData();
        }, { once: true });
    }

    async loadCustomerData() {
        try {
            console.log("Starting loadCustomerData for ID:", this.currentCustomerId);

            const response = await fetch(`/api/customer-preview/${this.currentCustomerId}`, {
                headers: { 'X-API-Key': API_KEY }
            });

            console.log("Response status:", response.status);

            if (!response.ok) {
                const errorText = await response.text();
                console.error("Error response:", errorText);
                throw new Error('Failed to load customer data');
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
        const { customer, contacts, apollo_match } = data;

        console.log("Customer object:", customer);
        console.log("Customer name:", customer.name);

        document.getElementById('leadFinderCustomerName').textContent = customer.name || '-';
        document.getElementById('leadFinderCustomerCountry').textContent = customer.country || '-';

        if (contacts?.items) {
            this.updateContacts(contacts.items);
        }

        this.updateApolloMatch(apollo_match);
    }

    updateContacts(contacts) {
        const contactsList = document.getElementById('leadFinderContactsList');
        const contactsCount = document.getElementById('contactsCount');

        contactsCount.textContent = contacts.length;

        if (contacts.length === 0) {
            contactsList.innerHTML = `
                <div class="text-center text-muted py-3">
                    <i class="bi bi-inbox" style="font-size: 2rem;"></i>
                    <p class="mb-0">No contacts yet</p>
                </div>
            `;
            return;
        }

        const contactsHtml = contacts.map(contact => `
            <div class="card mb-2">
                <div class="card-body py-2">
                    <div class="row align-items-center">
                        <div class="col">
                            <h6 class="mb-0">${contact.name}</h6>
                            <small class="text-muted">
                                ${contact.job_title || 'No title'}<br>
                                ${contact.email}
                            </small>
                        </div>
                        <div class="col-auto">
                            <button class="btn btn-sm btn-outline-primary send-lead-email-btn"
                                    data-contact-id="${contact.id}">
                                <i class="bi bi-envelope"></i>
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `).join('');

        contactsList.innerHTML = contactsHtml;
    }

   // In updateApolloMatch method, update the section where buttons are disabled:
updateApolloMatch(apolloData) {
    const badge = document.getElementById('apolloCompanyStatus');
    const searchBtn = document.getElementById('searchApolloCompanyBtn');
    const procurementBtn = document.getElementById('findProcurementLeadsBtn');
    const seniorBtn = document.getElementById('findSeniorLeadsBtn');
    const allLeadsBtn = document.getElementById('findAllLeadsBtn');

    // Remove any existing tooltips
    const existingTooltip = bootstrap.Tooltip.getInstance(badge);
    if (existingTooltip) {
        existingTooltip.dispose();
    }

    if (!apolloData) {
        // Unmatched state
        badge.className = 'badge rounded-pill bg-secondary';
        badge.textContent = 'Not Matched';
        searchBtn.innerHTML = '<i class="bi bi-search"></i> Match Company';

        // Disable lead search buttons
        if (procurementBtn) procurementBtn.disabled = true;
        if (seniorBtn) seniorBtn.disabled = true;
        if (allLeadsBtn) allLeadsBtn.disabled = true;

        // Update lead search message
        document.getElementById('apolloLeadsSection').innerHTML = `
            <div class="text-center text-muted py-5">
                <i class="bi bi-building" style="font-size: 3rem;"></i>
                <p class="mb-3">Apollo company match required</p>
                <small class="text-muted">Click "Match Company" to link this customer with Apollo</small>
            </div>
        `;
        return;
    }

    // Matched state
    badge.className = 'badge rounded-pill bg-success';
    badge.textContent = 'Matched';
    searchBtn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Change Company';

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

    // Enable lead search buttons
    if (procurementBtn) procurementBtn.disabled = false;
    if (seniorBtn) seniorBtn.disabled = false;
    if (allLeadsBtn) allLeadsBtn.disabled = false;

    // Update lead search message
    document.getElementById('apolloLeadsSection').innerHTML = `
        <div class="text-center text-muted py-5">
            <i class="bi bi-search" style="font-size: 3rem;"></i>
            <p class="mb-3">Ready to search for leads</p>
            <small class="text-muted">Use the buttons above to find procurement, senior, or all contacts</small>
        </div>
    `;
}

    async openApolloSearchModal() {
        if (!this.currentCustomerId) {
            console.error('No customer ID set');
            return;
        }
        this.apolloSearchModal.show();
        document.getElementById('apolloCompanySearchInput').value = '';
        document.getElementById('apolloCompanySearchResults').innerHTML = '';
    }

    async searchApolloCompanies() {
        if (!this.currentCustomerId) {
            console.error('No customer ID set');
            return;
        }

        const searchTerm = document.getElementById('apolloCompanySearchInput').value;
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
        const resultsContainer = document.getElementById('apolloCompanySearchResults');

        if (organizations.length === 0) {
            resultsContainer.innerHTML = `
                <div class="alert alert-info">
                    No companies found. Try a different search term.
                </div>
            `;
            return;
        }

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
                    <button class="btn btn-sm btn-primary match-apollo-company-btn"
                            data-apollo-id="${org.id}">
                        Select Company
                    </button>
                </div>
            </div>
        `).join('');

        resultsContainer.innerHTML = resultsHtml;

        resultsContainer.querySelectorAll('.match-apollo-company-btn').forEach(btn => {
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

            // Show success toast
            const toast = new bootstrap.Toast(this.createToast('Successfully matched with Apollo!'));
            toast.show();

            await this.loadCustomerData();
            this.apolloSearchModal.hide();
        } catch (error) {
            console.error('Error matching Apollo organization:', error);
        }
    }

    async searchLeads(type = 'procurement') {
        if (!this.currentCustomerId) {
            console.error('No customer ID set - cannot search leads');
            return;
        }

        console.log('Searching leads for customer:', this.currentCustomerId, 'type:', type);

        const leadsSection = document.getElementById('apolloLeadsSection');

        // Show loading state
        leadsSection.innerHTML = `
            <div class="text-center py-3">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
                <p class="mt-2 text-muted">Finding ${type} leads...</p>
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
                    <i class="bi bi-exclamation-triangle"></i>
                    Failed to load leads: ${error.message}
                </div>
            `;
        }
    }

  updateLeadsSection(leads, searchType) {
    const leadsSection = document.getElementById('apolloLeadsSection');

    if (!leads || leads.length === 0) {
        const messages = {
            'procurement': 'No procurement leads found',
            'general': 'No senior contacts found',
            'all': 'No leads found at this company'
        };

        const suggestions = {
            'procurement': 'Try searching for senior contacts or all leads instead',
            'general': 'Try searching for procurement professionals or all leads instead',
            'all': 'This company may not have detailed contact information available'
        };

        leadsSection.innerHTML = `
            <div class="text-center text-muted py-5">
                <i class="bi bi-person-x" style="font-size: 3rem;"></i>
                <p class="mb-3">${messages[searchType] || 'No leads found'}</p>
                <small class="text-muted">${suggestions[searchType] || ''}</small>
            </div>
        `;
        return;
    }

    const typeLabels = {
        'procurement': 'Procurement',
        'general': 'Senior',
        'all': 'All'
    };

    leadsSection.innerHTML = `
        <div class="mb-2">
            <span class="badge bg-info">${typeLabels[searchType] || 'Search'} Results: ${leads.length} leads</span>
        </div>
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
                                    <i class="bi bi-linkedin"></i> LinkedIn
                                </a>
                            ` : ''}
                        </div>
                        <div class="d-flex flex-column gap-1">
                            <button class="btn btn-sm btn-success"
                                    onclick="apolloLeadFinder.enrichAndAddContact('${lead.id}')">
                                <i class="bi bi-plus-circle"></i> Add
                            </button>
                        </div>
                    </div>
                </div>
            `).join('')}
        </div>
    `;
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
            const addResponse = await fetch('/customers/contacts/add', {
                method: 'POST',
                headers: {
                    'X-API-Key': API_KEY,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    customer_id: this.currentCustomerId,
                    name: enrichData.data.name,
                    email: enrichData.data.email,
                    job_title: enrichData.data.title || '',
                    phone: enrichData.data.phone || '',
                    second_name: enrichData.data.last_name || '',
                    status_id: null
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
            const toast = new bootstrap.Toast(this.createToast('Contact added successfully!'));
            toast.show();

        } catch (error) {
            console.error('Error adding contact:', error);
            alert(error.message);
        }
    }

    openManualEmailModal() {
        const manualEmailForm = document.getElementById('manualEmailForm');
        manualEmailForm.reset();
        this.manualEmailModal.show();
    }

    async saveManualEmail() {
        const name = document.getElementById('manualEmailName').value.trim();
        const email = document.getElementById('manualEmailAddress').value.trim();
        const jobTitle = document.getElementById('manualEmailJobTitle').value.trim();

        if (!name || !email) {
            alert('Name and email are required');
            return;
        }

        try {
            const response = await fetch('/customers/contacts/add', {
                method: 'POST',
                headers: {
                    'X-API-Key': API_KEY,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    customer_id: this.currentCustomerId,
                    name: name,
                    email: email,
                    job_title: jobTitle || '',
                    phone: '',
                    second_name: '',
                    status_id: null
                })
            });

            if (!response.ok) throw new Error('Failed to add contact');
            const data = await response.json();

            if (!data.success) {
                throw new Error(data.error || 'Failed to add contact');
            }

            // Refresh contacts list
            await this.loadCustomerData();

            // Show success message
            const toast = new bootstrap.Toast(this.createToast('Contact added successfully!'));
            toast.show();

            this.manualEmailModal.hide();

        } catch (error) {
            console.error('Error adding manual contact:', error);
            alert(error.message);
        }
    }

    showError(message) {
        this.errorElement.textContent = message;
        this.errorElement.classList.remove('d-none');
    }

    createToast(message) {
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
}

// Initialize the modal when DOM is fully loaded
document.addEventListener('DOMContentLoaded', () => {
    const t0 = performance.now();
    try {
        apolloLeadFinderInstance = new ApolloLeadFinderModal();

        // Make it globally available
        window.apolloLeadFinder = apolloLeadFinderInstance;
        window.apolloLeadFinderInstance = apolloLeadFinderInstance;

        console.log('Apollo Lead Finder Modal initialized successfully');
    } catch (error) {
        console.error('Error initializing Apollo Lead Finder Modal:', error);
    }
    const t1 = performance.now();
    console.log(`init.apollo_lead_finder ${Math.round(t1 - t0)}ms`);
});

// Provide a global method to show the modal
window.showApolloLeadFinder = (customerId) => {
    if (apolloLeadFinderInstance) {
        apolloLeadFinderInstance.show(customerId);
    } else {
        console.error('Apollo Lead Finder Modal not initialized');
    }
};

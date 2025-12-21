// static/js/tag_modal.js
const TagModal = {
    modal: null,
    successCallback: null,

    init: function(successCallback) {
        const modalElement = document.getElementById('createTagModal');
        if (!modalElement) {
            console.warn('createTagModal element not found, cannot initialize');
            return;
        }

        try {
            this.modal = new bootstrap.Modal(modalElement);
            this.successCallback = successCallback;

            // Reset form when modal is closed
            modalElement.addEventListener('hidden.bs.modal', () => {
                const form = document.getElementById('createTagForm');
                if (form) form.reset();

                const submitButton = document.querySelector('#createTagModal .btn-primary');
                if (submitButton) {
                    submitButton.disabled = false;
                    submitButton.innerHTML = 'Create Tag';
                }
            });
        } catch (error) {
            console.warn('Error initializing tag modal:', error);
        }
    },

    show: function() {
        if (this.modal) {
            this.modal.show();
        } else {
            console.warn('Cannot show tag modal - not initialized');
        }
    },

    hide: function() {
        if (this.modal) {
            this.modal.hide();
        } else {
            console.warn('Cannot hide tag modal - not initialized');
        }
    },

// static/js/tag_modal.js
create: function() {
    const tagName = document.getElementById('tagname').value.trim();
    const description = document.getElementById('tagDescription').value.trim();
    const parentTagId = document.getElementById('parentTagId').value.trim();

    console.log('Form values before send:', {
        tagName,
        description,
        parentTagId
    });

    const data = {
        tag: tagName,
        description: description || null,
        parent_tag_id: parentTagId ? parseInt(parentTagId) : null
    };

    console.log('Data being sent:', data);

    if (!tagName) {
        alert('Tag name is required');
        document.getElementById('tagname').focus();
        return;
    }

    const submitButton = document.querySelector('#createTagModal .btn-primary');
    submitButton.disabled = true;
    submitButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Creating...';

    fetch('/customers/api/tags', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            tag: tagName,
            description: description || null,
            parent_tag_id: parentTagId ? parseInt(parentTagId) : null
        })
    })
    .then(response => {
        if (!response.ok) {
            return response.text().then(text => {
                // Try to parse as JSON, if not, use text as error message
                try {
                    const json = JSON.parse(text);
                    throw new Error(json.error || 'Failed to create tag');
                } catch (e) {
                    throw new Error(`Server error: ${text.substring(0, 100)}`);
                }
            });
        }
        return response.json();
    })
    .then(data => {
        this.hide();
        document.getElementById('createTagForm').reset();
        if (this.successCallback) {
            this.successCallback(data);
        }
    })
    .catch(error => {
        console.error('Full error:', error);
        alert('Error creating tag: ' + error.message);
    })
    .finally(() => {
        submitButton.disabled = false;
        submitButton.innerHTML = 'Create Tag';
    });
}
};
class RichTextModal {
    constructor() {
        // Store references to DOM elements and state
        this.modalElement = null;
        this.modal = null;
        this.editor = null;
        this.config = {
            onSave: null,
            contentType: null,
            id: null
        };

        // Initialize when DOM is ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => this.initialize());
        } else {
            this.initialize();
        }
    }

    initialize() {
        // Get modal element
        this.modalElement = document.getElementById('richTextModal');
        if (!this.modalElement) {
            console.error('Modal element not found');
            return;
        }

        // Initialize Bootstrap modal
        try {
            this.modal = new bootstrap.Modal(this.modalElement);
        } catch (error) {
            console.error('Error initializing Bootstrap modal:', error);
            return;
        }

        // Add modal show listener
        this.modalElement.addEventListener('shown.bs.modal', () => {
            if (!this.editor) {
                this.initEditor();
            }
            // Set pending content if any
            if (this.pendingContent && this.editor) {
                this.editor.commands.setContent(this.pendingContent);
                this.pendingContent = null;
            }
        });

        // Add save button listener
        const saveButton = document.getElementById('saveRichText');
        if (saveButton) {
            saveButton.addEventListener('click', () => this.handleSave());
        } else {
            console.error('Save button not found');
        }
    }

    initEditor() {
        // Check if TipTap is loaded
        if (typeof tiptap === 'undefined') {
            console.error('TipTap not loaded. Make sure to include TipTap scripts before initializing the modal.');
            return;
        }

        const editorElement = document.querySelector('#tiptap-editor');
        if (!editorElement) {
            console.error('Editor element not found');
            return;
        }

        try {
            this.editor = new tiptap.Editor({
                element: editorElement,
                extensions: [
                    tiptap.StarterKit,
                    tiptap.TaskList,
                    tiptap.TaskItem.configure({
                        nested: true
                    })
                ],
                content: this.pendingContent || '',
                editable: true
            });
        } catch (error) {
            console.error('Error initializing TipTap editor:', error);
        }
    }

    open({ contentType, id, content = '', onSave }) {
        // Store configuration
        this.config.contentType = contentType;
        this.config.id = id;
        this.config.onSave = onSave;
        this.pendingContent = content;

        // Show modal
        if (this.modal) {
            this.modal.show();
        } else {
            console.error('Modal not initialized');
        }
    }

    handleSave() {
        if (this.editor && this.config.onSave) {
            const content = this.editor.getHTML();
            this.config.onSave(content);
            this.modal.hide();
        }
    }

    // Add cleanup method
    destroy() {
        if (this.editor) {
            this.editor.destroy();
        }
        if (this.modal) {
            this.modal.dispose();
        }
    }
}

// Initialize as global only after DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.richTextModal = new RichTextModal();
});
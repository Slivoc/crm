// ILS Upload Module - Can be used on any page
(function() {
    window.ILSUpload = {
        init: function(options) {
            const config = {
                buttonId: options.buttonId || 'ils-upload-btn',
                inputId: options.inputId || 'ils-file-input-compact',
                statsId: options.statsId || 'ils-stats-compact',
                onSuccess: options.onSuccess || null,
                onError: options.onError || null,
                showStats: options.showStats !== false // default true
            };

            const button = document.getElementById(config.buttonId);
            const input = document.getElementById(config.inputId);
            const statsDiv = document.getElementById(config.statsId);

            if (!button || !input) {
                console.error('ILS Upload: Required elements not found');
                return;
            }

            // Click button to trigger file input
            button.addEventListener('click', () => input.click());

            // Handle file selection
            input.addEventListener('change', (e) => {
                const file = e.target.files[0];
                if (file) {
                    this.uploadFile(file, config, statsDiv);
                }
            });
        },

        uploadFile: function(file, config, statsDiv) {
            if (!file.name.endsWith('.csv')) {
                alert('Please upload a CSV file');
                return;
            }

            const formData = new FormData();
            formData.append('file', file);

            // Show loading state
            const button = document.getElementById(config.buttonId);
            const originalHtml = button.innerHTML;
            button.disabled = true;
            button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Uploading...';

            fetch('/ils/upload', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                button.disabled = false;
                button.innerHTML = originalHtml;

                if (data.success) {
                    // Show success stats if enabled
                    if (config.showStats && statsDiv) {
                        const statsText = `${data.stats.total_records} records, ${data.stats.unique_parts} parts, ${data.stats.unique_suppliers} suppliers`;
                        statsDiv.querySelector('.ils-stats-text').textContent = statsText;
                        statsDiv.style.display = 'block';
                    }

                    // Call success callback if provided
                    if (config.onSuccess) {
                        config.onSuccess(data);
                    } else {
                        this.showToast(data.message || 'ILS data uploaded successfully', 'success');
                    }
                } else {
                    if (config.onError) {
                        config.onError(data);
                    } else {
                        alert('Error uploading ILS file: ' + data.error);
                    }
                }
            })
            .catch(error => {
                button.disabled = false;
                button.innerHTML = originalHtml;
                console.error('Error:', error);

                if (config.onError) {
                    config.onError(error);
                } else {
                    alert('Error uploading ILS file: ' + error.message);
                }
            });
        },

        showToast: function(message, type) {
            // Reuse the showToast function if it exists, otherwise create a simple alert
            if (typeof showToast === 'function') {
                showToast(message, type);
            } else {
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
        }
    };
})();
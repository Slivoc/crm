// manufacturer.js

function setupAutocomplete(input) {
    $(input).autocomplete({
        source: '/manufacturers/lookup',
        minLength: 2,
        appendTo: document.body,
        open: function(event, ui) {
            console.log('Menu opened');
        },
        search: function(event, ui) {
            console.log('Searching for:', $(this).val());
        },
        response: function(event, ui) {
            console.log('Got response:', ui.content);
        },
        select: function(event, ui) {
            console.log('Selected:', ui.item);
            const input = $(this);
            const lineId = input.data('line-id');

            if (lineId) {
                updateField(lineId, 'manufacturer_name', ui.item.name)
                    .then(response => {
                        if (response.success) {
                            input.val(ui.item.name);
                            input.removeClass('is-invalid').addClass('is-valid');
                        } else {
                            input.removeClass('is-valid').addClass('is-invalid');
                        }
                    });
            }
        }
    }).data("ui-autocomplete")._renderItem = function(ul, item) {
        return $("<li>")
            .append("<div>" + item.name + "</div>")
            .appendTo(ul);
    };
}

function initializeManufacturerAutocomplete() {
    console.log('Finding manufacturer inputs...');
    const inputs = $('.manufacturer-autocomplete');
    console.log('Found', inputs.length, 'manufacturer inputs');

    inputs.each(function() {
        console.log('Initializing autocomplete for input:', this);
       $(this).autocomplete({
    source: '/manufacturers/lookup',
    minLength: 2,
    select: function(event, ui) {
        console.log('Selected:', ui.item);
        const input = $(this);
        const lineId = input.data('line-id');
        console.log('LineId:', lineId);

        input.val(ui.item ? ui.item.name : input.val());

        if (lineId) {
            updateField(lineId, 'manufacturer_name', ui.item ? ui.item.name : input.val())
                .then(response => {
                    console.log('Update response:', response);
                    if (response && response.success) {
                        input.removeClass('is-invalid').addClass('is-valid');
                    } else {
                        input.removeClass('is-valid').addClass('is-invalid');
                        console.error('Update failed:', response);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    input.removeClass('is-valid').addClass('is-invalid');
                });
        }
        return false;
    },
    change: function(event, ui) {
        const input = $(this);
        const lineId = input.data('line-id');
        const value = input.val().trim();

        if (!ui.item && value && lineId) {
            // No match was selected but user entered a value
            if (confirm(`Create new manufacturer "${value}"?`)) {
                updateField(lineId, 'manufacturer_name', value)
                    .then(response => {
                        console.log('Update response:', response);
                        if (response && response.success) {
                            input.removeClass('is-invalid').addClass('is-valid');
                        } else {
                            input.removeClass('is-valid').addClass('is-invalid');
                            console.error('Update failed:', response);
                        }
                    })
                    .catch(error => {
                        console.error('Error:', error);
                        input.removeClass('is-valid').addClass('is-invalid');
                    });
            } else {
                input.val('');
            }
        }

                // Prevent default to handle the value update manually
                return false;
            }
        });
    });
}
// Call on document ready
$(document).ready(function() {
    const t0 = performance.now();
    if (console.time) {
        console.time('init.manufacturer');
    }
    console.log('Document ready, initializing manufacturer autocomplete');
    initializeManufacturerAutocomplete();
    if (console.timeEnd) {
        console.timeEnd('init.manufacturer');
    }
    console.log(`init.manufacturer ${Math.round(performance.now() - t0)}ms`);
});

// Initialize for any new manufacturer inputs that are added to the page
$(document).on('focus', '.manufacturer-autocomplete', function() {
    console.log('Input focused:', this);
    if (!$(this).data('ui-autocomplete')) {
        console.log('Initializing autocomplete for new input');
        setupAutocomplete(this);
    }
});

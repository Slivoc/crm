# Supplier quote extraction sources

Supplier quote extraction keeps both structured data and durable source evidence.

- PDF extraction stores the exact uploaded PDF.
- Email-body/text extraction stores the exact text submitted to the extractor as a UTF-8 text file.
- Microsoft Graph message and conversation IDs remain on the quote when available, but are not the only copy.
- Source files live under `uploads/supplier_quote_sources/`; quote headers store a portable file key plus filename, MIME type, size, kind, and SHA-256 hash.
- Saved sources can be downloaded from the supplier quote list.
- The upload limit is 20 MB per source.

The `uploads/supplier_quote_sources/` directory must be included in VPS backups. Files created by an extraction that is abandoned before the quote is saved are not linked to a database row; a future housekeeping job can remove old unreferenced files after an agreed retention period.

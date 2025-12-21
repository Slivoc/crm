import os
import time
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from models import get_db  # ✅

class ExcelFileHandler(FileSystemEventHandler):
    """Detects new Excel files and triggers the import process automatically."""

    def __init__(self, mapping_id):
        super().__init__()
        self.mapping_id = mapping_id  # Store mapping ID

    def on_created(self, event):
        """Triggered when a new Excel file is created."""
        if event.is_directory:
            return

        filename = event.src_path

        # 🔹 Ignore temporary Excel files (~$filename.xlsx)
        if filename.endswith(('.xls', '.xlsx')) and not os.path.basename(filename).startswith("~$"):
            print(f"New Excel file detected: {filename}")

            with get_db() as db:
                cursor = db.execute("INSERT INTO files (filepath) VALUES (?)", (filename,))
                db.commit()
                file_id = cursor.lastrowid

            from routes.handson import run_full_import  # Delayed import to prevent circular import
            run_full_import(file_id, self.mapping_id)  # Use stored mapping_id
        else:
            print(f"Skipping temporary file: {filename}")  # Debugging log


def start_folder_watcher(directory, mapping_id):
    """Starts a background watcher that detects new Excel files."""
    event_handler = ExcelFileHandler(mapping_id)  # Pass mapping_id to the handler
    observer = Observer()
    observer.schedule(event_handler, directory, recursive=False)
    observer.start()
    print(f"Watching directory: {directory} for new Excel files with mapping {mapping_id}...")

    try:
        while True:
            time.sleep(10)  # Keep running
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


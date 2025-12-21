import os
import imaplib
import logging
import csv
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# CSV Output Files
VALID_EMAILS_CSV = "imap_email_dump.csv"
ERROR_LOG_CSV = "imap_email_errors.csv"

def dump_imap_emails():
    """
    Fetches and logs Message-IDs from the IMAP server.
    Saves valid Message-IDs to a CSV file and errors to a separate log file.
    """
    email_host = os.getenv("EMAIL_HOST")
    email_port = int(os.getenv("EMAIL_PORT", 993))
    email_user = os.getenv("EMAIL_USER")
    email_password = os.getenv("EMAIL_PASSWORD")

    if not all([email_host, email_user, email_password]):
        logger.error("Missing IMAP credentials.")
        return

    mail = imaplib.IMAP4_SSL(email_host, email_port)
    mail.login(email_user, email_password)

    # Open CSV files
    with open(VALID_EMAILS_CSV, mode="w", newline="", encoding="utf-8") as valid_csv, \
         open(ERROR_LOG_CSV, mode="w", newline="", encoding="utf-8") as error_csv:
        
        valid_writer = csv.writer(valid_csv)
        error_writer = csv.writer(error_csv)
        
        # CSV Headers
        valid_writer.writerow(["Message-ID", "Folder", "Email ID"])
        error_writer.writerow(["Folder", "Email ID", "Error Message"])

        unique_messages = set()  # Store seen Message-IDs to avoid duplicates

        try:
            status, folders = mail.list()
            if status != "OK":
                logger.error("Failed to retrieve folders.")
                return

            for folder in folders:
                folder_decoded = folder.decode().split(' "." ')[-1].strip('"')
                logger.info(f"Checking folder: {folder_decoded}")

                try:
                    status, _ = mail.select(folder_decoded, readonly=True)
                    if status != "OK":
                        logger.warning(f"Could not select folder: {folder_decoded} (IMAP error)")
                        error_writer.writerow([folder_decoded, "N/A", "Could not select folder"])
                        continue

                    status, messages = mail.search(None, "ALL")
                    if status != "OK":
                        logger.warning(f"No emails found in folder: {folder_decoded}")
                        error_writer.writerow([folder_decoded, "N/A", "No emails found"])
                        continue

                    email_ids = messages[0].split()
                    logger.info(f"Found {len(email_ids)} emails in folder {folder_decoded}")

                    for email_id in email_ids:
                        # Fetch Message-ID header
                        status, msg_data = mail.fetch(email_id, "(BODY[HEADER.FIELDS (MESSAGE-ID)])")
                        if status != "OK" or not msg_data or not msg_data[0]:
                            logger.warning(f"🚨 Missing Message-ID for email {email_id} in folder {folder_decoded}")
                            error_writer.writerow([folder_decoded, email_id.decode(), "Missing Message-ID"])
                            continue

                        try:
                            # Extract Message-ID
                            msg_id = msg_data[0][1].decode().strip()
                            if not msg_id:
                                raise ValueError("Empty Message-ID")

                            logger.info(f"✅ Message-ID {msg_id} found for email {email_id}")

                            # Skip duplicates
                            if msg_id in unique_messages:
                                logger.info(f"⏩ Skipping duplicate Message-ID: {msg_id}")
                                continue

                            unique_messages.add(msg_id)

                            # Save to CSV
                            valid_writer.writerow([msg_id, folder_decoded, email_id.decode()])

                        except Exception as e:
                            logger.error(f"Error parsing Message-ID for email {email_id}: {e}")
                            error_writer.writerow([folder_decoded, email_id.decode(), str(e)])

                except imaplib.IMAP4.error as e:
                    logger.error(f"⚠️ Skipping folder '{folder_decoded}' due to IMAP error: {e}")
                    error_writer.writerow([folder_decoded, "N/A", str(e)])
                    continue

        finally:
            mail.logout()

    logger.info(f"✅ Email dump complete. Data saved to: {VALID_EMAILS_CSV} and errors logged in {ERROR_LOG_CSV}")

if __name__ == "__main__":
    dump_imap_emails()

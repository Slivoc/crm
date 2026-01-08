from flask import Blueprint, jsonify, request, session
from flask_login import current_user, login_required
from models import get_db_connection
import logging
import json
import os

notifications_bp = Blueprint('notifications', __name__, url_prefix='/api/notifications')


def _using_postgres():
    """Check if we're using PostgreSQL based on DATABASE_URL."""
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def create_notification(user_id, notification_type, title, message, link_url=None, link_text=None, metadata=None):
    """
    Create a notification for a user.

    Args:
        user_id: ID of the user to notify
        notification_type: Type of notification (e.g., 'scrape_complete', 'import_complete')
        title: Short title for the notification
        message: Full message text
        link_url: Optional URL to link to
        link_text: Optional text for the link button
        metadata: Optional dict of additional data (will be stored as JSON)

    Returns:
        notification_id: ID of the created notification
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        metadata_json = json.dumps(metadata) if metadata else None

        if _using_postgres():
            cur.execute("""
                INSERT INTO user_notifications
                (user_id, notification_type, title, message, link_url, link_text, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                RETURNING id
            """, (user_id, notification_type, title, message, link_url, link_text, metadata_json))
            row = cur.fetchone()
            notification_id = row['id'] if row else None
        else:
            cur.execute("""
                INSERT INTO user_notifications
                (user_id, notification_type, title, message, link_url, link_text, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, notification_type, title, message, link_url, link_text, metadata_json))
            notification_id = cur.lastrowid

        conn.commit()
        conn.close()

        logging.info(f"Created notification {notification_id} for user {user_id}: {title}")
        return notification_id

    except Exception as e:
        logging.exception(f"Failed to create notification: {e}")
        return None


@notifications_bp.route('/unread', methods=['GET'])
@login_required
def get_unread_notifications():
    """
    Get all unread notifications for the current user.
    Returns notifications created in the last 24 hours that haven't been read.
    """
    try:
        user_id = current_user.id if getattr(current_user, 'is_authenticated', False) else session.get('user_id')

        if not user_id:
            return jsonify(success=False, message="User not authenticated"), 401

        conn = get_db_connection()
        cur = conn.cursor()

        # Get unread notifications from the last 24 hours
        if _using_postgres():
            date_filter = "created_at > CURRENT_TIMESTAMP - INTERVAL '24 hours'"
        else:
            date_filter = "created_at > datetime('now', '-24 hours')"

        cur.execute(f"""
            SELECT
                id,
                notification_type,
                title,
                message,
                link_url,
                link_text,
                metadata,
                created_at
            FROM user_notifications
            WHERE user_id = ?
              AND is_read = FALSE
              AND {date_filter}
            ORDER BY created_at DESC
        """, (user_id,))

        notifications = []
        for row in cur.fetchall():
            notification = dict(row)
            # Parse metadata JSON if present
            if notification.get('metadata'):
                try:
                    notification['metadata'] = json.loads(notification['metadata'])
                except:
                    notification['metadata'] = None
            notifications.append(notification)

        conn.close()

        return jsonify(success=True, notifications=notifications)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@notifications_bp.route('/<int:notification_id>/mark-read', methods=['POST'])
@login_required
def mark_notification_read(notification_id):
    """
    Mark a notification as read.
    """
    try:
        user_id = current_user.id if getattr(current_user, 'is_authenticated', False) else session.get('user_id')

        if not user_id:
            return jsonify(success=False, message="User not authenticated"), 401

        conn = get_db_connection()
        cur = conn.cursor()

        # Verify the notification belongs to this user and mark it as read
        cur.execute("""
            UPDATE user_notifications
            SET is_read = TRUE, read_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
        """, (notification_id, user_id))

        conn.commit()
        conn.close()

        return jsonify(success=True)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500


@notifications_bp.route('/mark-all-read', methods=['POST'])
@login_required
def mark_all_read():
    """
    Mark all unread notifications as read for the current user.
    """
    try:
        user_id = current_user.id if getattr(current_user, 'is_authenticated', False) else session.get('user_id')

        if not user_id:
            return jsonify(success=False, message="User not authenticated"), 401

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            UPDATE user_notifications
            SET is_read = TRUE, read_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND is_read = FALSE
        """, (user_id,))

        updated_count = cur.rowcount
        conn.commit()
        conn.close()

        return jsonify(success=True, marked_count=updated_count)

    except Exception as e:
        logging.exception(e)
        return jsonify(success=False, message=str(e)), 500

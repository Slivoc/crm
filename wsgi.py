#!/usr/bin/env python3
"""
WSGI entry point for the Flask application using Waitress.
This file is used by Waitress WSGI server on Windows.
"""

import sys
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add the application directory to the Python path
sys.path.insert(0, os.path.dirname(__file__))

# Import your Flask app
from app import app  # Replace 'your_main_file' with the actual name of your main Python file

# Create the WSGI application
application = app

if __name__ == "__main__":
    # Run with Waitress when executed directly
    from waitress import serve

    # Configuration
    host = '0.0.0.0'
    port = 8080

    print("🚀 Starting Flask application with Waitress...")
    print(f"🌐 Server running at: http://localhost:{port}")
    print(f"🔗 Network access: http://{host}:{port}")
    print("⏹️  Press Ctrl+C to stop the server")
    print("-" * 50)

    try:
        serve(
            application,
            host=host,
            port=port,
            threads=8,  # Good for your complex app
            connection_limit=500,  # Reasonable limit
            cleanup_interval=30,  # Clean up every 30 seconds
            channel_timeout=300,  # 5 minutes timeout
            log_untrusted_proxy_headers=False,  # Security
            clear_untrusted_proxy_headers=True,  # Security
            expose_tracebacks=False  # Don't expose errors in production
        )
    except KeyboardInterrupt:
        print("\n⏹️  Server stopped by user")
    except Exception as e:
        print(f"❌ Error starting server: {e}")
        input("Press Enter to exit...")
        sys.exit(1)
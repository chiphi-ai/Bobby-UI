"""
WSGI configuration for PythonAnywhere deployment.
This file should be placed at /var/www/yourusername_pythonanywhere_com_wsgi.py
(or similar, depending on your PythonAnywhere setup)

IMPORTANT: Change 'yourusername' below to YOUR actual PythonAnywhere username!
"""

import sys
import os

# Add your project directory to the path
# CHANGE THIS to match your PythonAnywhere file structure
path = '/home/yourusername/phiai'  # UPDATE: Replace 'yourusername' with your PA username
if path not in sys.path:
    sys.path.insert(0, path)

# Change to your project directory
os.chdir(path)

# Set up environment variables BEFORE importing your app
# These will be overridden by PythonAnywhere's Web tab environment variables
os.environ.setdefault('FLASK_APP', 'web_app')
os.environ.setdefault('BASE_URL', 'https://phiartificialintelligence.com')

# Import and run the app
# The 'application' variable is what PythonAnywhere's WSGI server looks for
from web_app import app as application

# If you need to run initialization code, do it here
# (ensure_dirs, init_users_csv, etc. should already be called in web_app.py's if __name__)

if __name__ == "__main__":
    application.run()

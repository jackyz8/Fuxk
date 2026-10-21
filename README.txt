GLORY HOST CLOUD — PREMIUM EDITION

SETUP
1. Extract this ZIP.
2. Open main.py and change ADMIN_USERNAME and ADMIN_PASSWORD.
3. Run: python3 main.py
4. Expose port 8080 in your cloud platform.
5. Use HTTPS reverse proxy and firewall/IP restrictions before public deployment.

FEATURES
- Premium responsive dark dashboard
- Upload, edit, rename, delete, download and create folders
- ZIP extraction with path traversal and expanded-size checks
- Python app start / stop / restart
- Live app stdout/stderr logs
- pip install -r requirements.txt as a background task with live output
- Admin command console with background execution and live logs
- Task list with auto-refreshing output
- Standard-library dashboard; no pip install needed for panel itself

IMPORTANT
Hosted scripts, pip package build steps, and console commands execute with the dashboard OS user's permissions. Only allow trusted administrators. Do not expose this panel directly to the public internet; use HTTPS, strong credentials, network restrictions, and preferably a separate unprivileged account/container. This is a lightweight self-hosting panel, not a hardened multi-user service.

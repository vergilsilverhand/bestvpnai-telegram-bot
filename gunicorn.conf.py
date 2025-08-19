# Gunicorn configuration for OpenWebUI Telegram Bot

import os

# Server socket
bind = f"0.0.0.0:{os.environ.get('PORT', 5000)}"
backlog = 2048

# Worker processes
workers = 1
worker_class = "sync"
worker_connections = 1000
timeout = 600  # 10 minutes for long AI processing
keepalive = 2

# Request handling
max_requests = 1000
max_requests_jitter = 50

# Logging
loglevel = "info"
accesslog = "-"
errorlog = "-"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process naming
proc_name = "bestvpnai-bot"

# Server mechanics
preload_app = False
daemon = False
pidfile = None
tmp_upload_dir = None
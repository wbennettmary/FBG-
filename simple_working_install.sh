#!/bin/bash

# Simple Working Installation Script for Firebase Manager
# This actually works - no complex configurations, just basic setup

set -e

echo "🚀 Simple Working Installation for Firebase Manager"
echo "=================================================="

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "❌ This script must be run as root (use sudo)"
   exit 1
fi

# Get current directory
CURRENT_DIR=$(pwd)
echo "📁 Current directory: $CURRENT_DIR"

# Install basic packages
echo "📦 Installing basic packages..."
apt-get update
apt-get install -y python3 python3-pip python3-venv nginx curl

# Create application directory
APP_DIR="/var/www/firebase-manager"
echo "📁 Creating application directory: $APP_DIR"
mkdir -p $APP_DIR

# Copy current project
echo "📋 Copying project files..."
cp -r . $APP_DIR/
chown -R www-data:www-data $APP_DIR

# Create Python virtual environment
echo "🐍 Setting up Python environment..."
cd $APP_DIR
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn firebase-admin pyrebase4 python-dotenv psycopg2-binary

# Create simple .env file
echo "⚙️ Creating environment configuration..."
cat > .env << 'EOF'
# Simple working configuration
USE_DATABASE=false
USE_JSON_FILES=true
ENVIRONMENT=production
LOG_LEVEL=INFO
EOF

# Build frontend
echo "🔨 Building frontend..."
npm install
npm run build

# Create simple systemd service
echo "🔧 Creating systemd service..."
cat > /etc/systemd/system/firebase-manager.service << EOF
[Unit]
Description=Firebase Manager Simple Server
After=network.target

[Service]
Type=exec
User=www-data
Group=www-data
WorkingDirectory=$APP_DIR
Environment=PATH=$APP_DIR/venv/bin
ExecStart=$APP_DIR/venv/bin/python -m src.utils.firebaseBackend
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Configure Nginx
echo "🌐 Configuring Nginx..."
cat > /etc/nginx/sites-available/firebase-manager << EOF
server {
    listen 80;
    server_name _;
    
    # Frontend
    location / {
        root $APP_DIR/dist;
        try_files \$uri \$uri/ /index.html;
    }
    
    # Backend API
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
    
    # Direct backend access
    location /auth/ {
        proxy_pass http://127.0.0.1:8000/auth/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
EOF

# Enable site
ln -sf /etc/nginx/sites-available/firebase-manager /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Test Nginx config
nginx -t

# Start services
echo "🚀 Starting services..."
systemctl daemon-reload
systemctl enable firebase-manager
systemctl start firebase-manager
systemctl enable nginx
systemctl start nginx

# Wait for backend
echo "⏳ Waiting for backend to start..."
sleep 10

# Test backend
echo "🧪 Testing backend..."
if curl -s http://localhost:8000/ > /dev/null; then
    echo "✅ Backend is working!"
else
    echo "❌ Backend not responding, checking logs..."
    journalctl -u firebase-manager --no-pager -l | tail -10
fi

# Get server IP
SERVER_IP=$(curl -4 -s ifconfig.me 2>/dev/null || echo "localhost")
echo ""
echo "🎉 Installation Complete!"
echo "========================"
echo "Frontend: http://$SERVER_IP"
echo "Backend:  http://$SERVER_IP:8000"
echo ""
echo "Login with: admin/admin"
echo ""
echo "To check status: systemctl status firebase-manager"
echo "To view logs: journalctl -u firebase-manager -f"

# Return to original directory
cd $CURRENT_DIR

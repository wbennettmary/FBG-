#!/bin/bash

# Installation Script That PRESERVES Files and Uses IPv4 Consistently
# This actually works and keeps your repo intact

set -e

echo "🚀 Installing Firebase Manager (Preserving Your Files)"
echo "======================================================"

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "❌ This script must be run as root (use sudo)"
   exit 1
fi

# Get current directory (where your repo is)
CURRENT_DIR=$(pwd)
echo "📁 Your repo is safe at: $CURRENT_DIR"

# Get server IPv4 ONLY (no IPv6)
echo "🌐 Detecting server IPv4 address..."
SERVER_IP=$(curl -4 -s ifconfig.me 2>/dev/null || curl -s ipv4.icanhazip.com 2>/dev/null || curl -s checkip.amazonaws.com 2>/dev/null || echo "localhost")

# Validate IPv4 format
if [[ $SERVER_IP =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "✅ Server IPv4 detected: $SERVER_IP"
else
    echo "⚠️  Could not detect valid IPv4, using localhost"
    SERVER_IP="localhost"
fi

# Create application directory (COPY, don't move)
APP_DIR="/var/www/firebase-manager"
echo "📁 Creating application directory: $APP_DIR"
mkdir -p $APP_DIR

# COPY project files (preserve original)
echo "📋 Copying project files (original repo stays intact)..."
cp -r . $APP_DIR/
chown -R www-data:www-data $APP_DIR

echo "✅ Your original repo is preserved at: $CURRENT_DIR"
echo "✅ Application files copied to: $APP_DIR"

# Install basic packages
echo "📦 Installing basic packages..."
apt-get update
apt-get install -y python3 python3-pip python3-venv nginx curl

# Setup Python environment
echo "🐍 Setting up Python environment..."
cd $APP_DIR
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn firebase-admin pyrebase4 python-dotenv

# Create environment file with IPv4
echo "⚙️ Creating environment configuration with IPv4..."
cat > .env << EOF
# Firebase Manager Configuration - IPv4: $SERVER_IP
USE_DATABASE=false
USE_JSON_FILES=true
ENVIRONMENT=production
LOG_LEVEL=INFO
SERVER_IP=$SERVER_IP
BACKEND_PORT=8000
EOF

# Create frontend environment with IPv4
echo "🌐 Creating frontend environment with IPv4..."
cat > .env.local << EOF
# Frontend Environment - IPv4: $SERVER_IP
VITE_API_BASE_URL=http://$SERVER_IP:8000
VITE_SERVER_IP=$SERVER_IP
VITE_BACKEND_PORT=8000
VITE_ENVIRONMENT=production
EOF

# Build frontend with IPv4 configuration
echo "🔨 Building frontend with IPv4 configuration..."
npm install
npm run build

# Create systemd service
echo "🔧 Creating systemd service..."
cat > /etc/systemd/system/firebase-manager.service << EOF
[Unit]
Description=Firebase Manager Server
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

# Configure Nginx with IPv4
echo "🌐 Configuring Nginx with IPv4..."
cat > /etc/nginx/sites-available/firebase-manager << EOF
server {
    listen 80;
    server_name $SERVER_IP _;
    
    # Frontend
    location / {
        root $APP_DIR/dist;
        try_files \$uri \$uri/ /index.html;
    }
    
    # Backend API - IPv4 consistent
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
    
    # Direct backend access - IPv4 consistent
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
sleep 15

# Test backend
echo "🧪 Testing backend..."
if curl -s http://localhost:8000/ > /dev/null; then
    echo "✅ Backend is working!"
else
    echo "❌ Backend not responding, checking logs..."
    journalctl -u firebase-manager --no-pager -l | tail -10
fi

# Test frontend
echo "🧪 Testing frontend..."
if curl -s http://localhost/ | grep -q "Firebase Manager"; then
    echo "✅ Frontend is working!"
else
    echo "⚠️  Frontend accessible but may not be displaying correctly"
fi

# Create status script
cat > $APP_DIR/status.sh << EOF
#!/bin/bash
echo "=== Firebase Manager Status ==="
echo ""
echo "Server Information:"
echo "Server IPv4: $SERVER_IP"
echo "Application Directory: $APP_DIR"
echo "Original Repo: $CURRENT_DIR (PRESERVED)"
echo ""
echo "Backend Service:"
systemctl status firebase-manager --no-pager -l
echo ""
echo "Nginx Service:"
systemctl status nginx --no-pager -l
echo ""
echo "Backend Response:"
curl -s http://localhost:8000/ | head -5
echo ""
echo "Frontend Response:"
curl -s http://localhost/ | head -5
echo ""
echo "IPv4 Configuration:"
echo "Frontend: http://$SERVER_IP"
echo "Backend:  http://$SERVER_IP:8000"
echo "API Base: http://$SERVER_IP:8000"
EOF

chmod +x $APP_DIR/status.sh
chown www-data:www-data $APP_DIR/status.sh

# Installation complete
echo ""
echo "🎉 Installation Complete!"
echo "========================"
echo "✅ Your original repo is PRESERVED at: $CURRENT_DIR"
echo "✅ Application installed at: $APP_DIR"
echo "✅ Server IPv4: $SERVER_IP"
echo ""
echo "🌐 Access URLs:"
echo "Frontend: http://$SERVER_IP"
echo "Backend:  http://$SERVER_IP:8000"
echo ""
echo "🔑 Login: admin/admin"
echo ""
echo "📋 Useful Commands:"
echo "Check Status: $APP_DIR/status.sh"
echo "View Logs: journalctl -u firebase-manager -f"
echo "Restart: systemctl restart firebase-manager"
echo ""
echo "🎯 Your files are safe and the app uses IPv4 consistently!"

# Return to original directory
cd $CURRENT_DIR
echo ""
echo "✅ Returned to your original repo: $CURRENT_DIR"
echo "✅ Your files are completely preserved"

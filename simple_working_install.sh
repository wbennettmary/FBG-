#!/bin/bash

# Simple Working Installation Script for Firebase Manager
# This script sets up the existing backend without any complex migrations

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
APP_DIR="/var/www/firebase-manager"
SERVICE_USER="firebase"
SERVICE_GROUP="firebase"
CURRENT_DIR=$(pwd)

echo -e "${BLUE}🚀 Simple Firebase Manager Installation${NC}"
echo -e "${BLUE}=====================================${NC}"
echo ""

# Function to print colored output
print_status() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   print_error "This script must be run as root (use sudo)"
   exit 1
fi

print_info "Starting simple installation..."

# Install basic system packages
print_info "Installing system packages..."
apt-get update
apt-get install -y python3 python3-pip python3-venv nginx supervisor curl wget git

# Create service user if it doesn't exist
if ! id "$SERVICE_USER" &>/dev/null; then
    print_info "Creating service user: $SERVICE_USER"
    useradd -r -s /bin/bash -d $APP_DIR $SERVICE_USER
fi

# Create application directory
print_info "Setting up application directory..."
mkdir -p $APP_DIR
chown $SERVICE_USER:$SERVICE_GROUP $APP_DIR

# Copy current project to application directory (don't delete original)
print_info "Copying project files to $APP_DIR..."
cp -r . $APP_DIR/
chown -R $SERVICE_USER:$SERVICE_GROUP $APP_DIR

# Create Python virtual environment
print_info "Setting up Python virtual environment..."
cd $APP_DIR
sudo -u $SERVICE_USER python3 -m venv venv
sudo -u $SERVICE_USER $APP_DIR/venv/bin/pip install --upgrade pip

# Install only the packages that are actually needed
print_info "Installing Python dependencies..."
sudo -u $SERVICE_USER $APP_DIR/venv/bin/pip install fastapi uvicorn firebase-admin pyrebase4 python-dotenv google-auth requests python-multipart python-jose passlib aiofiles httpx

# Build frontend
print_info "Building frontend..."
cd $APP_DIR
sudo -u $SERVICE_USER npm install
sudo -u $SERVICE_USER npm run build

# Create systemd service
print_info "Creating systemd service..."
cat > /etc/systemd/system/firebase-manager.service << EOF
[Unit]
Description=Firebase Manager Backend
After=network.target

[Service]
Type=exec
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$APP_DIR
Environment=PATH=$APP_DIR/venv/bin
ExecStart=$APP_DIR/venv/bin/python -m src.utils.firebaseBackend
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Create supervisor configuration
print_info "Creating supervisor configuration..."
cat > /etc/supervisor/conf.d/firebase-manager.conf << EOF
[program:firebase-manager]
command=$APP_DIR/venv/bin/python -m src.utils.firebaseBackend
directory=$APP_DIR
user=$SERVICE_USER
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=$APP_DIR/logs/supervisor.log
stdout_logfile_maxbytes=50MB
stdout_logfile_backups=10
EOF

# Create logs directory
mkdir -p $APP_DIR/logs
chown -R $SERVICE_USER:$SERVICE_GROUP $APP_DIR/logs

# Configure Nginx
print_info "Configuring Nginx..."
cat > /etc/nginx/sites-available/firebase-manager << EOF
server {
    listen 80;
    server_name _;
    
    # Frontend static files
    location / {
        root $APP_DIR/dist;
        try_files \$uri \$uri/ /index.html;
        
        # Cache static assets
        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg)$ {
            expires 1y;
            add_header Cache-Control "public, immutable";
        }
    }
    
    # Backend API
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    # WebSocket support
    location /ws {
        proxy_pass http://127.0.0.1:8000/ws;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
EOF

# Enable the site
ln -sf /etc/nginx/sites-available/firebase-manager /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Test Nginx configuration
print_info "Testing Nginx configuration..."
nginx -t

# Start and enable services
print_info "Starting services..."
systemctl daemon-reload
systemctl enable firebase-manager
systemctl start firebase-manager
systemctl enable nginx
systemctl start nginx
systemctl enable supervisor
systemctl start supervisor

# Wait for backend to start
print_info "Waiting for backend to start..."
sleep 10

# Test if backend is responding
print_info "Testing backend..."
if curl -s --max-time 10 http://localhost:8000/ > /dev/null; then
    print_success "Backend is responding!"
else
    print_warning "Backend not responding yet, checking logs..."
    journalctl -u firebase-manager --no-pager -l --since "2 minutes ago" | tail -20
fi

# Test if frontend is accessible
print_info "Testing frontend..."
if curl -s http://localhost/ | grep -q "Firebase Manager"; then
    print_success "Frontend is accessible!"
else
    print_warning "Frontend may not be displaying correctly"
fi

# Create status script
cat > $APP_DIR/status.sh << 'EOF'
#!/bin/bash
echo "=== Firebase Manager Status ==="
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
EOF

chmod +x $APP_DIR/status.sh
chown $SERVICE_USER:$SERVICE_GROUP $APP_DIR/status.sh

# Installation complete
echo ""
echo -e "${GREEN}🎉 Installation Complete! 🎉${NC}"
echo ""
echo -e "${BLUE}Firebase Manager has been installed successfully!${NC}"
echo ""
echo -e "${YELLOW}Important Information:${NC}"
echo -e "  • Application Directory: ${APP_DIR}"
echo -e "  • Service User: ${SERVICE_USER}"
echo -e "  • Backend URL: http://localhost:8000"
echo -e "  • Frontend URL: http://localhost"
echo ""
echo -e "${YELLOW}Useful Commands:${NC}"
echo -e "  • Check Status: ${APP_DIR}/status.sh"
echo -e "  • View Logs: journalctl -u firebase-manager -f"
echo -e "  • Restart Service: systemctl restart firebase-manager"
echo ""
echo -e "${GREEN}Your Firebase Manager is now running! 🚀${NC}"

# Return to original directory
cd $CURRENT_DIR
print_info "Returned to original directory: $CURRENT_DIR"
print_info "Your project files are preserved here and copied to $APP_DIR"

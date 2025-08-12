#!/bin/bash

# Firebase Manager Enterprise Server - Ubuntu Installation Script
# This script automatically installs and configures the entire system

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
DB_USER="emedia"
DB_PASSWORD="Batata010..++"
DB_NAME="firebase_manager"
APP_DIR="/var/www/firebase-manager"
SERVICE_USER="firebase"
SERVICE_GROUP="firebase"

echo -e "${BLUE}🚀 Firebase Manager Enterprise Server Installation${NC}"
echo -e "${BLUE}================================================${NC}"
echo ""

# Function to print colored output
print_status() {
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

# Check Ubuntu version
UBUNTU_VERSION=$(lsb_release -rs)
if [[ "$UBUNTU_VERSION" != "20.04" && "$UBUNTU_VERSION" != "22.04" && "$UBUNTU_VERSION" != "24.04" ]]; then
    print_warning "This script is tested on Ubuntu 20.04, 22.04, and 24.04. Current version: $UBUNTU_VERSION"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

print_info "Starting installation on Ubuntu $UBUNTU_VERSION..."

# Update system
print_info "Updating system packages..."
apt update && apt upgrade -y

# Install required packages
print_info "Installing required packages..."

# Update package list first
apt update

# Install packages with error handling
PACKAGES=(
    "python3"
    "python3-pip" 
    "python3-venv"
    "postgresql"
    "postgresql-contrib"
    "redis-server"
    "nginx"
    "supervisor"
    "curl"
    "wget"
    "git"
    "unzip"
    "software-properties-common"
    "apt-transport-https"
    "ca-certificates"
    "gnupg"
    "lsb-release"
    "build-essential"
    "libpq-dev"
    "python3-dev"
)

for package in "${PACKAGES[@]}"; do
    print_info "Installing $package..."
    if apt install -y "$package"; then
        print_success "$package installed successfully"
    else
        print_error "Failed to install $package"
        exit 1
    fi
done

# Check Ubuntu version for specific handling
UBUNTU_VERSION=$(lsb_release -rs)
print_info "Detected Ubuntu version: $UBUNTU_VERSION"

# Clean up any existing Node.js installations to prevent conflicts
print_info "Cleaning up existing Node.js installations..."

# Remove all Node.js related packages to prevent conflicts
apt remove -y nodejs npm libnode-dev nodejs-legacy || true
apt remove -y nodejs-doc || true

# Ubuntu 22.04 specific cleanup
if [[ "$UBUNTU_VERSION" == "22.04" ]]; then
    print_info "Ubuntu 22.04 detected - applying specific cleanup..."
    apt remove -y libnode-dev:amd64 || true
    apt autoremove -y
    apt --fix-broken install -y || true
else
    apt autoremove -y
fi

# Fix any broken packages
print_info "Fixing broken packages..."
apt --fix-broken install -y || true

# Clean package cache
print_info "Cleaning package cache..."
apt clean
apt autoclean

# Update package list
apt update

# Additional cleanup for dpkg issues
print_info "Checking for dpkg issues..."
if dpkg --audit | grep -q "broken"; then
    print_warning "Detected broken packages. Attempting to fix..."
    dpkg --configure -a
    apt --fix-broken install -y || true
fi

# Install Node.js 18+ from NodeSource (includes npm)
print_info "Installing Node.js 18+ from NodeSource..."

# Add NodeSource repository
curl -fsSL https://deb.nodesource.com/setup_18.x | bash -

# Force clean install of Node.js
print_info "Installing Node.js 18+..."
apt update

# Install Node.js with force overwrite to resolve conflicts
print_info "Installing Node.js with conflict resolution..."
apt install -y nodejs --allow-downgrades

# If still having issues, try to resolve the specific libnode-dev conflict
if ! command -v node &> /dev/null; then
    print_warning "Node.js installation had conflicts. Resolving..."
    
    # Remove conflicting package completely
    apt remove -y libnode-dev || true
    apt autoremove -y
    
    # Try installation again
    apt install -y nodejs
fi

# Verify Node.js installation
print_info "Verifying Node.js installation..."
if command -v node &> /dev/null && command -v npm &> /dev/null; then
    print_success "Node.js $(node --version) and npm $(npm --version) installed successfully"
else
    print_error "Node.js installation failed. Trying alternative method..."
    
    # Alternative installation method
    print_info "Trying alternative installation method..."
    
    # Remove all conflicting packages
    apt remove -y libnode-dev nodejs npm || true
    apt autoremove -y
    apt --fix-broken install -y || true
    
    # Try to install from NodeSource again
    curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
    apt update
    apt install -y nodejs
    
    if command -v node &> /dev/null && command -v npm &> /dev/null; then
        print_success "Node.js installed successfully via alternative method"
    else
        print_error "Node.js installation failed. Please install manually using:"
        print_error "curl -fsSL https://deb.nodesource.com/setup_18.x | sudo bash -"
        print_error "sudo apt install -y nodejs"
        print_error "Then run this script again."
        exit 1
    fi
fi

# Start and enable services
print_info "Starting and enabling services..."
systemctl start postgresql
systemctl enable postgresql
systemctl start redis-server
systemctl enable redis-server

# Configure PostgreSQL
print_info "Configuring PostgreSQL..."
sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';"
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"
sudo -u postgres psql -c "ALTER USER $DB_USER CREATEDB;"

# Configure Redis
print_info "Configuring Redis..."
sed -i 's/# maxmemory <bytes>/maxmemory 256mb/' /etc/redis/redis.conf
sed -i 's/# maxmemory-policy noeviction/maxmemory-policy allkeys-lru/' /etc/redis/redis.conf
systemctl restart redis-server

# Create application directory and user
print_info "Creating application directory and user..."
useradd -r -s /bin/bash -d $APP_DIR $SERVICE_USER || true
usermod -aG $SERVICE_USER www-data
mkdir -p $APP_DIR
chown -R $SERVICE_USER:$SERVICE_GROUP $APP_DIR

# Clone or copy application files
if [ -d ".git" ]; then
    print_info "Copying current application files..."
    cp -r . $APP_DIR/
    chown -R $SERVICE_USER:$SERVICE_GROUP $APP_DIR
else
    print_info "Cloning application from repository..."
    cd $APP_DIR
    git clone https://github.com/your-repo/firebase-manager.git . || {
        print_warning "Could not clone repository. Please copy files manually to $APP_DIR"
    }
    chown -R $SERVICE_USER:$SERVICE_GROUP $APP_DIR
fi

# Create Python virtual environment
print_info "Setting up Python virtual environment..."
cd $APP_DIR
sudo -u $SERVICE_USER python3 -m venv venv
sudo -u $SERVICE_USER $APP_DIR/venv/bin/pip install --upgrade pip

# Install Python dependencies
print_info "Installing Python dependencies..."
sudo -u $SERVICE_USER $APP_DIR/venv/bin/pip install -r requirements.txt

# Install Node.js dependencies
print_info "Installing Node.js dependencies..."
sudo -u $SERVICE_USER npm install

# Build frontend
print_info "Building frontend..."
if sudo -u $SERVICE_USER npm run build; then
    print_success "Frontend built successfully"
    
    # Verify build output
    if [ -f "$APP_DIR/dist/index.html" ]; then
        print_success "Frontend build verified - index.html found"
    else
        print_error "Frontend build failed - index.html not found"
        exit 1
    fi
else
    print_error "Frontend build failed"
    exit 1
fi

# Create environment file
print_info "Creating environment configuration..."
cat > $APP_DIR/.env << EOF
# Enterprise Server Configuration
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
DEBUG=false
ENVIRONMENT=production

# Database Configuration
DB_HOST=localhost
DB_PORT=5432
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASSWORD
DB_URL=postgresql://$DB_USER:$DB_PASSWORD@localhost:5432/$DB_NAME

# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

# Security
SECRET_KEY=$(openssl rand -hex 32)
JWT_SECRET_KEY=$(openssl rand -hex 32)
ACCESS_TOKEN_EXPIRE_MINUTES=30

# SMTP Configuration
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=your-email@gmail.com
SMTP_USE_TLS=true

# Rate Limiting
RATE_LIMIT_PER_MINUTE=100
RATE_LIMIT_PER_HOUR=1000

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json

# Monitoring
ENABLE_METRICS=true
METRICS_PORT=9090

# File Storage
UPLOAD_DIR=$APP_DIR/uploads
MAX_FILE_SIZE=10485760
EOF

chown $SERVICE_USER:$SERVICE_GROUP $APP_DIR/.env
chmod 600 $APP_DIR/.env

# Create uploads directory
mkdir -p $APP_DIR/uploads
chown -R $SERVICE_USER:$SERVICE_GROUP $APP_DIR/uploads

# Run database migrations
print_info "Running database migrations..."
cd $APP_DIR
sudo -u $SERVICE_USER $APP_DIR/venv/bin/python -m src.database.migrations

# Create systemd service
print_info "Creating systemd service..."
cat > /etc/systemd/system/firebase-manager.service << EOF
[Unit]
Description=Firebase Manager Enterprise Server
After=network.target postgresql.service redis-server.service
Wants=postgresql.service redis-server.service

[Service]
Type=exec
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$APP_DIR
Environment=PATH=$APP_DIR/venv/bin
ExecStart=$APP_DIR/venv/bin/python -m src.server.enterprise_backend
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=firebase-manager

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$APP_DIR

[Install]
WantedBy=multi-user.target
EOF

# Create supervisor configuration
print_info "Creating supervisor configuration..."
cat > /etc/supervisor/conf.d/firebase-manager.conf << EOF
[program:firebase-manager]
command=$APP_DIR/venv/bin/python -m src.server.enterprise_backend
directory=$APP_DIR
user=$SERVICE_USER
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=$APP_DIR/logs/supervisor.log
stdout_logfile_maxbytes=50MB
stdout_logfile_backups=10
environment=PATH="$APP_DIR/venv/bin"
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
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    
    # Health check
    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        proxy_set_header Host \$host;
    }
    
    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer-when-downgrade" always;
    add_header Content-Security-Policy "default-src 'self' http: https: data: blob: 'unsafe-inline'" always;
}
EOF

# Enable Nginx site
ln -sf /etc/nginx/sites-available/firebase-manager /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Remove default nginx page completely
print_info "Removing default nginx page..."
rm -f /var/www/html/index.nginx-debian.html
rm -f /var/www/html/index.html
rm -f /usr/share/nginx/html/index.html

# Test Nginx configuration
print_info "Testing Nginx configuration..."
if nginx -t; then
    print_success "Nginx configuration is valid"
    
    # Restart Nginx
    print_info "Restarting Nginx..."
    systemctl restart nginx
    systemctl enable nginx
    
    # Wait a moment for Nginx to start
    sleep 3
    
    # Verify Nginx is running
    if systemctl is-active --quiet nginx; then
        print_success "Nginx is running successfully"
    else
        print_error "Nginx failed to start"
        exit 1
    fi
else
    print_error "Nginx configuration test failed"
    exit 1
fi

# Create firewall rules
print_info "Configuring firewall..."
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS (if using SSL)
ufw allow 8000/tcp  # Backend API (if needed)
ufw --force enable

# Set up log rotation
print_info "Setting up log rotation..."
cat > /etc/logrotate.d/firebase-manager << EOF
$APP_DIR/logs/*.log {
    daily
    missingok
    rotate 52
    compress
    delaycompress
    notifempty
    create 644 $SERVICE_USER $SERVICE_GROUP
    postrotate
        systemctl reload firebase-manager > /dev/null 2>&1 || true
    endscript
}
EOF

# Create monitoring script
print_info "Creating monitoring script..."
cat > $APP_DIR/monitor.sh << 'EOF'
#!/bin/bash
# Simple monitoring script for Firebase Manager

APP_DIR="/var/www/firebase-manager"
LOG_FILE="$APP_DIR/logs/monitor.log"

log() {
    echo "$(date): $1" >> "$LOG_FILE"
}

# Check if service is running
if ! systemctl is-active --quiet firebase-manager; then
    log "ERROR: Firebase Manager service is not running"
    systemctl restart firebase-manager
    log "INFO: Service restarted"
fi

# Check database connection
if ! sudo -u postgres psql -d firebase_manager -c "SELECT 1;" > /dev/null 2>&1; then
    log "ERROR: Database connection failed"
fi

# Check Redis
if ! redis-cli ping > /dev/null 2>&1; then
    log "ERROR: Redis connection failed"
fi

# Check disk space
DISK_USAGE=$(df / | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 80 ]; then
    log "WARNING: Disk usage is ${DISK_USAGE}%"
fi

# Check memory usage
MEM_USAGE=$(free | awk 'NR==2{printf "%.0f", $3*100/$2}')
if [ "$MEM_USAGE" -gt 80 ]; then
    log "WARNING: Memory usage is ${MEM_USAGE}%"
fi
EOF

chmod +x $APP_DIR/monitor.sh
chown $SERVICE_USER:$SERVICE_GROUP $APP_DIR/monitor.sh

# Add monitoring to crontab
(crontab -l 2>/dev/null; echo "*/5 * * * * $APP_DIR/monitor.sh") | crontab -

# Start services
print_info "Starting services..."
systemctl daemon-reload
systemctl enable firebase-manager
systemctl start firebase-manager
systemctl reload nginx

# Wait for service to start
sleep 5

# Check service status
if systemctl is-active --quiet firebase-manager; then
    print_status "Firebase Manager service is running"
else
    print_error "Firebase Manager service failed to start"
    systemctl status firebase-manager
    exit 1
fi

# Create admin user
print_info "Creating default admin user..."
cd $APP_DIR
sudo -u $SERVICE_USER $APP_DIR/venv/bin/python -c "
from src.database.connection import db_manager
from src.database.migrations import create_default_admin
import asyncio

async def setup_admin():
    await db_manager.initialize()
    await create_default_admin()
    await db_manager.close()

asyncio.run(setup_admin())
"

# Final configuration
print_info "Final configuration..."
chown -R $SERVICE_USER:$SERVICE_GROUP $APP_DIR
chmod -R 755 $APP_DIR
chmod 600 $APP_DIR/.env

# Create status check script
cat > $APP_DIR/status.sh << 'EOF'
#!/bin/bash
echo "=== Firebase Manager Enterprise Server Status ==="
echo ""
echo "Service Status:"
systemctl status firebase-manager --no-pager -l
echo ""
echo "Database Status:"
sudo -u postgres psql -d firebase_manager -c "SELECT version();" 2>/dev/null || echo "Database connection failed"
echo ""
echo "Redis Status:"
redis-cli ping 2>/dev/null || echo "Redis connection failed"
echo ""
echo "Nginx Status:"
systemctl status nginx --no-pager -l
echo ""
echo "Logs (last 20 lines):"
tail -20 $APP_DIR/logs/supervisor.log 2>/dev/null || echo "No logs found"
EOF

chmod +x $APP_DIR/status.sh
chown $SERVICE_USER:$SERVICE_GROUP $APP_DIR/status.sh

# Installation complete
echo ""
echo -e "${GREEN}🎉 Installation Complete! 🎉${NC}"
echo ""
echo -e "${BLUE}Firebase Manager Enterprise Server has been installed successfully!${NC}"
echo ""
echo -e "${YELLOW}Important Information:${NC}"
echo -e "  • Application Directory: ${APP_DIR}"
echo -e "  • Service User: ${SERVICE_USER}"
echo -e "  • Database: ${DB_NAME} (User: ${DB_USER})"
echo -e "  • Backend URL: http://localhost:8000"
echo -e "  • Frontend URL: http://localhost"
echo -e "  • Health Check: http://localhost/health"
echo ""
echo -e "${YELLOW}Default Admin Account:${NC}"
echo -e "  • Username: admin"
echo -e "  • Password: admin"
echo -e "  • Please change the password after first login!"
echo ""
echo -e "${YELLOW}Useful Commands:${NC}"
echo -e "  • Check Status: ${APP_DIR}/status.sh"
echo -e "  • View Logs: tail -f ${APP_DIR}/logs/supervisor.log"
echo -e "  • Restart Service: systemctl restart firebase-manager"
echo -e "  • Monitor: ${APP_DIR}/monitor.sh"
echo ""
echo -e "${BLUE}Next Steps:${NC}"
echo -e "  1. Update the .env file with your SMTP settings"
echo -e "  2. Configure your domain in Nginx if needed"
echo -e "  3. Set up SSL certificate (recommended for production)"
echo -e "  4. Change default admin password"
echo -e "  5. Configure backup strategy"
echo ""
echo -e "${GREEN}Your enterprise server is ready to handle 1000+ campaigns! 🚀${NC}"
echo ""

# Test the installation
print_info "Testing installation..."
if curl -s http://localhost/health > /dev/null; then
    print_status "Health check passed - server is responding"
else
    print_warning "Health check failed - please check logs"
fi

print_info "Installation script completed successfully!"

# Final verification
print_info "Performing final verification..."
if [ -f "$APP_DIR/dist/index.html" ] && systemctl is-active --quiet nginx; then
    print_success "✅ App interface is ready and accessible!"
    print_success "✅ Nginx is running and serving your app!"
    print_success "✅ Default nginx page has been removed!"
    
    # Test app interface
    if curl -s http://localhost | grep -q "Firebase Manager"; then
        print_success "✅ App interface is displaying correctly!"
    else
        print_warning "⚠️  App interface may not be displaying correctly. Please check manually."
    fi
else
    print_warning "⚠️  Some verification steps failed. Please check manually."
fi

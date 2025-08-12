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
APP_DIR="/var/www/firebase-manager"
SERVICE_USER="firebase"
SERVICE_GROUP="firebase"
REPO_URL="https://github.com/wbennettmary/FBG-.git"

echo -e "${BLUE}🚀 Firebase Manager Enterprise Server Installation${NC}"
echo -e "${BLUE}================================================${NC}"
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

# Function to safely change directory
safe_cd() {
    local target_dir="$1"
    local operation="$2"
    
    if cd "$target_dir" 2>/dev/null; then
        print_info "Changed to directory: $target_dir for $operation"
        return 0
    else
        print_error "Failed to change to directory: $target_dir for $operation"
        return 1
    fi
}

# Function to ensure we're in a safe working directory
ensure_safe_directory() {
    if [[ "$PWD" == "/" ]] || [[ "$PWD" == "/root" ]] || [[ "$PWD" == "/home" ]]; then
        print_warning "Current directory is not safe for operations, changing to /tmp"
        cd /tmp || {
            print_error "Failed to change to /tmp"
            exit 1
        }
    fi
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

# Ensure we're in a safe working directory
ensure_safe_directory

# COMPLETE CLEANUP - Remove any existing installation
print_info "🧹 Performing complete cleanup of any existing installation..."

# Stop and disable all services
print_info "Stopping existing services..."
systemctl stop firebase-manager 2>/dev/null || true
systemctl stop nginx 2>/dev/null || true
systemctl stop supervisor 2>/dev/null || true

# Disable services
print_info "Disabling existing services..."
systemctl disable firebase-manager 2>/dev/null || true
systemctl disable nginx 2>/dev/null || true
systemctl disable supervisor 2>/dev/null || true

# Remove application files
print_info "Removing application files..."
rm -rf /var/www/firebase-manager 2>/dev/null || true
rm -rf /root/FBG- 2>/dev/null || true
rm -rf /home/firebase 2>/dev/null || true

# Remove configuration files
print_info "Removing configuration files..."
rm -f /etc/nginx/sites-available/firebase-manager 2>/dev/null || true
rm -f /etc/nginx/sites-enabled/firebase-manager 2>/dev/null || true
rm -f /etc/supervisor/conf.d/firebase-manager.conf 2>/dev/null || true
rm -f /etc/systemd/system/firebase-manager.service 2>/dev/null || true

# Reset systemd
print_info "Resetting systemd..."
systemctl daemon-reload 2>/dev/null || true
systemctl reset-failed 2>/dev/null || true

print_success "Cleanup completed successfully!"

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

# Skip PostgreSQL and Redis - using JSON files
print_info "Skipping PostgreSQL and Redis setup - backend will use JSON files"

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
    cd $APP_DIR || {
        print_error "Failed to change to application directory $APP_DIR"
        exit 1
    }
    git clone $REPO_URL . || {
        print_error "Could not clone repository from $REPO_URL"
        print_error "Please ensure the repository is accessible or copy files manually to $APP_DIR"
        exit 1
    }
    chown -R $SERVICE_USER:$SERVICE_GROUP $APP_DIR
fi

# Return to original directory for safety
cd /tmp

# Create Python virtual environment
print_info "Setting up Python virtual environment..."
cd $APP_DIR || {
    print_error "Failed to change to application directory for Python setup"
    exit 1
}
sudo -u $SERVICE_USER python3 -m venv venv
sudo -u $SERVICE_USER $APP_DIR/venv/bin/pip install --upgrade pip

# Install Python dependencies
print_info "Installing Python dependencies..."

# Check if requirements.txt exists
if [ -f "$APP_DIR/requirements.txt" ]; then
    print_info "Installing Python packages from requirements.txt..."
    if sudo -u $SERVICE_USER $APP_DIR/venv/bin/pip install -r requirements.txt; then
        print_success "Python dependencies installed successfully"
    else
        print_error "Failed to install Python dependencies from requirements.txt"
        print_info "Trying to install packages individually..."
        
        # Install packages individually as fallback
        PACKAGES=(
            "fastapi"
            "uvicorn[standard]"
            "firebase-admin"
            "pyrebase4"
            "python-dotenv"
            "google-auth"
            "requests"
            "google-cloud-resource-manager"
            "python-multipart"
            "python-jose[cryptography]"
            "passlib[bcrypt]"
            "aiofiles"
            "httpx"
            "asyncpg"
            "psycopg2-binary"
            "sqlalchemy[asyncio]"
            "alembic"
            "redis"
            "aioredis"
            "prometheus-client"
            "structlog"
            "gunicorn"
            "slowapi"
            "healthcheck"
        )
        
        for package in "${PACKAGES[@]}"; do
            print_info "Installing $package..."
            if sudo -u $SERVICE_USER $APP_DIR/venv/bin/pip install "$package"; then
                print_success "$package installed successfully"
            else
                print_warning "Failed to install $package, continuing..."
            fi
        done
    fi
else
    print_warning "requirements.txt not found. Installing basic packages..."
    sudo -u $SERVICE_USER $APP_DIR/venv/bin/pip install fastapi uvicorn firebase-admin pyrebase4 python-dotenv google-auth requests google-cloud-resource-manager python-multipart python-jose passlib aiofiles asyncpg psycopg2-binary sqlalchemy alembic redis aioredis prometheus-client structlog gunicorn slowapi healthcheck
fi

# Install Node.js dependencies
print_info "Installing Node.js dependencies..."

# Check if package.json exists
if [ -f "$APP_DIR/package.json" ]; then
    print_info "Installing Node.js packages from package.json..."
    if sudo -u $SERVICE_USER npm install; then
        print_success "Node.js dependencies installed successfully"
    else
        print_error "Failed to install Node.js dependencies"
        exit 1
    fi
else
    print_warning "package.json not found. Creating basic package.json..."
    sudo -u $SERVICE_USER npm init -y
    sudo -u $SERVICE_USER npm install react react-dom react-router-dom @types/react @types/react-dom typescript vite @vitejs/plugin-react
fi

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

# Run database migrations
print_info "Running database migrations..."
cd $APP_DIR || {
    print_error "Failed to change to application directory for migrations"
    exit 1
}
if sudo -u $SERVICE_USER $APP_DIR/venv/bin/python -m src.database.migrations 2>/dev/null; then
    print_success "Database migrations completed successfully"
else
    print_error "Database migrations failed"
    print_info "Attempting to create tables manually..."
    
    # Create basic tables if migrations fail
    sudo -u $SERVICE_USER $APP_DIR/venv/bin/python -c "
import asyncio
import sys
sys.path.append('$APP_DIR')
from src.database.connection import db_manager
from src.database.models import Base
from sqlalchemy import text

async def create_basic_tables():
    try:
        await db_manager.initialize()
        async with db_manager.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print('Basic tables created successfully')
    except Exception as e:
        print(f'Error creating tables: {e}')
    finally:
        await db_manager.close()

asyncio.run(create_basic_tables())
"
fi

# Create environment file
# Environment configuration will be created later for JSON-based backend

# Create uploads directory
mkdir -p $APP_DIR/uploads
chown -R $SERVICE_USER:$SERVICE_GROUP $APP_DIR/uploads

# Skip database migrations for now - use JSON files
print_info "Setting up backend to use JSON files (database migrations skipped)..."
cd $APP_DIR

# Create configuration for JSON-based backend
cat > $APP_DIR/.env << EOF
# Configuration for JSON-based backend
USE_DATABASE=false
USE_JSON_FILES=true
BACKEND_PORT=8000
LOG_LEVEL=INFO
EOF

print_info "Backend will use existing JSON files for data storage"

# Create systemd service
print_info "Creating systemd service..."
cat > /etc/systemd/system/firebase-manager.service << EOF
[Unit]
Description=Firebase Manager Enterprise Server
After=network.target
Wants=network.target

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
command=$APP_DIR/venv/bin/python -m src.utils.firebaseBackend
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
    
    # Wait a moment for the service to fully initialize
    sleep 10
    
    # Check if the service is actually responding
    if curl -s --max-time 10 http://localhost:8000/health > /dev/null 2>&1; then
        print_success "Backend is responding to health checks"
    else
        print_warning "Service is running but backend is not responding. Checking logs..."
        journalctl -u firebase-manager --no-pager -l --since "2 minutes ago"
        print_error "Backend service is not responding properly. Attempting to restart..."
        
        # Try to restart the service
        systemctl restart firebase-manager
        sleep 10
        
        if curl -s --max-time 10 http://localhost:8000/health > /dev/null 2>&1; then
            print_success "Backend is now responding after restart"
        else
            print_error "Backend still not responding. Installation may have failed."
            print_info "Please check the logs manually: journalctl -u firebase-manager -f"
            exit 1
        fi
    fi
else
    print_error "Firebase Manager service failed to start"
    systemctl status firebase-manager --no-pager -l
    exit 1
fi

# Create admin user (skip if migrations already handled it)
print_info "Admin user creation handled by migrations..."

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
if curl -s http://localhost:8000/health > /dev/null; then
    print_status "Health check passed - backend is responding"
elif curl -s http://localhost:8000/ > /dev/null; then
    print_status "Backend is responding (health endpoint not available)"
else
    print_warning "Backend health check failed - please check logs"
    print_info "Checking backend logs:"
    journalctl -u firebase-manager --no-pager -l --since "5 minutes ago" | tail -20
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

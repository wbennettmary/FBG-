#!/bin/bash

# Server IP Detection Script for Firebase Manager
# This script detects the server's IPv4 address and creates proper environment files
# Works on any server (AWS, DigitalOcean, Vultr, etc.)

echo "🔍 Detecting Server Configuration for Firebase Manager..."
echo "======================================================"

# Function to print colored output
print_success() {
    echo -e "\033[0;32m✅ $1\033[0m"
}

print_warning() {
    echo -e "\033[1;33m⚠️  $1\033[0m"
}

print_error() {
    echo -e "\033[0;31m❌ $1\033[0m"
}

print_info() {
    echo -e "\033[0;34mℹ️  $1\033[0m"
}

# Detect server IP using multiple methods (IPv4 only)
print_info "Detecting server IPv4 address..."

# Method 1: curl with IPv4 flag
SERVER_IP=$(curl -4 -s ifconfig.me 2>/dev/null)
if [ $? -eq 0 ] && [[ $SERVER_IP =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    print_success "Method 1 (ifconfig.me): $SERVER_IP"
else
    print_warning "Method 1 failed, trying alternative..."
    
    # Method 2: ipv4.icanhazip.com
    SERVER_IP=$(curl -s ipv4.icanhazip.com 2>/dev/null)
    if [ $? -eq 0 ] && [[ $SERVER_IP =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        print_success "Method 2 (icanhazip): $SERVER_IP"
    else
        print_warning "Method 2 failed, trying alternative..."
        
        # Method 3: checkip.amazonaws.com (good for AWS)
        SERVER_IP=$(curl -s checkip.amazonaws.com 2>/dev/null)
        if [ $? -eq 0 ] && [[ $SERVER_IP =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            print_success "Method 3 (AWS checkip): $SERVER_IP"
        else
            print_warning "Method 3 failed, trying alternative..."
            
            # Method 4: Local network interface
            SERVER_IP=$(ip route get 8.8.8.8 | grep -oP 'src \K\S+' | head -1)
            if [ $? -eq 0 ] && [[ $SERVER_IP =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
                print_success "Method 4 (local interface): $SERVER_IP"
            else
                print_error "All IP detection methods failed!"
                SERVER_IP="localhost"
            fi
        fi
    fi
fi

# Validate final IP
if [[ $SERVER_IP =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    print_success "Valid IPv4 address detected: $SERVER_IP"
else
    print_warning "Invalid IP format, using localhost"
    SERVER_IP="localhost"
fi

# Get additional server info
print_info "Gathering server information..."
OS_INFO=$(cat /etc/os-release | grep PRETTY_NAME | cut -d'"' -f2 2>/dev/null || echo "Unknown")
KERNEL=$(uname -r)
ARCH=$(uname -m)
HOSTNAME=$(hostname)

echo ""
echo "📋 Server Information:"
echo "======================"
echo "Operating System: $OS_INFO"
echo "Kernel Version:  $KERNEL"
echo "Architecture:    $ARCH"
echo "Hostname:        $HOSTNAME"
echo "Public IPv4:     $SERVER_IP"
echo ""

# Create environment files
print_info "Creating environment configuration files..."

# Backend .env file
cat > .env << EOF
# Firebase Manager Server Configuration
# Generated automatically for server: $SERVER_IP

# Server Configuration
SERVER_IP=$SERVER_IP
SERVER_PORT=80
BACKEND_PORT=8000

# Database Configuration
USE_DATABASE=true
USE_JSON_FILES=true
DB_HOST=localhost
DB_PORT=5432
DB_NAME=firebase_manager
DB_USER=emedia
DB_PASSWORD=Batata010..++
DB_URL=postgresql://emedia:Batata010..++@localhost:5432/firebase_manager

# Application Configuration
ENVIRONMENT=production
LOG_LEVEL=INFO
CORS_ORIGINS=*
EOF

# Frontend .env.local file
cat > .env.local << EOF
# Frontend Environment Configuration
# Generated automatically for server: $SERVER_IP

VITE_API_BASE_URL=http://$SERVER_IP:8000
VITE_SERVER_IP=$SERVER_IP
VITE_BACKEND_PORT=8000
VITE_ENVIRONMENT=production
EOF

print_success "Environment files created:"
print_info "  • .env (backend configuration)"
print_info "  • .env.local (frontend configuration)"

# Create installation command
echo ""
echo "🚀 Installation Command:"
echo "========================"
echo "Run this command to install Firebase Manager:"
echo ""
echo "sudo ./aws_ubuntu_fixed_install.sh"
echo ""
echo "Or if you want to use custom database credentials:"
echo "sudo ./aws_ubuntu_fixed_install.sh --custom-db"
echo ""

# Test connectivity
print_info "Testing server connectivity..."
if ping -c 1 -W 3 8.8.8.8 > /dev/null 2>&1; then
    print_success "Internet connectivity: OK"
else
    print_warning "Internet connectivity: Limited (may be internal server)"
fi

if curl -s --max-time 5 http://$SERVER_IP > /dev/null 2>&1; then
    print_success "Local server accessibility: OK"
else
    print_info "Local server accessibility: Not accessible (normal for fresh install)"
fi

echo ""
echo "🎯 Next Steps:"
echo "=============="
echo "1. Review the generated .env and .env.local files"
echo "2. Modify database password if needed"
echo "3. Run the installation script: sudo ./aws_ubuntu_fixed_install.sh"
echo "4. Your server will be accessible at: http://$SERVER_IP"
echo "5. Backend API will be at: http://$SERVER_IP:8000"
echo ""
echo "✅ Server detection complete! Your Firebase Manager is ready to install."

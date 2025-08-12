#!/bin/bash

echo "🚨 IMMEDIATE DIAGNOSTIC AND FIX"
echo "==============================="

# Function to print colored output
print_error() { echo -e "\033[31m❌ $1\033[0m"; }
print_success() { echo -e "\033[32m✅ $1\033[0m"; }
print_info() { echo -e "\033[34mℹ️  $1\033[0m"; }

# Check current nginx sites
echo "🔍 Checking nginx sites..."
echo "Sites enabled:"
ls -la /etc/nginx/sites-enabled/
echo ""
echo "Sites available:"
ls -la /etc/nginx/sites-available/

# Check what's actually being served
echo ""
echo "🌐 Testing what nginx is actually serving..."
curl -I http://localhost/ 2>/dev/null | grep -E "(Server|Content-Type|Location)"

# Check if our index.html exists and what it contains
echo ""
echo "📄 Checking our frontend files..."
if [ -f "/var/www/firebase-manager/dist/index.html" ]; then
    print_success "Frontend index.html exists"
    echo "First few lines:"
    head -5 /var/www/firebase-manager/dist/index.html
else
    print_error "Frontend index.html missing!"
fi

# Check nginx configuration
echo ""
echo "🔧 Checking nginx configuration..."
if [ -f "/etc/nginx/sites-available/firebase-manager" ]; then
    print_success "Firebase Manager nginx config exists"
    echo "Root directive:"
    grep "root" /etc/nginx/sites-available/firebase-manager
else
    print_error "Firebase Manager nginx config missing!"
fi

# Check nginx error logs
echo ""
echo "📜 Recent nginx error logs:"
tail -10 /var/log/nginx/error.log 2>/dev/null || echo "No recent nginx errors"

# Check if nginx default is really gone
echo ""
echo "🗑️  Checking default nginx files..."
if [ -f "/etc/nginx/sites-enabled/default" ]; then
    print_error "Default site still enabled!"
    rm -f /etc/nginx/sites-enabled/default
    print_info "Removed default site"
else
    print_success "Default site properly removed"
fi

if [ -f "/var/www/html/index.nginx-debian.html" ]; then
    print_error "Default nginx page still exists!"
    rm -f /var/www/html/index.nginx-debian.html
    print_info "Removed default nginx page"
fi

# Force nginx reload
echo ""
echo "🔄 Force reloading nginx..."
systemctl reload nginx
sleep 2

# Test again
echo ""
echo "🧪 Testing after fixes..."
RESPONSE=$(curl -s http://localhost/ | head -1)
if [[ "$RESPONSE" == *"nginx"* ]]; then
    print_error "Still serving nginx default!"
    echo "Response: $RESPONSE"
    
    # More aggressive fix
    echo "🔨 Applying aggressive fix..."
    
    # Disable default site completely
    a2dissite default 2>/dev/null || echo "No Apache default site"
    
    # Check nginx main config
    echo "Main nginx config check:"
    grep -n "include.*sites-enabled" /etc/nginx/nginx.conf
    
    # Force our site
    ln -sf /etc/nginx/sites-available/firebase-manager /etc/nginx/sites-enabled/firebase-manager
    
    # Remove any other enabled sites
    cd /etc/nginx/sites-enabled/
    for site in *; do
        if [ "$site" != "firebase-manager" ]; then
            print_info "Removing conflicting site: $site"
            rm -f "$site"
        fi
    done
    
    # Test nginx config and restart
    nginx -t
    systemctl restart nginx
    sleep 3
    
    # Final test
    FINAL_RESPONSE=$(curl -s http://localhost/ | head -1)
    if [[ "$FINAL_RESPONSE" == *"nginx"* ]]; then
        print_error "STILL showing nginx default after aggressive fix!"
        echo "Final response: $FINAL_RESPONSE"
    else
        print_success "Fixed! Now serving our app"
    fi
else
    print_success "App is now serving correctly!"
fi

# Database diagnostic
echo ""
echo "🗄️  Database diagnostic..."
cd /var/www/firebase-manager

# Check if backend is running
if pgrep -f "firebaseBackend" > /dev/null; then
    print_success "Backend process is running"
else
    print_error "Backend process not running!"
    systemctl restart firebase-manager
    sleep 5
fi

# Check database connection
echo "Testing database connection..."
sudo -u postgres psql -d firebase_manager -c "SELECT username FROM app_users WHERE username='admin';" 2>/dev/null

# Check backend logs for database errors
echo ""
echo "📜 Recent backend logs:"
journalctl -u firebase-manager --since "5 minutes ago" --no-pager | tail -10

echo ""
echo "🎯 DIAGNOSTIC COMPLETE!"
echo "Check the output above to identify the exact issues."

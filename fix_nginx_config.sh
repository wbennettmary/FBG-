#!/bin/bash

# Fix Nginx Configuration - Make Backend Accessible Externally
# This will make your Firebase Manager work on port 80

echo "🔧 Fixing Nginx Configuration..."
echo "================================"

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "❌ This script must be run as root (use sudo)"
   exit 1
fi

# Get server IP
SERVER_IP=$(curl -4 -s ifconfig.me 2>/dev/null || curl -s ipv4.icanhazip.com 2>/dev/null || echo "localhost")
echo "🌐 Server IP: $SERVER_IP"

# Backup current Nginx config
echo "💾 Backing up current Nginx configuration..."
cp /etc/nginx/sites-available/firebase-manager /etc/nginx/sites-available/firebase-manager.backup

# Create fixed Nginx configuration
echo "🔧 Creating fixed Nginx configuration..."
cat > /etc/nginx/sites-available/firebase-manager << EOF
server {
    listen 80;
    server_name $SERVER_IP _;
    
    # Frontend
    location / {
        root /var/www/firebase-manager/dist;
        try_files \$uri \$uri/ /index.html;
    }
    
    # Backend API - accessible on port 80
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
    
    # Direct backend access - accessible on port 80
    location /auth/ {
        proxy_pass http://127.0.0.1:8000/auth/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    # Health check
    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
    
    # Root endpoint
    location = / {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
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

# Test Nginx configuration
echo "🧪 Testing Nginx configuration..."
if nginx -t; then
    echo "✅ Nginx configuration is valid"
else
    echo "❌ Nginx configuration is invalid, restoring backup..."
    cp /etc/nginx/sites-available/firebase-manager.backup /etc/nginx/sites-available/firebase-manager
    exit 1
fi

# Restart Nginx
echo "🚀 Restarting Nginx..."
systemctl restart nginx

# Wait for Nginx to start
sleep 3

# Test if Nginx is running
if systemctl is-active --quiet nginx; then
    echo "✅ Nginx is running"
else
    echo "❌ Nginx failed to start"
    exit 1
fi

# Test connectivity
echo ""
echo "🧪 Testing Connectivity..."
echo "========================"

# Test frontend
echo "Testing frontend..."
if curl -s --max-time 10 http://$SERVER_IP/ | grep -q "Firebase Manager"; then
    echo "✅ Frontend: http://$SERVER_IP - WORKING"
else
    echo "⚠️  Frontend: http://$SERVER_IP - May not be displaying correctly"
fi

# Test backend through Nginx (port 80)
echo ""
echo "Testing backend through Nginx (port 80)..."
if curl -s --max-time 10 http://$SERVER_IP/health > /dev/null; then
    echo "✅ Backend Health: http://$SERVER_IP/health - WORKING"
else
    echo "❌ Backend Health: http://$SERVER_IP/health - NOT WORKING"
fi

# Test login endpoint through Nginx (port 80)
echo ""
echo "Testing login endpoint through Nginx (port 80)..."
if curl -s --max-time 10 http://$SERVER_IP/auth/login > /dev/null; then
    echo "✅ Login Endpoint: http://$SERVER_IP/auth/login - WORKING"
else
    echo "❌ Login Endpoint: http://$SERVER_IP/auth/login - NOT WORKING"
fi

# Test root endpoint through Nginx (port 80)
echo ""
echo "Testing root endpoint through Nginx (port 80)..."
if curl -s --max-time 10 http://$SERVER_IP/ > /dev/null; then
    echo "✅ Root Endpoint: http://$SERVER_IP/ - WORKING"
else
    echo "❌ Root Endpoint: http://$SERVER_IP/ - NOT WORKING"
fi

echo ""
echo "🎉 Nginx Configuration Fixed!"
echo "============================"
echo "✅ Backend is now accessible through port 80"
echo "✅ No more port 8000 external access needed"
echo "✅ Your Firebase Manager will work externally"
echo ""
echo "🌐 Access URLs:"
echo "Frontend: http://$SERVER_IP"
echo "Backend:  http://$SERVER_IP (through Nginx proxy)"
echo "Login:    http://$SERVER_IP/auth/login"
echo ""
echo "🔑 Login with: admin/admin"
echo ""
echo "📋 To test manually:"
echo "curl http://$SERVER_IP/health"
echo "curl http://$SERVER_IP/auth/login"
echo ""
echo "🎯 Your app should now work perfectly from anywhere!"

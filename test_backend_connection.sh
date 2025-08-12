#!/bin/bash

# Test Backend Connection and Database Integration
# Run this after installation to verify everything is working

# Get server IP - FORCE IPv4 only
echo "🔍 Testing Firebase Manager Backend Connection..."
echo "================================================"

# Force IPv4 detection
SERVER_IP=$(curl -4 -s ifconfig.me 2>/dev/null || curl -s ipv4.icanhazip.com 2>/dev/null || curl -s checkip.amazonaws.com 2>/dev/null || echo "localhost")

# Validate IPv4 format
if [[ $SERVER_IP =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "✅ Server IPv4 detected: $SERVER_IP"
else
    echo "⚠️  Could not detect valid IPv4, using localhost"
    SERVER_IP="localhost"
fi
echo ""

# Test 1: Basic backend connectivity
echo "✅ Test 1: Basic Backend Connectivity"
echo "Testing: http://$SERVER_IP:8000/"
if curl -s --max-time 10 "http://$SERVER_IP:8000/" > /dev/null; then
    echo "   ✅ Backend is responding on port 8000"
else
    echo "   ❌ Backend not responding on port 8000"
    echo "   Checking if backend is running..."
    systemctl status firebase-manager --no-pager -l
    exit 1
fi
echo ""

# Test 2: Health check endpoint
echo "✅ Test 2: Health Check Endpoint"
echo "Testing: http://$SERVER_IP:8000/health"
HEALTH_RESPONSE=$(curl -s --max-time 10 "http://$SERVER_IP:8000/health")
if [ $? -eq 0 ]; then
    echo "   ✅ Health check endpoint responding"
    echo "   Response: $HEALTH_RESPONSE"
else
    echo "   ❌ Health check endpoint failed"
fi
echo ""

# Test 3: Database test endpoint
echo "✅ Test 3: Database Connection Test"
echo "Testing: http://$SERVER_IP:8000/auth/test-db"
DB_TEST_RESPONSE=$(curl -s --max-time 10 "http://$SERVER_IP:8000/auth/test-db")
if [ $? -eq 0 ]; then
    echo "   ✅ Database test endpoint responding"
    echo "   Response: $DB_TEST_RESPONSE"
else
    echo "   ❌ Database test endpoint failed"
fi
echo ""

# Test 4: Frontend accessibility
echo "✅ Test 4: Frontend Accessibility"
echo "Testing: http://$SERVER_IP/"
if curl -s --max-time 10 "http://$SERVER_IP/" | grep -q "Firebase Manager"; then
    echo "   ✅ Frontend is accessible and showing Firebase Manager"
else
    echo "   ⚠️  Frontend accessible but may not be displaying correctly"
    echo "   Response preview:"
    curl -s --max-time 10 "http://$SERVER_IP/" | head -5
fi
echo ""

# Test 5: Service status
echo "✅ Test 5: Service Status"
echo "Firebase Manager Service:"
systemctl status firebase-manager --no-pager -l | head -10
echo ""
echo "PostgreSQL Service:"
systemctl status postgresql --no-pager -l | head -5
echo ""
echo "Nginx Service:"
systemctl status nginx --no-pager -l | head -5
echo ""

# Test 6: Port usage
echo "✅ Test 6: Port Usage"
echo "Port 8000 (Backend):"
netstat -tlnp | grep :8000 || echo "   No process using port 8000"
echo ""
echo "Port 80 (Frontend):"
netstat -tlnp | grep :80 || echo "   No process using port 80"
echo ""

# Test 7: Environment variables
echo "✅ Test 7: Environment Variables"
echo "Checking .env file in /var/www/firebase-manager:"
if [ -f "/var/www/firebase-manager/.env" ]; then
    echo "   ✅ .env file exists"
    echo "   Contents:"
    cat /var/www/firebase-manager/.env | grep -v PASSWORD
else
    echo "   ❌ .env file not found"
fi
echo ""

# Test 8: Database connection from command line
echo "✅ Test 8: Direct Database Connection"
echo "Testing PostgreSQL connection as emedia user:"
if sudo -u emedia psql -d firebase_manager -c "SELECT version();" 2>/dev/null; then
    echo "   ✅ Direct database connection successful"
else
    echo "   ❌ Direct database connection failed"
fi
echo ""

# Test 9: Admin user in database
echo "✅ Test 9: Admin User Verification"
echo "Checking if admin user exists in database:"
if sudo -u emedia psql -d firebase_manager -c "SELECT username, role, is_active FROM app_users WHERE username = 'admin';" 2>/dev/null; then
    echo "   ✅ Admin user query successful"
else
    echo "   ❌ Admin user query failed"
fi
echo ""

echo "🎯 Test Summary:"
echo "================="
echo "If all tests pass, your Firebase Manager Professional server is ready!"
echo ""
echo "Next steps:"
echo "1. Open http://$SERVER_IP in your browser"
echo "2. Login with admin/admin"
echo "3. Start creating profiles, projects, and users"
echo ""
echo "If any tests fail, check the logs:"
echo "  Backend logs: journalctl -u firebase-manager -f"
echo "  Nginx logs: tail -f /var/log/nginx/error.log"
echo "  PostgreSQL logs: tail -f /var/log/postgresql/postgresql-*.log"

"""
Enhanced FastAPI Backend Service for Firebase Operations with Multi-Project Parallelism
Run this with: python src/utils/firebaseBackend.py
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Body, WebSocket, WebSocketDisconnect, Depends, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Set
import firebase_admin
from firebase_admin import credentials, auth
import pyrebase
import hashlib
import json
import os
import asyncio
import time
from datetime import datetime, date, timedelta, timezone
import logging
import concurrent.futures
import threading
from collections import defaultdict
import uuid
import random
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import math
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

# CRITICAL FIX: Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()  # Load .env file
    print("✅ Environment variables loaded successfully")
except ImportError:
    print("⚠️ python-dotenv not available, using system environment variables")

# Database imports and connection
import psycopg2
from psycopg2.extras import RealDictCursor
from urllib.parse import urlparse

# Database connection pool
import threading
db_connection_lock = threading.Lock()

def get_db_connection():
    """Get database connection with proper error handling"""
    try:
        db_url = os.getenv('DB_URL')
        if not db_url:
            raise Exception("Database URL not configured")
        
        parsed = urlparse(db_url)
        conn = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=parsed.path[1:],
            user=parsed.username,
            password=parsed.password,
            cursor_factory=RealDictCursor
        )
        return conn
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise

def init_database():
    """Initialize database tables and admin user"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Create tables with proper permissions
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS app_users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                email VARCHAR(255) UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(50) DEFAULT 'user',
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_permissions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES app_users(id) ON DELETE CASCADE,
                permission_name VARCHAR(100) NOT NULL,
                is_granted BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, permission_name)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS profiles (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                owner_id INTEGER REFERENCES app_users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                admin_email VARCHAR(255) NOT NULL,
                service_account JSONB NOT NULL,
                api_key VARCHAR(500) NOT NULL,
                profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
                owner_id INTEGER REFERENCES app_users(id) ON DELETE CASCADE,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS campaigns (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
                batch_size INTEGER DEFAULT 100,
                workers INTEGER DEFAULT 5,
                template TEXT,
                status VARCHAR(50) DEFAULT 'draft',
                owner_id INTEGER REFERENCES app_users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create admin user if doesn't exist
        cursor.execute("SELECT id FROM app_users WHERE username = 'admin'")
        if not cursor.fetchone():
            cursor.execute('''
                INSERT INTO app_users (username, email, password_hash, role, is_active)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            ''', ('admin', 'admin@firebase-manager.com', 
                  'admin', 
                  'admin', True))
            
            admin_id = cursor.fetchone()['id']
            
            # Add admin permissions
            permissions = ['projects', 'users', 'campaigns', 'templates', 'ai', 'test', 
                          'profiles', 'auditLogs', 'settings', 'smtp']
            
            for perm in permissions:
                cursor.execute('''
                    INSERT INTO user_permissions (user_id, permission_name, is_granted)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, permission_name) DO NOTHING
                ''', (admin_id, perm, True))
            
            logger.info("Admin user created successfully")
        
        conn.commit()
        cursor.close()
        conn.close()
        logger.info("Database initialized successfully")
        
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise

# Google Cloud imports for project deletion
try:
    from google.cloud import resourcemanager
    GOOGLE_CLOUD_AVAILABLE = True
except ImportError:
    GOOGLE_CLOUD_AVAILABLE = False
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
from fastapi.responses import JSONResponse
from fastapi import Response

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Log environment configuration
logger.info(f"Environment: {os.getenv('ENVIRONMENT', 'development')}")
logger.info(f"Database URL configured: {'DB_URL' in os.environ}")
logger.info(f"Use Database: {os.getenv('USE_DATABASE', 'false')}")

# Log Google Cloud availability
if not GOOGLE_CLOUD_AVAILABLE:
    logger.warning("Google Cloud libraries not available. Project deletion from Google Cloud will not work.")

app = FastAPI(title="Firebase Email Campaign Backend", version="2.0.0")

# Configure request size limits for large HTML templates
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    max_age=3600,
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Global storage
firebase_apps = {}
pyrebase_apps = {}
active_campaigns = {}
campaign_stats = {}
daily_counts = {}
campaign_results = {}  # New: Store detailed campaign results
campaign_errors = {}   # New: Store detailed error logs
PROJECTS_FILE = 'projects.json'
CAMPAIGNS_FILE = 'campaigns.json'
DAILY_COUNTS_FILE = 'daily_counts.json'
CAMPAIGN_RESULTS_FILE = 'campaign_results.json'  # New: Campaign results persistence
projects = {}
AUDIT_LOG_FILE = 'audit.log'
AI_KEYS_FILE = 'ai_keys.json'
ai_keys = {}
AI_NEGATIVE_PROMPT_FILE = 'ai_negative_prompt.txt'
PROFILES_FILE = 'profiles.json'
ROLE_PERMISSIONS_FILE = 'role_permissions.json'
SMTP_SETTINGS_FILE = 'smtp_settings.json'
PASSWORD_RESET_TOKENS_FILE = 'password_reset_tokens.json'
APP_USERS_FILE = 'app_users.json'

# Admin service account for Google Cloud operations
ADMIN_SERVICE_ACCOUNT_FILE = 'admin_service_account.json'
admin_credentials = None

# WebSocket manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

ws_manager = ConnectionManager()

# Basic admin security for privileged endpoints (local-only)
security = HTTPBasic()

def verify_basic_admin(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    correct_username = secrets.compare_digest(credentials.username, "admin")
    correct_password = secrets.compare_digest(credentials.password, "admin")
    if not (correct_username and correct_password):
        raise HTTPException(status_code=401, detail="Unauthorized")

# App users store (for login and team management)
def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

# Define available features
DEFAULT_FEATURES = ['projects','users','campaigns','templates','ai','test','profiles','auditLogs','settings','smtp']

def compute_effective_permissions(username: str) -> Dict[str, bool]:
    """Compute effective permissions for a user based on role and overrides"""
    users = load_app_users()
    user = next((u for u in users.get('users', []) if u.get('username') == username), None)
    if not user:
        return { feature: False for feature in DEFAULT_FEATURES }
    
    role = user.get('role', 'user')
    # Admin: full access; others: deny-by-default so only overrides apply
    if role == 'admin':
        base = { feature: True for feature in DEFAULT_FEATURES }
    else:
        base = { feature: False for feature in DEFAULT_FEATURES }
    
    overrides = user.get('overrides', {}) or {}
    effective = { **{ feature: False for feature in DEFAULT_FEATURES }, **base }
    for k, v in overrides.items():
        if k in DEFAULT_FEATURES:
            effective[k] = bool(v)
    return effective

def load_app_users() -> Dict[str, Dict[str, str]]:
    # Structure: { "users": [{"username":"admin","password_hash":"...","role":"admin"}, ...] }
    if not os.path.exists(APP_USERS_FILE):
        # Initialize with default admin matching current local setup
        default = {
            "users": [
                {"username": "admin", "password_hash": _hash_password("Batata010..++"), "role": "admin"}
            ]
        }
        with open(APP_USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(default, f, indent=2)
        return default
    try:
        with open(APP_USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"users": []}

def save_app_users(data: Dict[str, Any]) -> None:
    try:
        with open(APP_USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save app users: {e}")

# Role permissions management
DEFAULT_FEATURES = [
    'projects','users','campaigns','templates','ai','test','profiles','auditLogs','settings','smtp'
]

def load_role_permissions() -> Dict[str, Dict[str, bool]]:
    if not os.path.exists(ROLE_PERMISSIONS_FILE):
        default = {
            'admin': { feature: True for feature in DEFAULT_FEATURES },
            'it': { feature: False for feature in DEFAULT_FEATURES },
            'user': { feature: False for feature in DEFAULT_FEATURES }
        }
        with open(ROLE_PERMISSIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(default, f, indent=2)
        return default
    try:
        with open(ROLE_PERMISSIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_role_permissions(perms: Dict[str, Dict[str, bool]]) -> None:
    with open(ROLE_PERMISSIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(perms, f, indent=2)

def compute_effective_permissions(username: str) -> Dict[str, bool]:
    users = load_app_users()
    roles = load_role_permissions()
    user = next((u for u in users.get('users', []) if u.get('username') == username), None)
    if not user:
        return { feature: False for feature in DEFAULT_FEATURES }
    role = user.get('role', 'user')
    # Admin: full access; others: deny-by-default so only overrides apply
    if role == 'admin':
        base = { feature: True for feature in DEFAULT_FEATURES }
    else:
        base = { feature: False for feature in DEFAULT_FEATURES }
    overrides = user.get('overrides', {}) or {}
    effective = { **{ feature: False for feature in DEFAULT_FEATURES }, **base }
    for k, v in overrides.items():
        effective[k] = bool(v)
    return effective

# SMTP settings and password reset tokens
def load_smtp_settings() -> Dict[str, Any]:
    if not os.path.exists(SMTP_SETTINGS_FILE):
        default = {"host": "","port": 587,"username": "","password": "","from": "","use_tls": True}
        with open(SMTP_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(default, f, indent=2)
        return default
    with open(SMTP_SETTINGS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_smtp_settings(data: Dict[str, Any]) -> None:
    with open(SMTP_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def load_reset_tokens() -> Dict[str, Any]:
    if not os.path.exists(PASSWORD_RESET_TOKENS_FILE):
        with open(PASSWORD_RESET_TOKENS_FILE, 'w', encoding='utf-8') as f:
            json.dump({}, f)
        return {}
    with open(PASSWORD_RESET_TOKENS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_reset_tokens(data: Dict[str, Any]) -> None:
    with open(PASSWORD_RESET_TOKENS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # Keep connection alive
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

# Helper to broadcast events
async def notify_ws(event: str, data: dict):
    await ws_manager.broadcast({"event": event, "data": data})

def write_audit_log(user, action, details):
    entry = {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'user': user or 'unknown',
        'action': action,
        'details': details
    }
    try:
        with open(AUDIT_LOG_FILE, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    except Exception as e:
        print(f"Failed to write audit log: {e}")

# --- App User Management Endpoints ---

class AppUserCreate(BaseModel):
    username: str
    password: str
    role: str = "member"  # admin|member
    overrides: Optional[Dict[str, bool]] = None

class AppUserUpdate(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None
    overrides: Optional[Dict[str, bool]] = None

@app.post("/auth/login")
def auth_login(body: Dict[str, str] = Body(...)):
    username = (body.get('username') or '').strip()
    password = body.get('password') or ''
    
    if not username or not password:
        raise HTTPException(status_code=401, detail="Username and password required")
    
    try:
        # Use database only for authentication
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT u.username, u.password_hash, u.role, u.id 
            FROM app_users u 
            WHERE u.username = %s AND u.is_active = TRUE
        """, (username,))
        
        user_data = cursor.fetchone()
        
        if not user_data:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        # Check password (plain text - no hashing)
        if user_data['password_hash'] != password:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        # Get permissions from database
        cursor.execute("""
            SELECT permission_name 
            FROM user_permissions 
            WHERE user_id = %s AND is_granted = TRUE
        """, (user_data['id'],))
        
        permissions = [row['permission_name'] for row in cursor.fetchall()]
        
        cursor.close()
        conn.close()
        
        logger.info(f"User {username} authenticated successfully with {len(permissions)} permissions")
        return {
            "success": True, 
            "username": username, 
            "role": user_data['role'], 
            "permissions": permissions
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Authentication error for {username}: {e}")
        raise HTTPException(status_code=500, detail="Authentication service error")

@app.get("/app-users")
def list_app_users(_: None = Depends(verify_basic_admin)):
    data = load_app_users()
    # Do not return password hashes
    return {"users": [{"username": u.get('username'), "role": u.get('role', 'member'), "overrides": u.get('overrides', {}), "email": u.get('email', '')} for u in data.get('users', [])]}

@app.post("/app-users")
def create_app_user(user: dict, _: None = Depends(verify_basic_admin)):
    data = load_app_users()
    if any(u.get('username') == user.get('username') for u in data.get('users', [])):
        raise HTTPException(status_code=400, detail="Username already exists")
    # Always initialize overrides field to prevent missing permissions
    clean_overrides = {}
    user_overrides = user.get('overrides', {}) or {}
    if user_overrides:
        # Only accept known features
        for k, v in user_overrides.items():
            if k in DEFAULT_FEATURES:
                clean_overrides[k] = bool(v)
    
    entry = {
        "username": user.get('username'), 
        "password_hash": _hash_password(user.get('password', '')), 
        "role": user.get('role', 'user'),
        "overrides": clean_overrides,
        "email": user.get('email', '')  # Add email field
    }
    data['users'].append(entry)
    save_app_users(data)
    return {"success": True}

@app.put("/app-users/{username}")
def update_app_user(username: str, update: dict, _: None = Depends(verify_basic_admin)):
    data = load_app_users()
    found = False
    for u in data.get('users', []):
        if u.get('username') == username:
            if update.get('password') is not None:
                u['password_hash'] = _hash_password(update.get('password'))
            if update.get('role') is not None:
                u['role'] = update.get('role')
            if update.get('overrides') is not None and isinstance(update.get('overrides'), dict):
                # Merge overrides instead of replacing completely, only accept known features
                existing_overrides = u.get('overrides', {}) or {}
                for k, v in update.get('overrides', {}).items():
                    if k in DEFAULT_FEATURES:
                        existing_overrides[k] = bool(v)
                u['overrides'] = existing_overrides
            # Update email if provided
            if update.get('email') is not None:
                u['email'] = update.get('email', '')
            # Ensure overrides field exists for all users
            if 'overrides' not in u:
                u['overrides'] = {}
            # Ensure email field exists
            if 'email' not in u:
                u['email'] = ''
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="User not found")
    save_app_users(data)
    # Broadcast permission update to connected clients
    try:
        asyncio.create_task(ws_manager.broadcast({"event": "permissions_updated", "data": {"username": username}}))
    except Exception:
        pass
    return {"success": True}

# Role permissions endpoints
@app.get("/role-permissions")
def get_role_permissions(_: None = Depends(verify_basic_admin)):
    return load_role_permissions()

@app.get("/auth/test-db")
def test_database_connection():
    """Test database connection and show admin user info"""
    try:
        import psycopg2
        import os
        
        db_url = os.getenv('DB_URL')
        if not db_url:
            return {"status": "error", "message": "No database URL configured"}
        
        from urllib.parse import urlparse
        parsed = urlparse(db_url)
        
        conn = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=parsed.path[1:],
            user=parsed.username,
            password=parsed.password
        )
        
        cursor = conn.cursor()
        
        # Check if admin user exists
        cursor.execute("SELECT username, role, is_active FROM app_users WHERE username = 'admin'")
        admin_user = cursor.fetchone()
        
        # Count total users
        cursor.execute("SELECT COUNT(*) FROM app_users")
        total_users = cursor.fetchone()[0]
        
        # Count permissions
        cursor.execute("SELECT COUNT(*) FROM user_permissions")
        total_permissions = cursor.fetchone()[0]
        
        cursor.close()
        conn.close()
        
        return {
            "status": "success",
            "database": "connected",
            "admin_user": {
                "exists": admin_user is not None,
                "username": admin_user[0] if admin_user else None,
                "role": admin_user[1] if admin_user else None,
                "active": admin_user[2] if admin_user else None
            },
            "total_users": total_users,
            "total_permissions": total_permissions
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.put("/role-permissions")
def put_role_permissions(perms: Dict[str, Dict[str, bool]], _: None = Depends(verify_basic_admin)):
    # Basic validation
    for role, feats in perms.items():
        for k, v in feats.items():
            if k not in DEFAULT_FEATURES:
                raise HTTPException(status_code=400, detail=f"Unknown feature: {k}")
            feats[k] = bool(v)
    save_role_permissions(perms)
    try:
        asyncio.create_task(ws_manager.broadcast({"event": "roles_updated", "data": {}}))
    except Exception:
        pass
    return {"success": True}

# SMTP settings endpoints
@app.get("/settings/smtp")
def get_smtp_settings(_: None = Depends(verify_basic_admin)):
    return load_smtp_settings()

@app.put("/settings/smtp")
def put_smtp_settings(data: Dict[str, Any], _: None = Depends(verify_basic_admin)):
    save_smtp_settings(data)
    return {"success": True}

@app.get("/auth/effective")
def get_effective_permissions(username: str = Query(..., description="Username to get permissions for")):
    """Return effective role and permissions for a given username."""
    users = load_app_users()
    user = next((u for u in users.get('users', []) if u.get('username') == username), None)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    perms = compute_effective_permissions(username)
    return {"username": username, "role": user.get('role', 'user'), "permissions": perms}

@app.delete("/app-users/{username}")
def delete_app_user(username: str, _: None = Depends(verify_basic_admin)):
    data = load_app_users()
    before = len(data.get('users', []))
    data['users'] = [u for u in data.get('users', []) if u.get('username') != username]
    if len(data['users']) == before:
        raise HTTPException(status_code=404, detail="User not found")
    save_app_users(data)
    return {"success": True}

@app.post("/app-users/migrate")
def migrate_app_users(_: None = Depends(verify_basic_admin)):
    """Ensure all users have an overrides field"""
    data = load_app_users()
    migrated = 0
    for u in data.get('users', []):
        if 'overrides' not in u:
            u['overrides'] = {}
            migrated += 1
    if migrated > 0:
        save_app_users(data)
    return {"success": True, "migrated": migrated}

@app.post("/auth/forgot-password")
def forgot_password(request: dict):
    """Send password reset email via SMTP"""
    username_or_email = request.get('username', '').strip()
    if not username_or_email:
        raise HTTPException(status_code=400, detail="Username or email is required")
    
    # Check if user exists by username OR email
    users = load_app_users()
    user = None
    
    # First try to find by username
    user = next((u for u in users.get('users', []) if u.get('username') == username_or_email), None)
    
    # If not found by username, try by email
    if not user:
        user = next((u for u in users.get('users', []) if u.get('email') == username_or_email), None)
    
    if not user:
        # Don't reveal if user exists or not for security
        return {"success": True, "message": "If the username or email exists, a reset email has been sent."}
    
    # Generate reset token (simple implementation)
    import secrets
    reset_token = secrets.token_urlsafe(32)
    
    # Store reset token with expiration (24 hours)
    from datetime import datetime, timedelta
    expiry = (datetime.now() + timedelta(hours=24)).isoformat()
    
    # Save reset token to user record
    user['reset_token'] = reset_token
    user['reset_token_expiry'] = expiry
    save_app_users(users)
    
    # Send email via SMTP
    try:
        smtp_settings = load_smtp_settings()
        if not smtp_settings.get('host'):
            raise HTTPException(status_code=500, detail="SMTP not configured")
        
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        # Create reset URL (assuming frontend runs on same host)
        reset_url = f"http://localhost:8080/reset-password?token={reset_token}&username={user.get('username')}"
        
        # Create email
        msg = MIMEMultipart()
        msg['From'] = smtp_settings.get('from', smtp_settings.get('username'))
        
        # Use user's email if available, otherwise use the input (which might be email)
        recipient_email = user.get('email') or username_or_email
        msg['To'] = recipient_email
        msg['Subject'] = "Password Reset Request"
        
        body = f"""
        Hello {user.get('username')},
        
        You requested a password reset for your Firebase Manager account.
        
        Click the link below to reset your password:
        {reset_url}
        
        This link will expire in 24 hours.
        
        If you didn't request this reset, please ignore this email.
        
        Best regards,
        Firebase Manager Team
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        # Send email
        server = smtplib.SMTP(smtp_settings['host'], smtp_settings.get('port', 587))
        if smtp_settings.get('use_tls', True):
            server.starttls()
        if smtp_settings.get('username') and smtp_settings.get('password'):
            server.login(smtp_settings['username'], smtp_settings['password'])
        server.send_message(msg)
        server.quit()
        
        logger.info(f"Password reset email sent to {user.get('username')}")
        return {"success": True, "message": "Password reset email sent successfully."}
        
    except Exception as e:
        logger.error(f"Failed to send password reset email: {e}")
        raise HTTPException(status_code=500, detail="Failed to send password reset email")

@app.post("/auth/reset-password")
def reset_password(request: dict):
    """Reset password using token"""
    username = request.get('username', '').strip()
    token = request.get('token', '').strip()
    new_password = request.get('password', '').strip()
    
    if not all([username, token, new_password]):
        raise HTTPException(status_code=400, detail="Username, token, and new password are required")
    
    # Find user and validate token
    users = load_app_users()
    user = next((u for u in users.get('users', []) if u.get('username') == username), None)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid reset token")
    
    if user.get('reset_token') != token:
        raise HTTPException(status_code=400, detail="Invalid reset token")
    
    # Check token expiry
    from datetime import datetime
    try:
        expiry = datetime.fromisoformat(user.get('reset_token_expiry', ''))
        if datetime.now() > expiry:
            raise HTTPException(status_code=400, detail="Reset token has expired")
    except:
        raise HTTPException(status_code=400, detail="Invalid reset token")
    
    # Reset password
    user['password_hash'] = _hash_password(new_password)
    # Clear reset token
    user.pop('reset_token', None)
    user.pop('reset_token_expiry', None)
    
    save_app_users(users)
    logger.info(f"Password reset successfully for user: {username}")
    
    return {"success": True, "message": "Password reset successfully"}

def save_projects_to_file():
    try:
        with open(PROJECTS_FILE, 'w') as f:
            json.dump(list(projects.values()), f, indent=2)
    except Exception as e:
        logger.error(f"Error saving projects: {str(e)}")

def load_projects_from_file():
    global projects
    logger.info(f"load_projects_from_file called. Current projects count: {len(projects) if projects else 0}")
    
    if not os.path.exists(PROJECTS_FILE):
        logger.info("PROJECTS_FILE does not exist, setting projects to empty dict")
        projects = {}
        return []
    try:
        with open(PROJECTS_FILE, 'r') as f:
            loaded = json.load(f)
            logger.info(f"Loaded {len(loaded)} projects from file")
            
            # Only update global projects if it's empty (first load)
            if not projects:
                logger.info("Global projects is empty, initializing from file")
                projects = {}
                for project in loaded:
                    project_id = project['serviceAccount'].get('project_id')
                    if project_id:
                        projects[project_id] = project
                logger.info(f"Initialized global projects with {len(projects)} projects")
                
                # --- Auto-reinitialize all projects into firebase_apps on startup ---
                for project_id, project in projects.items():
                    try:
                        from firebase_admin import credentials, initialize_app
                        cred = credentials.Certificate(project['serviceAccount'])
                        firebase_app = initialize_app(cred, name=project_id)
                        firebase_apps[project_id] = firebase_app
                        logger.info(f"Auto-initialized project {project_id} into firebase_apps on startup.")
                    except Exception as e:
                        logger.error(f"Failed to auto-initialize project {project_id} on startup: {e}")
            else:
                logger.info(f"Global projects already has {len(projects)} projects, skipping reinitialization")
        return loaded
    except Exception as e:
        logger.error(f"Error loading projects: {str(e)}")
        return []

def save_campaigns_to_file():
    try:
        with open(CAMPAIGNS_FILE, 'w') as f:
            json.dump(list(active_campaigns.values()), f, indent=2)
    except Exception as e:
        logger.error(f"Error saving campaigns: {str(e)}")

def load_campaigns_from_file():
    if not os.path.exists(CAMPAIGNS_FILE):
        return
    try:
        with open(CAMPAIGNS_FILE, 'r') as f:
            loaded = json.load(f)
            for campaign in loaded:
                active_campaigns[campaign['id']] = campaign
    except Exception as e:
        logger.error(f"Error loading campaigns: {str(e)}")

def save_daily_counts():
    try:
        with open(DAILY_COUNTS_FILE, 'w') as f:
            json.dump(daily_counts, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving daily counts: {str(e)}")

def load_daily_counts():
    if not os.path.exists(DAILY_COUNTS_FILE):
        return
    try:
        with open(DAILY_COUNTS_FILE, 'r') as f:
            loaded = json.load(f)
            daily_counts.clear()
            daily_counts.update(loaded)
    except Exception as e:
        logger.error(f"Error loading daily counts: {str(e)}")

def save_campaign_results_to_file():
    try:
        with open(CAMPAIGN_RESULTS_FILE, 'w') as f:
            json.dump(campaign_results, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving campaign results: {str(e)}")

def load_campaign_results_from_file():
    if not os.path.exists(CAMPAIGN_RESULTS_FILE):
        return
    try:
        with open(CAMPAIGN_RESULTS_FILE, 'r') as f:
            loaded = json.load(f)
            campaign_results.clear()
            campaign_results.update(loaded)
    except Exception as e:
        logger.error(f"Error loading campaign results: {str(e)}")

def reset_daily_counts_at_midnight():
    def reset_loop():
        while True:
            now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=1)))
            next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            sleep_seconds = (next_midnight - now).total_seconds()
            time.sleep(max(1, sleep_seconds))
            daily_counts.clear()
            save_daily_counts()
            logger.info("Daily counts reset at midnight GMT+1")
    t = threading.Thread(target=reset_loop, daemon=True)
    t.start()

# Call on startup
load_projects_from_file()
load_campaigns_from_file()
load_daily_counts()
load_campaign_results_from_file()  # New: Load campaign results
reset_daily_counts_at_midnight()

# Data models
class ProjectCreate(BaseModel):
    name: str
    adminEmail: str
    serviceAccount: Dict[str, Any]
    apiKey: str

class UserImport(BaseModel):
    emails: List[str]
    projectIds: List[str]

class CampaignCreate(BaseModel):
    name: str
    projectIds: List[str]
    selectedUsers: Dict[str, List[str]]
    batchSize: int
    workers: int
    template: Optional[str] = None

class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    batchSize: Optional[int] = None
    workers: Optional[int] = None
    template: Optional[str] = None

class BulkUserDelete(BaseModel):
    projectIds: List[str]
    userIds: Optional[List[str]] = None  # If None, delete all users

# New: Enhanced campaign tracking models
class CampaignResult(BaseModel):
    campaign_id: str
    project_id: str
    total_users: int
    successful: int
    failed: int
    errors: List[Dict[str, Any]]
    start_time: str
    end_time: Optional[str] = None
    status: str  # 'running', 'completed', 'failed', 'partial'

class UserResult(BaseModel):
    user_id: str
    email: str
    status: str  # 'success', 'failed', 'pending'
    error_message: Optional[str] = None
    timestamp: str

# Daily count management
def increment_daily_count(project_id: str):
    """Increment daily count for a project"""
    today = date.today().isoformat()
    key = f"{project_id}_{today}"
    
    if key not in daily_counts:
        daily_counts[key] = {"project_id": project_id, "date": today, "sent": 0}
    
    daily_counts[key]["sent"] += 1
    save_daily_counts()

def get_daily_count(project_id: str) -> int:
    """Get daily count for a project"""
    today = date.today().isoformat()
    key = f"{project_id}_{today}"
    return daily_counts.get(key, {}).get("sent", 0)

@app.get("/")
async def root():
    """Root endpoint for basic connectivity test"""
    return {
        "message": "Firebase Manager Professional Backend",
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
        "environment": os.getenv('ENVIRONMENT', 'development')
    }

@app.get("/health")
async def health_check():
    """Health check endpoint to verify backend is running and database is connected"""
    try:
        # Check if we can load environment variables
        env_status = {
            "environment": os.getenv('ENVIRONMENT', 'development'),
            "use_database": os.getenv('USE_DATABASE', 'false'),
            "db_url_configured": 'DB_URL' in os.environ,
            "backend_port": os.getenv('BACKEND_PORT', '8000')
        }
        
        # Check database connection if configured
        db_status = {"connected": False, "error": None}
        if os.getenv('USE_DATABASE') == 'true' and os.getenv('DB_URL'):
            try:
                import psycopg2
                from urllib.parse import urlparse
                
                db_url = os.getenv('DB_URL')
                parsed = urlparse(db_url)
                
                conn = psycopg2.connect(
                    host=parsed.hostname,
                    port=parsed.port or 5432,
                    database=parsed.path[1:],
                    user=parsed.username,
                    password=parsed.password
                )
                
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                cursor.close()
                conn.close()
                
                db_status = {"connected": True, "error": None}
            except Exception as e:
                db_status = {"connected": False, "error": str(e)}
        
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "environment": env_status,
            "database": db_status,
            "services": {
                "fastapi": "running",
                "postgresql": "connected" if db_status["connected"] else "disconnected"
            }
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        }

@app.post("/projects")
async def add_project(project: ProjectCreate, request: Request):
    try:
        logger.info(f"Adding project: {project.name}")
        project_id = project.serviceAccount.get('project_id')
        if not project_id:
            logger.error("Invalid service account - missing project_id")
            raise HTTPException(status_code=400, detail="Invalid service account - missing project_id")
        
        # Get profile ID from request body if provided
        profile_id = None
        try:
            body = await request.json()
            profile_id = body.get('profileId')
            logger.info(f"Profile ID from request: {profile_id}")
        except Exception as e:
            logger.warning(f"Could not parse request body for profileId: {e}")
            pass
        
        # Remove existing project if it exists
        if project_id in firebase_apps:
            try:
                firebase_admin.delete_app(firebase_apps[project_id])
            except Exception as e:
                logger.warning(f"Error removing old Firebase app: {e}")
            del firebase_apps[project_id]
        if project_id in pyrebase_apps:
            del pyrebase_apps[project_id]
        
        # Initialize Firebase Admin SDK
        try:
            cred = credentials.Certificate(project.serviceAccount)
            firebase_app = firebase_admin.initialize_app(cred, name=project_id)
            firebase_apps[project_id] = firebase_app
        except Exception as e:
            logger.error(f"Failed to initialize Firebase Admin SDK for {project_id}: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to initialize Firebase Admin SDK: {e}")
        
        # Initialize Pyrebase
        try:
            # Check if this project already has a custom authDomain saved
            stored_project = projects.get(project_id, {})
            auth_domain = stored_project.get('authDomain', f"{project_id}.firebaseapp.com")
            
            pyrebase_config = {
                "apiKey": project.apiKey,
                "authDomain": auth_domain,
                "databaseURL": f"https://{project_id}-default-rtdb.firebaseio.com",
                "storageBucket": f"{project_id}.appspot.com",
            }
            pyrebase_app = pyrebase.initialize_app(pyrebase_config)
            pyrebase_apps[project_id] = pyrebase_app
        except Exception as e:
            logger.error(f"Failed to initialize Pyrebase for {project_id}: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to initialize Pyrebase: {e}")
        
        # Get current user for ownership
        current_user = get_current_user_from_request(request)
        
        # Store project with profile association and ownership
        projects[project_id] = {
            'name': project.name,
            'adminEmail': project.adminEmail,
            'serviceAccount': project.serviceAccount,
            'apiKey': project.apiKey,
            'profileId': profile_id,
            'ownerId': current_user
        }
        save_projects_to_file()
        
        # Link project to profile if profile_id provided
        if profile_id:
            profiles = load_profiles_from_file()
            profile_found = False
            for profile in profiles:
                if profile['id'] == profile_id:
                    if project_id not in profile['projectIds']:
                        profile['projectIds'].append(project_id)
                    profile_found = True
                    break
            if profile_found:
                save_profiles_to_file(profiles)
                logger.info(f"Project {project_id} linked to profile {profile_id}")
            else:
                logger.warning(f"Profile {profile_id} not found when linking project {project_id}")
        
        logger.info(f"Project {project_id} added successfully")
        logger.info(f"Current projects in firebase_apps: {list(firebase_apps.keys())}")
        return {"success": True, "project_id": project_id, "profile_id": profile_id}
    except Exception as e:
        logger.error(f"Failed to add project: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to add project: {str(e)}")

@app.delete("/projects/{project_id}")
async def remove_project(project_id: str):
    if project_id not in projects:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Remove project from all profiles
    profiles = load_profiles_from_file()
    profiles_updated = False
    for profile in profiles:
        if project_id in profile['projectIds']:
            profile['projectIds'].remove(project_id)
            profiles_updated = True
    
    if profiles_updated:
        save_profiles_to_file(profiles)
        logger.info(f"Project {project_id} removed from all profiles")
    
    # Remove from Firebase apps
    if project_id in firebase_apps:
        try:
            firebase_admin.delete_app(firebase_apps[project_id])
        except Exception as e:
            logger.warning(f"Error removing Firebase app: {e}")
        del firebase_apps[project_id]
    
    if project_id in pyrebase_apps:
        del pyrebase_apps[project_id]
    
    del projects[project_id]
    save_projects_to_file()
    write_audit_log('admin', 'delete_project', {'project_id': project_id})
    asyncio.create_task(notify_ws('delete_project', {'project_id': project_id}))
    return {"success": True}

@app.delete("/projects/{project_id}/google-cloud")
async def delete_project_from_google_cloud(project_id: str):
    """
    Delete a Firebase project from Google Cloud using project's own service account credentials.
    This will permanently delete the project from Google Cloud Console.
    """
    if not GOOGLE_CLOUD_AVAILABLE:
        raise HTTPException(
            status_code=500, 
            detail="Google Cloud libraries not available. Please install google-cloud-resource-manager"
        )
    
    if project_id not in projects:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Get project's own service account credentials
    project_data = projects[project_id]
    service_account_data = project_data.get('serviceAccount')
    
    if not service_account_data:
        raise HTTPException(
            status_code=500,
            detail="Project service account not found"
        )
    
    try:
        # Create credentials from project's service account
        project_creds = service_account.Credentials.from_service_account_info(service_account_data)
        
        logger.info(f"Attempting to delete project {project_id} from Google Cloud using project credentials...")
        
        # Create Resource Manager client with project credentials
        client = resourcemanager.ProjectsClient(credentials=project_creds)
        
        # Delete the project
        # Note: This will permanently delete the project from Google Cloud
        operation = client.delete_project(name=f"projects/{project_id}")
        
        # Wait for the operation to complete
        result = operation.result()
        
        logger.info(f"Successfully deleted project {project_id} from Google Cloud")
        
        # Also remove from local storage
        await remove_project(project_id)
        
        write_audit_log('admin', 'delete_project_from_google_cloud', {
            'project_id': project_id,
            'status': 'success'
        })
        
        return {
            "success": True,
            "message": f"Project {project_id} has been permanently deleted from Google Cloud",
            "operation": str(result)
        }
        
    except Exception as e:
        error_msg = f"Failed to delete project {project_id} from Google Cloud: {str(e)}"
        logger.error(error_msg)
        
        write_audit_log('admin', 'delete_project_from_google_cloud', {
            'project_id': project_id,
            'status': 'failed',
            'error': str(e)
        })
        
        raise HTTPException(status_code=500, detail=error_msg)

@app.post("/projects/bulk-delete-google-cloud")
async def bulk_delete_projects_from_google_cloud(request: Request):
    """
    Bulk delete multiple Firebase projects from Google Cloud using each project's own credentials.
    """
    if not GOOGLE_CLOUD_AVAILABLE:
        raise HTTPException(
            status_code=500, 
            detail="Google Cloud libraries not available. Please install google-cloud-resource-manager"
        )
    
    try:
        data = await request.json()
        project_ids = data.get('projectIds', [])
        
        if not isinstance(project_ids, list):
            raise HTTPException(status_code=400, detail="Expected list of project IDs")
        
        if not project_ids:
            raise HTTPException(status_code=400, detail="No project IDs provided")
        
        results = []
        successful = []
        failed = []
        
        for project_id in project_ids:
            try:
                if project_id not in projects:
                    failed.append({
                        "project_id": project_id,
                        "reason": "Project not found in local storage"
                    })
                    continue
                
                # Get project's own service account credentials
                project_data = projects[project_id]
                service_account_data = project_data.get('serviceAccount')
                
                if not service_account_data:
                    failed.append({
                        "project_id": project_id,
                        "reason": "Project service account not found"
                    })
                    continue
                
                # Create credentials from project's service account
                project_creds = service_account.Credentials.from_service_account_info(service_account_data)
                
                logger.info(f"Attempting to delete project {project_id} from Google Cloud using project credentials...")
                
                # Create Resource Manager client with project credentials
                client = resourcemanager.ProjectsClient(credentials=project_creds)
                
                # Delete the project using project credentials
                operation = client.delete_project(name=f"projects/{project_id}")
                
                # Wait for the operation to complete
                result = operation.result()
                
                # Also remove from local storage
                await remove_project(project_id)
                
                successful.append(project_id)
                results.append({
                    "project_id": project_id,
                    "status": "success",
                    "operation": str(result)
                })
                
                logger.info(f"Successfully deleted project {project_id} from Google Cloud")
                
            except Exception as e:
                error_msg = f"Failed to delete project {project_id}: {str(e)}"
                logger.error(error_msg)
                
                failed.append({
                    "project_id": project_id,
                    "reason": str(e)
                })
                results.append({
                    "project_id": project_id,
                    "status": "failed",
                    "error": str(e)
                })
        
        # Write audit log
        write_audit_log('admin', 'bulk_delete_projects_from_google_cloud', {
            'project_ids': project_ids,
            'successful': successful,
            'failed': failed
        })
        
        return {
            "success": len(failed) == 0,
            "results": results,
            "summary": {
                "total": len(project_ids),
                "successful": len(successful),
                "failed": len(failed)
            }
        }
        
    except Exception as e:
        error_msg = f"Bulk delete operation failed: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

@app.get("/projects")
async def list_projects(request: Request, search: Optional[str] = None, limit: Optional[int] = None, offset: Optional[int] = None):
    current_user = get_current_user_from_request(request)
    
    # Check if user is admin
    users = load_app_users()
    user_data = next((u for u in users.get('users', []) if u.get('username') == current_user), None)
    is_admin = user_data and user_data.get('role') == 'admin'
    
    # Get projects from global variable (already loaded)
    projects_data = list(projects.values()) if projects else []
    logger.info(f"list_projects: current_user={current_user}, is_admin={is_admin}, projects_count={len(projects_data)}")
    logger.info(f"Global projects keys: {list(projects.keys()) if projects else 'None'}")
    
    # Add status to each project and support search/pagination
    project_list = []
    term = (search or "").strip().lower()
    
    for project in projects_data:
        # Get project_id from serviceAccount
        project_id = project.get('serviceAccount', {}).get('project_id')
        if not project_id:
            continue
            
        # Migrate existing projects to admin ownership if no owner set
        if 'ownerId' not in project:
            project['ownerId'] = 'admin'
            
        # Filter by ownership (admin sees all, users see only their own)
        if not is_admin and project.get('ownerId') != current_user:
            continue
            
        status = "active" if project_id in firebase_apps else "error"
        project_with_status = dict(project)
        project_with_status["status"] = status
        project_with_status["id"] = project_id  # Ensure id is present
        
        if term:
            name_lc = (project_with_status.get('name') or '').lower()
            email_lc = (project_with_status.get('adminEmail') or '').lower()
            if term not in name_lc and term not in email_lc and term not in project_id.lower():
                continue
        project_list.append(project_with_status)
    
    # Save projects if we migrated any
    if any('ownerId' not in project for project in projects_data):
        save_projects_to_file()
    
    total = len(project_list)
    if limit is not None and offset is not None:
        try:
            l = max(1, min(int(limit), 500))
            o = max(0, int(offset))
            project_list = project_list[o:o+l]
        except Exception:
            pass
    return {"projects": project_list, "total": total}

@app.get("/projects/{project_id}/users")
async def load_users(project_id: str, limit: Optional[int] = 1000, page_token: Optional[str] = None, search: Optional[str] = None):
    try:
        if project_id not in firebase_apps:
            # Project not initialized or not found
            raise HTTPException(status_code=404, detail="Project not found or not initialized. Please check your service account and project setup.")

        app = firebase_apps[project_id]
        # Firebase Admin SDK does not support arbitrary offset; use page tokens.
        # If a page_token is provided, resume from there; otherwise start fresh.
        try:
            page = auth.list_users(app=app, page_token=page_token) if page_token else auth.list_users(app=app)
        except Exception as e:
            logger.error(f"Firebase connection error for project {project_id}: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Failed to connect to Firebase: {str(e)}")

        result_users = []
        next_token: Optional[str] = None
        max_to_collect = max(1, min(int(limit or 1000), 1000))  # Firebase returns up to 1000 per page
        search_lc = (search or "").strip().lower()

        # Collect up to 'limit' users from the current page only to prevent huge loads
        for user in page.users:
            if len(result_users) >= max_to_collect:
                break
            if search_lc:
                email_lc = (user.email or "").lower()
                name_lc = (user.display_name or "").lower()
                if search_lc not in email_lc and search_lc not in name_lc:
                    continue
            created_at = None
            if user.user_metadata and user.user_metadata.creation_timestamp:
                try:
                    if hasattr(user.user_metadata.creation_timestamp, 'timestamp'):
                        created_at = datetime.fromtimestamp(user.user_metadata.creation_timestamp.timestamp()).isoformat()
                    else:
                        created_at = str(user.user_metadata.creation_timestamp)
                except Exception:
                    created_at = None
            result_users.append({
                "uid": user.uid,
                "email": user.email or "",
                "displayName": user.display_name,
                "disabled": user.disabled,
                "emailVerified": user.email_verified,
                "createdAt": created_at,
            })

        next_token = page.next_page_token if hasattr(page, 'next_page_token') else None
        return {"users": result_users, "nextPageToken": next_token}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Failed to load users for project {project_id}: {str(e)}")
        # Return a clear error message
        raise HTTPException(status_code=500, detail=f"Failed to load users: {str(e)}")

@app.post("/projects/users/import")
async def import_users_parallel(user_import: UserImport):
    """Import users across multiple projects in parallel"""
    try:
        project_ids = user_import.projectIds
        emails = user_import.emails
        user = getattr(user_import, 'user', 'admin') if hasattr(user_import, 'user') else 'admin'
        # Split emails across projects
        emails_per_project = len(emails) // len(project_ids)
        remainder = len(emails) % len(project_ids)
        project_email_chunks = {}
        start_idx = 0
        for i, project_id in enumerate(project_ids):
            chunk_size = emails_per_project + (1 if i < remainder else 0)
            project_email_chunks[project_id] = emails[start_idx:start_idx + chunk_size]
            start_idx += chunk_size
        # Import in parallel
        async def import_to_project(project_id: str, emails_chunk: List[str]):
            if project_id not in firebase_apps:
                return {"project_id": project_id, "imported": 0, "error": "Project not found"}
            app = firebase_apps[project_id]
            batch_size = 1000
            total_imported = 0
            for i in range(0, len(emails_chunk), batch_size):
                batch_emails = emails_chunk[i:i + batch_size]
                batch = []
                for email in batch_emails:
                    uid = hashlib.md5(email.encode()).hexdigest().lower()
                    user_record = auth.ImportUserRecord(email=email, uid=uid)
                    batch.append(user_record)
                try:
                    results = auth.import_users(batch, app=app)
                    total_imported += results.success_count
                    await asyncio.sleep(0.1)  # Brief pause between batches
                except Exception as e:
                    logger.error(f"Import batch failed for {project_id}: {str(e)}")
            return {"project_id": project_id, "imported": total_imported}
        # Execute imports in parallel
        tasks = []
        for project_id, emails_chunk in project_email_chunks.items():
            task = import_to_project(project_id, emails_chunk)
            tasks.append(task)
        results = await asyncio.gather(*tasks)
        total_imported = sum(result["imported"] for result in results)
        # Audit log and WebSocket notification
        write_audit_log(user, 'import_users', {'project_ids': project_ids, 'total_imported': total_imported, 'emails': emails})
        asyncio.create_task(notify_ws('import_users', {'project_ids': project_ids, 'total_imported': total_imported, 'user': user}))
        return {
            "success": True,
            "total_imported": total_imported,
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to import users: {str(e)}")

@app.delete("/projects/users/bulk")
async def bulk_delete_users(bulk_delete: dict):
    logger.info("/projects/users/bulk endpoint called")
    try:
        project_ids = bulk_delete.get('projectIds')
        user_ids = bulk_delete.get('userIds')
        if not isinstance(project_ids, list):
            return error_response("Expected list of project IDs", code="invalid_input", status_code=400)
        
        deleted = []
        failed = []
        total_deleted = 0
        
        for project_id in project_ids:
            try:
                if project_id not in firebase_apps:
                    failed.append({"project_id": project_id, "reason": "Project not found or not initialized"})
                    continue
                
                # Get Firebase Admin Auth instance for this project
                admin_auth = firebase_apps[project_id]
                project_deleted = 0
                project_failed = 0
                
                if user_ids:
                    # Delete specific users
                    for user_id in user_ids:
                        try:
                            # Use Firebase Admin SDK to delete user
                            auth.delete_user(user_id, app=admin_auth)
                            project_deleted += 1
                            total_deleted += 1
                            logger.info(f"Deleted user {user_id} from project {project_id}")
                        except Exception as e:
                            project_failed += 1
                            logger.error(f"Failed to delete user {user_id} from project {project_id}: {e}")
                else:
                    # Delete all users in the project
                    try:
                        # List all users and delete them in batches
                        page = auth.list_users(app=admin_auth)
                        while page:
                            user_uids = [user.uid for user in page.users]
                            if user_uids:
                                try:
                                    # Delete users in batch using Firebase Admin SDK
                                    results = auth.delete_users(user_uids, app=admin_auth)
                                    project_deleted += results.success_count
                                    total_deleted += results.success_count
                                    project_failed += results.failure_count
                                    logger.info(f"Deleted {results.success_count} users from project {project_id}")
                                except Exception as e:
                                    logger.error(f"Failed to delete batch of users from project {project_id}: {e}")
                                    project_failed += len(user_uids)
                            
                            # Get next page
                            page = page.get_next_page() if page.has_next_page else None
                    except Exception as e:
                        failed.append({"project_id": project_id, "reason": f"Failed to list users: {str(e)}"})
                        continue
                
                deleted.append({
                    "project_id": project_id, 
                    "deleted_count": project_deleted,
                    "failed_count": project_failed
                })
                
            except Exception as e:
                logger.error(f"Failed to delete users for project {project_id}: {e}")
                failed.append({"project_id": project_id, "reason": str(e)})
        
        if failed:
            return {
                "success": len(deleted) > 0,
                "deleted": deleted,
                "failed": failed,
                "total_deleted": total_deleted,
                "error": "Some user deletions failed.",
                "code": "partial_failure"
            }
            
        return {
            "success": True, 
            "deleted": deleted,
            "total_deleted": total_deleted,
            "message": f"Successfully deleted {total_deleted} users"
        }
        
    except Exception as e:
        logger.error(f"Error in bulk delete users: {str(e)}")
        return error_response(f"Failed to delete users: {str(e)}", code="server_error", status_code=500)

# Campaign Management
@app.post("/campaigns")
async def create_campaign(campaign: CampaignCreate, request: Request):
    """Create a new campaign with user ownership"""
    try:
        current_user = get_current_user_from_request(request)
        campaign_id = str(uuid.uuid4())
        
        campaign_data = {
            "id": campaign_id,
            "name": campaign.name,
            "projectIds": campaign.projectIds,
            "selectedUsers": campaign.selectedUsers,
            "batchSize": campaign.batchSize,
            "workers": campaign.workers,
            "template": campaign.template,
            "status": "pending",
            "createdAt": datetime.now().isoformat(),
            "processed": 0,
            "successful": 0,
            "failed": 0,
            "errors": [],
            "projectStats": {pid: {"processed": 0, "successful": 0, "failed": 0} for pid in campaign.projectIds},
            "ownerId": current_user  # Set ownership to current user
        }
        
        active_campaigns[campaign_id] = campaign_data
        save_campaigns_to_file()
        
        return {"success": True, "campaign_id": campaign_id, "campaign": campaign_data}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create campaign: {str(e)}")

@app.get("/campaigns")
async def list_campaigns(request: Request, page: int = 1, limit: int = 10):
    """List campaigns with user isolation and pagination, sorted by creation date (newest first)"""
    try:
        current_user = get_current_user_from_request(request)
        
        # Check if user is admin
        users = load_app_users()
        user_data = next((u for u in users.get('users', []) if u.get('username') == current_user), None)
        is_admin = user_data and user_data.get('role') == 'admin'
        
        # Load campaigns from file to get ownership info
        campaigns_data = load_campaigns_from_file()
        
        # Filter campaigns based on user access
        if is_admin:
            # Admin sees all campaigns
            campaigns_list = list(active_campaigns.values())
        else:
            # Users see only their own campaigns
            user_campaigns = [c for c in campaigns_data if c.get('ownerId') == current_user]
            # Only show campaigns that are also in active_campaigns
            campaigns_list = [c for c in user_campaigns if c.get('id') in active_campaigns]
        
        # Sort by creation date (newest first)
        campaigns_list.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
        
        # Calculate pagination
        total_campaigns = len(campaigns_list)
        total_pages = (total_campaigns + limit - 1) // limit
        start_index = (page - 1) * limit
        end_index = start_index + limit
        
        # Get campaigns for current page
        paginated_campaigns = campaigns_list[start_index:end_index]
        
        return {
            "campaigns": paginated_campaigns,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total_campaigns,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1
            }
        }
    except Exception as e:
        logger.error(f"Error in list_campaigns: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to list campaigns: {str(e)}")

@app.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str):
    """Get campaign details"""
    if campaign_id not in active_campaigns:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return active_campaigns[campaign_id]

@app.put("/campaigns/{campaign_id}")
async def update_campaign(campaign_id: str, campaign_update: CampaignUpdate):
    """Update campaign settings"""
    if campaign_id not in active_campaigns:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    campaign = active_campaigns[campaign_id]
    
    if campaign["status"] == "running":
        raise HTTPException(status_code=400, detail="Cannot update running campaign")
    
    if campaign_update.name:
        campaign["name"] = campaign_update.name
    if campaign_update.batchSize:
        campaign["batchSize"] = campaign_update.batchSize
    if campaign_update.workers:
        campaign["workers"] = campaign_update.workers
    if campaign_update.template:
        campaign["template"] = campaign_update.template
    
    save_campaigns_to_file()
    write_audit_log('admin', 'update_template', {'project_ids': campaign['projectIds'], 'fields_updated': list(campaign_update.dict(exclude_none=True).keys())})
    asyncio.create_task(notify_ws('template_update', {'project_id': campaign['id'], 'fields_updated': list(campaign_update.dict(exclude_none=True).keys())}))
    return {"success": True, "campaign": campaign}

@app.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: str):
    """Delete a campaign"""
    if campaign_id not in active_campaigns:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    campaign = active_campaigns[campaign_id]
    
    if campaign["status"] == "running":
        raise HTTPException(status_code=400, detail="Cannot delete running campaign")
    
    del active_campaigns[campaign_id]
    if campaign_id in campaign_stats:
        del campaign_stats[campaign_id]
    
    save_campaigns_to_file()
    write_audit_log('admin', 'delete_campaign', {'campaign_id': campaign_id})
    asyncio.create_task(notify_ws('delete_campaign', {'campaign_id': campaign_id}))
    return {"success": True}

@app.post("/campaigns/{campaign_id}/start")
async def start_campaign(campaign_id: str):
    """Start a campaign with multi-project parallelism"""
    try:
        if campaign_id not in active_campaigns:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        campaign = active_campaigns[campaign_id]
        
        if campaign["status"] == "running":
            raise HTTPException(status_code=400, detail="Campaign already running")
        
        campaign["status"] = "running"
        campaign["startedAt"] = datetime.now().isoformat()
        
        return {"success": True}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start campaign: {str(e)}")

@app.get("/projects/{project_id}/daily-count")
async def get_project_daily_count(project_id: str):
    """Get daily count for a project"""
    count = get_daily_count(project_id)
    return {"project_id": project_id, "date": date.today().isoformat(), "sent": count}

@app.get("/daily-counts")
async def get_all_daily_counts():
    """Get all daily counts"""
    return {"daily_counts": daily_counts}

def fire_all_emails(project_id, user_ids, campaign_id, workers, lightning, app_name=None):
    import concurrent.futures
    import logging
    
    # Ensure all IDs are strings and not None
    project_id = str(project_id) if project_id is not None else ''
    campaign_id = str(campaign_id) if campaign_id is not None else ''
    user_ids = [str(uid) for uid in user_ids if uid is not None]
    
    logger.info(f"[{project_id}] Starting fire_all_emails: {len(user_ids)} users, workers={workers}, lightning={lightning}")
    
    # Use existing Firebase apps instead of re-initializing
    if project_id not in firebase_apps:
        logger.error(f"[{project_id}] Project not found in firebase_apps")
        return 0
    
    if project_id not in pyrebase_apps:
        logger.error(f"[{project_id}] Project not found in pyrebase_apps")
        return 0
    
    firebase_app = firebase_apps[project_id]
    pyrebase_app = pyrebase_apps[project_id]
    pyrebase_auth = pyrebase_app.auth()
    
    # Get user emails efficiently
    user_emails = {}
    logger.info(f"[{project_id}] Looking up emails for {len(user_ids)} users")
    
    try:
        for uid in user_ids:
            try:
                user = auth.get_user(uid, app=firebase_app)
                if uid is not None and user.email is not None:
                    user_emails[str(uid)] = str(user.email)
                    logger.debug(f"[{project_id}] Found email for user {uid}: {user.email}")
                else:
                    logger.warning(f"[{project_id}] User {uid} has no email")
            except Exception as e:
                logger.error(f"[{project_id}] Failed to get user {uid}: {e}")
                continue
    except Exception as e:
        logger.error(f"[{project_id}] Failed to get user emails: {e}")
    
    email_list = list(user_emails.values())
    logger.info(f"[{project_id}] Found {len(email_list)} valid emails out of {len(user_ids)} users")
    
    # Optimize worker configuration
    if workers is None:
        workers = 10  # Increased default workers
    
    try:
        workers = int(workers)
    except Exception:
        workers = 10
    
    cpu_count = os.cpu_count() or 1
    
    if lightning:
        max_workers = min(cpu_count * 4, 50)  # More aggressive for lightning mode
    else:
        max_workers = min(workers, 30)  # Cap at 30 for normal mode
    
    try:
        max_workers = int(max_workers)
    except Exception:
        max_workers = 10
    
    if not isinstance(max_workers, int) or max_workers < 1:
        max_workers = 1
    
    logger.info(f"[{project_id}] Starting optimized parallel send with {max_workers} workers for {len(email_list)} emails.")
    
    # --- CAMPAIGN TRACKING ---
    create_campaign_result(campaign_id, project_id, len(user_emails))
    
    def fire_email(email):
        # Find user_id for this email
        user_id = None
        for uid, em in user_emails.items():
            if em == email:
                user_id = uid
                break
        
        try:
            logger.info(f"[{project_id}] Sending password reset email to: {email}")
            pyrebase_auth.send_password_reset_email(str(email))
            logger.info(f"[{project_id}] Successfully sent to {email}")
            update_campaign_result(campaign_id, project_id, True, user_id=user_id, email=email)
            return True
        except Exception as e:
            logger.error(f"[ERROR][{project_id}] Failed to send to {email}: {str(e)}")
            update_campaign_result(campaign_id, project_id, False, user_id=user_id, email=email, error=str(e))
            return False
    
    # Filter out empty strings from email_list
    email_list = [e for e in email_list if e]
    
    # Process emails in batches for better performance
    batch_size = max(1, len(email_list) // max_workers)
    successful_sends = 0
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all emails for processing
        future_to_email = {executor.submit(fire_email, email): email for email in email_list}
        
        # Collect results as they complete
        for future in concurrent.futures.as_completed(future_to_email):
            email = future_to_email[future]
            try:
                result = future.result()
                if result:
                    successful_sends += 1
            except Exception as e:
                logger.error(f"[ERROR][{project_id}] Exception for {email}: {e}")
    
    logger.info(f"[{project_id}] Finished sending {len(email_list)} emails with {max_workers} workers. Successful: {successful_sends}")
    
    # Update daily count
    increment_daily_count(project_id)
    
    # Audit logging
    write_audit_log('admin', 'send_campaign', {
        'campaign_id': campaign_id, 
        'project_id': project_id,
        'workers': max_workers, 
        'lightning': lightning,
        'emails_sent': successful_sends,
        'total_emails': len(email_list)
    })
    
    # WebSocket notification
    asyncio.create_task(notify_ws('send_campaign', {
        'campaign_id': campaign_id, 
        'project_id': project_id,
        'workers': max_workers, 
        'lightning': lightning,
        'emails_sent': successful_sends
    }))
    
    return successful_sends

@app.post("/campaigns/send")
async def send_campaign(request: Request):
    """Optimized campaign send endpoint with true parallel processing and no delays."""
    try:
        # Parse JSON body
        try:
            request_data = await request.json()
            logger.info(f"Campaign send request received: {request_data}")
        except Exception as json_error:
            logger.error(f"Failed to parse JSON request: {json_error}")
            return {"success": False, "error": "Invalid JSON request"}
        
        # Handle different request formats from frontend
        projects = request_data.get('projects')
        project_id = request_data.get('projectId')  # Single project format
        user_ids = request_data.get('userIds')  # Single project format
        lightning = request_data.get('lightning', False)
        workers = request_data.get('workers')
        campaign_id = request_data.get('campaignId', f"campaign_{int(time.time())}")
        
        # Convert single project format to multi-project format
        if project_id and user_ids:
            projects = [{'projectId': project_id, 'userIds': user_ids}]
        
        if not projects or not isinstance(projects, list):
            logger.error(f"No projects provided in request: {request_data}")
            return {"success": False, "error": "No projects provided"}
            
        # Optimize worker configuration
        if workers is None:
            workers = 10  # Increased default
        try:
            workers = int(workers)
        except Exception:
            workers = 10
            
        logger.info(f"Starting optimized campaign send: {len(projects)} projects, workers: {workers}, lightning: {lightning}")
        
        import concurrent.futures
        loop = asyncio.get_event_loop()
        
        # Track results for summary
        total_successful = 0
        total_failed = 0
        project_results = []
        
        # Execute all projects in true parallel - no delays
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(projects)) as executor:
            futures = []
            
            for proj in projects:
                proj_id_val = proj.get('projectId', '')
                project_id = str(proj_id_val) if proj_id_val is not None else ''
                user_ids = [str(uid) if uid is not None else '' for uid in (proj.get('userIds', []) or [])]
                # Filter out empty strings from user_ids
                user_ids = [uid for uid in user_ids if uid]
                app_name = f"{project_id}_{os.getpid()}_{uuid.uuid4().hex[:6]}"
                
                # Create campaign result tracking
                create_campaign_result(campaign_id, project_id, len(user_ids))
                
                futures.append({
                    'future': loop.run_in_executor(executor, fire_all_emails, project_id, user_ids, campaign_id, workers, lightning, app_name),
                    'project_id': project_id,
                    'user_count': len(user_ids)
                })
            
            # Wait for all to complete and collect results immediately
            for fut_info in futures:
                try:
                    result = await fut_info['future']
                    
                    # Get campaign results for this project
                    project_campaign_results = get_campaign_results(campaign_id, fut_info['project_id'])
                    
                    if project_campaign_results:
                        for res in project_campaign_results:
                            if res['project_id'] == fut_info['project_id']:
                                total_successful += res.get('successful', 0)
                                total_failed += res.get('failed', 0)
                                project_results.append({
                                    'project_id': fut_info['project_id'],
                                    'successful': res.get('successful', 0),
                                    'failed': res.get('failed', 0),
                                    'total': res.get('total_users', fut_info['user_count'])
                                })
                                break
                    else:
                        # Fallback if no results tracked
                        project_results.append({
                            'project_id': fut_info['project_id'],
                            'successful': fut_info['user_count'],
                            'failed': 0,
                            'total': fut_info['user_count']
                        })
                        total_successful += fut_info['user_count']
                        
                except Exception as e:
                    logger.error(f"Campaign send failed for project {fut_info['project_id']}: {e}")
                    total_failed += fut_info['user_count']
                    project_results.append({
                        'project_id': fut_info['project_id'],
                        'successful': 0,
                        'failed': fut_info['user_count'],
                        'total': fut_info['user_count'],
                        'error': str(e)
                    })
        
        # Prepare optimized response
        response = {
            "success": total_failed == 0,
            "summary": {
                "successful": total_successful,
                "failed": total_failed,
                "total": total_successful + total_failed
            },
            "project_results": project_results,
            "campaign_id": campaign_id,
            "workers": workers,
            "lightning": lightning,
            "message": f"Campaign completed: {total_successful} successful, {total_failed} failed"
        }
        
        logger.info(f"Optimized campaign send completed: {response}")
        return response
        
    except Exception as e:
        logger.error(f"Optimized campaign send failed: {str(e)}")
        return {"success": False, "error": str(e), "summary": {"successful": 0, "failed": 0, "total": 0}}

@app.post("/test-reset-email")
async def test_reset_email(request: Request):
    data = await request.json()
    email = data.get("email")
    project_id = data.get("project_id")
    
    logger.info(f"Test reset email request: email={email}, project_id={project_id}")
    
    if not email or not project_id:
        return {"success": False, "error": "Missing email or project_id"}
    
    if project_id not in pyrebase_apps:
        logger.error(f"Project {project_id} not found in pyrebase_apps")
        return {"success": False, "error": "Project not initialized"}
    
    if project_id not in firebase_apps:
        logger.error(f"Project {project_id} not found in firebase_apps")
        return {"success": False, "error": "Firebase Admin not initialized for this project"}
    
    firebase = pyrebase_apps[project_id]
    auth_client = firebase.auth()
    admin_auth = firebase_apps[project_id]
    
    created_user_uid = None
    user_deleted = False
    
    try:
        logger.info(f"Starting test email process for {email} in project {project_id}")
        
        # Generate random password
        an = random.randint(50, 9215)
        password = f'r{an}ompa{an}ordmf'
        
        # Create user with Firebase Auth
        logger.info(f"Creating test user: {email}")
        user = auth_client.create_user_with_email_and_password(email, password)
        created_user_uid = user['localId']
        
        logger.info(f"Test user created: {email} with UID: {created_user_uid}")
        
        # Send password reset email
        logger.info(f"Sending password reset email to: {email}")
        auth_client.send_password_reset_email(email)
        
        logger.info(f"Password reset email sent successfully to: {email}")
        
        # Wait a moment for the email to be sent
        await asyncio.sleep(2)
        
        # Force delete the test user using Firebase Admin SDK
        logger.info(f"Deleting test user: {email} with UID: {created_user_uid}")
        try:
            auth.delete_user(created_user_uid, app=admin_auth)
            logger.info(f"Test user deleted successfully: {email} with UID: {created_user_uid}")
            user_deleted = True
        except Exception as delete_error:
            logger.error(f"Failed to delete test user {created_user_uid}: {delete_error}")
            
            # Try alternative deletion method
            try:
                logger.info(f"Trying alternative deletion by email: {email}")
                user_record = auth.get_user_by_email(email, app=admin_auth)
                auth.delete_user(user_record.uid, app=admin_auth)
                logger.info(f"Test user deleted by email: {email}")
                user_deleted = True
            except Exception as alt_delete_error:
                logger.error(f"Alternative deletion also failed: {alt_delete_error}")
                user_deleted = False
        
        logger.info(f"Test email process completed: email={email}, user_deleted={user_deleted}")
        
        return {
            "success": True, 
            "message": f"Test email sent to {email} and test user {'deleted' if user_deleted else 'deletion failed'}",
            "user_created": True,
            "user_deleted": user_deleted
        }
        
    except Exception as e:
        logger.error(f"Test reset email failed: {str(e)}")
        
        # Cleanup: Try to delete the user if it was created
        if created_user_uid:
            logger.info(f"Attempting cleanup for created user: {created_user_uid}")
            try:
                auth.delete_user(created_user_uid, app=admin_auth)
                logger.info(f"Cleanup: Test user {created_user_uid} deleted after error")
                cleanup_success = True
            except Exception as cleanup_error:
                logger.error(f"Cleanup failed: {str(cleanup_error)}")
                cleanup_success = False
                
                # Try alternative cleanup
                try:
                    logger.info(f"Trying alternative cleanup for: {email}")
                    user_record = auth.get_user_by_email(email, app=admin_auth)
                    auth.delete_user(user_record.uid, app=admin_auth)
                    logger.info(f"Alternative cleanup successful for {email}")
                    cleanup_success = True
                except Exception as alt_cleanup_error:
                    logger.error(f"Alternative cleanup also failed: {alt_cleanup_error}")
                    cleanup_success = False
        else:
            cleanup_success = False
        
        return {
            "success": False, 
            "error": str(e),
            "user_created": bool(created_user_uid),
            "user_deleted": cleanup_success
        }

@app.post("/gemini")
async def gemini_api(request: Request):
    data = await request.json()
    prompt = data.get("prompt")
    # Dummy response for now
    return {"response": f"Gemini API would respond to: {prompt}"}

@app.post("/test-smtp")
async def test_smtp(request: Request):
    """Simple SMTP test endpoint"""
    try:
        smtp_settings = load_smtp_settings()
        logger.info(f"SMTP Settings loaded: {smtp_settings}")
        
        if not smtp_settings.get('host'):
            return {"success": False, "message": "SMTP host not configured", "settings": smtp_settings}
        
        return {"success": True, "message": "SMTP settings loaded", "settings": smtp_settings}
        
    except Exception as e:
        logger.error(f"Error testing SMTP: {e}")
        return {"success": False, "message": f"Error: {str(e)}"}

@app.post("/projects/{project_id}/reconnect")
async def reconnect_project(project_id: str):
    try:
        project = projects.get(project_id)
        if not project:
            return {"success": False, "error": "Project not found"}
        # Remove existing app if present
        if project_id in firebase_apps:
            try:
                firebase_admin.delete_app(firebase_apps[project_id])
            except Exception as e:
                logger.warning(f"Error removing old Firebase app: {e}")
            del firebase_apps[project_id]
        # Re-initialize Firebase Admin SDK
        cred = credentials.Certificate(project['serviceAccount'])
        firebase_app = firebase_admin.initialize_app(cred, name=project_id)
        firebase_apps[project_id] = firebase_app
        # Re-initialize Pyrebase with custom authDomain if available
        auth_domain = project.get('authDomain', f"{project_id}.firebaseapp.com")
        pyrebase_config = {
            "apiKey": project['apiKey'],
            "authDomain": auth_domain,
            "databaseURL": f"https://{project_id}-default-rtdb.firebaseio.com",
            "storageBucket": f"{project_id}.appspot.com",
        }
        pyrebase_app = pyrebase.initialize_app(pyrebase_config)
        pyrebase_apps[project_id] = pyrebase_app
        logger.info(f"Project {project_id} reconnected successfully")
        return {"success": True, "project_id": project_id}
    except Exception as e:
        logger.error(f"Failed to reconnect project {project_id}: {str(e)}")
        return {"success": False, "error": str(e)}

@app.get("/projects/analytics")
def get_projects_analytics():
    today = date.today().isoformat()
    analytics = {}
    # Aggregate total and today counts
    for key, count_data in daily_counts.items():
        project_id = count_data["project_id"]
        analytics.setdefault(project_id, {"total_sent": 0, "sent_today": 0, "campaigns": 0})
        analytics[project_id]["total_sent"] += count_data["sent"]
        if count_data["date"] == today:
            analytics[project_id]["sent_today"] += count_data["sent"]
    # Count campaigns per project
    for campaign in active_campaigns.values():
        for pid in campaign.get("projectIds", []):
            analytics.setdefault(pid, {"total_sent": 0, "sent_today": 0, "campaigns": 0})
            analytics[pid]["campaigns"] += 1
    return analytics

@app.post("/users/move")
async def move_users(data: dict = Body(...)):
    user = data.get('user', 'admin')
    try:
        source = data.get('source_project')
        target = data.get('target_project')
        user_ids = data.get('user_ids', [])
        if not source or not target or not user_ids:
            return {"success": False, "error": "Missing source, target, or user_ids"}
        if source not in firebase_apps or target not in firebase_apps:
            return {"success": False, "error": "Invalid project(s)"}
        admin_src = firebase_apps[source]
        admin_tgt = firebase_apps[target]
        pyrebase_tgt = pyrebase_apps[target]
        auth_src = auth
        auth_tgt = pyrebase_tgt.auth()
        moved = 0
        for uid in user_ids:
            try:
                user = auth.get_user(uid, app=admin_src)
                email = user.email
                if not email:
                    continue
                # Remove from source
                auth.delete_user(uid, app=admin_src)
                # Add to target
                password = 'TempPass123!@#'  # You may want to generate a random password
                user_tgt = auth_tgt.create_user_with_email_and_password(email, password)
                moved += 1
            except Exception as e:
                continue
        write_audit_log(user, 'move_users', {'from_project': data.get('from_project'), 'to_project': data.get('to_project'), 'userIds': data.get('userIds')})
        asyncio.create_task(notify_ws('move_users', {'from_project': data.get('from_project'), 'to_project': data.get('to_project'), 'userIds': data.get('userIds'), 'user': user}))
        return {"success": True, "moved": moved}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to move users: {str(e)}")

@app.post("/users/copy")
async def copy_users(data: dict = Body(...)):
    user = data.get('user', 'admin')
    try:
        source = data.get('source_project')
        targets = data.get('target_projects', [])
        user_ids = data.get('user_ids', [])
        if not source or not targets or not user_ids:
            return {"success": False, "error": "Missing source, targets, or user_ids"}
        if source not in firebase_apps:
            return {"success": False, "error": "Invalid source project"}
        admin_src = firebase_apps[source]
        users_to_copy = []
        for uid in user_ids:
            try:
                user = auth.get_user(uid, app=admin_src)
                email = user.email
                if email:
                    users_to_copy.append(email)
            except Exception as e:
                continue
        copied = 0
        for tgt in targets:
            if tgt not in pyrebase_apps:
                continue
            pyrebase_tgt = pyrebase_apps[tgt]
            auth_tgt = pyrebase_tgt.auth()
            for email in users_to_copy:
                try:
                    password = 'TempPass123!@#'  # You may want to generate a random password
                    auth_tgt.create_user_with_email_and_password(email, password)
                    copied += 1
                except Exception as e:
                    continue
        write_audit_log(user, 'copy_users', {'from_project': data.get('from_project'), 'to_project': data.get('to_project'), 'userIds': data.get('userIds')})
        asyncio.create_task(notify_ws('copy_users', {'from_project': data.get('from_project'), 'to_project': data.get('to_project'), 'userIds': data.get('userIds'), 'user': user}))
        return {"success": True, "copied": copied}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to copy users: {str(e)}")

# New: Campaign management functions
def create_campaign_result(campaign_id: str, project_id: str, total_users: int):
    """Create a new campaign result entry"""
    result = {
        "campaign_id": str(campaign_id) if campaign_id is not None else '',
        "project_id": str(project_id) if project_id is not None else '',
        "total_users": total_users,
        "successful": 0,
        "failed": 0,
        "errors": [],
        "start_time": datetime.now().isoformat(),
        "end_time": None,
        "status": "running"
    }
    key = f"{campaign_id}_{project_id}"
    campaign_results[key] = result
    save_campaign_results_to_file()
    return result

def update_campaign_result(campaign_id: str, project_id: str, success: bool, user_id: Optional[str] = None, email: Optional[str] = None, error: Optional[str] = None):
    """Update campaign result with success/failure"""
    campaign_id = str(campaign_id) if campaign_id is not None else ''
    project_id = str(project_id) if project_id is not None else ''
    user_id = str(user_id) if user_id is not None else ''
    email = str(email) if email is not None else ''
    error = str(error) if error is not None else ''
    key = f"{campaign_id}_{project_id}"
    if key not in campaign_results:
        return
    if success:
        campaign_results[key]["successful"] += 1
    else:
        campaign_results[key]["failed"] += 1
        if error and user_id:
            campaign_results[key]["errors"].append({
                "user_id": user_id,
                "email": email,
                "error": error,
                "timestamp": datetime.now().isoformat()
            })
    # Update status if all users processed
    total = campaign_results[key]["total_users"]
    processed = campaign_results[key]["successful"] + campaign_results[key]["failed"]
    if processed >= total:
        campaign_results[key]["end_time"] = datetime.now().isoformat()
        campaign_results[key]["status"] = "completed" if campaign_results[key]["failed"] == 0 else "partial"
    save_campaign_results_to_file()

def get_campaign_results(campaign_id: Optional[str] = None, project_id: Optional[str] = None):
    """Get campaign results, optionally filtered"""
    campaign_id = str(campaign_id) if campaign_id is not None else ''
    project_id = str(project_id) if project_id is not None else ''
    if campaign_id and project_id:
        key = f"{campaign_id}_{project_id}"
        return campaign_results.get(key)
    elif campaign_id:
        return {k: v for k, v in campaign_results.items() if k.startswith(f"{campaign_id}_")}
    else:
        return campaign_results

@app.get("/campaigns/{campaign_id}/results")
async def get_campaign_results_endpoint(campaign_id: str, project_id: Optional[str] = None):
    """Get detailed results for a specific campaign"""
    try:
        campaign_id = str(campaign_id) if campaign_id is not None else ''
        project_id = str(project_id) if project_id is not None else ''
        results = get_campaign_results(campaign_id, project_id)
        if not results:
            return {"success": False, "error": "Campaign not found"}
        return {"success": True, "results": results}
    except Exception as e:
        logger.error(f"Error getting campaign results: {str(e)}")
        return {"success": False, "error": str(e)}

@app.get("/campaigns/results/all")
async def get_all_campaign_results():
    """Get all campaign results"""
    try:
        return {"success": True, "results": campaign_results}
    except Exception as e:
        logger.error(f"Error getting all campaign results: {str(e)}")
        return {"success": False, "error": str(e)}

@app.post("/campaigns/{campaign_id}/retry")
async def retry_failed_campaign(campaign_id: str, request: dict):
    """Retry failed emails from a campaign"""
    try:
        campaign_id = str(campaign_id) if campaign_id is not None else ''
        project_id = str(request.get('projectId')) if request.get('projectId') is not None else ''
        if not project_id:
            return {"success": False, "error": "Project ID required"}
        # Get campaign results
        campaign_result = get_campaign_results(campaign_id, project_id)
        if not campaign_result:
            return {"success": False, "error": "Campaign not found"}
        # Get failed emails
        failed_emails = campaign_result.get("errors", [])
        if not failed_emails:
            return {"success": False, "error": "No failed emails to retry"}
        # Extract user IDs and emails for retry
        retry_users = []
        for error in failed_emails:
            email_val = error.get("email")
            user_id_val = error.get("user_id")
            if email_val:
                retry_users.append({
                    "user_id": str(user_id_val) if user_id_val is not None else '',
                    "email": str(email_val) if email_val is not None else ''
                })
        if not retry_users:
            return {"success": False, "error": "No valid emails to retry"}
        # Create new retry campaign
        retry_campaign_id = f"{campaign_id}_retry_{int(time.time())}"
        # Call lightning send batch for retry
        retry_request = {
            "projectId": project_id,
            "userIds": [user["user_id"] for user in retry_users if user["user_id"]],
            "lightning": True,
            "campaignId": retry_campaign_id
        }
        # This would normally call the lightning endpoint, but for now return the retry data
        return {
            "success": True,
            "retry_campaign_id": retry_campaign_id,
            "retry_users": retry_users,
            "message": f"Retry campaign created for {len(retry_users)} failed emails"
        }
    except Exception as e:
        logger.error(f"Error retrying campaign: {str(e)}")
        return {"success": False, "error": str(e)}

@app.get("/campaigns/{campaign_id}/export")
async def export_campaign_results(campaign_id: str, format: str = "json"):
    """Export campaign results in various formats"""
    try:
        campaign_id = str(campaign_id) if campaign_id is not None else ''
        results = get_campaign_results(campaign_id)
        if not results:
            return {"success": False, "error": "Campaign not found"}
        if format.lower() == "csv":
            # Convert to CSV format
            csv_data = []
            for key, result in results.items():
                project_id = str(result.get("project_id", "") or "")
                total = result.get("total_users", 0)
                successful = result.get("successful", 0)
                failed = result.get("failed", 0)
                status = str(result.get("status", "") or "")
                start_time = str(result.get("start_time", "") or "")
                end_time = str(result.get("end_time", "") or "")
                csv_data.append(f"{campaign_id},{project_id},{total},{successful},{failed},{status},{start_time},{end_time}")
            csv_header = "campaign_id,project_id,total_users,successful,failed,status,start_time,end_time\n"
            csv_content = csv_header + "\n".join(csv_data)
            return {
                "success": True,
                "format": "csv",
                "data": csv_content,
                "filename": f"campaign_{campaign_id}_results.csv"
            }
        else:
            # Default JSON format
            return {
                "success": True,
                "format": "json",
                "data": results,
                "filename": f"campaign_{campaign_id}_results.json"
            }
    except Exception as e:
        logger.error(f"Error exporting campaign results: {str(e)}")
        return {"success": False, "error": str(e)}

@app.get("/campaigns/analytics/summary")
async def get_campaign_analytics():
    """Get overall campaign analytics"""
    try:
        total_campaigns = len(set([k.split('_')[0] for k in campaign_results.keys()]))
        total_projects = len(set([v.get('project_id') for v in campaign_results.values()]))
        
        # Calculate totals
        total_users = sum([v.get('total_users', 0) for v in campaign_results.values()])
        total_successful = sum([v.get('successful', 0) for v in campaign_results.values()])
        total_failed = sum([v.get('failed', 0) for v in campaign_results.values()])
        
        # Calculate success rate
        success_rate = (total_successful / total_users * 100) if total_users > 0 else 0
        
        # Get recent campaigns (last 7 days)
        week_ago = datetime.now() - timedelta(days=7)
        recent_campaigns = [
            v for v in campaign_results.values() 
            if datetime.fromisoformat(v.get('start_time', '1970-01-01')) > week_ago
        ]
        
        return {
            "success": True,
            "analytics": {
                "total_campaigns": total_campaigns,
                "total_projects": total_projects,
                "total_users": total_users,
                "total_successful": total_successful,
                "total_failed": total_failed,
                "success_rate": round(success_rate, 2),
                "recent_campaigns": len(recent_campaigns)
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting campaign analytics: {str(e)}")
        return {"success": False, "error": str(e)}

class ResetTemplateUpdate(BaseModel):
    senderName: Optional[str] = None
    fromAddress: Optional[str] = None
    replyTo: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    project_id: str
    user: Optional[str] = None

class BulkResetTemplateUpdate(BaseModel):
    senderName: Optional[str] = None
    fromAddress: Optional[str] = None
    replyTo: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    authDomain: Optional[str] = None  # Add domain field
    project_ids: List[str]
    user: Optional[str] = None

@app.post("/api/update-reset-template")
async def update_reset_template(data: ResetTemplateUpdate, request: Request):
    """Update reset password template for a single project"""
    try:
        logger.info(f"Template update request received for project {data.project_id}")
        logger.info(f"Body length: {len(data.body) if data.body else 0} characters")
        return await _update_reset_template_internal(data.senderName, data.fromAddress, data.replyTo, data.subject, data.body, None, [data.project_id], data.user)
    except Exception as e:
        logger.error(f"Template update failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Template update failed: {str(e)}")

@app.post("/api/update-reset-template-bulk")
async def update_reset_template_bulk(data: BulkResetTemplateUpdate, request: Request):
    """Update reset password template for multiple projects in parallel"""
    try:
        logger.info(f"Bulk template update request received for {len(data.project_ids)} projects")
        logger.info(f"Body length: {len(data.body) if data.body else 0} characters")
        return await _update_reset_template_internal(data.senderName, data.fromAddress, data.replyTo, data.subject, data.body, data.authDomain, data.project_ids, data.user)
    except Exception as e:
        logger.error(f"Bulk template update failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Bulk template update failed: {str(e)}")

async def _update_reset_template_internal(senderName: Optional[str] = None, fromAddress: Optional[str] = None, replyTo: Optional[str] = None, subject: Optional[str] = None, body: Optional[str] = None, authDomain: Optional[str] = None, project_ids: List[str] = [], user: Optional[str] = None):
    # Sanitize and build payload only with provided fields
    def build_payload():
        template = {}
        if senderName is not None:
            if not isinstance(senderName, str) or len(senderName) > 100000:
                raise HTTPException(status_code=400, detail="Invalid senderName - too long")
            template["senderDisplayName"] = senderName
        if fromAddress is not None:
            if not isinstance(fromAddress, str) or len(fromAddress) > 100000:
                raise HTTPException(status_code=400, detail="Invalid fromAddress - too long")
            template["senderLocalPart"] = fromAddress
        if replyTo is not None:
            if not isinstance(replyTo, str) or len(replyTo) > 100000:
                raise HTTPException(status_code=400, detail="Invalid replyTo - too long")
            template["replyTo"] = replyTo
        if subject is not None:
            if not isinstance(subject, str) or len(subject) > 100000:
                raise HTTPException(status_code=400, detail="Invalid subject - too long")
            template["subject"] = subject
        if body is not None:
            if not isinstance(body, str) or len(body) > 1000000:  # Increased to 1MB for large HTML templates
                raise HTTPException(status_code=400, detail="Invalid body - too long (max 1MB)")
            template["body"] = body
        return template

    results = []
    template_patch = build_payload()
    if not template_patch:
        return {"success": False, "error": "No fields to update"}

    async def update_single_project(project_id: str):
        try:
            logger.info(f"Updating template for project: {project_id}")
            project = projects.get(project_id)
            if not project:
                logger.error(f"Project not found: {project_id}")
                return {"project_id": project_id, "success": False, "error": "Project not found"}
            
            service_account_info = project['serviceAccount']
            if not service_account_info:
                return {"project_id": project_id, "success": False, "error": "Service account missing"}
            credentials = service_account.Credentials.from_service_account_info(
                service_account_info,
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            authed_session = AuthorizedSession(credentials)
            
            # First update the email template
            if template_patch:
                url = f"https://identitytoolkit.googleapis.com/v2/projects/{project_id}/config?updateMask=notification.sendEmail.resetPasswordTemplate"
                payload = {
                    "notification": {
                        "sendEmail": {
                            "resetPasswordTemplate": template_patch
                        }
                    }
                }
                logger.info(f"Sending template update to Firebase API for project {project_id}")
                response = authed_session.patch(url, json=payload)
                if not response.ok:
                    error_text = response.text
                    logger.error(f"Firebase API error for project {project_id}: {response.status_code} - {error_text}")
                    return {"project_id": project_id, "success": False, "error": f"Firebase API error: {response.status_code} - {error_text}"}
                response.raise_for_status()
                logger.info(f"Reset template updated for project {project_id} by {user or 'unknown'} at {datetime.now().isoformat()}")
            
            # Update domain configuration if provided
            if authDomain and authDomain.strip():
                # Update local storage
                project['authDomain'] = authDomain.strip()
                
                # Update Firebase Auth domain configuration
                domain_url = f"https://identitytoolkit.googleapis.com/v2/projects/{project_id}/config?updateMask=signIn.allowDuplicateEmails,signIn.email,authorizedDomains"
                domain_payload = {
                    "authorizedDomains": [authDomain.strip(), f"{project_id}.firebaseapp.com"],
                    "signIn": {
                        "email": {
                            "enabled": True,
                            "passwordRequired": True
                        },
                        "allowDuplicateEmails": False
                    }
                }
                try:
                    domain_response = authed_session.patch(domain_url, json=domain_payload)
                    domain_response.raise_for_status()
                    logger.info(f"Updated Firebase Auth domain configuration for project {project_id} to include {authDomain.strip()}")
                except Exception as domain_error:
                    logger.warning(f"Failed to update Firebase Auth domain config for {project_id}: {domain_error}")
                
                # Also try to update the email sender domain via Identity Platform
                try:
                    sender_url = f"https://identitytoolkit.googleapis.com/v2/projects/{project_id}/config?updateMask=notification.sendEmail.method,notification.sendEmail.smtp"
                    sender_payload = {
                        "notification": {
                            "sendEmail": {
                                "method": "DEFAULT",
                                "smtp": {
                                    "senderEmail": f"noreply@{authDomain.strip()}",
                                    "host": "smtp.gmail.com",
                                    "port": 587,
                                    "username": f"noreply@{authDomain.strip()}",
                                    "securityMode": "START_TLS"
                                }
                            }
                        }
                    }
                    # Note: This might require additional SMTP configuration
                    # sender_response = authed_session.patch(sender_url, json=sender_payload)
                    # sender_response.raise_for_status()
                except Exception as sender_error:
                    logger.warning(f"SMTP configuration update not performed for {project_id}: {sender_error}")
                
                logger.info(f"Updated auth domain for project {project_id} to {authDomain.strip()}")
                write_audit_log(user, "update_domain", {
                    "project_id": project_id,
                    "old_domain": f"{project_id}.firebaseapp.com",
                    "new_domain": authDomain.strip()
                })
            
            write_audit_log(user, 'update_template', {'project_ids': project_ids, 'fields_updated': list(template_patch.keys()) if template_patch else []})
            asyncio.create_task(notify_ws('template_update', {'project_id': project_id, 'fields_updated': list(template_patch.keys()) if template_patch else [], 'user': user}))
            return {"project_id": project_id, "success": True, "message": "Reset password template and domain updated."}
        except Exception as e:
            logger.error(f"Failed to update reset template for project {project_id}: {e}")
            return {"project_id": project_id, "success": False, "error": str(e)}

    update_tasks = [update_single_project(project_id) for project_id in project_ids]
    results = await asyncio.gather(*update_tasks)
    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]
    
    # Save projects if any domain was updated
    if authDomain and authDomain.strip() and len(successful) > 0:
        save_projects_to_file()
    
    return {
        "success": len(failed) == 0,
        "results": results,
        "summary": {
            "total": len(project_ids),
            "successful": len(successful),
            "failed": len(failed)
        }
    }

@app.get('/audit-logs')
async def get_audit_logs(limit: int = 100):
    if not os.path.exists(AUDIT_LOG_FILE):
        return {'logs': []}
    with open(AUDIT_LOG_FILE, 'r') as f:
        lines = f.readlines()
        logs = [json.loads(line) for line in lines[-limit:]]
    logs.reverse()  # Most recent first
    return {'logs': logs}

def error_response(detail, code=None, status_code=400, context=None):
    logger.error(f"API Error [{code or status_code}]: {detail} | Context: {context}")
    return {"success": False, "error": detail, "code": code or status_code, "context": context}

@app.post("/campaigns/bulk-delete")
async def bulk_delete_campaigns(request: Request):
    logger.info("/campaigns/bulk-delete endpoint called")
    try:
        ids = await request.json()
        logger.info(f"Received for bulk delete: {ids}")
        if not isinstance(ids, list):
            return error_response("Expected list of campaign IDs", code="invalid_input", status_code=400)
        deleted = []
        failed = []
        for campaign_id in ids:
            try:
                if campaign_id in active_campaigns:
                    del active_campaigns[campaign_id]
                    deleted.append(campaign_id)
                    write_audit_log('admin', 'delete_campaign', {'campaign_id': campaign_id})
                    asyncio.create_task(notify_ws('delete_campaign', {'campaign_id': campaign_id}))
                else:
                    failed.append({"campaign_id": campaign_id, "reason": "Not found or not active"})
            except Exception as e:
                logger.error(f"Failed to delete campaign {campaign_id}: {e}")
                failed.append({"campaign_id": campaign_id, "reason": str(e)})
        save_campaigns_to_file()
        logger.info(f"Deleted campaigns: {deleted}")
        if failed:
            return {
                "success": False,
                "deleted": deleted,
                "failed": failed,
                "error": "Some campaigns could not be deleted.",
                "code": "partial_failure"
            }
        return {"success": True, "deleted": deleted}
    except Exception as e:
        logger.error(f"Error in bulk delete campaigns: {str(e)}")
        return error_response(f"Failed to delete campaigns: {str(e)}", code="server_error", status_code=500)

@app.post("/projects/bulk-delete")
async def bulk_delete_projects(request: Request):
    logger.info("/projects/bulk-delete endpoint called")
    try:
        ids = await request.json()
        logger.info(f"Received for bulk delete: {ids}")
        logger.info(f"Current projects before deletion: {list(projects.keys())}")
        if not isinstance(ids, list):
            return error_response("Expected list of project IDs", code="invalid_input", status_code=400)
        
        # Remove projects from all profiles first
        profiles = load_profiles_from_file()
        profiles_updated = False
        for profile in profiles:
            for project_id in ids:
                if project_id in profile['projectIds']:
                    profile['projectIds'].remove(project_id)
                    profiles_updated = True
        
        if profiles_updated:
            save_profiles_to_file(profiles)
            logger.info(f"Projects removed from all profiles")
        
        deleted = []
        failed = []
        for project_id in ids:
            try:
                if project_id in projects:
                    # Remove from firebase_apps and pyrebase_apps
                    if project_id in firebase_apps:
                        try:
                            firebase_admin.delete_app(firebase_apps[project_id])
                        except Exception as e:
                            logger.warning(f"Error removing Firebase app for {project_id}: {e}")
                        del firebase_apps[project_id]
                    if project_id in pyrebase_apps:
                        del pyrebase_apps[project_id]
                    del projects[project_id]
                    write_audit_log('admin', 'delete_project', {'project_id': project_id})
                    asyncio.create_task(notify_ws('delete_project', {'project_id': project_id}))
                    deleted.append(project_id)
                else:
                    logger.warning(f"Project {project_id} not found for bulk delete.")
                    failed.append({"project_id": project_id, "reason": "Not found"})
            except Exception as e:
                logger.error(f"Failed to delete project {project_id}: {e}")
                failed.append({"project_id": project_id, "reason": str(e)})
        
        save_projects_to_file()
        logger.info(f"Deleted projects: {deleted}")
        logger.info(f"Current projects after deletion: {list(projects.keys())}")
        if failed:
            return {
                "success": False,
                "deleted": deleted,
                "failed": failed,
                "error": "Some projects could not be deleted.",
                "code": "partial_failure"
            }
        return {"success": True, "deleted": deleted}
    except Exception as e:
        logger.error(f"Error in bulk delete projects: {str(e)}")
        return error_response(f"Failed to delete projects: {str(e)}", code="server_error", status_code=500)

def load_ai_keys():
    global ai_keys
    if os.path.exists(AI_KEYS_FILE):
        try:
            with open(AI_KEYS_FILE, 'r') as f:
                ai_keys = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load AI keys: {e}")
            ai_keys = {}
    else:
        ai_keys = {}

def save_ai_keys():
    try:
        with open(AI_KEYS_FILE, 'w') as f:
            json.dump(ai_keys, f)
    except Exception as e:
        logger.error(f"Failed to save AI keys: {e}")

# Load keys on startup
load_ai_keys()

def load_admin_service_account():
    """Load admin service account for Google Cloud operations"""
    global admin_credentials
    if not os.path.exists(ADMIN_SERVICE_ACCOUNT_FILE):
        logger.warning(f"Admin service account file not found: {ADMIN_SERVICE_ACCOUNT_FILE}")
        return False
    
    try:
        with open(ADMIN_SERVICE_ACCOUNT_FILE, 'r') as f:
            service_account_data = json.load(f)
        
        admin_credentials = service_account.Credentials.from_service_account_info(service_account_data)
        logger.info("Admin service account loaded successfully")
        return True
    except Exception as e:
        logger.error(f"Error loading admin service account: {str(e)}")
        return False

def get_admin_credentials():
    """Get admin credentials for Google Cloud operations"""
    if admin_credentials is None:
        if not load_admin_service_account():
            return None
    return admin_credentials

security = HTTPBasic()

def is_admin(credentials: HTTPBasicCredentials = Depends(security)):
    # Simple admin check (replace with real auth in production)
    return credentials.username == 'admin' and credentials.password == 'admin'

@app.post('/ai/set-key')
async def set_ai_key(service: str, key: str, credentials: HTTPBasicCredentials = Depends(security)):
    if not is_admin(credentials):
        logger.warning(f"Unauthorized attempt to set AI key for {service}")
        raise HTTPException(status_code=401, detail='Unauthorized')
    
    if not key or not key.strip():
        logger.error(f"Empty key provided for {service}")
        raise HTTPException(status_code=400, detail='API key cannot be empty')
    
    try:
        ai_keys[service] = key.strip()
        save_ai_keys()
        logger.info(f"AI key for {service} set by admin successfully")
        return {"success": True, "message": f"API key for {service} saved successfully"}
    except Exception as e:
        logger.error(f"Failed to save AI key for {service}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save API key: {str(e)}")

@app.get('/ai/get-key')
async def get_ai_key(service: str, credentials: HTTPBasicCredentials = Depends(security)):
    if not is_admin(credentials):
        raise HTTPException(status_code=401, detail='Unauthorized')
    key = ai_keys.get(service)
    return {"key": key}

@app.get('/ai/status')
async def get_ai_status():
    """Get status of all AI services (no auth required)"""
    status = {}
    for service in ['mistral', 'githubai']:
        key = ai_keys.get(service)
        status[service] = {
            'configured': bool(key),
            'key_length': len(key) if key else 0
        }
    return status

@app.post('/ai/mistral-generate')
async def mistral_generate(request: Request):
    data = await request.json()
    prompt = data.get('prompt')
    max_tokens = data.get('max_tokens', 256)
    temperature = data.get('temperature', 0.7)
    use_negative = data.get('use_negative', False)
    if not prompt:
        raise HTTPException(status_code=400, detail='Prompt is required')
    if use_negative:
        neg = load_negative_prompt()
        if neg:
            prompt = f"{prompt}\nNegative prompt: {neg}"
    api_key = ai_keys.get('mistral')
    if not api_key:
        raise HTTPException(status_code=400, detail='Mistral API key not set')
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    payload = {
        'model': 'mistral-tiny',
        'messages': [
            {'role': 'user', 'content': prompt}
        ],
        'max_tokens': max_tokens,
        'temperature': temperature,
    }
    try:
        response = requests.post('https://api.mistral.ai/v1/chat/completions', headers=headers, json=payload)
        if response.status_code == 200:
            result = response.json()
            return {'success': True, 'result': result}
        else:
            logger.error(f"Mistral API error: {response.status_code} - {response.text}")
            return {'success': False, 'error': response.text, 'status_code': response.status_code}
    except Exception as e:
        logger.error(f"Mistral API call failed: {e}")
        raise HTTPException(status_code=500, detail=f"Mistral API call failed: {e}")

@app.post('/ai/githubai-generate')
async def githubai_generate(request: Request):
    data = await request.json()
    prompt = data.get('prompt')
    max_tokens = data.get('max_tokens', 256)
    temperature = data.get('temperature', 0.7)
    if not prompt:
        raise HTTPException(status_code=400, detail='Prompt is required')
    api_key = ai_keys.get('githubai')
    if not api_key:
        raise HTTPException(status_code=400, detail='GitHub AI API key not set')
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'X-GitHub-Api-Version': '2022-11-28'
    }
    payload = {
        'model': 'github/gpt4o-mini',
        'messages': [
            {'role': 'user', 'content': prompt}
        ],
        'max_tokens': max_tokens,
        'temperature': temperature,
    }
    try:
        response = requests.post('https://api.github.com/v1/chat/completions', headers=headers, json=payload)
        if response.status_code == 200:
            result = response.json()
            return {'success': True, 'result': result}
        else:
            logger.error(f"GitHub AI API error: {response.status_code} - {response.text}")
            return {'success': False, 'error': response.text, 'status_code': response.status_code}
    except Exception as e:
        logger.error(f"GitHub AI API call failed: {e}")
        raise HTTPException(status_code=500, detail=f"GitHub AI API call failed: {e}")

def load_negative_prompt():
    if os.path.exists(AI_NEGATIVE_PROMPT_FILE):
        with open(AI_NEGATIVE_PROMPT_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    return ''

def save_negative_prompt(prompt: str):
    with open(AI_NEGATIVE_PROMPT_FILE, 'w', encoding='utf-8') as f:
        f.write(prompt or '')

@app.get('/ai/negative-prompt')
async def get_negative_prompt():
    return {"negative_prompt": load_negative_prompt()}

@app.post('/ai/negative-prompt')
async def set_negative_prompt(data: dict, credentials: HTTPBasicCredentials = Depends(security)):
    if not is_admin(credentials):
        raise HTTPException(status_code=401, detail='Unauthorized')
    prompt = data.get('negative_prompt', '')
    save_negative_prompt(prompt)
    logger.info(f"Negative prompt updated by admin.")
    return {"success": True, "negative_prompt": prompt}

@app.post('/admin/service-account')
async def upload_admin_service_account(request: Request, credentials: HTTPBasicCredentials = Depends(security)):
    """Upload admin service account for Google Cloud operations"""
    if not is_admin(credentials):
        raise HTTPException(status_code=401, detail='Unauthorized')
    
    try:
        data = await request.json()
        service_account_data = data.get('serviceAccount')
        
        if not service_account_data:
            raise HTTPException(status_code=400, detail='Service account data is required')
        
        # Validate service account structure
        required_fields = ['type', 'project_id', 'private_key_id', 'private_key', 'client_email', 'client_id']
        for field in required_fields:
            if field not in service_account_data:
                raise HTTPException(status_code=400, detail=f'Service account missing required field: {field}')
        
        # Save to file
        with open(ADMIN_SERVICE_ACCOUNT_FILE, 'w') as f:
            json.dump(service_account_data, f, indent=2)
        
        # Reload admin credentials
        global admin_credentials
        admin_credentials = None
        if load_admin_service_account():
            logger.info("Admin service account uploaded and loaded successfully")
            return {"success": True, "message": "Admin service account uploaded successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to load admin service account")
            
    except Exception as e:
        logger.error(f"Failed to upload admin service account: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload admin service account: {str(e)}")

@app.get('/admin/service-account/status')
async def get_admin_service_account_status(credentials: HTTPBasicCredentials = Depends(security)):
    """Check if admin service account is configured"""
    if not is_admin(credentials):
        raise HTTPException(status_code=401, detail='Unauthorized')
    
    admin_creds = get_admin_credentials()
    return {
        "configured": admin_creds is not None,
        "file_exists": os.path.exists(ADMIN_SERVICE_ACCOUNT_FILE)
    }

SESSION_TOKENS = set()
SESSION_SECRET = 'supersecretkey'  # In production, use a secure random value

@app.post('/login')
async def login(data: dict):
    username = data.get('username')
    password = data.get('password')
    if username == 'admin' and password == 'Batata010..++':
        # Generate a simple session token
        token = secrets.token_hex(32)
        SESSION_TOKENS.add(token)
        resp = JSONResponse({"success": True, "token": token})
        resp.set_cookie(key="session_token", value=token, httponly=True, samesite="lax")
        return resp
    return {"success": False, "error": "Invalid username or password."}

def require_session(request: Request):
    token = request.cookies.get("session_token")
    if not token or token not in SESSION_TOKENS:
        raise HTTPException(status_code=401, detail="Not authenticated")

# Example usage in a protected endpoint:
# @app.get('/protected')
# async def protected(request: Request):
#     require_session(request)
#     return {"success": True, "message": "You are authenticated."}

def load_profiles_from_file():
    if not os.path.exists(PROFILES_FILE):
        return []
    try:
        with open(PROFILES_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading profiles: {str(e)}")
        return []

def save_profiles_to_file(profiles):
    try:
        with open(PROFILES_FILE, 'w') as f:
            json.dump(profiles, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving profiles: {str(e)}")

def get_current_user_from_request(request: Request) -> str:
    """Extract current user from request headers or return 'anonymous'"""
    try:
        # Try to get from app-username header (set by frontend)
        username = request.headers.get('X-App-Username')
        logger.info(f"Request headers: {dict(request.headers)}")
        logger.info(f"Extracted username: {username}")
        if username and username.strip():
            return username.strip()
        
        # Try to get from referer header to extract username from URL
        referer = request.headers.get('referer', '')
        if referer and 'localhost:8080' in referer:
            # This is likely a frontend request, try to get username from localStorage or session
            # For now, fallback to 'admin' for frontend requests to prevent projects from disappearing
            logger.warning("No X-App-Username header found, but this appears to be a frontend request. Treating as admin to prevent data loss.")
            return 'admin'
        
        # Fallback to 'anonymous' - no admin privileges by default
        logger.warning("No X-App-Username header found, treating as anonymous")
        return 'anonymous'
    except Exception as e:
        logger.error(f"Error extracting username: {e}")
        return 'anonymous'

def filter_user_data(data_list: list, user: str, is_admin: bool = False) -> list:
    """Filter data to show only user's own data, unless user is admin"""
    logger.info(f"Filtering data for user: {user}, is_admin: {is_admin}, total items: {len(data_list)}")
    
    if is_admin:
        logger.info("User is admin, returning all data")
        return data_list
    
    # Only return items owned by this specific user
    filtered = [item for item in data_list if item.get('ownerId') == user]
    logger.info(f"After filtering for user {user}: {len(filtered)} items")
    for item in filtered:
        logger.info(f"  - {item.get('name', 'NO_NAME')} owned by {item.get('ownerId', 'NO_OWNER')}")
    
    return filtered

@app.get('/profiles')
def get_profiles(request: Request):
    current_user = get_current_user_from_request(request)
    logger.info(f"=== GET /profiles - Current user: {current_user} ===")
    
    # Check if user is admin
    users = load_app_users()
    user_data = next((u for u in users.get('users', []) if u.get('username') == current_user), None)
    is_admin = user_data and user_data.get('role') == 'admin'
    logger.info(f"User data found: {bool(user_data)}, is_admin: {is_admin}")
    
    all_profiles = load_profiles_from_file()
    logger.info(f"Total profiles in file: {len(all_profiles)}")
    
    # Migrate existing profiles to admin ownership if no owner set
    migrated = 0
    for profile in all_profiles:
        if 'ownerId' not in profile:
            profile['ownerId'] = 'admin'
            migrated += 1
    if migrated > 0:
        save_profiles_to_file(all_profiles)
        logger.info(f"Migrated {migrated} profiles to admin ownership")
    
    # Filter profiles based on user access
    user_profiles = filter_user_data(all_profiles, current_user, is_admin)
    logger.info(f"Returning {len(user_profiles)} profiles for user {current_user}")
    return {"profiles": user_profiles}

@app.post('/profiles')
def add_profile(profile: dict, request: Request):
    current_user = get_current_user_from_request(request)
    profiles = load_profiles_from_file()
    
    # Always assign a unique id and createdAt if not present
    if 'id' not in profile or not profile['id']:
        profile['id'] = str(int(time.time() * 1000))
    if 'createdAt' not in profile:
        profile['createdAt'] = datetime.utcnow().isoformat() + 'Z'
    if 'projectIds' not in profile:
        profile['projectIds'] = []
    
    # Set ownership to current user
    profile['ownerId'] = current_user
    
    profiles.append(profile)
    save_profiles_to_file(profiles)
    return {"success": True, "profile": profile}

@app.put('/profiles/{profile_id}')
def update_profile(profile_id: str, updates: dict, request: Request):
    current_user = get_current_user_from_request(request)
    
    # Check if user is admin
    users = load_app_users()
    user_data = next((u for u in users.get('users', []) if u.get('username') == current_user), None)
    is_admin = user_data and user_data.get('role') == 'admin'
    
    profiles = load_profiles_from_file()
    found = False
    for p in profiles:
        if p["id"] == profile_id:
            # Check ownership (admin can edit all, users can only edit their own)
            if not is_admin and p.get('ownerId') != current_user:
                raise HTTPException(status_code=403, detail="Permission denied")
            p.update(updates)
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="Profile not found")
    save_profiles_to_file(profiles)
    return {"success": True}

@app.delete('/profiles/{profile_id}')
def delete_profile(profile_id: str, request: Request):
    current_user = get_current_user_from_request(request)
    
    # Check if user is admin
    users = load_app_users()
    user_data = next((u for u in users.get('users', []) if u.get('username') == current_user), None)
    is_admin = user_data and user_data.get('role') == 'admin'
    
    profiles = load_profiles_from_file()
    profile_to_delete = None

    # Find the profile to delete
    for profile in profiles:
        if profile["id"] == profile_id:
            # Check ownership (admin can delete all, users can only delete their own)
            if not is_admin and profile.get('ownerId') != current_user:
                raise HTTPException(status_code=403, detail="Permission denied")
            profile_to_delete = profile
            break

    if not profile_to_delete:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    # Remove profile association from all projects
    project_ids_to_unlink = profile_to_delete.get('projectIds', [])
    projects_updated = False
    
    for project_id in project_ids_to_unlink:
        if project_id in projects:
            projects[project_id]['profileId'] = None
            projects_updated = True
    
    if projects_updated:
        save_projects_to_file()
        logger.info(f"Unlinked projects from deleted profile {profile_id}")
    
    # Remove the profile
    profiles = [p for p in profiles if p["id"] != profile_id]
    save_profiles_to_file(profiles)
    
    return {"success": True}

@app.post('/profiles/{profile_id}/link-projects')
async def link_projects_to_profile(profile_id: str, request: Request):
    try:
        data = await request.json()
        project_ids = data.get('projectIds', [])
        
        profiles = load_profiles_from_file()
        profile_found = False
        
        for profile in profiles:
            if profile["id"] == profile_id:
                # Add new project IDs to profile
                for project_id in project_ids:
                    if project_id not in profile['projectIds']:
                        profile['projectIds'].append(project_id)
                
                # Update project associations
                for project_id in project_ids:
                    if project_id in projects:
                        projects[project_id]['profileId'] = profile_id
                
                profile_found = True
                break
        
        if not profile_found:
            raise HTTPException(status_code=404, detail="Profile not found")
        
        save_profiles_to_file(profiles)
        save_projects_to_file()
        
        return {"success": True, "linked_projects": project_ids}
    except Exception as e:
        logger.error(f"Failed to link projects to profile: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to link projects: {str(e)}")

@app.post('/profiles/{profile_id}/unlink-projects')
async def unlink_projects_from_profile(profile_id: str, request: Request):
    try:
        data = await request.json()
        project_ids = data.get('projectIds', [])
        
        profiles = load_profiles_from_file()
        profile_found = False
        
        for profile in profiles:
            if profile["id"] == profile_id:
                # Remove project IDs from profile
                for project_id in project_ids:
                    if project_id in profile['projectIds']:
                        profile['projectIds'].remove(project_id)
                
                # Remove project associations
                for project_id in project_ids:
                    if project_id in projects:
                        projects[project_id]['profileId'] = None
                
                profile_found = True
                break
        
        if not profile_found:
            raise HTTPException(status_code=404, detail="Profile not found")
        
        save_profiles_to_file(profiles)
        save_projects_to_file()
        
        return {"success": True, "unlinked_projects": project_ids}
    except Exception as e:
        logger.error(f"Failed to unlink projects from profile: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to unlink projects: {str(e)}")

# Domain Management Models
class DomainUpdate(BaseModel):
    project_id: str
    new_auth_domain: str
    user: Optional[str] = None

class BulkDomainUpdate(BaseModel):
    project_ids: List[str]
    new_auth_domain: str
    user: Optional[str] = None

class SMTPConfig(BaseModel):
    project_id: str
    sender_email: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    security_mode: str = "START_TLS"  # START_TLS, SSL, or NONE
    user: Optional[str] = None

class BulkSMTPConfig(BaseModel):
    project_ids: List[str]
    sender_email: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    security_mode: str = "START_TLS"
    user: Optional[str] = None

@app.post("/api/update-project-domain")
async def update_project_domain(data: DomainUpdate, request: Request):
    """Update the auth domain for a single Firebase project"""
    try:
        project_id = data.project_id
        new_auth_domain = data.new_auth_domain.strip()
        user = data.user or 'admin'
        
        # Validate domain format
        if not new_auth_domain or '.' not in new_auth_domain:
            raise HTTPException(status_code=400, detail="Invalid domain format")
        
        # Check if project exists
        if project_id not in projects:
            raise HTTPException(status_code=404, detail="Project not found")
        
        project = projects[project_id]
        
        # Update the project's auth domain
        project['authDomain'] = new_auth_domain
        
        # Save to file
        save_projects_to_file()
        
        # Log the change
        write_audit_log(user, "update_domain", {
            "project_id": project_id,
            "old_domain": f"{project_id}.firebaseapp.com",
            "new_domain": new_auth_domain
        })
        
        logger.info(f"Updated auth domain for project {project_id} to {new_auth_domain}")
        
        return {
            "success": True,
            "project_id": project_id,
            "new_auth_domain": new_auth_domain,
            "message": f"Domain updated successfully to {new_auth_domain}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update domain for project {data.project_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update domain: {str(e)}")

@app.post("/api/update-project-domain-bulk")
async def update_project_domain_bulk(data: BulkDomainUpdate, request: Request):
    """Update the auth domain for multiple Firebase projects"""
    try:
        project_ids = data.project_ids
        new_auth_domain = data.new_auth_domain.strip()
        user = data.user or 'admin'
        
        # Validate domain format
        if not new_auth_domain or '.' not in new_auth_domain:
            raise HTTPException(status_code=400, detail="Invalid domain format")
        
        if not project_ids:
            raise HTTPException(status_code=400, detail="No projects selected")
        
        results = []
        successful = 0
        failed = 0
        
        for project_id in project_ids:
            try:
                # Check if project exists
                if project_id not in projects:
                    results.append({
                        "project_id": project_id,
                        "success": False,
                        "error": "Project not found"
                    })
                    failed += 1
                    continue
                
                project = projects[project_id]
                
                # Update the project's auth domain
                project['authDomain'] = new_auth_domain
                
                # Log the change
                write_audit_log(user, "update_domain", {
                    "project_id": project_id,
                    "old_domain": f"{project_id}.firebaseapp.com",
                    "new_domain": new_auth_domain
                })
                
                results.append({
                    "project_id": project_id,
                    "success": True,
                    "new_auth_domain": new_auth_domain
                })
                successful += 1
                
            except Exception as e:
                logger.error(f"Failed to update domain for project {project_id}: {e}")
                results.append({
                    "project_id": project_id,
                    "success": False,
                    "error": str(e)
                })
                failed += 1
        
        # Save all changes to file
        if successful > 0:
            save_projects_to_file()
        
        return {
            "success": True,
            "summary": {
                "total": len(project_ids),
                "successful": successful,
                "failed": failed
            },
            "results": results,
            "new_auth_domain": new_auth_domain
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update domains in bulk: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update domains: {str(e)}")

@app.get("/api/project-domains")
async def get_project_domains():
    """Get current auth domains for all projects with verification info"""
    try:
        domain_info = []
        for project_id, project in projects.items():
            current_auth_domain = project.get('authDomain', f"{project_id}.firebaseapp.com")
            default_domain = f"{project_id}.firebaseapp.com"
            has_custom_domain = current_auth_domain != default_domain
            
            domain_info.append({
                "project_id": project_id,
                "project_name": project.get('name', 'Unknown'),
                "current_auth_domain": current_auth_domain,
                "default_domain": default_domain,
                "has_custom_domain": has_custom_domain,
                "is_firebase_initialized": project_id in firebase_apps,
                "is_pyrebase_initialized": project_id in pyrebase_apps,
                "smtp_configured": "smtpConfig" in project,
                "status": "✅ Active" if project_id in firebase_apps else "❌ Inactive"
            })
        
        custom_domain_count = sum(1 for d in domain_info if d["has_custom_domain"])
        active_count = sum(1 for d in domain_info if d["is_firebase_initialized"])
        
        return {
            "success": True,
            "domains": domain_info,
            "summary": {
                "total_projects": len(domain_info),
                "custom_domain_count": custom_domain_count,
                "active_projects": active_count,
                "smtp_configured_count": sum(1 for d in domain_info if d["smtp_configured"])
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to get project domains: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get domains: {str(e)}")

@app.post("/api/configure-smtp")
async def configure_smtp(data: SMTPConfig, request: Request):
    """Configure SMTP settings for a single Firebase project"""
    try:
        project_id = data.project_id
        user = data.user or 'admin'
        
        # Check if project exists
        if project_id not in projects:
            raise HTTPException(status_code=404, detail="Project not found")
        
        project = projects[project_id]
        service_account_info = project['serviceAccount']
        
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        authed_session = AuthorizedSession(credentials)
        
        # Configure SMTP settings
        url = f"https://identitytoolkit.googleapis.com/v2/projects/{project_id}/config?updateMask=notification.sendEmail.method,notification.sendEmail.smtp"
        payload = {
            "notification": {
                "sendEmail": {
                    "method": "SMTP",
                    "smtp": {
                        "senderEmail": data.sender_email,
                        "host": data.smtp_host,
                        "port": data.smtp_port,
                        "username": data.smtp_username,
                        "password": data.smtp_password,
                        "securityMode": data.security_mode
                    }
                }
            }
        }
        
        response = authed_session.patch(url, json=payload)
        response.raise_for_status()
        
        # Store SMTP config in project (without password for security)
        project['smtpConfig'] = {
            "senderEmail": data.sender_email,
            "host": data.smtp_host,
            "port": data.smtp_port,
            "username": data.smtp_username,
            "securityMode": data.security_mode
        }
        save_projects_to_file()
        
        logger.info(f"SMTP configuration updated for project {project_id}")
        write_audit_log(user, "configure_smtp", {
            "project_id": project_id,
            "sender_email": data.sender_email,
            "smtp_host": data.smtp_host
        })
        
        return {
            "success": True,
            "project_id": project_id,
            "message": f"SMTP configuration updated for {project_id}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to configure SMTP for project {data.project_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to configure SMTP: {str(e)}")

@app.post("/api/configure-smtp-bulk")
async def configure_smtp_bulk(data: BulkSMTPConfig, request: Request):
    """Configure SMTP settings for multiple Firebase projects"""
    try:
        project_ids = data.project_ids
        user = data.user or 'admin'
        
        if not project_ids:
            raise HTTPException(status_code=400, detail="No projects selected")
        
        results = []
        successful = 0
        failed = 0
        
        for project_id in project_ids:
            try:
                # Check if project exists
                if project_id not in projects:
                    results.append({
                        "project_id": project_id,
                        "success": False,
                        "error": "Project not found"
                    })
                    failed += 1
                    continue
                
                project = projects[project_id]
                service_account_info = project['serviceAccount']
                
                credentials = service_account.Credentials.from_service_account_info(
                    service_account_info,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                authed_session = AuthorizedSession(credentials)
                
                # Configure SMTP settings
                url = f"https://identitytoolkit.googleapis.com/v2/projects/{project_id}/config?updateMask=notification.sendEmail.method,notification.sendEmail.smtp"
                payload = {
                    "notification": {
                        "sendEmail": {
                            "method": "SMTP",
                            "smtp": {
                                "senderEmail": data.sender_email,
                                "host": data.smtp_host,
                                "port": data.smtp_port,
                                "username": data.smtp_username,
                                "password": data.smtp_password,
                                "securityMode": data.security_mode
                            }
                        }
                    }
                }
                
                response = authed_session.patch(url, json=payload)
                response.raise_for_status()
                
                # Store SMTP config in project (without password for security)
                project['smtpConfig'] = {
                    "senderEmail": data.sender_email,
                    "host": data.smtp_host,
                    "port": data.smtp_port,
                    "username": data.smtp_username,
                    "securityMode": data.security_mode
                }
                
                logger.info(f"SMTP configuration updated for project {project_id}")
                write_audit_log(user, "configure_smtp", {
                    "project_id": project_id,
                    "sender_email": data.sender_email,
                    "smtp_host": data.smtp_host
                })
                
                results.append({
                    "project_id": project_id,
                    "success": True,
                    "message": "SMTP configuration updated"
                })
                successful += 1
                
            except Exception as e:
                logger.error(f"Failed to configure SMTP for project {project_id}: {e}")
                results.append({
                    "project_id": project_id,
                    "success": False,
                    "error": str(e)
                })
                failed += 1
        
        # Save all changes to file
        if successful > 0:
            save_projects_to_file()
        
        return {
            "success": True,
            "summary": {
                "total": len(project_ids),
                "successful": successful,
                "failed": failed
            },
            "results": results
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to configure SMTP in bulk: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to configure SMTP: {str(e)}")

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    adminEmail: Optional[str] = None
    serviceAccount: Optional[Dict[str, Any]] = None
    apiKey: Optional[str] = None
    profileId: Optional[str] = None

@app.put("/projects/{project_id}")
async def update_project(
    project_id: str = Path(..., description="The project ID to update"),
    update: ProjectUpdate = Body(...)
):
    if project_id not in projects:
        raise HTTPException(status_code=404, detail="Project not found")
    project = projects[project_id]
    updated = False
    # Update fields if provided
    if update.name is not None:
        project['name'] = update.name
        updated = True
    if update.adminEmail is not None:
        project['adminEmail'] = update.adminEmail
        updated = True
    if update.apiKey is not None:
        project['apiKey'] = update.apiKey
        updated = True
    if update.profileId is not None:
        project['profileId'] = update.profileId
        updated = True
    if update.serviceAccount is not None:
        project['serviceAccount'] = update.serviceAccount
        updated = True
        # Re-initialize Firebase Admin SDK
        try:
            if project_id in firebase_apps:
                firebase_admin.delete_app(firebase_apps[project_id])
                del firebase_apps[project_id]
            cred = credentials.Certificate(update.serviceAccount)
            firebase_app = firebase_admin.initialize_app(cred, name=project_id)
            firebase_apps[project_id] = firebase_app
        except Exception as e:
            logger.error(f"Failed to re-initialize Firebase Admin SDK for {project_id}: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to re-initialize Firebase Admin SDK: {e}")
        # Re-initialize Pyrebase
        try:
            auth_domain = project.get('authDomain', f"{project_id}.firebaseapp.com")
            pyrebase_config = {
                "apiKey": project['apiKey'],
                "authDomain": auth_domain,
                "databaseURL": f"https://{project_id}-default-rtdb.firebaseio.com",
                "storageBucket": f"{project_id}.appspot.com",
            }
            pyrebase_app = pyrebase.initialize_app(pyrebase_config)
            pyrebase_apps[project_id] = pyrebase_app
        except Exception as e:
            logger.error(f"Failed to re-initialize Pyrebase for {project_id}: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to re-initialize Pyrebase: {e}")
    save_projects_to_file()
    return {"success": True, "project_id": project_id, "updated": updated}

if __name__ == "__main__":
    import uvicorn
    import os
    
    # Initialize database first
    try:
        init_database()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        exit(1)
    
    # Load initial data (kept for compatibility)
    load_projects_from_file()
    load_campaigns_from_file()
    load_daily_counts()
    load_campaign_results_from_file()
    load_ai_keys()
    load_admin_service_account()
    load_profiles_from_file()
    
    # Start the daily reset thread
    reset_daily_counts_at_midnight()
    
    logger.info("Starting Firebase Campaign Backend...")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

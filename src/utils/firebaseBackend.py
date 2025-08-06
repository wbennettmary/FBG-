"""
Enhanced FastAPI Backend Service for Firebase Operations with Multi-Project Parallelism
Run this with: python src/utils/firebaseBackend.py
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Body, WebSocket, WebSocketDisconnect, Depends, Path
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

def safe_initialize_firebase_app(project_id: str, service_account_data: dict):
    """Safely initialize a Firebase app, checking if it already exists first"""
    try:
        cred = credentials.Certificate(service_account_data)
        # Check if app already exists before initializing
        try:
            firebase_app = firebase_admin.get_app(project_id)
            logger.info(f"Firebase app {project_id} already exists, reusing it")
            return firebase_app
        except ValueError:
            firebase_app = firebase_admin.initialize_app(cred, name=project_id)
            logger.info(f"Initialized new Firebase app {project_id}")
            return firebase_app
    except Exception as e:
        logger.error(f"Failed to initialize Firebase Admin SDK for {project_id}: {e}")
        raise e

# Log Google Cloud availability
if not GOOGLE_CLOUD_AVAILABLE:
    logger.warning("Google Cloud libraries not available. Project deletion from Google Cloud will not work.")

app = FastAPI(title="Firebase Email Campaign Backend", version="2.0.0")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Increase request size limits for large templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response

class LargeRequestMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        # Increase max request size to 50MB for large templates
        if request.method == "POST" and any(path in request.url.path for path in ["/api/update-reset-template", "/api/update-reset-template-bulk"]):
            # Set a larger limit for template updates
            request.scope["client_max_size"] = 50 * 1024 * 1024  # 50MB
        return await call_next(request)

app.add_middleware(LargeRequestMiddleware)

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

def save_projects_to_file():
    try:
        with open(PROJECTS_FILE, 'w') as f:
            json.dump(list(projects.values()), f, indent=2)
    except Exception as e:
        logger.error(f"Error saving projects: {str(e)}")

def load_projects_from_file():
    global projects
    if not os.path.exists(PROJECTS_FILE):
        projects = {}
        return
    try:
        with open(PROJECTS_FILE, 'r') as f:
            loaded = json.load(f)
            projects = {}
            for project in loaded:
                project_id = project['serviceAccount'].get('project_id')
                if project_id:
                    projects[project_id] = project
        # --- Auto-reinitialize all projects into firebase_apps on startup ---
        for project_id, project in projects.items():
            try:
                cred = credentials.Certificate(project['serviceAccount'])
                firebase_app = firebase_admin.initialize_app(cred, name=project_id)
                firebase_apps[project_id] = firebase_app
                firebase_config = project.get("firebaseConfig")
                if not firebase_config:
                    raise Exception("Missing firebaseConfig for project")
                pyrebase_app = pyrebase.initialize_app(firebase_config)
                pyrebase_apps[project_id] = pyrebase_app
                logger.info(f"Re-initialized project {project_id} from file")
            except Exception as e:
                logger.error(f"Failed to re-initialize project from file: {str(e)}")
    except Exception as e:
        logger.error(f"Error loading projects: {str(e)}")

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
    apiKey: Optional[str] = None
    firebaseConfig: Optional[Dict[str, Any]] = None  # NEW: full config
    profileId: Optional[str] = None
    appId: Optional[str] = None

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
    return {"message": "Firebase Email Campaign Backend v2.0", "status": "running"}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "projects_connected": len(firebase_apps),
        "active_campaigns": len(active_campaigns),
        "version": "2.0.0"
    }

# Helper to validate firebaseConfig
REQUIRED_FIREBASE_CONFIG_FIELDS = [
    "apiKey", "authDomain", "databaseURL", "storageBucket", "projectId", "appId"
]
def validate_firebase_config(config):
    missing = [k for k in REQUIRED_FIREBASE_CONFIG_FIELDS if k not in config or not config[k]]
    if missing:
        raise ValueError(f"Missing required firebaseConfig fields: {', '.join(missing)}")

@app.post("/projects")
async def add_project(project: ProjectCreate, request: Request):
    try:
        logger.info(f"Adding project: {project.name}")
        project_id = project.serviceAccount.get('project_id')
        if not project_id:
            logger.error("Invalid service account - missing project_id")
            raise HTTPException(status_code=400, detail="Invalid service account - missing project_id")
        # Remove existing project if it exists
        if project_id in firebase_apps:
            try:
                firebase_admin.delete_app(firebase_apps[project_id])
            except Exception as e:
                logger.warning(f"Error removing old Firebase app: {e}")
            del firebase_apps[project_id]
        if project_id in pyrebase_apps:
            del pyrebase_apps[project_id]
        # Validate and use full firebaseConfig
        firebase_config = project.firebaseConfig or {}
        if not firebase_config:
            firebase_config = {
                "apiKey": project.apiKey,
                "authDomain": f"{project_id}.firebaseapp.com",
                "databaseURL": f"https://{project_id}-default-rtdb.firebaseio.com",
                "storageBucket": f"{project_id}.appspot.com",
                "projectId": project_id,
                "appId": project.appId if hasattr(project, 'appId') else None
            }
        logger.info(f"firebaseConfig for {project_id}: {firebase_config}")
        try:
            validate_firebase_config(firebase_config)
        except Exception as e:
            logger.error(f"Invalid firebaseConfig: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid firebaseConfig: {e}")
        # Initialize Firebase Admin SDK
        try:
            firebase_app = safe_initialize_firebase_app(project_id, project.serviceAccount)
            firebase_apps[project_id] = firebase_app
        except Exception as e:
            logger.error(f"Failed to initialize Firebase Admin SDK for {project_id}: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to initialize Firebase Admin SDK: {e}")
        # Initialize Pyrebase
        try:
            pyrebase_app = pyrebase.initialize_app(firebase_config)
            pyrebase_apps[project_id] = pyrebase_app
            logger.info(f"Initialized Pyrebase app {project_id} with config: {firebase_config}")
        except Exception as e:
            logger.error(f"Failed to initialize Pyrebase for {project_id}: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to initialize Pyrebase: {e}")
        # Store project with full config
        projects[project_id] = {
            'name': project.name,
            'adminEmail': project.adminEmail,
            'serviceAccount': project.serviceAccount,
            'apiKey': project.apiKey,
            'firebaseConfig': firebase_config,
            'profileId': project.profileId
        }
        save_projects_to_file()
        logger.info(f"Project {project_id} added successfully")
        logger.info(f"Current projects in firebase_apps: {list(firebase_apps.keys())}")
        return {"success": True, "project_id": project_id}
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
async def list_projects():
    # Add status to each project
    project_list = []
    for project_id, project in projects.items():
        status = "active" if project_id in firebase_apps else "error"
        project_with_status = dict(project)
        project_with_status["status"] = status
        project_with_status["id"] = project_id  # Ensure id is present
        project_list.append(project_with_status)
    return {"projects": project_list}

@app.get("/projects/{project_id}/users")
async def load_users(project_id: str):
    try:
        if project_id not in firebase_apps:
            # Project not initialized or not found
            raise HTTPException(status_code=404, detail="Project not found or not initialized. Please check your service account and project setup.")

        app = firebase_apps[project_id]
        users = []
        try:
            page = auth.list_users(app=app)
        except Exception as e:
            # If the credentials are invalid or Firebase connection fails
            logger.error(f"Firebase connection error for project {project_id}: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Failed to connect to Firebase: {str(e)}")

        while page:
            for user in page.users:
                created_at = None
                if user.user_metadata and user.user_metadata.creation_timestamp:
                    try:
                        if hasattr(user.user_metadata.creation_timestamp, 'timestamp'):
                            created_at = datetime.fromtimestamp(user.user_metadata.creation_timestamp.timestamp()).isoformat()
                        else:
                            created_at = str(user.user_metadata.creation_timestamp)
                    except:
                        created_at = None

                users.append({
                    "uid": user.uid,
                    "email": user.email or "",
                    "displayName": user.display_name,
                    "disabled": user.disabled,
                    "emailVerified": user.email_verified,
                    "createdAt": created_at,
                })
            page = page.get_next_page() if page.has_next_page else None

        return {"users": users}
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
async def create_campaign(campaign: CampaignCreate):
    """Create a new campaign"""
    try:
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
            "projectStats": {pid: {"processed": 0, "successful": 0, "failed": 0} for pid in campaign.projectIds}
        }
        
        active_campaigns[campaign_id] = campaign_data
        save_campaigns_to_file()
        
        return {"success": True, "campaign_id": campaign_id, "campaign": campaign_data}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create campaign: {str(e)}")

@app.get("/campaigns")
async def list_campaigns(page: int = 1, limit: int = 10):
    """List all campaigns with pagination, sorted by creation date (newest first)"""
    try:
        # Convert campaigns to list and sort by creation date (newest first)
        campaigns_list = list(active_campaigns.values())
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

def get_project_firebase_config(project):
    firebase_config = project.get("firebaseConfig")
    if not firebase_config:
        raise Exception("Missing firebaseConfig for project")
    return firebase_config

def fire_all_emails(project_id, user_ids, campaign_id, workers, lightning, app_name=None):
    import pyrebase
    import firebase_admin
    from firebase_admin import auth
    import os
    import json
    import logging
    import concurrent.futures
    
    # Ensure all IDs are strings and not None
    project_id = str(project_id) if project_id is not None else ''
    campaign_id = str(campaign_id) if campaign_id is not None else ''
    user_ids = [str(uid) for uid in user_ids if uid is not None]
    
    # Re-initialize Firebase and Pyrebase in the process
    PROJECTS_FILE = 'projects.json'
    with open(PROJECTS_FILE, 'r') as f:
        loaded = json.load(f)
        project = next((p for p in loaded if p['serviceAccount'].get('project_id') == project_id), None)
    
    if not project:
        return
    
    cred = firebase_admin.credentials.Certificate(project['serviceAccount'])
    
    # Use a unique app name for each process to avoid conflicts
    if not app_name or app_name is None:
        app_name = f"{project_id}_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    
    # For unique app names, we can safely initialize without checking
        firebase_app = firebase_admin.initialize_app(cred, name=app_name)
    logger.info(f"Initialized Firebase app {app_name} for campaign processing")
    
    firebase_config = project.get("firebaseConfig")
    if not firebase_config:
        raise Exception("Missing firebaseConfig for project")
    pyrebase_app = pyrebase.initialize_app(firebase_config)
    pyrebase_auth = pyrebase_app.auth()
    
    # Get user emails efficiently
    user_emails = {}
    try:
        for uid in user_ids:
            try:
                user = auth.get_user(uid, app=firebase_app)
                if uid is not None and user.email is not None:
                    user_emails[str(uid)] = str(user.email)
            except Exception:
                continue
    except Exception:
        pass
    
    email_list = list(user_emails.values())
    
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
    
    logging.info(f"[{project_id}] Starting optimized parallel send with {max_workers} workers for {len(email_list)} emails.")
    
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
            pyrebase_auth.send_password_reset_email(str(email))
            logging.info(f"[{project_id}] Sent to {email}")
            update_campaign_result(campaign_id, project_id, True, user_id=user_id, email=email)
            return True
        except Exception as e:
            logging.error(f"[ERROR][{project_id}] Failed to send to {email}: {str(e)}")
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
                logging.error(f"[ERROR][{project_id}] Exception for {email}: {e}")
    
    logging.info(f"[{project_id}] Finished sending {len(email_list)} emails with {max_workers} workers. Successful: {successful_sends}")
    
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
async def send_campaign(request: dict):
    """Optimized campaign send endpoint with true parallel processing and no delays."""
    logger.info("=== CAMPAIGN SEND ENDPOINT CALLED ===")
    logger.info(f"Received request: {request}")
    try:
        # Handle different request formats from frontend
        projects = request.get('projects')
        project_id = request.get('projectId')  # Single project format
        user_ids = request.get('userIds')  # Single project format
        lightning = request.get('lightning', False)
        workers = request.get('workers')
        campaign_id = request.get('campaignId', f"campaign_{int(time.time())}")
        
        # Convert single project format to multi-project format
        if project_id and user_ids:
            projects = [{'projectId': project_id, 'userIds': user_ids}]
        
        if not projects or not isinstance(projects, list):
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
    logger.info("=== TEST RESET EMAIL ENDPOINT CALLED ===")
    data = await request.json()
    logger.info(f"Received data: {data}")
    email = data.get("email")
    project_id = data.get("project_id")
    
    logger.info(f"Email: {email}, Project ID: {project_id}")
    
    if not email or not project_id:
        logger.error("Missing email or project_id")
        return {"success": False, "error": "Missing email or project_id"}
    
    project = projects.get(project_id)
    logger.info(f"Project found: {project is not None}")
    if not project:
        logger.error(f"Project {project_id} not found")
        return {"success": False, "error": "Project not found"}
    firebase_config = project.get("firebaseConfig")
    logger.info(f"Firebase config found: {firebase_config is not None}")
    if not firebase_config:
        logger.error(f"Missing firebaseConfig for project {project_id}")
        return {"success": False, "error": "Missing firebaseConfig for project"}
    if project_id not in pyrebase_apps:
        pyrebase_app = pyrebase.initialize_app(firebase_config)
        pyrebase_apps[project_id] = pyrebase_app
    else:
        pyrebase_app = pyrebase_apps[project_id]
    firebase = pyrebase_app
    auth_client = firebase.auth()
    admin_auth = firebase_apps[project_id]
    
    created_user_uid = None
    
    try:
        # Generate random password
        an = random.randint(50, 9215)
        password = f'r{an}ompa{an}ordmf'
        
        # Create user with Firebase Auth
        user = auth_client.create_user_with_email_and_password(email, password)
        created_user_uid = user['localId']
        
        logger.info(f"Test user created: {email} with UID: {created_user_uid}")
        
        # Send password reset email
        auth_client.send_password_reset_email(email)
        
        logger.info(f"Password reset email sent to: {email}")
        
        # Wait a moment for the email to be sent (reduced wait time)
        await asyncio.sleep(1)
        
        # Force delete the test user using Firebase Admin SDK
        try:
            auth.delete_user(created_user_uid, app=admin_auth)
            logger.info(f"Test user deleted: {email} with UID: {created_user_uid}")
            user_deleted = True
        except Exception as delete_error:
            logger.error(f"Failed to delete test user {created_user_uid}: {delete_error}")
            user_deleted = False
            
            # Try alternative deletion method
            try:
                # Try to delete by email if UID method failed
                user_record = auth.get_user_by_email(email, app=admin_auth)
                auth.delete_user(user_record.uid, app=admin_auth)
                logger.info(f"Test user deleted by email: {email}")
                user_deleted = True
            except Exception as alt_delete_error:
                logger.error(f"Alternative deletion also failed: {alt_delete_error}")
                user_deleted = False
        
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
            try:
                auth.delete_user(created_user_uid, app=admin_auth)
                logger.info(f"Cleanup: Test user {created_user_uid} deleted after error")
                cleanup_success = True
            except Exception as cleanup_error:
                logger.error(f"Cleanup failed: {str(cleanup_error)}")
                cleanup_success = False
                
                # Try alternative cleanup
                try:
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
        if project_id in pyrebase_apps:
            del pyrebase_apps[project_id]
        firebase_config = project.get("firebaseConfig")
        if not firebase_config:
            return {"success": False, "error": "Missing firebaseConfig for project"}
        cred = credentials.Certificate(project['serviceAccount'])
        firebase_app = firebase_admin.initialize_app(cred, name=project_id)
        firebase_apps[project_id] = firebase_app
        pyrebase_app = pyrebase.initialize_app(firebase_config)
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
    return await _update_reset_template_internal(data.senderName, data.fromAddress, data.replyTo, data.subject, data.body, None, [data.project_id], data.user)

@app.post("/api/update-reset-template-bulk")
async def update_reset_template_bulk(data: BulkResetTemplateUpdate, request: Request):
    """Update reset password template for multiple projects in parallel"""
    logger.info("=== UPDATE RESET TEMPLATE BULK ENDPOINT CALLED ===")
    logger.info(f"Received data length - senderName: {len(data.senderName) if data.senderName else 0}, fromAddress: {len(data.fromAddress) if data.fromAddress else 0}, replyTo: {len(data.replyTo) if data.replyTo else 0}, subject: {len(data.subject) if data.subject else 0}, body: {len(data.body) if data.body else 0}, authDomain: {len(data.authDomain) if data.authDomain else 0}")
    logger.info(f"Project IDs: {data.project_ids}")
    
    # Validate input
    if not data.project_ids:
        raise HTTPException(status_code=400, detail="No projects selected")
    
    # Check template size limits
    if data.body and len(data.body) > 1000000:  # 1MB limit
        raise HTTPException(status_code=400, detail="Template body too large (max 1MB)")
    
    if data.subject and len(data.subject) > 1000:
        raise HTTPException(status_code=400, detail="Subject too long (max 1000 characters)")
    
    return await _update_reset_template_internal(data.senderName, data.fromAddress, data.replyTo, data.subject, data.body, data.authDomain, data.project_ids, data.user)

async def _update_reset_template_internal(senderName: Optional[str] = None, fromAddress: Optional[str] = None, replyTo: Optional[str] = None, subject: Optional[str] = None, body: Optional[str] = None, authDomain: Optional[str] = None, project_ids: List[str] = [], user: Optional[str] = None):
    # Sanitize and build payload only with provided fields
    def build_payload():
        template = {}
        if senderName is not None:
            if not isinstance(senderName, str) or len(senderName) > 100000:
                raise HTTPException(status_code=400, detail="Invalid senderName (max 100KB)")
            template["senderDisplayName"] = senderName
        if fromAddress is not None:
            if not isinstance(fromAddress, str) or len(fromAddress) > 100000:
                raise HTTPException(status_code=400, detail="Invalid fromAddress (max 100KB)")
            template["senderLocalPart"] = fromAddress
        if replyTo is not None:
            if not isinstance(replyTo, str) or len(replyTo) > 100000:
                raise HTTPException(status_code=400, detail="Invalid replyTo (max 100KB)")
            template["replyTo"] = replyTo
        if subject is not None:
            if not isinstance(subject, str) or len(subject) > 100000:
                raise HTTPException(status_code=400, detail="Invalid subject (max 100KB)")
            template["subject"] = subject
        if body is not None:
            if not isinstance(body, str) or len(body) > 1000000:  # 1MB limit
                raise HTTPException(status_code=400, detail="Invalid body (max 1MB)")
            template["body"] = body
        return template

    results = []
    template_patch = build_payload()
    if not template_patch:
        return {"success": False, "error": "No fields to update"}

    async def update_single_project(project_id: str):
        try:
            project = projects.get(project_id)
            if not project:
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
                response = authed_session.patch(url, json=payload)
                response.raise_for_status()
                logger.info(f"Reset template updated for project {project_id} by {user or 'unknown'} at {datetime.now().isoformat()}")
            
            # Update domain configuration if provided
            if authDomain and authDomain.strip():
                # Update local storage
                project['authDomain'] = authDomain.strip()
                
                # Update Firebase Auth domain configuration using the correct API
                try:
                    # First, get current authorized domains
                    domains_url = f"https://identitytoolkit.googleapis.com/v2/projects/{project_id}/config"
                    domains_response = authed_session.get(domains_url)
                    domains_response.raise_for_status()
                    current_config = domains_response.json()
                    
                    # Extract current authorized domains
                    current_domains = current_config.get('authorizedDomains', [])
                    new_domain = authDomain.strip()
                    
                    # Add new domain if not already present
                    if new_domain not in current_domains:
                        current_domains.append(new_domain)
                        
                        # Update with new domains
                        domain_url = f"https://identitytoolkit.googleapis.com/v2/projects/{project_id}/config?updateMask=authorizedDomains"
                        domain_payload = {
                            "authorizedDomains": current_domains
                        }
                        
                        domain_response = authed_session.patch(domain_url, json=domain_payload)
                        domain_response.raise_for_status()
                        logger.info(f"Successfully updated authorized domains for project {project_id}: {current_domains}")
                    else:
                        logger.info(f"Domain {new_domain} already authorized for project {project_id}")
                        
                except Exception as domain_error:
                    logger.error(f"Failed to update Firebase Auth domain config for {project_id}: {domain_error}")
                    # Continue with template update even if domain update fails
                
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

@app.get('/profiles')
def get_profiles():
    return {"profiles": load_profiles_from_file()}

@app.post('/profiles')
def add_profile(profile: dict):
    profiles = load_profiles_from_file()
    # Always assign a unique id and createdAt if not present
    if 'id' not in profile or not profile['id']:
        profile['id'] = str(int(time.time() * 1000))
    if 'createdAt' not in profile:
        profile['createdAt'] = datetime.utcnow().isoformat() + 'Z'
    if 'projectIds' not in profile:
        profile['projectIds'] = []
    profiles.append(profile)
    save_profiles_to_file(profiles)
    return {"success": True}

@app.put('/profiles/{profile_id}')
def update_profile(profile_id: str, updates: dict):
    profiles = load_profiles_from_file()
    found = False
    for p in profiles:
        if p["id"] == profile_id:
            p.update(updates)
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="Profile not found")
    save_profiles_to_file(profiles)
    return {"success": True}

@app.delete('/profiles/{profile_id}')
def delete_profile(profile_id: str):
    profiles = load_profiles_from_file()
    profile_to_delete = None
    
    # Find the profile to delete
    for profile in profiles:
        if profile["id"] == profile_id:
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
        
        logger.info(f"=== UPDATE PROJECT DOMAIN ENDPOINT CALLED ===")
        logger.info(f"Project ID: {project_id}, New Domain: {new_auth_domain}")
        
        # Validate domain format
        if not new_auth_domain or '.' not in new_auth_domain:
            raise HTTPException(status_code=400, detail="Invalid domain format")
        
        # Check if project exists
        if project_id not in projects:
            raise HTTPException(status_code=404, detail="Project not found")
        
        project = projects[project_id]
        service_account_info = project['serviceAccount']
        
        # Create authenticated session
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        authed_session = AuthorizedSession(credentials)
        
        # First, get current authorized domains
        try:
            domains_url = f"https://identitytoolkit.googleapis.com/v2/projects/{project_id}/config"
            domains_response = authed_session.get(domains_url)
            domains_response.raise_for_status()
            current_config = domains_response.json()
            
            current_domains = current_config.get('authorizedDomains', [])
            logger.info(f"Current authorized domains: {current_domains}")
            
            # Add new domain if not already present
            if new_auth_domain not in current_domains:
                current_domains.append(new_auth_domain)
                
                # Update with new domains
                url = f"https://identitytoolkit.googleapis.com/v2/projects/{project_id}/config?updateMask=authorizedDomains"
                payload = {
                    "authorizedDomains": current_domains
                }
                
                logger.info(f"Updating domains with payload: {payload}")
                response = authed_session.patch(url, json=payload)
                response.raise_for_status()
                
                logger.info(f"Successfully updated authorized domains: {current_domains}")
            else:
                logger.info(f"Domain {new_auth_domain} already authorized")
                
        except Exception as domain_error:
            logger.error(f"Failed to update authorized domains: {domain_error}")
            raise HTTPException(status_code=500, detail=f"Failed to update authorized domains: {str(domain_error)}")
        
        # Update the project's auth domain in local storage
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
            "authorized_domains": current_domains,
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
                service_account_info = project['serviceAccount']
                
                # Create authenticated session
                credentials = service_account.Credentials.from_service_account_info(
                    service_account_info,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                authed_session = AuthorizedSession(credentials)
                
                # Update authorized domains in Firebase
                try:
                    domains_url = f"https://identitytoolkit.googleapis.com/v2/projects/{project_id}/config"
                    domains_response = authed_session.get(domains_url)
                    domains_response.raise_for_status()
                    current_config = domains_response.json()
                    
                    current_domains = current_config.get('authorizedDomains', [])
                    
                    # Add new domain if not already present
                    if new_auth_domain not in current_domains:
                        current_domains.append(new_auth_domain)
                        
                        # Update with new domains
                        url = f"https://identitytoolkit.googleapis.com/v2/projects/{project_id}/config?updateMask=authorizedDomains"
                        payload = {
                            "authorizedDomains": current_domains
                        }
                        
                        response = authed_session.patch(url, json=payload)
                        response.raise_for_status()
                        logger.info(f"Updated authorized domains for {project_id}: {current_domains}")
                    else:
                        logger.info(f"Domain {new_auth_domain} already authorized for {project_id}")
                        
                except Exception as domain_error:
                    logger.error(f"Failed to update authorized domains for {project_id}: {domain_error}")
                    raise Exception(f"Failed to update authorized domains: {str(domain_error)}")
                
                # Update the project's auth domain in local storage
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
            
            # Try to get real authorized domains from Firebase
            authorized_domains = []
            try:
                if project_id in firebase_apps and 'serviceAccount' in project:
                    service_account_info = project['serviceAccount']
                    credentials = service_account.Credentials.from_service_account_info(
                        service_account_info,
                        scopes=["https://www.googleapis.com/auth/cloud-platform"]
                    )
                    authed_session = AuthorizedSession(credentials)
                    
                    domains_url = f"https://identitytoolkit.googleapis.com/v2/projects/{project_id}/config"
                    domains_response = authed_session.get(domains_url)
                    domains_response.raise_for_status()
                    current_config = domains_response.json()
                    authorized_domains = current_config.get('authorizedDomains', [])
                    logger.info(f"Retrieved authorized domains for {project_id}: {authorized_domains}")
            except Exception as e:
                logger.warning(f"Failed to get authorized domains for {project_id}: {e}")
                authorized_domains = [default_domain]  # Fallback to default
            
            domain_info.append({
                "project_id": project_id,
                "project_name": project.get('name', 'Unknown'),
                "current_auth_domain": current_auth_domain,
                "default_domain": default_domain,
                "authorized_domains": authorized_domains,
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

@app.put("/projects/{project_id}/auth-domain")
async def update_auth_domain(project_id: str, data: dict):
    """Update the authDomain in the firebaseConfig for a project and re-init Pyrebase."""
    new_auth_domain = data.get("authDomain")
    if not new_auth_domain:
        raise HTTPException(status_code=400, detail="Missing authDomain")
    project = projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    firebase_config = project.get("firebaseConfig")
    if not firebase_config:
        raise HTTPException(status_code=400, detail="No firebaseConfig for project")
    firebase_config["authDomain"] = new_auth_domain
    # Re-init Pyrebase
    try:
        pyrebase_app = pyrebase.initialize_app(firebase_config)
        pyrebase_apps[project_id] = pyrebase_app
        logger.info(f"Updated authDomain for {project_id} to {new_auth_domain}")
    except Exception as e:
        logger.error(f"Failed to re-init Pyrebase for {project_id}: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to re-init Pyrebase: {e}")
    save_projects_to_file()
    return {"success": True, "authDomain": new_auth_domain}

if __name__ == "__main__":
    import uvicorn
    
    # Load initial data
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

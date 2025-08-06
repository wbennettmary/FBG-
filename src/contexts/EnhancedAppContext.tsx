import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { useToast } from '@/hooks/use-toast';

// Types
export interface User {
  uid: string;
  email: string;
  displayName?: string;
  disabled: boolean;
  emailVerified: boolean;
  createdAt?: string;
}

export interface Project {
  id: string;
  name: string;
  adminEmail: string;
  apiKey: string;
  serviceAccount: any;
  status: 'loading' | 'active' | 'error';
  createdAt: string;
  profileId?: string;
}

export interface Campaign {
  id: string;
  name: string;
  projectIds: string[];
  selectedUsers: { [projectId: string]: string[] };
  batchSize: number;
  workers: number;
  template?: string;
  status: 'pending' | 'running' | 'paused' | 'completed' | 'failed';
  createdAt: string;
  startedAt?: string;
  completedAt?: string;
  processed: number;
  successful: number;
  failed: number;
  errors: string[];
  projectStats: { [projectId: string]: { processed: number; successful: number; failed: number } };
}

export interface DailyCount {
  project_id: string;
  date: string;
  sent: number;
}

export interface Profile {
  id: string;
  name: string;
  description: string;
  projectIds: string[];
  createdAt: string;
}

interface EnhancedAppContextType {
  // Projects
  projects: Project[];
  addProject: (projectData: any) => Promise<void>;
  removeProject: (id: string) => Promise<void>;
  setProjects: React.Dispatch<React.SetStateAction<Project[]>>;
  reloadProjectsAndProfiles: () => Promise<void>;
  
  // Users
  users: { [projectId: string]: User[] };
  loadUsers: (projectId: string) => Promise<void>;
  importUsers: (projectIds: string[], emails: string[]) => Promise<number>;
  bulkDeleteUsers: (projectIds: string[], userIds?: string[]) => Promise<void>;
  refreshAllUsers: () => Promise<void>;
  
  // Campaigns
  campaigns: Campaign[];
  currentCampaign: Campaign | null;
  createCampaign: (campaignData: any) => Promise<void>;
  updateCampaign: (campaignId: string, updates: any) => Promise<void>;
  deleteCampaign: (campaignId: string) => Promise<void>;
  startCampaign: (campaignId: string) => Promise<void>;
  pauseCampaign: (campaignId: string) => Promise<void>;
  resumeCampaign: (campaignId: string) => Promise<void>;
  
  // Daily counts
  dailyCounts: { [key: string]: DailyCount };
  getDailyCount: (projectId: string) => number;
  
  // Loading states
  loading: boolean;
  setLoading: (loading: boolean) => void;
  
  // Profiles
  profiles: Profile[];
  activeProfile?: string;
  setActiveProfile: (profileId: string) => void;
  addProfile: (profile: Omit<Profile, 'id' | 'createdAt'>) => void;
  removeProfile: (profileId: string) => void;
  updateProfile: (profileId: string, updates: Partial<Profile>) => void;
  loadCampaigns: () => Promise<void>;
  bulkRemoveProjects: (ids: string[]) => Promise<void>;
}

const EnhancedAppContext = createContext<EnhancedAppContextType | undefined>(undefined);

  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://139.59.213.238:8000';

// Defensive filter for valid projects
function filterValidProjects(projects: any[]) {
  return (projects || []).filter(p => p && typeof p.id === 'string' && p.id.trim() !== '' && p.id !== 'undefined');
}

// Remove all localStorageService usage and legacy logic
// All state is loaded from and updated via the backend

export const EnhancedAppProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { toast } = useToast();
  
  // State
  const [projects, setProjects] = useState<Project[]>([]);
  const [users, setUsers] = useState<{ [projectId: string]: User[] }>({});
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [currentCampaign, setCurrentCampaign] = useState<Campaign | null>(null);
  const [dailyCounts, setDailyCounts] = useState<{ [key: string]: DailyCount }>({});
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [activeProfile, setActiveProfileState] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);

  // API helpers
  const apiCall = async (endpoint: string, options: RequestInit = {}) => {
    try {
      const response = await fetch(`${API_BASE_URL}${endpoint}`, {
        headers: {
          'Content-Type': 'application/json',
          ...options.headers,
        },
        ...options,
      });
      if (!response.ok) {
        throw new Error(`API call failed: ${response.status}`);
      }
      return await response.json();
    } catch (error) {
      console.error(`API call to ${endpoint} failed:`, error);
      throw error;
    }
  };

  // Fetch all data from backend
  const fetchAllData = async () => {
    setLoading(true);
    try {
      console.log('fetchAllData: Starting data fetch...');
      // 1. Projects
      const backendProjects = await apiCall('/projects', { method: 'GET' });
      console.log('fetchAllData: Backend projects:', backendProjects);
      const filteredProjects = (backendProjects.projects || []).map(p => ({
        ...p,
        status: p.status === 'active' ? 'active' : (p.status === 'error' ? 'error' : 'loading'),
      })).filter(p => p && p.id);
      console.log('fetchAllData: Filtered projects:', filteredProjects);
      setProjects(filteredProjects);
      
      // 2. Users for all projects
      const usersObj: { [projectId: string]: User[] } = {};
      await Promise.all(filteredProjects.map(async (project) => {
        try {
          const response = await apiCall(`/projects/${project.id}/users`);
          usersObj[project.id] = response.users;
        } catch (error) {
          usersObj[project.id] = [];
        }
      }));
      setUsers(usersObj);
      
      // 3. Profiles
      const backendProfiles = await apiCall('/profiles', { method: 'GET' });
      const profilesWithDefaults = (backendProfiles.profiles || []).map(profile => ({
        ...profile,
        description: profile.description || '',
      }));
      setProfiles(profilesWithDefaults);
      
      // 4. Campaigns
      const responseCampaigns = await apiCall('/campaigns', { method: 'GET' });
      setCampaigns(responseCampaigns.campaigns || []);
      
      // 5. Daily counts
      const responseDailyCounts = await apiCall('/daily-counts', { method: 'GET' });
      setDailyCounts(responseDailyCounts.daily_counts || {});
      
      console.log('fetchAllData: Data fetch completed successfully');
    } catch (error) {
      console.error('fetchAllData: Error:', error);
      toast({ title: 'Error', description: 'Failed to load data from backend.', variant: 'destructive' });
    } finally {
      setLoading(false);
    }
  };

  // On mount, fetch all data
  useEffect(() => {
    fetchAllData();
  }, []);

  // CRUD: Projects
  // Add project: always link to selected profile
  const addProject = async (projectData: any) => {
    setLoading(true);
    try {
      // Send project data and profileId separately to match backend expectation
      const requestBody = {
        ...projectData,
        profileId: activeProfile, // always link to selected profile
      };
      await apiCall('/projects', {
        method: 'POST',
        body: JSON.stringify(requestBody),
      });
      await fetchAllData();
      toast({ title: 'Project Added', description: `${projectData.name} added successfully.` });
    } catch (error) {
      toast({ title: 'Error', description: 'Failed to add project.', variant: 'destructive' });
    } finally {
      setLoading(false);
    }
  };

  // Remove project: remove from profile's projectIds
  const removeProject = async (id: string) => {
    setLoading(true);
    try {
      // Remove project from backend
      await apiCall(`/projects/${id}`, { method: 'DELETE' });
      // Remove project from all profiles' projectIds in backend
      const affectedProfiles = profiles.filter(profile => profile.projectIds.includes(id));
      for (const profile of affectedProfiles) {
        const newProjectIds = profile.projectIds.filter(pid => pid !== id);
        await apiCall(`/profiles/${profile.id}`, {
          method: 'PUT',
          body: JSON.stringify({ projectIds: newProjectIds }),
        });
      }
      await fetchAllData();
      toast({ title: 'Project Removed', description: 'Project deleted successfully.' });
    } catch (error) {
      toast({ title: 'Error', description: 'Failed to delete project.', variant: 'destructive' });
    } finally {
      setLoading(false);
    }
  };

  // Fix removeProject to only remove the selected project and update profiles correctly
  // This function is now redundant as removeProject handles both project and profile linking
  const bulkRemoveProjects = async (ids: string[]) => {
    setLoading(true);
    try {
      console.log('bulkRemoveProjects: Starting deletion of projects:', ids);
      const response = await apiCall(`/projects/bulk-delete`, {
        method: 'POST',
        body: JSON.stringify(ids), // Backend expects array directly, not wrapped in object
        headers: { 'Content-Type': 'application/json' },
      });
      console.log('bulkRemoveProjects: Backend response:', response);
      
      // Force a complete data refresh
      console.log('bulkRemoveProjects: Refreshing data...');
      await fetchAllData();
      console.log('bulkRemoveProjects: Data refresh completed');
      
      toast({ title: 'Projects Removed', description: `${ids.length} project(s) deleted successfully.` });
    } catch (error) {
      console.error('bulkRemoveProjects: Error:', error);
      toast({ title: 'Error', description: 'Failed to delete projects.', variant: 'destructive' });
    } finally {
      setLoading(false);
    }
  };

  // CRUD: Users
  const loadUsers = async (projectId: string) => {
    try {
      const response = await apiCall(`/projects/${projectId}/users`);
      setUsers(prev => ({ ...prev, [projectId]: response.users }));
    } catch (error) {
      console.error(`Failed to load users for project ${projectId}:`, error);
      throw error;
    }
  };

  const bulkDeleteUsers = async (projectIds: string[], userIds?: string[]) => {
    try {
      const response = await apiCall('/projects/users/bulk', {
        method: 'DELETE',
        body: JSON.stringify({
          projectIds,
          userIds: userIds || null  // null means delete all users
        }),
      });
      
      if (response.success) {
        // Reload users for affected projects
        await Promise.all(projectIds.map(id => loadUsers(id)));
        
        toast({
          title: "Users Deleted",
          description: response.message || `Successfully deleted ${response.total_deleted || 'users'}`,
        });
        
        return response;
      } else {
        throw new Error(response.error || 'Failed to delete users');
      }
    } catch (error) {
      console.error('Bulk delete users failed:', error);
      toast({
        title: "Delete Failed",
        description: error.message || "Failed to delete users. Please try again.",
        variant: "destructive",
      });
      throw error;
    }
  };

  // CRUD: Profiles
  const addProfile = async (profileData: Omit<Profile, 'id' | 'createdAt'>) => {
      setLoading(true);
    try {
      const profileWithDefaults = {
        ...profileData,
        description: profileData.description || '',
      };
      await apiCall('/profiles', {
        method: 'POST',
        body: JSON.stringify(profileWithDefaults),
      });
      await fetchAllData();
      toast({ title: 'Profile Added', description: `${profileData.name} added successfully.` });
    } catch (error) {
      toast({ title: 'Error', description: 'Failed to add profile.', variant: 'destructive' });
    } finally {
      setLoading(false);
    }
  };
  // Remove profile: unlink all associated projects
  const removeProfile = async (profileId: string) => {
      setLoading(true);
    try {
      // Unlink all projects from this profile
      const projectsToUnlink = projects.filter(p => p.profileId === profileId);
      for (const project of projectsToUnlink) {
        await apiCall(`/projects/${project.id}`, {
          method: 'PUT',
          body: JSON.stringify({ profileId: undefined }),
      });
      }
      // Remove profile from backend
      await apiCall(`/profiles/${profileId}`, { method: 'DELETE' });
      await fetchAllData();
      toast({ title: 'Profile Removed', description: 'Profile deleted successfully.' });
    } catch (error) {
      toast({ title: 'Error', description: 'Failed to delete profile.', variant: 'destructive' });
    } finally {
      setLoading(false);
    }
  };
  const updateProfile = async (profileId: string, updates: Partial<Profile>) => {
    setLoading(true);
    try {
      await apiCall(`/profiles/${profileId}`, {
        method: 'PUT',
        body: JSON.stringify(updates),
      });
      await fetchAllData();
      toast({ title: 'Profile Updated', description: 'Profile updated successfully.' });
    } catch (error) {
      toast({ title: 'Error', description: 'Failed to update profile.', variant: 'destructive' });
    } finally {
      setLoading(false);
    }
  };

  // Campaign management with proper workers and batch size usage
  const loadCampaigns = async () => {
    try {
      const response = await apiCall('/campaigns');
      const merged = await mergeCampaignResults(response.campaigns);
      setCampaigns(merged);
      // Set current campaign if there's a running one
      const runningCampaign = merged.find((c: Campaign) => c.status === 'running');
      if (runningCampaign) {
        setCurrentCampaign(runningCampaign);
      }
    } catch (error) {
      console.error('Failed to load campaigns:', error);
    }
  };

  const createCampaign = async (campaignData: any) => {
    try {
      const response = await apiCall('/campaigns', {
        method: 'POST',
        body: JSON.stringify(campaignData),
      });
      
      if (response.success) {
        setCampaigns(prev => [response.campaign, ...prev]);
        
        toast({
          title: "Campaign Created",
          description: `Campaign "${campaignData.name}" has been created successfully.`,
        });
      }
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to create campaign.",
        variant: "destructive",
      });
      throw error;
    }
  };

  const updateCampaign = async (campaignId: string, updates: any) => {
    try {
      const response = await apiCall(`/campaigns/${campaignId}`, {
        method: 'PUT',
        body: JSON.stringify(updates),
      });
      
      if (response.success) {
        setCampaigns(prev => prev.map(c => c.id === campaignId ? response.campaign : c));
        
        toast({
          title: "Campaign Updated",
          description: "Campaign has been updated successfully.",
        });
      }
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to update campaign.",
        variant: "destructive",
      });
      throw error;
    }
  };

  const deleteCampaign = async (campaignId: string) => {
    try {
      await apiCall(`/campaigns/${campaignId}`, { method: 'DELETE' });
      setCampaigns(prev => prev.filter(c => c.id !== campaignId));
      
      if (currentCampaign?.id === campaignId) {
        setCurrentCampaign(null);
      }
      
      toast({
        title: "Campaign Deleted",
        description: "Campaign has been deleted successfully.",
      });
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to delete campaign.",
        variant: "destructive",
      });
      throw error;
    }
  };

  const startCampaign = async (campaignId: string) => {
    try {
      const campaign = campaigns.find(c => c.id === campaignId);
      if (!campaign) {
        throw new Error('Campaign not found');
      }

      console.log(`Starting campaign with ${campaign.workers} workers and batch size ${campaign.batchSize}`);
      
      const response = await apiCall(`/campaigns/${campaignId}/start`, { 
        method: 'POST',
        body: JSON.stringify({
          workers: campaign.workers,
          batchSize: campaign.batchSize
        })
      });
      
      if (response.success) {
        // Update campaign status
        setCampaigns(prev => prev.map(c => 
          c.id === campaignId 
            ? { ...c, status: 'running' as const, startedAt: new Date().toISOString() }
            : c
        ));
        
        setCurrentCampaign({ ...campaign, status: 'running', startedAt: new Date().toISOString() });
        
        // Monitor campaign progress
        const progressInterval = setInterval(async () => {
          try {
            const progressResponse = await apiCall(`/campaigns/${campaignId}`);
            setCampaigns(prev => prev.map(c => c.id === campaignId ? progressResponse : c));
            setCurrentCampaign(progressResponse);
            
            if (progressResponse.status === 'completed' || progressResponse.status === 'failed') {
              clearInterval(progressInterval);
              setCurrentCampaign(null);
            }
          } catch (error) {
            console.error('Failed to update campaign progress:', error);
            clearInterval(progressInterval);
          }
        }, 2000);
        
        toast({
          title: "Campaign Started",
          description: `Password reset campaign is running with ${campaign.workers} workers and batch size ${campaign.batchSize}.`,
        });
      }
    } catch (error) {
      console.error('Failed to start campaign:', error);
      toast({
        title: "Error",
        description: "Failed to start campaign. Please check your backend connection.",
        variant: "destructive",
      });
      throw error;
    }
  };

  const pauseCampaign = async (campaignId: string) => {
    // Implementation for pause functionality
    console.log('Pause campaign:', campaignId);
  };

  const resumeCampaign = async (campaignId: string) => {
    // Implementation for resume functionality
    console.log('Resume campaign:', campaignId);
  };

  const updateCampaignProgress = async (campaignId: string) => {
    try {
      const response = await apiCall(`/campaigns/${campaignId}`);
      setCampaigns(prev => prev.map(c => c.id === campaignId ? response : c));
      
      if (currentCampaign?.id === campaignId) {
        setCurrentCampaign(response);
      }
    } catch (error) {
      console.error('Failed to update campaign progress:', error);
    }
  };

  // Daily counts
  const loadDailyCounts = async () => {
    try {
      const response = await apiCall('/daily-counts');
      setDailyCounts(response.daily_counts);
    } catch (error) {
      console.error('Failed to load daily counts:', error);
    }
  };

  const getDailyCount = (projectId: string): number => {
    const today = new Date().toISOString().split('T')[0];
    const key = `${projectId}_${today}`;
    return dailyCounts[key]?.sent || 0;
  };

  // Helper to merge campaign results into campaigns
  const mergeCampaignResults = async (campaigns: Campaign[]) => {
    try {
      const resultsResponse = await apiCall('/campaigns/results/all');
      const results = resultsResponse.results || {};
      return campaigns.map(campaign => {
        // Merge all project results for this campaign
        const projectResults: any[] = Object.values(results).filter((r: any) => r.campaign_id === campaign.id);
        let processed = 0, successful = 0, failed = 0, status = campaign.status;
        let projectStats: any = {};
        if (projectResults.length > 0) {
          processed = projectResults.reduce((sum: number, r: any) => sum + (Number(r.successful) || 0) + (Number(r.failed) || 0), 0);
          successful = projectResults.reduce((sum: number, r: any) => sum + (Number(r.successful) || 0), 0);
          failed = projectResults.reduce((sum: number, r: any) => sum + (Number(r.failed) || 0), 0);
          // If any project is running, status is running; else completed/failed
          if (projectResults.some((r: any) => r.status === 'running')) status = 'running';
          else if (projectResults.every((r: any) => r.status === 'completed')) status = 'completed';
          else if (projectResults.some((r: any) => r.status === 'partial')) status = 'failed'; // fallback to allowed type
          projectStats = Object.fromEntries(projectResults.map((r: any) => [r.project_id, {
            processed: (Number(r.successful) || 0) + (Number(r.failed) || 0),
            successful: Number(r.successful) || 0,
            failed: Number(r.failed) || 0
          }]));
        }
        return {
          ...campaign,
          processed,
          successful,
          failed,
          status,
          projectStats,
        };
      });
      } catch (error) {
      return campaigns;
      }
  };

  // On app load, after loading projects, load all users in the background
  useEffect(() => {
    const validProjects = filterValidProjects(projects);
    if (validProjects.length > 0) {
      // loadAllUsers(); // This function is no longer needed as users are loaded directly
    }
  }, [projects]); // Use projects array instead of projects.length to avoid unnecessary re-renders

  // On app load, fetch profiles from backend
  useEffect(() => {
    (async () => {
      try {
        const backendProfiles = await apiCall('/profiles', { method: 'GET' });
        setProfiles(backendProfiles.profiles || []);
      } catch (error) {
        console.error('Failed to load profiles:', error);
      }
    })();
  }, []); // Empty dependency array - this should only run once

  // Expose a refreshAllUsers function for Users page
  const refreshAllUsers = async () => {
    // This function is no longer needed as users are loaded directly
  };

  useEffect(() => {
    const ws = new WebSocket(`ws://${API_BASE_URL.replace('http://', '').replace('https://', '')}/ws`);
    ws.onmessage = async (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (!msg.event) return;
        switch (msg.event) {
          case 'import_users':
            toast({
              title: 'Users Imported (Real-Time)',
              description: `Imported ${msg.data.total_imported} users across ${msg.data.project_ids.length} projects.`
            });
            // Use fetchAllData instead of individual loadUsers calls
            await fetchAllData();
            break;
          case 'bulk_delete_users':
            toast({
              title: 'Users Deleted (Real-Time)',
              description: `Deleted ${msg.data.total_deleted} users across ${msg.data.project_ids.length} projects.`
            });
            await fetchAllData();
            break;
          case 'move_users':
            toast({
              title: 'Users Moved (Real-Time)',
              description: `Moved users from ${msg.data.from_project} to ${msg.data.to_project}.`
            });
            await fetchAllData();
            break;
          case 'copy_users':
            toast({
              title: 'Users Copied (Real-Time)',
              description: `Copied users from ${msg.data.from_project} to ${msg.data.to_project}.`
            });
            await fetchAllData();
            break;
          case 'delete_project':
            toast({
              title: 'Project Deleted (Real-Time)',
              description: `Project ${msg.data.project_id} has been deleted.`
            });
            await fetchAllData();
            break;
          default:
            break;
        }
      } catch (e) {
        // Ignore parse errors
      }
    };
    return () => ws.close();
  }, []); // Empty dependency array - fetchAllData is stable

  const setProjectsSafe = (projects: Project[]) => {
    const filtered = filterValidProjects(projects);
    console.log('setProjectsSafe:', filtered);
    setProjects(filtered);
  };

  const reloadProjectsAndProfiles = async () => {
    await fetchAllData();
  };

  const value: EnhancedAppContextType = {
    // Projects
    projects,
    addProject,
    removeProject,
    setProjects: setProjectsSafe,
    reloadProjectsAndProfiles,
    
    // Users
    users,
    loadUsers,
    importUsers: async (projectIds: string[], emails: string[]) => {
      setLoading(true);
      try {
        const response = await apiCall('/projects/users/import', {
          method: 'POST',
          body: JSON.stringify({ emails, projectIds }),
        });
        
        if (response.success) {
          // Reload users for affected projects
          await Promise.all(projectIds.map(id => loadUsers(id)));
          
          toast({
            title: "Import Successful",
            description: `Successfully imported ${response.total_imported} users across ${projectIds.length} projects.`,
          });
          
          return response.total_imported;
        }
        return 0;
      } catch (error) {
        toast({
          title: "Import Failed",
          description: "Failed to import users. Please try again.",
          variant: "destructive",
        });
        throw error;
      } finally {
        setLoading(false);
      }
    },
    bulkDeleteUsers: bulkDeleteUsers,
    refreshAllUsers: async () => {
      // This function is no longer needed as users are loaded directly
    },
    
    // Campaigns
    campaigns,
    currentCampaign,
    createCampaign,
    updateCampaign,
    deleteCampaign,
    startCampaign,
    pauseCampaign,
    resumeCampaign,
    
    // Daily counts
    dailyCounts,
    getDailyCount,
    
    // Loading
    loading,
    setLoading,
    
    // Profiles
    profiles,
    activeProfile,
    setActiveProfile: (profileId: string) => {
      console.log('Setting active profile:', profileId);
      setActiveProfileState(profileId);
    },
    addProfile: addProfile,
    removeProfile: removeProfile,
    updateProfile: updateProfile,
    loadCampaigns,
    bulkRemoveProjects,
  };

  return (
    <EnhancedAppContext.Provider value={value}>
      {children}
    </EnhancedAppContext.Provider>
  );
};

export const useEnhancedApp = () => {
  const context = useContext(EnhancedAppContext);
  if (context === undefined) {
    throw new Error('useEnhancedApp must be used within an EnhancedAppProvider');
  }
  return context;
};

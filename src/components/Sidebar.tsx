import React, { useState } from 'react';
import { 
  Home, 
  Users, 
  FolderOpen, 
  Mail, 
  FileText, 
  Settings, 
  Plus,
  Trash2,
  Brain,
  TestTube
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { Profile } from '@/services/LocalStorageService';
import { useAuth } from '@/contexts/AppContext';

interface SidebarProps {
  currentPage: string;
  setCurrentPage: (page: string) => void;
  profiles: Profile[];
  activeProfile?: string;
  onProfileChange: (profileId: string) => void;
  onAddProfile: (profile: Omit<Profile, 'id' | 'createdAt'>) => void;
  onRemoveProfile: (profileId: string) => void;
  projectCounts: { [profileId: string]: number };
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentPage,
  setCurrentPage,
  profiles,
  activeProfile,
  onProfileChange,
  onAddProfile,
  onRemoveProfile,
  projectCounts
}) => {
  const [showAddProfile, setShowAddProfile] = useState(false);
  const [newProfileName, setNewProfileName] = useState('');
  const [newProfileDescription, setNewProfileDescription] = useState('');
  const { logout } = useAuth();

  const menuItems = [
    { id: 'dashboard', label: 'Dashboard', icon: Home },
    { id: 'projects', label: 'Projects', icon: FolderOpen },
    { id: 'users', label: 'Users', icon: Users },
    { id: 'campaigns', label: 'Campaigns', icon: Mail },
    { id: 'templates', label: 'Templates', icon: FileText },
    { id: 'ai', label: 'AI Management', icon: Brain },
    { id: 'test', label: 'Test Campaign', icon: TestTube },
    { id: 'profiles', label: 'Profiles', icon: Settings },
    { id: 'audit-logs', label: 'Audit Logs', icon: FileText },
  ];

  const handleAddProfile = () => {
    if (newProfileName.trim()) {
      onAddProfile({
        name: newProfileName.trim(),
        description: newProfileDescription.trim(),
        projectIds: [],
      });
      setNewProfileName('');
      setNewProfileDescription('');
      setShowAddProfile(false);
    }
  };



  const activeProfileData = profiles.find(p => p.id === activeProfile);

  return (
    <div className="w-64 bg-gray-800 border-r border-gray-700 flex flex-col">
      <div className="p-6 border-b border-gray-700">
        <h1 className="text-xl font-bold text-white mb-4">Firebase Manager</h1>
        
        {/* Profile Selection */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label className="text-gray-300 text-sm">Active Profile</Label>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowAddProfile(true)}
              className="text-gray-400 hover:text-white p-1"
            >
              <Plus className="w-4 h-4" />
            </Button>
          </div>
          
          {profiles.length > 0 ? (
            <Select value={activeProfile} onValueChange={onProfileChange}>
              <SelectTrigger className="bg-gray-700 border-gray-600 text-white">
                <SelectValue placeholder="Select profile" />
              </SelectTrigger>
              <SelectContent className="bg-gray-700 border-gray-600">
                {profiles.map((profile) => (
                  <SelectItem key={profile.id} value={profile.id} className="text-white hover:bg-gray-600">
                    <div className="flex items-center justify-between w-full">
                      <span>{profile.name}</span>
                      <Badge variant="secondary" className="ml-2">
                        {projectCounts[profile.id] || 0}
                      </Badge>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <div className="text-gray-400 text-sm text-center py-2">
              No profiles yet
            </div>
          )}
          
          {activeProfileData && (
            <div className="text-xs text-gray-500">
              {activeProfileData.description || 'No description'}
            </div>
          )}
        </div>
      </div>

      <nav className="flex-1 p-4">
        <ul className="space-y-2">
          {menuItems.map((item) => {
            const Icon = item.icon;
            return (
              <li key={item.id}>
                <button
                  onClick={() => setCurrentPage(item.id)}
                  className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left transition-colors ${
                    currentPage === item.id
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-300 hover:bg-gray-700 hover:text-white'
                  }`}
                >
                  <Icon className="w-5 h-5" />
                  {item.label}
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Add Profile Dialog */}
      <Dialog open={showAddProfile} onOpenChange={setShowAddProfile}>
        <DialogContent className="bg-gray-800 border-gray-700">
          <DialogHeader>
            <DialogTitle className="text-white">Add New Profile</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label htmlFor="profileName" className="text-gray-300">Profile Name</Label>
              <Input
                id="profileName"
                value={newProfileName}
                onChange={(e) => setNewProfileName(e.target.value)}
                placeholder="Production, Development, etc."
                className="bg-gray-700 border-gray-600 text-white"
              />
            </div>
            <div>
              <Label htmlFor="profileDescription" className="text-gray-300">Description (Optional)</Label>
              <Input
                id="profileDescription"
                value={newProfileDescription}
                onChange={(e) => setNewProfileDescription(e.target.value)}
                placeholder="Brief description of this profile"
                className="bg-gray-700 border-gray-600 text-white"
              />
            </div>
            <div className="flex gap-2">
              <Button onClick={handleAddProfile} className="bg-green-600 hover:bg-green-700">
                Add Profile
              </Button>
              <Button 
                variant="outline" 
                onClick={() => setShowAddProfile(false)}
                className="border-gray-600 text-gray-300 hover:bg-gray-700"
              >
                Cancel
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <button
        onClick={() => { logout(); window.location.href = '/login'; }}
        className="w-full bg-red-600 hover:bg-red-700 mt-4 text-white font-bold py-2 px-4 rounded"
      >
        Logout
      </button>

    </div>
  );
};

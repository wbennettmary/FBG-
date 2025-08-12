// API Configuration for Firebase Manager
// Automatically detects server environment and configures endpoints

interface APIConfig {
  baseURL: string;
  wsURL: string;
  timeout: number;
}

// Detect server environment
const isProduction = import.meta.env.PROD;
const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';

// Get server IP from environment or detect automatically
const getServerIP = (): string => {
  // If we have a specific server IP from environment
  if (import.meta.env.VITE_SERVER_IP) {
    return import.meta.env.VITE_SERVER_IP;
  }
  
  // If we're on the server itself, use current hostname
  if (!isLocalhost) {
    return window.location.hostname;
  }
  
  // Default to localhost for development
  return 'localhost';
};

// Get backend port
const getBackendPort = (): number => {
  return parseInt(import.meta.env.VITE_BACKEND_PORT || '8000');
};

// Create API configuration
const createAPIConfig = (): APIConfig => {
  const serverIP = getServerIP();
  const backendPort = getBackendPort();
  
  return {
    baseURL: `http://${serverIP}:${backendPort}`,
    wsURL: `ws://${serverIP}:${backendPort}/ws`,
    timeout: 30000
  };
};

// Export the configuration
export const apiConfig = createAPIConfig();

// Log configuration for debugging
console.log('🌐 API Configuration:', {
  environment: import.meta.env.MODE,
  serverIP: getServerIP(),
  backendPort: getBackendPort(),
  baseURL: apiConfig.baseURL,
  wsURL: apiConfig.wsURL,
  isProduction,
  isLocalhost
});

// Helper function to get full API URL
export const getAPIUrl = (endpoint: string): string => {
  // Remove leading slash if present
  const cleanEndpoint = endpoint.startsWith('/') ? endpoint.slice(1) : endpoint;
  return `${apiConfig.baseURL}/${cleanEndpoint}`;
};

// Helper function to get WebSocket URL
export const getWebSocketUrl = (): string => {
  return apiConfig.wsURL;
};

export default apiConfig;

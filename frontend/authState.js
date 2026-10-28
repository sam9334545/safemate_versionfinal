/**
 * SafeMate - Centralized Authentication & Access State Manager
 * 
 * Manages the three distinct product access modes:
 *  - MODE A: 'authenticated' (Personal Account with verified backend identity)
 *  - MODE B: 'guest' (Scoped ephemeral session via backend /guest-session)
 *  - MODE C: 'demo' (Zero-friction Judge Demo Mode, fully isolated local simulation)
 *  - 'unauthenticated' (Visitor at sign-in entry)
 *  - 'loading' (Initial session validation in progress)
 */

import { DEMO_USER } from './demoData.js';

const STORAGE_KEYS = {
  ACCESS_MODE: 'safemate_access_mode',
  TOKEN: 'safemate_jwt_token',
  USER_PROFILE: 'safemate_user_profile',
  DEMO_STATE: 'safemate_demo_active'
};

class AuthStateManager {
  constructor() {
    this.apiBaseUrl = (typeof window !== 'undefined' && window.API_BASE_URL !== undefined)
      ? window.API_BASE_URL
      : ((typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname))
          ? ''
          : 'https://safemate-backend-tseo.onrender.com');
    
    this.state = {
      accessMode: 'loading', // 'authenticated' | 'guest' | 'demo' | 'unauthenticated' | 'loading'
      user: null,
      token: null,
      isLoading: true,
      error: null,
      backendStatus: 'checking' // 'online' | 'offline' | 'checking'
    };

    this.listeners = new Set();
  }

  /**
   * Subscribe to state changes
   * @param {Function} listener callback receiving current state
   * @returns {Function} unsubscribe function
   */
  subscribe(listener) {
    this.listeners.add(listener);
    // Immediately invoke with current state
    try {
      listener(this.getState());
    } catch (e) {
      console.error('SafeMateAuth listener error:', e);
    }
    return () => this.listeners.delete(listener);
  }

  notify() {
    const currentState = this.getState();
    this.listeners.forEach((listener) => {
      try {
        listener(currentState);
      } catch (err) {
        console.error('Error in SafeMateAuth subscriber:', err);
      }
    });
  }

  getState() {
    return { ...this.state };
  }

  isDemoMode() {
    return this.state.accessMode === 'demo';
  }

  isGuestMode() {
    return this.state.accessMode === 'guest';
  }

  isAuthenticated() {
    return this.state.accessMode === 'authenticated';
  }

  getAuthHeaders() {
    if (this.state.token && (this.state.accessMode === 'authenticated' || this.state.accessMode === 'guest')) {
      return { Authorization: `Bearer ${this.state.token}` };
    }
    return {};
  }

  /**
   * Initialize state from storage and validate with backend if token exists
   */
  async init() {
    this.state.isLoading = true;
    this.notify();

    const storedMode = localStorage.getItem(STORAGE_KEYS.ACCESS_MODE);
    const storedToken = localStorage.getItem(STORAGE_KEYS.TOKEN);
    const storedUser = localStorage.getItem(STORAGE_KEYS.USER_PROFILE);

    // If explicit Demo mode was chosen, restore demo mode directly
    if (storedMode === 'demo') {
      this.enterDemoMode(false);
      return;
    }

    if (storedToken) {
      try {
        // Test connectivity and restore session
        const res = await fetch(`${this.apiBaseUrl}/api/v1/auth/me`, {
          method: 'GET',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${storedToken}`
          }
        });

        if (res.ok) {
          const profile = await res.json();
          this.state.token = storedToken;
          this.state.user = profile;
          this.state.accessMode = profile.is_guest ? 'guest' : 'authenticated';
          this.state.backendStatus = 'online';
          this.state.isLoading = false;
          this.state.error = null;
          this.persistSession(this.state.accessMode, storedToken, profile);
          this.notify();
          return;
        } else {
          // Token expired or invalid
          console.warn('Stored token is invalid or expired. Resetting session.');
          this.clearSessionStorage();
        }
      } catch (networkErr) {
        console.warn('Backend unavailable during session restoration:', networkErr);
        this.state.backendStatus = 'offline';
        // If we had a stored profile, retain read-only representation or offer demo mode
        if (storedUser) {
          try {
            this.state.user = JSON.parse(storedUser);
            this.state.token = storedToken;
            this.state.accessMode = storedMode || 'authenticated';
            this.state.isLoading = false;
            this.state.error = 'Backend is currently offline. Operating in cached offline state.';
            this.notify();
            return;
          } catch (e) {
            this.clearSessionStorage();
          }
        }
      }
    }

    // Default: unauthenticated
    this.state.accessMode = 'unauthenticated';
    this.state.user = null;
    this.state.token = null;
    this.state.isLoading = false;
    this.notify();
  }

  /**
   * Register a new personal account
   */
  async register(email, password, fullName = '') {
    this.state.isLoading = true;
    this.state.error = null;
    this.notify();

    try {
      const res = await fetch(`${this.apiBaseUrl}/api/v1/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: email.trim(),
          password: password,
          full_name: fullName.trim() || undefined
        })
      });

      const data = await res.json();
      if (!res.ok) {
        const errorDetail = data.detail || (Array.isArray(data.detail) ? data.detail[0].msg : 'Registration failed');
        this.state.error = errorDetail;
        this.state.isLoading = false;
        this.notify();
        throw new Error(errorDetail);
      }

      // Auto-login after successful registration
      return await this.login(email, password);
    } catch (err) {
      this.state.isLoading = false;
      this.state.error = err.message || 'Registration failed';
      this.notify();
      throw err;
    }
  }

  /**
   * Log into personal account
   */
  async login(email, password) {
    this.state.isLoading = true;
    this.state.error = null;
    this.notify();

    try {
      const res = await fetch(`${this.apiBaseUrl}/api/v1/auth/login-json`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: email.trim(),
          password: password
        })
      });

      const data = await res.json();
      if (!res.ok) {
        const errorDetail = data.detail || 'Invalid email or password';
        this.state.error = errorDetail;
        this.state.isLoading = false;
        this.notify();
        throw new Error(errorDetail);
      }

      const token = data.access_token;

      // Fetch user profile from /me
      const meRes = await fetch(`${this.apiBaseUrl}/api/v1/auth/me`, {
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        }
      });

      let profile = { email: email.trim(), is_guest: false, access_mode: 'personal_account' };
      if (meRes.ok) {
        profile = await meRes.json();
      }

      this.state.token = token;
      this.state.user = profile;
      this.state.accessMode = 'authenticated';
      this.state.isLoading = false;
      this.state.error = null;
      this.state.backendStatus = 'online';

      this.persistSession('authenticated', token, profile);
      this.notify();
      return profile;
    } catch (err) {
      this.state.isLoading = false;
      this.state.error = err.message || 'Login failed';
      this.notify();
      throw err;
    }
  }

  /**
   * Mode B: Explore as Guest (requests guest token from backend, with graceful fallback to demo)
   */
  async enterGuestMode() {
    this.state.isLoading = true;
    this.state.error = null;
    this.notify();

    try {
      const res = await fetch(`${this.apiBaseUrl}/api/v1/auth/guest-session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });

      if (res.ok) {
        const data = await res.json();
        const token = data.access_token;
        const guestUser = {
          id: data.user_id || 'guest_user',
          email: 'guest@safemate.local',
          full_name: 'Guest Explorer',
          is_guest: true,
          access_mode: 'guest',
          badge: 'GUEST ACCESS'
        };

        this.state.token = token;
        this.state.user = guestUser;
        this.state.accessMode = 'guest';
        this.state.isLoading = false;
        this.state.backendStatus = 'online';

        this.persistSession('guest', token, guestUser);
        this.notify();
        return guestUser;
      } else {
        console.warn('Guest endpoint returned status', res.status, '- activating Demo Mode fallback');
        return this.enterDemoMode(true, 'Backend guest endpoint returned error. Activated Judge Demo Mode.');
      }
    } catch (err) {
      console.warn('Guest session network failure, falling back to local Judge Demo Mode:', err);
      return this.enterDemoMode(true, 'Backend is offline. Activated Judge Demo Mode with local sample data.');
    }
  }

  /**
   * Mode C: Judge Demo Mode (Instant, zero-friction, decoupled from backend & credentials)
   * @param {boolean} persist whether to remember mode across page refreshes
   * @param {string} fallbackNotice optional notice if triggered as fallback
   */
  enterDemoMode(persist = true, fallbackNotice = null) {
    // Isolated demo state
    this.state.accessMode = 'demo';
    this.state.user = { ...DEMO_USER };
    this.state.token = 'demo_jwt_simulated_token_not_for_backend';
    this.state.isLoading = false;
    this.state.error = fallbackNotice;

    if (persist) {
      localStorage.setItem(STORAGE_KEYS.ACCESS_MODE, 'demo');
      localStorage.setItem(STORAGE_KEYS.USER_PROFILE, JSON.stringify(DEMO_USER));
      localStorage.removeItem(STORAGE_KEYS.TOKEN); // do not send dummy token to real endpoints
    }

    this.notify();
    return this.state.user;
  }

  /**
   * Exit Demo Mode and return to clean sign-in screen
   */
  exitDemoMode() {
    this.clearSessionStorage();
    this.state.accessMode = 'unauthenticated';
    this.state.user = null;
    this.state.token = null;
    this.state.isLoading = false;
    this.state.error = null;
    this.notify();
  }

  /**
   * Logout from Personal Account or Guest Session
   */
  logout() {
    this.clearSessionStorage();
    this.state.accessMode = 'unauthenticated';
    this.state.user = null;
    this.state.token = null;
    this.state.isLoading = false;
    this.state.error = null;
    this.notify();
  }

  persistSession(mode, token, user) {
    try {
      localStorage.setItem(STORAGE_KEYS.ACCESS_MODE, mode);
      if (token) localStorage.setItem(STORAGE_KEYS.TOKEN, token);
      if (user) localStorage.setItem(STORAGE_KEYS.USER_PROFILE, JSON.stringify(user));
    } catch (e) {
      console.warn('Unable to persist session to localStorage', e);
    }
  }

  clearSessionStorage() {
    try {
      localStorage.removeItem(STORAGE_KEYS.ACCESS_MODE);
      localStorage.removeItem(STORAGE_KEYS.TOKEN);
      localStorage.removeItem(STORAGE_KEYS.USER_PROFILE);
      localStorage.removeItem(STORAGE_KEYS.DEMO_STATE);
    } catch (e) {
      console.warn('Unable to clear session storage', e);
    }
  }
}

// Singleton instance
export const authState = new AuthStateManager();

// Attach to window for vanilla JavaScript access in app.js
if (typeof window !== 'undefined') {
  window.SafeMateAuth = authState;
}

export default authState;

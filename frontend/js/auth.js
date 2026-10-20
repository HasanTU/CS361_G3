/**
 * auth.js - Shared authentication utility for e-Mailbox
 * Handles session storage, role checks, and redirect guards.
 */

const AUTH_KEY = 'emb_session';
const TOKEN_KEY = 'emb_token';

const Auth = {
  setSession(user, token = null) {
    sessionStorage.setItem(AUTH_KEY, JSON.stringify(user));
    if (token) {
      sessionStorage.setItem(TOKEN_KEY, token);
    }
  },

  getSession() {
    try {
      const raw = sessionStorage.getItem(AUTH_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  },

  getToken() {
    return sessionStorage.getItem(TOKEN_KEY);
  },

  clearSession() {
    sessionStorage.removeItem(AUTH_KEY);
    sessionStorage.removeItem(TOKEN_KEY);
  },

  isLoggedIn() {
    return this.getSession() !== null;
  },

  getRole() {
    const s = this.getSession();
    return s ? s.role : null;
  },

  requireAuth() {
    if (!this.isLoggedIn()) {
      window.location.replace('login.html');
      return false;
    }
    return true;
  },

  requireAdmin() {
    if (!this.requireAuth()) return false;
    if (this.getRole() !== 'admin') {
      sessionStorage.setItem('emb_redirect_notice', 'ไม่มีสิทธิ์เข้าถึงหน้านี้');
      window.location.replace('lecturer_view.html');
      return false;
    }
    return true;
  },

  consumeNotice() {
    const msg = sessionStorage.getItem('emb_redirect_notice');
    if (msg) sessionStorage.removeItem('emb_redirect_notice');
    return msg;
  },

  logout() {
    this.clearSession();
    window.location.replace('login.html');
  }
};

import { api } from './api.js';

export const formatCurrency = (val, currency = 'EUR') => {
  if (val === null || val === undefined) return '-';
  return new Intl.NumberFormat('it-IT', {
    style: 'currency',
    currency: currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }).format(val);
};

export const formatPercent = (val) => {
  if (val === null || val === undefined) return '-';
  const sign = val > 0 ? '+' : '';
  return `${sign}${val.toFixed(2)}%`;
};

export const formatDate = (dateString) => {
  if (!dateString) return '-';
  return new Intl.DateTimeFormat('it-IT', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric'
  }).format(new Date(dateString));
};

export const formatDateTime = (dateString) => {
  if (!dateString) return '-';
  return new Intl.DateTimeFormat('it-IT', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date(dateString));
};

export const showToast = (message, type = 'info') => {
  let container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${message}</span>`;
  
  container.appendChild(toast);
  
  // Trigger reflow to animate
  setTimeout(() => toast.classList.add('show'), 10);
  
  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 300);
  }, 3000);
};

export const showLoading = (elementId = null) => {
  if (elementId) {
    const el = document.getElementById(elementId);
    if (el) {
      let overlay = el.querySelector('.loader-overlay');
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'loader-overlay';
        overlay.innerHTML = '<div class="spinner"></div>';
        el.style.position = 'relative';
        el.appendChild(overlay);
      }
      overlay.classList.add('active');
    }
  }
};

export const hideLoading = (elementId = null) => {
  if (elementId) {
    const el = document.getElementById(elementId);
    if (el) {
      const overlay = el.querySelector('.loader-overlay');
      if (overlay) overlay.classList.remove('active');
    }
  }
};

const initSidebar = () => {
  const currentPath = window.location.pathname;
  const links = document.querySelectorAll('.nav-link');
  
  links.forEach(link => {
    const href = link.getAttribute('href');
    if (currentPath.endsWith(href) || (currentPath === '/' && href === '/static/index.html')) {
      link.classList.add('active');
    } else {
      link.classList.remove('active');
    }
  });

  const toggle = document.querySelector('.mobile-toggle');
  const sidebar = document.querySelector('.sidebar');
  if (toggle && sidebar) {
    toggle.addEventListener('click', () => {
      sidebar.classList.toggle('open');
    });
  }

  // Add User Footer to Sidebar if not already present
  if (sidebar && !sidebar.querySelector('.sidebar-footer')) {
    const username = localStorage.getItem('auth_username') || 'Utente';
    const footer = document.createElement('div');
    footer.className = 'sidebar-footer';
    footer.style.cssText = 'padding: 16px 20px; border-top: 1px solid var(--border-color); display: flex; align-items: center; justify-content: space-between; font-size: 0.85rem;';
    footer.innerHTML = `
      <div style="display: flex; align-items: center; gap: 8px; overflow: hidden;">
        <span style="font-size: 1.1rem;">👤</span>
        <span style="font-weight: 600; color: var(--text-primary); text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">${username}</span>
      </div>
      <button id="btnLogout" title="Disconnetti" style="background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 1.1rem; padding: 4px 6px; border-radius: 4px; transition: var(--transition);" onmouseover="this.style.color='var(--danger-color)'" onmouseout="this.style.color='var(--text-muted)'">
        🚪
      </button>
    `;
    sidebar.appendChild(footer);

    const btnLogout = footer.querySelector('#btnLogout');
    if (btnLogout) {
      btnLogout.addEventListener('click', async () => {
        if (confirm('Sei sicuro di voler effettuare il logout?')) {
          await api.logout();
        }
      });
    }
  }
};

const checkAuth = async () => {
  if (window.location.pathname.includes('login.html')) return;

  try {
    const me = await api.getMe();
    if (me && me.username) {
      localStorage.setItem('auth_username', me.username);
    }
  } catch (e) {
    // Handled by api.js redirect on 401
  }
};

const loadGoogleFont = () => {
  const link = document.createElement('link');
  link.href = 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap';
  link.rel = 'stylesheet';
  document.head.appendChild(link);
};

document.addEventListener('DOMContentLoaded', () => {
  loadGoogleFont();
  initSidebar();
  checkAuth();
});


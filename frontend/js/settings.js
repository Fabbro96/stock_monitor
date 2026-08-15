import { api } from './api.js';
import { showLoading, hideLoading, showToast, formatDate } from './app.js';

let alertRules = [];
let currentUser = null;

const renderAlertRules = () => {
  const tbody = document.getElementById('alertRulesBody');
  if (!tbody) return;
  if (alertRules.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">Nessuna regola di alert configurata.</td></tr>';
    return;
  }
  
  tbody.innerHTML = alertRules.map(rule => `
    <tr>
        <td><span class="font-bold text-primary">${rule.ticker}</span></td>
        <td><span class="badge ${rule.direction === 'UP' ? 'badge-buy' : 'badge-sell'}">${rule.direction}</span></td>
        <td class="font-mono font-bold">${rule.threshold}%</td>
        <td><span class="badge ${rule.active !== false ? 'badge-buy' : 'badge-sell'}">${rule.active !== false ? 'Attivo' : 'Inattivo'}</span></td>
        <td class="text-center">
            <button type="button" class="btn btn-ghost btn-sm text-loss" title="Elimina regola" onclick="window.deleteAlert(${rule.id})">🗑️</button>
        </td>
    </tr>
  `).join('');
};

const renderUsersTable = (users) => {
  const tbody = document.getElementById('usersTableBody');
  if (!tbody) return;

  if (!users || users.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">Nessun utente registrato oltre all\'amministratore.</td></tr>';
    return;
  }

  tbody.innerHTML = users.map(u => `
    <tr>
      <td>
        <span class="font-bold ${u.is_admin ? 'text-primary' : ''}">${u.username}</span>
        ${u.id === currentUser?.id ? '<span class="text-xs text-muted"> (Tu)</span>' : ''}
      </td>
      <td>
        <span class="badge ${u.is_admin ? 'badge-admin' : 'badge-hold'}">
          ${u.is_admin ? '👑 Amministratore' : '👤 Utente'}
        </span>
      </td>
      <td class="text-secondary text-sm">${formatDate(u.created_at)}</td>
      <td class="text-secondary text-sm">${u.last_login ? formatDate(u.last_login) : 'Mai'}</td>
      <td class="text-center">
        ${u.id !== currentUser?.id ? `
          <button type="button" class="btn btn-ghost btn-sm text-loss" title="Elimina utente" onclick="window.deleteUser(${u.id}, '${u.username}')">🗑️</button>
        ` : '<span class="text-xs text-muted">-</span>'}
      </td>
    </tr>
  `).join('');
};

const loadUsers = async () => {
  try {
    const users = await api.getUsers();
    renderUsersTable(users);
  } catch (e) {
    console.error('Errore caricamento utenti:', e);
  }
};

const renderTimeInputs = (count, values = []) => {
  const container = document.getElementById('timesContainer');
  if (!container) return;
  let html = '';
  for (let i = 0; i < count; i++) {
    const val = values[i] || (i === 0 ? '09:00' : '18:00');
    html += `<input type="time" name="reportTime" value="${val}" style="width: 120px;" required>`;
  }
  container.innerHTML = html;
};

const loadSettings = async () => {
  try {
    showLoading('settingsContent');

    // 1. Get Me & check admin status
    try {
      currentUser = await api.getMe();
      if (currentUser && currentUser.is_admin) {
        const adminSection = document.getElementById('adminUsersSection');
        if (adminSection) adminSection.style.display = 'block';
        loadUsers();
      }
    } catch (e) {
      console.warn('Impossibile verificare i permessi utente:', e);
    }

    // 2. Get Settings
    const settings = await api.getSettings().catch(() => ({ 
      strategy: 'mixed', 
      budget: 10000, 
      markets: ['IT', 'US', 'EU'], 
      reportFreq: 2, 
      reportTimes: ['09:00', '18:00'], 
      apiStatus: { telegram: false, gemini: true, gemini_model: 'gemini-3.7-flash', reddit: false } 
    }));
    
    // Strategy
    document.getElementById('strategyType').value = settings.strategy || 'mixed';
    document.getElementById('targetBudget').value = settings.budget || 10000;
    
    const markets = settings.markets || [];
    document.getElementById('marketIt').checked = markets.includes('IT');
    document.getElementById('marketUs').checked = markets.includes('US');
    document.getElementById('marketEu').checked = markets.includes('EU');

    // Notifications
    document.getElementById('reportFreq').value = settings.reportFreq || 2;
    renderTimeInputs(settings.reportFreq || 2, settings.reportTimes || ['09:00', '18:00']);

    // API Status Badges
    const statusObj = settings.apiStatus || {};
    
    const geminiEl = document.getElementById('statusGemini');
    if (geminiEl) {
      const active = Boolean(statusObj.gemini);
      geminiEl.className = `badge ${active ? 'badge-buy' : 'badge-sell'}`;
      geminiEl.textContent = active ? '✅ Attivo' : '❌ Non Configurato';
    }

    const telegramEl = document.getElementById('statusTelegram');
    if (telegramEl) {
      const active = Boolean(statusObj.telegram);
      telegramEl.className = `badge ${active ? 'badge-buy' : 'badge-sell'}`;
      telegramEl.textContent = active ? '✅ Attivo' : '⚪ Opzionale (Off)';
    }

    if (statusObj.gemini_model) {
      const modelEl = document.getElementById('modelGemini');
      if (modelEl) modelEl.textContent = statusObj.gemini_model;
    }


    // Alerts
    alertRules = await api.getAlertRules().catch(() => []);
    renderAlertRules();

  } catch(e) {
    showToast('Errore nel caricamento delle impostazioni', 'error');
  } finally {
    hideLoading('settingsContent');
  }
};

window.deleteAlert = async (id) => {
  if (confirm('Sei sicuro di voler eliminare questa regola di alert?')) {
    try {
      await api.deleteAlertRule(id);
      showToast('Regola eliminata con successo', 'success');
      alertRules = alertRules.filter(r => r.id !== id);
      renderAlertRules();
    } catch(e) {
      showToast('Errore durante l\'eliminazione della regola', 'error');
    }
  }
};

window.deleteUser = async (id, username) => {
  if (confirm(`Sei sicuro di voler eliminare l'utente "${username}"?`)) {
    try {
      await api.deleteUser(id);
      showToast(`Utente "${username}" eliminato con successo`, 'success');
      loadUsers();
    } catch (e) {
      showToast(e.message || 'Errore durante l\'eliminazione dell\'utente', 'error');
    }
  }
};

const initSettings = () => {
  loadSettings();

  document.getElementById('reportFreq').addEventListener('change', (e) => {
    const existing = Array.from(document.querySelectorAll('input[name="reportTime"]')).map(i => i.value);
    renderTimeInputs(parseInt(e.target.value), existing);
  });

  // Create User (Admin Only)
  const btnCreateUser = document.getElementById('btnCreateUser');
  if (btnCreateUser) {
    btnCreateUser.addEventListener('click', async () => {
      const usernameInput = document.getElementById('newUsername');
      const passwordInput = document.getElementById('newUserPassword');
      const isAdminInput = document.getElementById('newUserIsAdmin');

      const username = usernameInput.value.trim();
      const password = passwordInput.value;
      const is_admin = isAdminInput.checked;

      if (!username || !password) {
        showToast('Compila nome utente e password', 'error');
        return;
      }

      if (password.length < 8) {
        showToast('La password deve contenere almeno 8 caratteri', 'error');
        return;
      }

      btnCreateUser.disabled = true;
      btnCreateUser.textContent = 'Creazione...';

      try {
        await api.createUser({ username, password, is_admin });
        showToast(`Utente "${username}" creato con successo!`, 'success');
        usernameInput.value = '';
        passwordInput.value = '';
        isAdminInput.checked = false;
        loadUsers();
      } catch (err) {
        showToast(err.message || 'Errore durante la creazione dell\'utente', 'error');
      } finally {
        btnCreateUser.disabled = false;
        btnCreateUser.textContent = 'Crea Utente';
      }
    });
  }

  // Add Alert Rule
  document.getElementById('btnAddAlert').addEventListener('click', async () => {
    const ticker = document.getElementById('alertTicker').value.trim().toUpperCase();
    const threshold = parseFloat(document.getElementById('alertThreshold').value);
    const direction = document.getElementById('alertDir').value;

    if (!ticker || isNaN(threshold)) {
      showToast('Compila tutti i campi della regola alert', 'error');
      return;
    }

    try {
      const newAlert = { ticker, threshold, direction, active: true };
      const created = await api.addAlertRule(newAlert);
      alertRules.push(created || { id: Date.now(), ...newAlert });
      renderAlertRules();
      
      document.getElementById('alertTicker').value = '';
      document.getElementById('alertThreshold').value = '';
      showToast(`Regola per ${ticker} aggiunta`, 'success');
    } catch(e) {
      showToast('Errore durante il salvataggio dell\'alert', 'error');
    }
  });

  // Test Telegram
  document.getElementById('btnTestTelegram').addEventListener('click', async () => {
    try {
      await api.testTelegram();
      showToast('Messaggio di test Telegram inviato!', 'success');
    } catch(e) {
      showToast('Errore invio messaggio: verifica token e chat_id in .env', 'error');
    }
  });

  // Save Settings
  document.getElementById('settingsForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const markets = [];
      if (document.getElementById('marketIt').checked) markets.push('IT');
      if (document.getElementById('marketUs').checked) markets.push('US');
      if (document.getElementById('marketEu').checked) markets.push('EU');

      const reportTimes = Array.from(document.querySelectorAll('input[name="reportTime"]')).map(i => i.value);

      const data = {
        strategy: document.getElementById('strategyType').value,
        budget: parseFloat(document.getElementById('targetBudget').value) || 0,
        markets,
        reportFreq: parseInt(document.getElementById('reportFreq').value),
        reportTimes
      };

      await api.updateSettings(data);
      showToast('Impostazioni salvate con successo!', 'success');
    } catch(e) {
      showToast('Errore durante il salvataggio delle impostazioni', 'error');
    }
  });

  // Change Password
  const btnChangePassword = document.getElementById('btnChangePassword');
  if (btnChangePassword) {
    btnChangePassword.addEventListener('click', async () => {
      const currentPassword = document.getElementById('currentPassword').value;
      const newPassword = document.getElementById('newPassword').value;
      const alertBox = document.getElementById('passwordAlert');

      if (alertBox) alertBox.style.display = 'none';

      if (!currentPassword || !newPassword) {
        showToast('Inserisci sia la password attuale che la nuova', 'error');
        return;
      }

      if (newPassword.length < 8) {
        showToast('La nuova password deve contenere almeno 8 caratteri', 'error');
        return;
      }

      btnChangePassword.disabled = true;
      try {
        await api.changePassword(currentPassword, newPassword);
        showToast('Password aggiornata con successo!', 'success');
        document.getElementById('currentPassword').value = '';
        document.getElementById('newPassword').value = '';
      } catch (err) {
        showToast(err.message || 'Errore durante la modifica della password', 'error');
        if (alertBox) {
          alertBox.style.display = 'block';
          alertBox.textContent = err.message || 'Errore durante la modifica della password';
        }
      } finally {
        btnChangePassword.disabled = false;
      }
    });
  }
};

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initSettings);
} else {
  initSettings();
}

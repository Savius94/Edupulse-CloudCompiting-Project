let authMode = 'login';
let billingCycle = 'monthly';
let token = localStorage.getItem('edupulse_token') || null;
let currentUser = JSON.parse(localStorage.getItem('edupulse_user') || 'null');
let preselectedPlan = 500;

const PRICING = {
  monthly: {
    200: { price: '500 €', sub: "Sans engagement (ou 450 €/mois engagé à l'année)" },
    500: { price: '1 000 €', sub: "Sans engagement (ou 900 €/mois engagé à l'année)" },
    1000: { price: '2 000 €', sub: "Sans engagement (ou 1 800 €/mois engagé à l'année)" }
  },
  annual: {
    200: { price: '450 €', sub: 'Facturé annuellement (5 400 € HT/an au lieu de 6 000 €)' },
    500: { price: '900 €', sub: 'Facturé annuellement (10 800 € HT/an au lieu de 12 000 €)' },
    1000: { price: '1 800 €', sub: 'Facturé annuellement (21 600 € HT/an au lieu de 24 000 €)' }
  }
};

function setBillingCycle(cycle) {
  billingCycle = cycle;
  const btnM = document.getElementById('btn-billing-monthly');
  const btnA = document.getElementById('btn-billing-annual');
  if (!btnM || !btnA) return;

  if (cycle === 'monthly') {
    btnM.className = 'px-5 py-2 rounded-xl text-sm font-semibold transition bg-indigo-600 text-white';
    btnA.className = 'px-5 py-2 rounded-xl text-sm font-semibold transition text-slate-600 hover:text-slate-900 flex items-center gap-2';
  } else {
    btnA.className = 'px-5 py-2 rounded-xl text-sm font-semibold transition bg-indigo-600 text-white flex items-center gap-2';
    btnM.className = 'px-5 py-2 rounded-xl text-sm font-semibold transition text-slate-600 hover:text-slate-900';
  }

  [200, 500, 1000].forEach(plan => {
    const pEl = document.getElementById(`price-${plan}`);
    const sEl = document.getElementById(`subprice-${plan}`);
    if (pEl) pEl.textContent = PRICING[cycle][plan].price;
    if (sEl) sEl.textContent = PRICING[cycle][plan].sub;
  });
}

function selectPlanFromPricing(planUsers) {
  preselectedPlan = planUsers;
  if (currentUser && token) {
    window.location.href = `/espace-client?plan=${planUsers}`;
  } else {
    openAuthModal('register');
  }
}

// Menu Paramètres (Bascule rapide entre les comptes)
function toggleDevMenu() {
  const menu = document.getElementById('dev-menu');
  if (menu) menu.classList.toggle('hidden');
}

document.addEventListener('click', (e) => {
  const menu = document.getElementById('dev-menu');
  if (menu && !menu.classList.contains('hidden') && !e.target.closest('#dev-menu') && !e.target.closest('button[onclick="toggleDevMenu()"]')) {
    menu.classList.add('hidden');
  }
});

async function quickSwitch(role) {
  const menu = document.getElementById('dev-menu');
  if (menu) menu.classList.add('hidden');

  if (role === 'guest') {
    logout();
    return;
  }

  try {
    const res = await fetch(`/api/auth/demo-switch/${role}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.access_token) {
      saveSession(data.access_token, data.user);
      window.location.href = data.user.role === 'SUPER_ADMIN' ? '/superadmin' : '/espace-client';
      return;
    }
    alert(data.detail || 'Impossible de basculer vers ce rôle.');
  } catch (err) {
    alert('Erreur de connexion au serveur lors du changement de rôle.');
  }
}

function saveSession(jwt, user) {
  token = jwt;
  currentUser = user;
  localStorage.setItem('edupulse_token', jwt);
  localStorage.setItem('edupulse_user', JSON.stringify(user));
  updateHeaderUI();
}

function logout(redirectHome = true) {
  token = null;
  currentUser = null;
  localStorage.removeItem('edupulse_token');
  localStorage.removeItem('edupulse_user');
  updateHeaderUI();
  if (redirectHome && window.location.pathname !== '/') {
    window.location.href = '/';
  }
}

function goToMySpace() {
  if (currentUser && token) {
    window.location.href = currentUser.role === 'SUPER_ADMIN' ? '/superadmin' : '/espace-client';
  } else {
    openAuthModal('login');
  }
}

function updateHeaderUI() {
  const guestEl = document.getElementById('header-guest');
  const userEl = document.getElementById('header-user');
  const badgeEl = document.getElementById('user-badge');
  const btnHeader = document.getElementById('btn-my-space-header');
  const btnHero = document.getElementById('btn-my-space-hero');

  if (currentUser && token) {
    const isSA = currentUser.role === 'SUPER_ADMIN';
    if (guestEl) guestEl.classList.add('hidden');
    if (userEl) {
      userEl.classList.remove('hidden');
      userEl.classList.add('flex');
    }
    if (badgeEl) {
      badgeEl.textContent = `${currentUser.full_name || currentUser.email} (${isSA ? 'Super-Admin' : 'Espace Client'})`;
    }
    if (btnHeader) btnHeader.textContent = isSA ? 'Administration Cloud →' : 'Mon espace établissements →';
    if (btnHero) btnHero.textContent = isSA ? "Accéder à l'administration →" : 'Accéder à mon espace →';
  } else {
    if (guestEl) guestEl.classList.remove('hidden');
    if (userEl) {
      userEl.classList.add('hidden');
      userEl.classList.remove('flex');
    }
    if (btnHero) btnHero.textContent = 'Se connecter à mon espace';
  }
}

// Authentification Modale
function openAuthModal(mode) {
  const modal = document.getElementById('auth-modal');
  if (!modal) return;
  authMode = mode;
  modal.classList.remove('hidden');
  document.getElementById('auth-error').classList.add('hidden');
  document.getElementById('field-fullname').classList.toggle('hidden', mode === 'login');
  document.getElementById('auth-modal-title').textContent = mode === 'login' ? 'Connexion à votre espace' : 'Créer votre compte établissement';
  document.getElementById('tab-login').className = mode === 'login'
    ? 'py-2 rounded-lg text-xs font-semibold bg-white shadow-sm text-slate-900'
    : 'py-2 rounded-lg text-xs font-semibold text-slate-600';
  document.getElementById('tab-register').className = mode === 'register'
    ? 'py-2 rounded-lg text-xs font-semibold bg-white shadow-sm text-slate-900'
    : 'py-2 rounded-lg text-xs font-semibold text-slate-600';
}

function closeAuthModal() {
  const modal = document.getElementById('auth-modal');
  if (modal) modal.classList.add('hidden');
}

async function handleAuthSubmit(e) {
  e.preventDefault();
  const email = document.getElementById('auth-email').value.trim();
  const password = document.getElementById('auth-password').value;
  const full_name = document.getElementById('auth-fullname').value.trim() || 'Administrateur Établissement';
  const errEl = document.getElementById('auth-error');
  errEl.classList.add('hidden');

  const endpoint = authMode === 'login' ? '/api/auth/login' : '/api/auth/register';
  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, full_name })
    });
    const data = await res.json();
    if (!res.ok) {
      errEl.textContent = data.detail || 'Identifiants invalides';
      errEl.classList.remove('hidden');
      return;
    }
    saveSession(data.access_token, data.user);
    closeAuthModal();
    if (data.user.role === 'SUPER_ADMIN') {
      window.location.href = '/superadmin';
    } else {
      const planParam = preselectedPlan && authMode === 'register' ? `?plan=${preselectedPlan}` : '';
      window.location.href = `/espace-client${planParam}`;
    }
  } catch (err) {
    errEl.textContent = 'Erreur de communication avec le serveur.';
    errEl.classList.remove('hidden');
  }
}

// Contact Commercial (>1000 users)
function openContactModal() {
  const modal = document.getElementById('contact-modal');
  if (!modal) return;
  document.getElementById('contact-success').classList.add('hidden');
  modal.classList.remove('hidden');
}

function closeContactModal() {
  const modal = document.getElementById('contact-modal');
  if (modal) modal.classList.add('hidden');
}

function submitContactForm(e) {
  e.preventDefault();
  document.getElementById('contact-success').classList.remove('hidden');
  setTimeout(() => closeContactModal(), 1800);
}

// Résolution des URLs d'établissements
function getTenantTargetUrl(t, type = 'primary') {
  if (!t) return '#';
  const host = window.location.hostname;
  const directPort = t.direct_port || (8100 + (t.id || 1));
  const slug = t.slug;

  const directHost = (host === 'localhost' || host === '127.0.0.1' || host.endsWith('.localhost')) ? 'localhost' : host;
  const directUrl = `http://${directHost}:${directPort}`;
  const domainUrl = `http://${slug}.edupulse.mnbll.fr`;
  const localPort = window.location.port === '8000' ? ':8000' : '';
  const localSubdomainUrl = `http://${slug}.localhost${localPort}`;

  if (type === 'direct') return directUrl;
  if (type === 'domain') return domainUrl;
  if (type === 'local') return localSubdomainUrl;

  if (host.includes('edupulse.mnbll.fr') || host.endsWith('.mnbll.fr')) {
    return domainUrl;
  }
  if ((host === 'localhost' || host === '127.0.0.1' || host.endsWith('.localhost')) && (window.location.port === '' || window.location.port === '80' || window.location.port === '8000')) {
    return localSubdomainUrl;
  }
  if (host === 'localhost' || host === '127.0.0.1' || /^(\d{1,3}\.){3}\d{1,3}$/.test(host)) {
    return directUrl;
  }
  return domainUrl;
}

// ================= ESPACE CLIENT (/espace-client) =================
async function loadMyTenants() {
  const container = document.getElementById('client-tenants-list');
  if (!container) return;

  if (!token || !currentUser) {
    openAuthModal('login');
    return;
  }

  try {
    const res = await fetch('/api/tenants/me', {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (res.status === 401) {
      logout(false);
      openAuthModal('login');
      return;
    }
    if (!res.ok) throw new Error();
    const tenants = await res.json();
    renderClientTenants(tenants);
  } catch (e) {
    container.innerHTML = `<p class="text-xs text-slate-400 py-6 text-center">Aucun établissement configuré sur votre compte pour le moment.</p>`;
  }
}

function renderClientTenants(tenants) {
  const container = document.getElementById('client-tenants-list');
  if (!container) return;

  if (!tenants || tenants.length === 0) {
    container.innerHTML = `
      <div class="py-12 px-4 text-center border-2 border-dashed border-slate-200 rounded-2xl">
        <p class="text-sm font-semibold text-slate-700">Aucun établissement configuré sur votre compte</p>
        <p class="text-xs text-slate-400 mt-1">Remplissez le formulaire à gauche et cliquez sur « Mettre en service mon établissement ».</p>
      </div>
    `;
    return;
  }

  container.innerHTML = tenants.map(t => {
    const isActive = t.status === 'RUNNING';
    const primaryUrl = getTenantTargetUrl(t, 'primary');
    const directUrl = getTenantTargetUrl(t, 'direct');
    const displayDomain = `${t.slug}.edupulse.mnbll.fr`;

    return `
      <div class="p-5 rounded-2xl border border-slate-200 bg-slate-50/50 hover:bg-white hover:shadow-md transition flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div class="space-y-1.5">
          <div class="flex items-center gap-2.5">
            <span class="w-2.5 h-2.5 rounded-full ${isActive ? 'bg-emerald-500' : 'bg-amber-500'}"></span>
            <h3 class="font-bold text-base text-slate-900">${t.institution_name}</h3>
            <span class="text-[11px] font-semibold px-2.5 py-0.5 rounded-full ${isActive ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}">
              ${isActive ? 'En service' : 'Suspendu'}
            </span>
          </div>
          <div class="flex flex-wrap items-center gap-2 pt-1">
            <label class="text-xs text-slate-600 font-medium">Forfait :</label>
            <select id="client-plan-select-${t.slug}" class="px-2.5 py-1 rounded-lg border border-slate-300 bg-white text-xs font-semibold text-slate-800">
              <option value="200" ${t.plan_max_users === 200 ? 'selected' : ''}>Essentiel (200 users • 1↔3 rép.)</option>
              <option value="500" ${t.plan_max_users === 500 ? 'selected' : ''}>Campus (500 users • 2↔5 rép.)</option>
              <option value="1000" ${t.plan_max_users === 1000 ? 'selected' : ''}>Académie (1000 users • 2↔8 rép.)</option>
            </select>
            <button onclick="clientChangePlan('${t.slug}', ${t.plan_max_users})"
              class="px-3 py-1 rounded-lg bg-slate-900 hover:bg-slate-800 text-white text-[11px] font-semibold transition">
              Appliquer
            </button>
          </div>
          <p class="text-xs text-slate-500 flex flex-wrap items-center gap-2">
            <span>Adresse web dédiée : <a href="${primaryUrl}" target="_blank" class="font-semibold text-indigo-600 hover:underline">${displayDomain}</a></span>
            <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-indigo-50 text-indigo-700 font-semibold text-[11px] border border-indigo-200">
              🐝 ${t.orchestrator || 'SWARM'} : ${t.replicas || 1} réplica(s) actif(s) (Auto-Scale ${t.min_replicas || 1}↔${t.max_replicas || 3})
            </span>
          </p>
        </div>

        <div class="flex flex-col sm:items-end gap-2">
          <div class="flex items-center gap-2">
            <a href="${primaryUrl}" target="_blank"
              class="inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-sm transition">
              <span>Accéder à mon établissement</span>
              <span>→</span>
            </a>
            <button onclick="clientDeleteTenant('${t.slug}')" title="Supprimer cet établissement"
              class="px-3 py-2.5 rounded-xl border border-rose-200 bg-rose-50 hover:bg-rose-100 text-rose-700 text-xs font-semibold transition">
              🗑️ Supprimer
            </button>
          </div>
          <div class="flex items-center gap-3 text-[11px] text-slate-400">
            <a href="${primaryUrl}" target="_blank" class="hover:text-indigo-600 underline font-mono">${primaryUrl.replace(/^https?:\/\//, '')}</a>
            <span>•</span>
            <a href="${directUrl}" target="_blank" class="hover:text-indigo-600 underline">Port direct :${8100 + (t.id || 1)}</a>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

async function handleCreateTenant(e) {
  e.preventDefault();
  const institution_name = document.getElementById('tenant-name').value.trim();
  const slug = document.getElementById('tenant-slug').value.trim().toLowerCase();
  const plan_max_users = parseInt(document.getElementById('tenant-plan').value, 10);
  const msgEl = document.getElementById('create-tenant-msg');
  const btn = document.getElementById('btn-create-tenant');

  btn.disabled = true;
  btn.textContent = '⏳ Mise en service de votre espace en cours...';
  msgEl.className = 'text-xs p-3.5 rounded-xl bg-indigo-50 text-indigo-700 font-medium border border-indigo-200';
  msgEl.textContent = `Création et préparation de l'espace dédié pour « ${institution_name} » en cours, veuillez patienter quelques secondes...`;
  msgEl.classList.remove('hidden');

  try {
    const res = await fetch('/api/tenants', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify({ institution_name, slug, plan_max_users })
    });
    const data = await res.json();

    if (!res.ok) {
      msgEl.className = 'text-xs p-3.5 rounded-xl bg-rose-50 text-rose-700 font-medium border border-rose-200';
      msgEl.textContent = data.detail || 'Erreur lors de la création.';
      return;
    }

    const openLink = getTenantTargetUrl(data, 'primary');
    const fallbackDirectLink = getTenantTargetUrl(data, 'direct');

    msgEl.className = 'text-xs p-4 rounded-xl bg-emerald-50 text-emerald-800 border border-emerald-200 space-y-2';
    msgEl.innerHTML = `
      <p class="font-bold text-sm">✅ Votre établissement « ${data.institution_name} » est maintenant en service !</p>
      <p>Votre espace dédié est opérationnel et accessible immédiatement :</p>
      <div class="pt-2 flex flex-wrap items-center gap-3">
        <a href="${openLink}" target="_blank" class="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-emerald-700 hover:bg-emerald-800 text-white font-semibold text-xs shadow-sm transition">
          <span>Ouvrir l'espace ${data.institution_name}</span>
          <span>→</span>
        </a>
        <span class="text-xs font-mono text-emerald-900 bg-emerald-100/70 px-2.5 py-1 rounded-lg">${openLink}</span>
        <a href="${fallbackDirectLink}" target="_blank" class="text-[11px] text-emerald-700 hover:underline">Accès port direct (:${data.direct_port || (8100 + (data.id || 1))})</a>
      </div>
    `;
    e.target.reset();
    await loadMyTenants();
  } catch (err) {
    msgEl.className = 'text-xs p-3.5 rounded-xl bg-rose-50 text-rose-700 font-medium border border-rose-200';
    msgEl.textContent = 'Erreur réseau lors de la mise en service.';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Mettre en service mon établissement';
  }
}

async function clientChangePlan(slug, currentPlan) {
  const sel = document.getElementById(`client-plan-select-${slug}`);
  const msgEl = document.getElementById('client-plan-msg');
  if (!sel) return;
  const newPlan = parseInt(sel.value, 10);
  if (newPlan === currentPlan) return;

  const label = newPlan > currentPlan ? 'Upgrade' : 'Downgrade';
  if (msgEl) {
    msgEl.className = 'text-xs p-3.5 rounded-xl mb-4 bg-indigo-50 text-indigo-700 border border-indigo-200 font-medium';
    msgEl.textContent = `⏳ ${label} du forfait de « ${slug} » vers ${newPlan} utilisateurs en cours (ajustement des réplicas et du quota)...`;
    msgEl.classList.remove('hidden');
  }

  try {
    const res = await fetch(`/api/tenants/${slug}/change-plan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ plan_max_users: newPlan })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (msgEl) {
        msgEl.className = 'text-xs p-3.5 rounded-xl mb-4 bg-rose-50 text-rose-700 border border-rose-200 font-medium';
        msgEl.textContent = `❌ ${data.detail || 'Impossible de modifier le forfait.'}`;
      }
      sel.value = String(currentPlan);
      return;
    }
    if (msgEl) {
      msgEl.className = 'text-xs p-3.5 rounded-xl mb-4 bg-emerald-50 text-emerald-800 border border-emerald-200 font-medium';
      msgEl.textContent = `✅ Forfait mis à jour (${newPlan} utilisateurs max • ${data.replicas} réplicas actifs, plage ${data.min_replicas}↔${data.max_replicas}).`;
    }
    await loadMyTenants();
  } catch (e) {
    if (msgEl) {
      msgEl.className = 'text-xs p-3.5 rounded-xl mb-4 bg-rose-50 text-rose-700 border border-rose-200 font-medium';
      msgEl.textContent = '❌ Erreur réseau lors de la modification du forfait.';
    }
  }
}

async function clientDeleteTenant(slug) {
  if (!confirm(`Supprimer définitivement l'établissement « ${slug} » et toutes ses données ?`)) return;
  await fetch(`/api/tenants/${slug}`, {
    method: 'DELETE',
    headers: { 'Authorization': `Bearer ${token}` }
  });
  await loadMyTenants();
}

// ================= SUPER-ADMIN GLOBAL (/superadmin) =================
async function refreshSuperAdmin() {
  if (!token || !currentUser || currentUser.role !== 'SUPER_ADMIN') {
    openAuthModal('login');
    return;
  }

  try {
    const [vmRes, tenantsRes, usersRes] = await Promise.all([
      fetch('/api/superadmin/vm-stats', { headers: { 'Authorization': `Bearer ${token}` } }),
      fetch('/api/superadmin/tenants', { headers: { 'Authorization': `Bearer ${token}` } }),
      fetch('/api/superadmin/users', { headers: { 'Authorization': `Bearer ${token}` } })
    ]);

    if (vmRes.status === 401 || vmRes.status === 403 || tenantsRes.status === 401) {
      logout(false);
      openAuthModal('login');
      return;
    }

    if (vmRes.ok) {
      const vm = await vmRes.json();
      document.getElementById('metric-cpu').textContent = `${vm.cpu_percent} %`;
      document.getElementById('bar-cpu').style.width = `${Math.min(vm.cpu_percent, 100)}%`;
      const cpuModeBadge = document.getElementById('badge-cpu-mode');
      if (cpuModeBadge) cpuModeBadge.classList.toggle('hidden', !vm.simulated_cpu);

      document.getElementById('metric-ram').textContent = `${vm.ram_used_mb} / ${vm.ram_total_mb} Mo`;
      document.getElementById('bar-ram').style.width = `${Math.min(vm.ram_percent, 100)}%`;
      document.getElementById('metric-disk').textContent = `${vm.disk_used_gb} / ${vm.disk_total_gb} Go`;
      document.getElementById('bar-disk').style.width = `${Math.min(vm.disk_percent, 100)}%`;

      if (vm.swarm) {
        const swarmBadge = document.getElementById('swarm-status-badge');
        const btnInitSwarm = document.getElementById('btn-init-swarm');
        const nodesSummary = document.getElementById('swarm-nodes-summary');
        const repSub = document.getElementById('metric-replicas-sub');

        if (vm.swarm.swarm_active) {
          swarmBadge.textContent = `● Cluster Swarm Actif (${vm.swarm.nodes_count} noeud(s))`;
          swarmBadge.className = 'text-xs font-bold px-2.5 py-1 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30';
          if (btnInitSwarm) btnInitSwarm.classList.add('hidden');
        } else {
          swarmBadge.textContent = '○ Mode Hybride Compose (Swarm non initialisé)';
          swarmBadge.className = 'text-xs font-bold px-2.5 py-1 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30';
          if (btnInitSwarm) btnInitSwarm.classList.remove('hidden');
        }
        if (nodesSummary) {
          const repHost = vm.swarm.served_by_replica ? ` | Servi par: ${vm.swarm.served_by_replica}` : '';
          nodesSummary.textContent = `Mode: ${vm.swarm.orchestrator_mode} | Noeuds: ${vm.swarm.nodes_count} | Services Swarm: ${vm.swarm.swarm_services_count} | Total Réplicas: ${vm.total_replicas || 0}${repHost}`;
        }
        const nodesGrid = document.getElementById('swarm-nodes-grid');
        if (nodesGrid && Array.isArray(vm.swarm.nodes) && vm.swarm.nodes.length > 0) {
          nodesGrid.innerHTML = vm.swarm.nodes.map(n => {
            const isReady = (n.status || '').toLowerCase() === 'ready';
            const isMgr = n.role === 'Manager';
            return `
              <div class="p-3 rounded-xl bg-slate-800/80 border ${isReady ? 'border-slate-700' : 'border-rose-500/50'} flex items-center justify-between gap-2">
                <div>
                  <div class="flex items-center gap-2">
                    <span class="w-2 h-2 rounded-full ${isReady ? 'bg-emerald-400' : 'bg-rose-500'}"></span>
                    <span class="text-xs font-bold text-white">${n.hostname}</span>
                  </div>
                  <p class="text-[11px] text-slate-400 mt-0.5">${isMgr ? 'Manager (Registry + Proxy + BDD)' : 'Worker (Apps + Traefik)'}</p>
                </div>
                <span class="text-[10px] font-bold px-2 py-0.5 rounded-full ${isReady ? 'bg-emerald-500/20 text-emerald-300' : 'bg-rose-500/20 text-rose-300'}">${n.status}</span>
              </div>
            `;
          }).join('');
        }
        if (repSub) {
          repSub.textContent = `${vm.total_replicas || 0} réplica(s) en ligne (${vm.swarm.orchestrator_mode})`;
        }
      }

      const eventsContainer = document.getElementById('sa-cloud-events');
      if (eventsContainer && Array.isArray(vm.events) && vm.events.length > 0) {
        const pillarColors = {
          ELASTICITY: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30',
          SCALABILITY: 'text-indigo-400 bg-indigo-500/10 border-indigo-500/30',
          AGILITY: 'text-amber-400 bg-amber-500/10 border-amber-500/30',
          SWARM: 'text-sky-400 bg-sky-500/10 border-sky-500/30'
        };
        eventsContainer.innerHTML = vm.events.map(ev => {
          const badgeCls = pillarColors[ev.pillar] || pillarColors.SWARM;
          return `
            <div class="flex items-start gap-2 py-1 border-b border-slate-900 last:border-0">
              <span class="text-slate-500 shrink-0">[${ev.timestamp}]</span>
              <span class="px-1.5 py-0.2 rounded text-[10px] font-bold border ${badgeCls} shrink-0">${ev.pillar}</span>
              <span class="text-slate-200">${ev.message}</span>
            </div>
          `;
        }).join('');
      }
    }

    if (tenantsRes.ok) {
      const tenants = await tenantsRes.json();
      const totalReps = tenants.reduce((acc, t) => acc + (t.status === 'RUNNING' ? (t.replicas || 1) : 0), 0);
      document.getElementById('metric-stacks').textContent = `${tenants.length} stack(s) • ${totalReps} rép.`;
      const tbody = document.getElementById('sa-tenants-tbody');
      tbody.innerHTML = tenants.map(t => {
        const tenantUrl = getTenantTargetUrl(t, 'primary');
        const reps = t.replicas || 1;
        const minR = t.min_replicas || 1;
        const maxR = t.max_replicas || 3;
        const autoOn = t.autoscale_enabled !== false;

        return `
        <tr>
          <td class="py-3 px-2">
            <div class="font-semibold text-slate-900">${t.institution_name}</div>
            <div class="text-[11px] text-slate-400">${t.owner_email || '-'}</div>
          </td>
          <td class="py-3 px-2 font-mono text-xs">
            <a href="${tenantUrl}" target="_blank" class="text-indigo-600 hover:underline flex items-center gap-1 font-semibold">
              <span>${t.slug}.edupulse.mnbll.fr</span>
              <span class="text-[10px]">↗</span>
            </a>
          </td>
          <td class="py-3 px-2">
            <div class="flex items-center gap-1.5">
              <select id="sa-plan-select-${t.slug}" class="px-2 py-1 rounded-lg border border-slate-300 bg-white text-xs font-semibold text-slate-800">
                <option value="200" ${t.plan_max_users === 200 ? 'selected' : ''}>200 users (1↔3)</option>
                <option value="500" ${t.plan_max_users === 500 ? 'selected' : ''}>500 users (2↔5)</option>
                <option value="1000" ${t.plan_max_users === 1000 ? 'selected' : ''}>1000 users (2↔8)</option>
              </select>
              <button onclick="saChangePlan('${t.slug}', ${t.plan_max_users})" title="Appliquer le changement de forfait (Upgrade/Downgrade)"
                class="px-2 py-1 rounded-lg bg-slate-900 hover:bg-slate-800 text-white text-[11px] font-semibold transition">
                OK
              </button>
            </div>
          </td>
          <td class="py-3 px-2">
            <div class="inline-flex items-center gap-1.5 bg-slate-100 px-2 py-1 rounded-xl border border-slate-200">
              <button onclick="saScaleTenant('${t.slug}', -1)" title="Scale-In (-1 réplica)"
                class="w-6 h-6 rounded-lg bg-white hover:bg-slate-200 text-slate-700 font-bold text-xs shadow-sm transition">-</button>
              <span class="text-xs font-extrabold text-indigo-700 px-1.5">${reps} rép.</span>
              <button onclick="saScaleTenant('${t.slug}', 1)" title="Scale-Out (+1 réplica)"
                class="w-6 h-6 rounded-lg bg-white hover:bg-slate-200 text-slate-700 font-bold text-xs shadow-sm transition">+</button>
            </div>
          </td>
          <td class="py-3 px-2">
            <button onclick="saToggleAutoscale('${t.slug}')"
              class="text-[11px] font-bold px-2.5 py-1 rounded-full transition border ${autoOn ? 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100' : 'bg-slate-100 text-slate-500 border-slate-200 hover:bg-slate-200'}">
              ${autoOn ? `⚡ Auto (${minR}↔${maxR})` : 'Manuel'}
            </button>
          </td>
          <td class="py-3 px-2">
            <span class="text-[11px] font-bold px-2.5 py-0.5 rounded-full ${t.status === 'RUNNING' ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-200 text-slate-700'}">
              ${t.status}
            </span>
          </td>
          <td class="py-3 px-2 text-right space-x-2 whitespace-nowrap">
            <button onclick="saRollingUpdateTenant('${t.slug}')" title="Mise à jour sans coupure (Zero-Downtime Rolling Update)"
              class="text-xs font-semibold text-indigo-600 hover:underline">
              🔄 Rolling Update
            </button>
            <button onclick="saToggleTenant('${t.slug}', '${t.status}')" class="text-xs font-semibold text-amber-600 hover:underline">
              ${t.status === 'RUNNING' ? 'Stopper' : 'Démarrer'}
            </button>
            <button onclick="saDeleteTenant('${t.slug}')" class="text-xs font-semibold text-rose-600 hover:underline">
              Supprimer
            </button>
          </td>
        </tr>
      `;
      }).join('') || `<tr><td colspan="7" class="py-6 text-center text-xs text-slate-400">Aucun stack déployé</td></tr>`;
    }

    if (usersRes.ok) {
      const users = await usersRes.json();
      const ubody = document.getElementById('sa-users-tbody');
      ubody.innerHTML = users.map(u => `
        <tr>
          <td class="py-3 px-2 text-xs text-slate-400">#${u.id}</td>
          <td class="py-3 px-2 font-medium text-slate-900">${u.full_name || '-'}</td>
          <td class="py-3 px-2 text-xs text-slate-600">${u.email}</td>
          <td class="py-3 px-2 text-xs font-semibold">${u.role}</td>
          <td class="py-3 px-2 text-xs">${u.tenant_count || 0}</td>
          <td class="py-3 px-2 text-right">
            ${u.role !== 'SUPER_ADMIN' ? `<button onclick="saDeleteUser(${u.id})" class="text-xs font-semibold text-rose-600 hover:underline">Supprimer compte</button>` : '<span class="text-xs text-slate-300">Protégé</span>'}
          </td>
        </tr>
      `).join('');
    }
  } catch (e) {}
}

async function handleSuperAdminCreateStack(e) {
  e.preventDefault();
  const institution_name = document.getElementById('sa-inst-name').value.trim();
  const slug = document.getElementById('sa-slug').value.trim().toLowerCase();
  const owner_email = document.getElementById('sa-owner-email').value.trim();
  const plan_max_users = parseInt(document.getElementById('sa-plan').value, 10);

  await fetch('/api/superadmin/tenants', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
    body: JSON.stringify({ institution_name, slug, owner_email, plan_max_users })
  });
  e.target.reset();
  refreshSuperAdmin();
}

async function saToggleTenant(slug, currentStatus) {
  const action = currentStatus === 'RUNNING' ? 'stop' : 'start';
  await fetch(`/api/superadmin/tenants/${slug}/${action}`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}` }
  });
  refreshSuperAdmin();
}

async function saChangePlan(slug, currentPlan) {
  const sel = document.getElementById(`sa-plan-select-${slug}`);
  if (!sel) return;
  const newPlan = parseInt(sel.value, 10);
  if (newPlan === currentPlan) return;

  const res = await fetch(`/api/superadmin/tenants/${slug}/change-plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
    body: JSON.stringify({ plan_max_users: newPlan })
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    alert(data.detail || 'Impossible de modifier le forfait.');
    sel.value = String(currentPlan);
    return;
  }
  await refreshSuperAdmin();
}

async function saScaleTenant(slug, delta) {
  const res = await fetch(`/api/superadmin/tenants/${slug}/scale`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
    body: JSON.stringify({ delta })
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    alert(data.detail || 'Erreur lors du scaling manuel.');
    return;
  }
  await refreshSuperAdmin();
}

async function saToggleAutoscale(slug) {
  await fetch(`/api/superadmin/tenants/${slug}/autoscale-toggle`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}` }
  });
  refreshSuperAdmin();
}

async function saRollingUpdateTenant(slug) {
  await fetch(`/api/superadmin/tenants/${slug}/rolling-update`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}` }
  });
  refreshSuperAdmin();
}

async function saSimulateLoad(cpuLoad) {
  await fetch('/api/superadmin/autoscale/simulate-load', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
    body: JSON.stringify({ cpu_load: cpuLoad })
  });
  refreshSuperAdmin();
}

async function saInitSwarm() {
  await fetch('/api/superadmin/swarm/init', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}` }
  });
  refreshSuperAdmin();
}

async function saDeleteTenant(slug) {
  if (!confirm(`Supprimer définitivement le stack ${slug} et ses données ?`)) return;
  await fetch(`/api/superadmin/tenants/${slug}`, {
    method: 'DELETE',
    headers: { 'Authorization': `Bearer ${token}` }
  });
  refreshSuperAdmin();
}

async function saDeleteUser(userId) {
  if (!confirm('Supprimer ce compte client et tous ses stacks associés ?')) return;
  await fetch(`/api/superadmin/users/${userId}`, {
    method: 'DELETE',
    headers: { 'Authorization': `Bearer ${token}` }
  });
  refreshSuperAdmin();
}

// Initialisation selon la page chargée
document.addEventListener('DOMContentLoaded', () => {
  updateHeaderUI();
  const path = window.location.pathname;

  if (path === '/espace-client' || path === '/client') {
    const urlParams = new URLSearchParams(window.location.search);
    const planParam = urlParams.get('plan');
    const selectEl = document.getElementById('tenant-plan');
    if (planParam && selectEl && ['200', '500', '1000'].includes(planParam)) {
      selectEl.value = planParam;
    }
    loadMyTenants();
  } else if (path === '/superadmin' || path === '/admin') {
    refreshSuperAdmin();
  }
});

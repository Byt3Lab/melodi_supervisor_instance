// @ts-nocheck
import { createApp } from '/static/base/melodiJS/index.js';

function startInstanceApp() {
  const mountEl = document.getElementById('melodi-supervisor-instance-app');
  if (!mountEl) return;
  const tplEl = document.getElementById('tpl-supervisor-instance-spa');
  const templateHtml = tplEl ? tplEl.innerHTML : '<div>Template introuvable</div>';

  const app = createApp({
    delimiters: ['[[', ']]'],
    data() {
      return {
        status: null as any,
        busy: false,
        error: null as string | null,
        successMsg: null as string | null,
        pairForm: {
          center_url: '',
          instance_name: '',
          enrollment_token: '',
          allow_insecure_http: false,
        },
      };
    },
    methods: {
      async fetchStatus() {
        this.error = null;
        try {
          const res = await window.melodi.api.get('/api/melodi_supervisor_instance/status');
          this.status = res?.data || res;
        } catch (e: any) {
          this.error = e?.message || 'Impossible de charger le statut superviseur.';
        }
      },
      async pairWithCenter() {
        this.busy = true;
        this.error = null;
        this.successMsg = null;
        try {
          const res = await window.melodi.api.post('/api/melodi_supervisor_instance/pair', this.pairForm);
          if (res?.success) {
            this.pairForm.enrollment_token = '';
            this.successMsg = res.message || 'Instance enrôlée avec succès.';
            await this.fetchStatus();
          } else {
            this.error = res?.error || 'Échec du pairage.';
          }
        } catch (e: any) {
          this.error = e?.data?.error || e?.message || 'Erreur lors du pairage.';
        } finally {
          this.busy = false;
        }
      },
      async syncNow() {
        this.busy = true;
        this.error = null;
        this.successMsg = null;
        try {
          const res = await window.melodi.api.post('/api/melodi_supervisor_instance/sync', {});
          if (res?.success) {
            this.successMsg = `Synchronisation terminée (manifest v${res.manifest_version}).`;
            await this.fetchStatus();
          } else {
            this.error = res?.error || 'Échec de la synchronisation.';
          }
        } catch (e: any) {
          this.error = e?.data?.error || e?.message || 'Erreur synchronisation.';
        } finally {
          this.busy = false;
        }
      },
      async disconnect() {
        if (!window.confirm("Déconnecter cette instance du Superviseur Central et repasser en mode 'standalone' ?")) {
          return;
        }
        this.busy = true;
        this.error = null;
        try {
          const res = await window.melodi.api.post('/api/melodi_supervisor_instance/disconnect', {});
          if (res?.success) {
            this.successMsg = res.message || 'Instance déconnectée.';
            await this.fetchStatus();
          } else {
            this.error = res?.error || 'Action refusée.';
          }
        } catch (e: any) {
          this.error = e?.data?.error || e?.message || 'Erreur déconnexion.';
        } finally {
          this.busy = false;
        }
      },
    },
    async mounted() {
      await this.fetchStatus();
    },
    template: templateHtml,
  });

  app.mount('#melodi-supervisor-instance-app');
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', startInstanceApp);
} else {
  startInstanceApp();
}

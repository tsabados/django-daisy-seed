/* ── App Header — Alpine.js component for the sticky header ──
 *
 * Manages:
 *   - Brand scraping status (SSE)
 *   - Toast notifications
 *   - (future) Credits updates
 *   - (future) Notifications
 *
 * Usage in template:
 *   <header x-data="appHeader({ projectId: 123, isScraping: false })">
 */

function appHeader({ projectId = null, isScraping = false, isImporting = false, credits = 0 } = {}) {
  return {
    // ── Brand scraping state ─────────────────────────────────────────
    isScraping,

    // ── Product import state ─────────────────────────────────────────
    isImporting,

    // ── Credits state ────────────────────────────────────────────────
    credits,

    // ── Lifecycle ────────────────────────────────────────────────────
    init() {
      this._listenBrandEvents();
      this._listenImportEvents();
      this._listenCreditsEvents();
    },

    destroy() {
      document.removeEventListener('brand:scrape_started', this._onScrapingStarted);
      document.removeEventListener('brand:scrape_completed', this._onScraped);
      document.removeEventListener('brand:scrape_error', this._onScrapeError);
      document.removeEventListener('media_library:import_started', this._onImportStarted);
      document.removeEventListener('media_library:import_completed', this._onImportCompleted);
      document.removeEventListener('media_library:import_error', this._onImportError);
      document.removeEventListener('credits:updated', this._onCreditsUpdated);
    },

    // ── Brand scraping events ────────────────────────────────────────
    _listenBrandEvents() {
      this._onScrapingStarted = () => { this.isScraping = true; };
      this._onScraped = () => {
        this.isScraping = false;
        this.toast('Brand scraping complete!', 'success');
      };
      this._onScrapeError = (e) => {
        this.isScraping = false;
        const msg = (e.detail && e.detail.error) || 'Brand scraping failed.';
        this.toast(msg, 'error');
      };

      document.addEventListener('brand:scrape_started', this._onScrapingStarted);
      document.addEventListener('brand:scrape_completed', this._onScraped);
      document.addEventListener('brand:scrape_error', this._onScrapeError);
    },

    // ── Product import events ────────────────────────────────────────
    _listenImportEvents() {
      this._onImportStarted = () => { this.isImporting = true; };
      this._onImportCompleted = () => {
        this.isImporting = false;
        this.toast('Product import complete!', 'success');
      };
      this._onImportError = (e) => {
        this.isImporting = false;
        const msg = (e.detail && e.detail.error) || 'Product import failed.';
        this.toast(msg, 'error');
      };

      document.addEventListener('media_library:import_started', this._onImportStarted);
      document.addEventListener('media_library:import_completed', this._onImportCompleted);
      document.addEventListener('media_library:import_error', this._onImportError);
    },

    // ── Credits events ───────────────────────────────────────────────
    _listenCreditsEvents() {
      this._onCreditsUpdated = (e) => {
        if (e.detail && e.detail.credits !== undefined) {
          this.credits = e.detail.credits;
        }
      };
      document.addEventListener('credits:updated', this._onCreditsUpdated);
    },

    // ── Toast notifications ──────────────────────────────────────────
    toast(message, type = 'info') {
      const container = document.getElementById('app-toast-container');
      if (!container) return;

      const colorMap = {
        success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
        error: 'border-red-200 bg-red-50 text-red-800',
        info: 'border-blue-200 bg-blue-50 text-blue-800',
        warning: 'border-amber-200 bg-amber-50 text-amber-800',
      };

      const iconMap = {
        success: '<svg xmlns="http://www.w3.org/2000/svg" class="size-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>',
        error: '<svg xmlns="http://www.w3.org/2000/svg" class="size-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"/></svg>',
        info: '<svg xmlns="http://www.w3.org/2000/svg" class="size-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
        warning: '<svg xmlns="http://www.w3.org/2000/svg" class="size-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"/></svg>',
      };

      const colors = colorMap[type] || colorMap.info;
      const icon = iconMap[type] || iconMap.info;

      const el = document.createElement('div');
      el.className = 'pointer-events-auto flex items-center gap-2 rounded-xl border px-4 py-3 text-sm shadow-lg ' + colors;
      el.innerHTML = icon + '<span>' + message + '</span>';
      container.appendChild(el);
      setTimeout(() => el.remove(), 6000);
    },
  };
}

/* ── Brand Scrape Modal — Alpine.js component for the scrape overlay ──
 *
 * Listens for brand:scrape_completed / brand:scrape_error SSE events and either
 * accepts the layer (closing the modal and triggering up-on-accepted) or
 * shows an inline error so the user can retry.
 *
 * Usage in template:
 *   <div x-data="brandScrapeModal({ scraping: true })">
 */
function brandScrapeModal({ scraping = false } = {}) {
  return {
    scraping,
    error: '',

    init() {
      if (!this.scraping) return;

      this._onScraped = () => {
        this._cleanup();
        try {
          if (up.layer.count > 1) up.layer.accept();
        } catch (e) {}
      };

      this._onScrapeError = (e) => {
        this._cleanup();
        this.scraping = false;
        this.error = (e.detail && e.detail.error) || 'Scraping failed.';
      };

      document.addEventListener('brand:scrape_completed', this._onScraped);
      document.addEventListener('brand:scrape_error', this._onScrapeError);
    },

    _cleanup() {
      document.removeEventListener('brand:scrape_completed', this._onScraped);
      document.removeEventListener('brand:scrape_error', this._onScrapeError);
    },

    destroy() {
      this._cleanup();
    },
  };
}

/* ── Home Overview — Alpine.js component for the home page CTA blocks ──
 *
 * Tracks brand/social onboarding state and reacts to SSE events:
 *   - brand:scrape_started / brand:scrape_completed / brand:scrape_error
 *
 * Usage in template:
 *   <div x-data="homeOverview({ hasBrand: false, hasSocials: false, isScraping: false })">
 */
function homeOverview({ hasBrand = false, hasSocials = false, isScraping = false } = {}) {
  return {
    hasBrand,
    hasSocials,
    isScraping,

    init() {
      this._onScrapingStarted = () => { this.isScraping = true; };
      this._onScraped = () => { this.isScraping = false; this.hasBrand = true; };
      this._onScrapeError = () => { this.isScraping = false; };
      this._onProvisioningStarted = (e) => {
        this.isScraping = true;
        var name = (e.detail && e.detail.project_name) || e.project_name;
        if (name) {
          var el = document.getElementById('current-project-name');
          if (el) el.textContent = name;
        }
      };

      document.addEventListener('brand:scrape_started', this._onScrapingStarted);
      document.addEventListener('brand:scrape_completed', this._onScraped);
      document.addEventListener('brand:scrape_error', this._onScrapeError);
      document.addEventListener('project:provisioning_started', this._onProvisioningStarted);
    },

    destroy() {
      document.removeEventListener('brand:scrape_started', this._onScrapingStarted);
      document.removeEventListener('brand:scrape_completed', this._onScraped);
      document.removeEventListener('brand:scrape_error', this._onScrapeError);
      document.removeEventListener('project:provisioning_started', this._onProvisioningStarted);
    },
  };
}

/* ── Social Media Post Composer ── */

document.addEventListener('alpine:init', () => {
  'use strict';

  // ── Post Composer ──────────────────────────────────────────────────────

  Alpine.data('postComposer', () => ({
    PLATFORM_LIMITS: {
      linkedin: 3000,
      x: 280,
      facebook: 63206,
      instagram: 2200,
    },

    // ── Mode state ────────────────────────────────────────────────────────
    mode: 'ai',              // 'ai' or 'editor'

    activeTab: 'all',
    sharedText: '',
    overrideTextShown: {},
    overrideMediaShown: {},
    overrideText: {},

    // ── AI state ──────────────────────────────────────────────────────────
    topic: '',
    postType: 'lifestyle',
    mediaType: 'image',       // 'image' or 'video'
    videoType: 'teaser',      // matches VIDEO_TYPES in video_poc
    selectedBrief: null,      // full brief dict selected by user
    savedBriefs: [],          // briefs saved to DB for this post
    seedMedia: [],           // [{key, mediaPk, url}]  key=client uid, mediaPk=DB pk
    _tempIdCounter: 0,
    generating: false,
    generationStep: '',
    suggestingTopic: false,
    topicSuggestions: [],
    expandedBriefId: null,
    _generationSseCleanup: null,
    _topicSseCleanup: null,

    // ── Preview carousel state ────────────────────────────────────────────
    carouselIndex: 0,
    carouselHover: false,
    captionExpanded: false,

    // ── Image state ───────────────────────────────────────────────────────
    sharedMedia: [],        // [{key, mediaPk, url}]  key=client uid, mediaPk=DB pk
    platformMedia: {},      // {platform: [{key, mediaPk, url}]}

    // ── Dirty state ───────────────────────────────────────────────────────
    isDirty: false,

    // ── Platform management state ─────────────────────────────────────────
    allPlatforms: [],       // all platforms initially available
    activePlatforms: [],    // platforms currently shown (subset of allPlatforms)
    showAddPlatformDropdown: false,

    // ── Publish / status state ────────────────────────────────────────────
    postStatus: '',       // mirrors server-side post.status
    scheduledAt: '',      // ISO string of scheduled_at, empty when not scheduled
    postId: null,
    unscheduleUrl: null,

    // ── Lifecycle ─────────────────────────────────────────────────────────

    init() {
      // Read publish URL from data attribute (only present for existing posts)
      this.publishUrl = this.$el.dataset.publishUrl || null;
      this.publishPanelUrl = this.$el.dataset.publishPanelUrl || null;
      this.postId = this.$el.dataset.postId || null;
      this.postStatus = this.$el.dataset.postStatus || '';
      this.scheduledAt = this.$el.dataset.scheduledAt || '';
      this.unscheduleUrl = this.$el.dataset.unscheduleUrl || null;

      // Restore video fields for existing posts
      if (this.$el.dataset.mediaType) this.mediaType = this.$el.dataset.mediaType;
      if (this.$el.dataset.videoType) this.videoType = this.$el.dataset.videoType;
      if (this.$el.dataset.videoBrief) {
        try { this.selectedBrief = JSON.parse(this.$el.dataset.videoBrief); } catch (e) { 
          console.error('Failed to parse video brief JSON:', e);
        }
      }
      if (this.$el.dataset.videoSuggestions) {
        try { this.savedBriefs = JSON.parse(this.$el.dataset.videoSuggestions); } catch (e) { 
          console.error('Failed to parse video suggestions JSON:', e);
        }
      }

      this._subscribeGenerationSSE();
      this._subscribeTopicSuggestionsSSE();

      // If post is currently generating, resume SSE listener
      const processingStatus = this.$el.dataset.processingStatus || '';
      if (processingStatus === 'generating') {
        this.generating = true;
        this.generationStep = 'Generating your post...';
      }

      // Listen for post-changed SSE to keep the status badge in sync
      this._postChangedHandler = (e) => {
        if (e.detail && e.detail.post_id == this.postId) {
          this.postStatus = e.detail.status || this.postStatus;
          if (e.detail.scheduled_at !== undefined) {
            this.scheduledAt = e.detail.scheduled_at || '';
          }
        }
      };
      document.addEventListener('post-changed', this._postChangedHandler);
      const ta = this.$el.querySelector('#id_shared_text');
      if (ta) this.sharedText = ta.value;

      // Set initial mode from data attribute (edit: editor unless post is empty), new posts: ai
      const initialMode = this.$el.dataset.initialMode;
      this.mode = initialMode || 'ai';

      // Load topic and postType from hidden fields
      const topicField = document.getElementById('id_topic');
      if (topicField) this.topic = topicField.value || '';
      const postTypeField = document.getElementById('id_post_type');
      if (postTypeField) {
        this.postType = postTypeField.value || 'lifestyle';
        postTypeField.value = this.postType;
      }

      this.$el.querySelectorAll('[id^="panel-"]:not(#panel-all)').forEach(panel => {
        const platform = panel.id.replace('panel-', '');
        const textToggle = panel.querySelector('.use-shared-text-toggle');
        const mediaToggle = panel.querySelector('.use-shared-media-toggle');
        const overrideField = panel.querySelector('.override-text-field');

        this.overrideTextShown[platform] = textToggle ? !textToggle.checked : false;
        this.overrideMediaShown[platform] = mediaToggle ? !mediaToggle.checked : false;
        this.overrideText[platform] = overrideField ? overrideField.value : '';
      });

      // Load selected shared media
      const sharedEl = document.getElementById('selected-shared-media-json');
      if (sharedEl) {
        const data = JSON.parse(sharedEl.textContent);
        this.sharedMedia = data.map(item => ({
          key: item.media_id,
          mediaPk: item.media,
          url: item.url,
          is_video: item.is_video || false,
        }));
      }

      // Load selected platform override media
      const platformEl = document.getElementById('selected-platform-media-json');
      if (platformEl) {
        const data = JSON.parse(platformEl.textContent);
        this.platformMedia = {};
        for (const [platform, media] of Object.entries(data)) {
          this.platformMedia[platform] = media.map(item => ({
            key: item.media_id,
            mediaPk: item.media,
            url: item.url,
            is_video: item.is_video || false,
          }));
        }
      }

      // Load seed media
      const seedEl = document.getElementById('selected-seed-media-json');
      if (seedEl) {
        const data = JSON.parse(seedEl.textContent);
        this.seedMedia = data.slice(0, 8).map(item => ({
          key: this._nextTempId(),
          mediaPk: item.media,
          url: item.url,
          is_video: item.is_video || false,
        }));
      }

      // Load enabled platforms for add/remove tab functionality
      const enabledPlatformsEl = document.getElementById('all-platforms-json');
      if (enabledPlatformsEl) {
        this.allPlatforms = JSON.parse(enabledPlatformsEl.textContent);
      }
      const activePlatformsEl = document.getElementById('active-platforms-json');
      if (activePlatformsEl) {
        this.activePlatforms = JSON.parse(activePlatformsEl.textContent);
      } else {
        this.activePlatforms = [...this.allPlatforms];
      }

      // Apply prefill from query params (used by inspiration cards)
      const prefillTopicEl = document.getElementById('prefill-topic-json');
      if (prefillTopicEl) {
        this.topic = prefillTopicEl.textContent.trim();
        const topicHidden = document.getElementById('id_topic');
        if (topicHidden) topicHidden.value = this.topic;
      }
      const prefillModeEl = document.getElementById('prefill-mode-json');
      if (prefillModeEl) {
        const prefillMode = prefillModeEl.textContent.trim();
        if (prefillMode === 'ai' || prefillMode === 'editor') {
          this.mode = prefillMode;
        }
      }

      // Auto-trigger AI suggest when opened from catalog
      const autoSuggestEl = document.getElementById('prefill-auto-suggest-json');
      if (autoSuggestEl && this.seedMedia.length > 0 && !this.topic) {
        this.$nextTick(() => this.suggestTopic());
      }

      // Track dirty state on any form input/change
      const postForm = document.getElementById('post-form');
      if (postForm) {
        this._dirtyHandler = () => { this.isDirty = true; };
        postForm.addEventListener('input', this._dirtyHandler);
        postForm.addEventListener('change', this._dirtyHandler);
      }

      // Listen for media picker acceptance
      this._pickerHandler = (event) => {
        if (!event.value) return;
        // Image editor result: {mediaPk, url}
        if (event.value.mediaPk && !event.value.mediaIds) {
          this.addEditorResultToSeeds(event.value);
          return;
        }
        // Image picker result: {target, mediaIds, urls}
        if (event.value.mediaIds) this.pickerAccepted(event.value);
      };
      document.addEventListener('up:layer:accepted', this._pickerHandler);

      // Intercept any dismiss attempt (Close button, backdrop, Escape) on this layer
      this._ownLayer = up.layer.current;
      this._dismissConfirmed = false;
      this._dismissListener = (event) => {
        if (this.isDirty && !this._dismissConfirmed) {
          event.preventDefault();
          this._showConfirmDialog();
        }
      };
      this._ownLayer.on('up:layer:dismiss', this._dismissListener);
    },

    destroy() {
      document.removeEventListener('up:layer:accepted', this._pickerHandler);
      document.removeEventListener('post-changed', this._postChangedHandler);
      if (this._ownLayer) this._ownLayer.off('up:layer:dismiss', this._dismissListener);
      if (this._generationSseCleanup) this._generationSseCleanup();
      if (this._topicSseCleanup) this._topicSseCleanup();
      const postForm = document.getElementById('post-form');
      if (postForm && this._dirtyHandler) {
        postForm.removeEventListener('input', this._dirtyHandler);
        postForm.removeEventListener('change', this._dirtyHandler);
      }
    },

    // ── Cancel with dirty check ───────────────────────────────────────────

    _showConfirmDialog() {
      up.layer.open({
        mode: 'modal',
        size: 'small',
        content: `
          <div class="p-6 text-center">
            <h3 class="text-lg font-semibold text-zinc-900 mb-2">Unsaved Changes</h3>
            <p class="text-sm text-zinc-600 mb-5">You have unsaved changes. Do you want to save before leaving?</p>
            <div class="flex items-center justify-center gap-3">
              <button up-accept='{"action": "save"}' class="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500">Save</button>
              <button up-accept='{"action": "discard"}' class="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-500">Discard Changes</button>
            </div>
          </div>
        `,
        onAccepted: async (event) => {
          if (event.value?.action === 'save') {
            await this.savePost(true);
          } else if (event.value?.action === 'discard') {
            this._dismissConfirmed = true;
            this._ownLayer.dismiss();
          }
        },
        onDismissed: () => {
          // User dismissed the confirm modal (backdrop/escape) — do nothing
        },
      });
    },

    openPublishPanel() {
      if (!this.publishPanelUrl) return;
      up.layer.open({
        url: this.publishPanelUrl,
        mode: 'modal',
        size: 'small',
        cache: false,
        history: false,
      });
    },

    // ── Mode switching ────────────────────────────────────────────────────

    switchMode(newMode) {
      this.mode = newMode;
    },

    // ── Hidden field sync ─────────────────────────────────────────────────

    syncHiddenField(fieldId, value) {
      const el = document.getElementById(fieldId);
      if (el) el.value = value;
    },

    // ── Tab management ────────────────────────────────────────────────────

    activateTab(tab) {
      if (tab !== 'all' && !this.activePlatforms.includes(tab)) return;
      this.activeTab = tab;
    },

    // ── Platform add / remove ─────────────────────────────────────────────

    removePlatform(platform) {
      this.activePlatforms = this.activePlatforms.filter(p => p !== platform);
      if (this.activeTab === platform) {
        this.activeTab = 'all';
      }
      this.isDirty = true;
    },

    addPlatform(platform) {
      // Re-insert in the correct order according to allPlatforms
      this.activePlatforms = this.allPlatforms.filter(p =>
        this.activePlatforms.includes(p) || p === platform
      );
      this.showAddPlatformDropdown = false;
      this.activeTab = platform;
      this.isDirty = true;
    },

    // ── Preview ───────────────────────────────────────────────────────────

    previewLabel() {
      if (this.activeTab === 'all') return 'All Platforms';
      const btn = this.$el.querySelector(`.tab-btn[data-tab="${this.activeTab}"]`);
      return btn ? btn.textContent.trim() : this.activeTab;
    },

    previewText() {
      const text = this.effectiveText(this.activeTab);
      return text || 'Write something above to see a preview\u2026';
    },

    previewMedia() {
      if (this.activeTab === 'all') return this.sharedMedia;
      const platform = this.activeTab;
      if (this.overrideMediaShown[platform]) {
        return this.platformMedia[platform] || [];
      }
      return this.sharedMedia;
    },

    // Reset carousel when media change
    _resetCarousel() {
      this.carouselIndex = 0;
    },

    previewTextFormatted() {
      const text = this.effectiveText(this.activeTab);
      if (!text) return '';
      const escaped = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\n/g, '<br>');
      return escaped
        .replace(/(#\w+)/g, '<span class="text-indigo-500 font-medium">$1</span>')
        .replace(/(@\w+)/g, '<span class="text-sky-500 font-medium">$1</span>');
    },

    // ── Text helpers ──────────────────────────────────────────────────────

    effectiveText(platform) {
      if (!platform || platform === 'all') return this.sharedText;
      if (this.overrideTextShown[platform]) return this.overrideText[platform] || '';
      return this.sharedText;
    },

    sharedCharCountLabel() {
      return this.sharedText.length + ' characters';
    },

    // ── Character counting ────────────────────────────────────────────────

    charCount(platform) {
      return this.effectiveText(platform).length;
    },

    charCountLabel(platform) {
      const limit = this.PLATFORM_LIMITS[platform] || 0;
      return this.charCount(platform) + ' / ' + limit;
    },

    charBarWidth(platform) {
      const count = this.charCount(platform);
      const limit = this.PLATFORM_LIMITS[platform] || 1;
      return Math.min((count / limit) * 100, 100) + '%';
    },

    charBarClass(platform) {
      const count = this.charCount(platform);
      const limit = this.PLATFORM_LIMITS[platform] || 0;
      if (count > limit) return 'bg-red-500';
      if (count > limit * 0.8) return 'bg-amber-400';
      return 'bg-emerald-400';
    },

    isOverLimit(platform) {
      const limit = this.PLATFORM_LIMITS[platform] || 0;
      return this.charCount(platform) > limit;
    },

    overLimitBy(platform) {
      const limit = this.PLATFORM_LIMITS[platform] || 0;
      return this.charCount(platform) - limit;
    },

    // ── Override text toggle ──────────────────────────────────────────────

    onSharedTextToggle(platform, event) {
      const checked = event.target.checked;
      if (!checked && !this.overrideText[platform]) {
        // Seed override textarea with current shared text as starting point
        this.overrideText[platform] = this.sharedText;
        this.$nextTick(() => {
          const panel = this.$el.querySelector('#panel-' + platform);
          if (panel) {
            const field = panel.querySelector('.override-text-field');
            if (field) field.value = this.overrideText[platform];
          }
        });
      }
      this.overrideTextShown[platform] = !checked;
    },

    onSharedMediaToggle(platform, event) {
      const checked = event.target.checked;
      if (!checked && !(this.platformMedia[platform] && this.platformMedia[platform].length)) {
        // Seed platform media with shared media as starting point
        this.platformMedia = {
          ...this.platformMedia,
          [platform]: this.sharedMedia.map(img => ({ key: this._nextTempId(), mediaPk: img.mediaPk, url: img.url })),
        };
      }
      this.overrideMediaShown[platform] = !checked;
    },

    // ── AI: Suggest Topic ─────────────────────────────────────────────────

    async suggestTopic() {
      if (this.suggestingTopic) return;
      this.suggestingTopic = true;
      this.topicSuggestions = [];
      this.selectedBrief = null;
      try {
        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value;
        const resp = await fetch('/social-media/ai/suggest-topic/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify({
            seed_media_ids: this.seedMedia.map(i => i.mediaPk),
            media_type: this.mediaType,
            video_type: this.videoType,
          }),
        });
        const data = await resp.json();
        if (data.error) {
          console.error('Suggest topic error:', data.error);
          this.suggestingTopic = false;
        }
        // suggestingTopic stays true until SSE topic-suggestions fires
      } catch (e) {
        console.error('Failed to suggest topic:', e);
        this.suggestingTopic = false;
      }
    },

    _subscribeTopicSuggestionsSSE() {
      if (this._topicSseCleanup) this._topicSseCleanup();

      const handler = (e) => {
        if (!e.detail) return;
        this.suggestingTopic = false;
        if (e.detail.error) {
          console.error('Topic suggestion error:', e.detail.error);
          return;
        }
        if (e.detail.topics) {
          // Image mode: plain text list
          this.topicSuggestions = e.detail.topics.map(t => ({ type: 'text', value: t }));
        } else if (e.detail.briefs) {
          // Video mode: brief objects
          this.topicSuggestions = e.detail.briefs.map(b => ({ type: 'brief', value: b }));
          this.savedBriefs = e.detail.briefs;
          this.isDirty = true;
        }
      };

      document.addEventListener('topic-suggestions', handler);
      this._topicSseCleanup = () => {
        document.removeEventListener('topic-suggestions', handler);
        this._topicSseCleanup = null;
      };
    },

    selectTopic(suggestion) {
      if (suggestion.type === 'brief') {
        const brief = suggestion.value;
        this.selectedBrief = brief;
        this.syncHiddenField('id_video_brief', JSON.stringify(brief));
      } else {
        this.selectedBrief = null;
        this.topic = suggestion.value;
        this.syncHiddenField('id_topic', this.topic);
        this.syncHiddenField('id_video_brief', '');
      }
      this.topicSuggestions = [];
      this.isDirty = true;
    },

    generateButtonLabel() {
      if (this.mediaType === 'video') return 'Generate Video Post (5 credits)';
      return 'Generate Post (1 credit)';
    },

    // ── AI: Generate Post ─────────────────────────────────────────────────

    async generatePost() {
      if (this.generating) return;
      this.generating = true;
      try {
        await this.savePost(false, 'generate');
        this.generationStep = this.mediaType === 'video' ? 'Generating video script…' : 'Generating your post…';
      } catch (e) {
        this.generating = false;
        console.error('Failed to generate post:', e);
        let msg = e.message || 'Generation failed';
        if (e.status === 402 && e.data?.credits_required) {
          msg = `Not enough credits. This action requires ${e.data.credits_required} credit(s).`;
        }
        up.layer.open({
          content: `<div class="p-6 text-center"><h2 class="text-lg font-semibold text-red-700 mb-2">Insufficient Credits</h2><p class="text-sm text-zinc-600 mb-4">${msg}</p><a href="/credits/pricing/" class="inline-block rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-indigo-500">Buy Credits</a></div>`,
          mode: 'modal',
          size: 'small',
        });
      }
    },

    _subscribeGenerationSSE() {
      if (this._generationSseCleanup) this._generationSseCleanup();

      let timer = null;

      const handler = (e) => {
        if (e.detail && e.detail.post_id == this.postId) {
          cleanup();
          this._onGenerationDone(e.detail);
        }
      };

      const cleanup = () => {
        document.removeEventListener('generation-done', handler);
        if (timer) { clearTimeout(timer); timer = null; }
        this._generationSseCleanup = null;
      };

      document.addEventListener('generation-done', handler);
      this._generationSseCleanup = cleanup;

      // Safety timeout: 5 min for image, 20 min for video
      const timeoutMs = this.mediaType === 'video' ? 20 * 60 * 1000 : 5 * 60 * 1000;
      timer = setTimeout(() => {
        cleanup();
        this.generating = false;
        this.generationStep = '';
      }, timeoutMs);
    },

    _onGenerationDone(data) {
      if (data.processing_status === 'completed' && data.post_id) {
        up.navigate('#post-composer', {
          url: `/social-media/${data.post_id}/edit/`,
          layer: 'current',
          history: false,
        });
      }
    },

    closeWhileGenerating() {
      up.layer.dismiss();
    },

    // ── Textarea helpers ──────────────────────────────────────────────────

    // Return the textarea element for the given tab (defaults to activeTab).
    getTextarea(platform) {
      const p = platform !== undefined ? platform : this.activeTab;
      if (!p || p === 'all') {
        return this.$root.querySelector('#id_shared_text');
      }
      return this.$root.querySelector(`#panel-${p} .override-text-field`);
    },

    // Update textarea value + reactive state for the given tab.
    updateText(platform, text) {
      const p = platform !== undefined ? platform : this.activeTab;
      const ta = this.getTextarea(p);
      if (ta) ta.value = text;
      if (!p || p === 'all') {
        this.sharedText = text;
      } else {
        this.overrideText = { ...this.overrideText, [p]: text };
      }
    },

    // ── Temp ID helper ────────────────────────────────────────────────────

    _nextTempId() {
      return --this._tempIdCounter;
    },

    // ── Seed Image Management ─────────────────────────────────────────────

    removeSeedImage(key) {
      this.seedMedia = this.seedMedia.filter(i => i.key !== key);
      this.isDirty = true;
    },

    addEditorResultToSeeds({ mediaPk, url }) {
      if (!mediaPk || this.seedMedia.some(i => i.mediaPk === mediaPk)) return;
      if (this.seedMedia.length >= 8) return;
      this.seedMedia = [...this.seedMedia, { key: this._nextTempId(), mediaPk, url }];
      this.isDirty = true;
    },

    // ── Image Picker (Unpoly modal) ───────────────────────────────────────

    openPicker(target) {
      let currentMedia;
      if (target === 'seed') {
        currentMedia = this.seedMedia;
      } else if (target === 'shared') {
        currentMedia = this.sharedMedia;
      } else {
        currentMedia = this.platformMedia[target.replace('platform:', '')] || [];
      }
      const selectedIds = currentMedia.map(i => i.mediaPk).join(',');
      const allowVideo = target !== 'seed';
      up.layer.open({
        url: `/media-library/media-picker/?target=${encodeURIComponent(target)}&selected=${selectedIds}&allow_video=${allowVideo ? '1' : '0'}`,
        target: '#media-picker',
        mode: 'modal',
        history: false,
        size: 'large',
      });
    },

    pickerAccepted({ target, mediaIds, urls, isVideoMap }) {
      this.isDirty = true;
      if (target === 'seed') {
        // Seed media: replace fully, limit to 8
        this.seedMedia = mediaIds.slice(0, 8).map(id => ({
          key: this._nextTempId(),
          mediaPk: id,
          url: urls[id],
          is_video: (isVideoMap && isVideoMap[id]) || false,
        }));
        // Auto-suggest topic when seed media change
        if (this.seedMedia.length > 0 && !this.topic) {
          this.suggestTopic();
        }
        return;
      }

      if (target === 'shared') {
        const newShared = this.sharedMedia.filter(img => mediaIds.includes(img.mediaPk));
        const existingPks = this.sharedMedia.map(i => i.mediaPk);
        mediaIds.forEach(id => {
          if (!existingPks.includes(id)) {
            newShared.push({ key: this._nextTempId(), mediaPk: id, url: urls[id], is_video: (isVideoMap && isVideoMap[id]) || false });
          }
        });
        this.sharedMedia = newShared;
        this._resetCarousel();
      } else {
        const platform = target.replace('platform:', '');
        const existing = this.platformMedia[platform] || [];
        const existingPks = existing.map(i => i.mediaPk);
        const newList = existing.filter(img => mediaIds.includes(img.mediaPk));
        mediaIds.forEach(id => {
          if (!existingPks.includes(id)) {
            newList.push({ key: this._nextTempId(), mediaPk: id, url: urls[id], is_video: (isVideoMap && isVideoMap[id]) || false });
          }
        });
        this.platformMedia = { ...this.platformMedia, [platform]: newList };
      }
    },

    // ── Shared media removal ──────────────────────────────────────────────

    removeSharedImage(key) {
      this.sharedMedia = this.sharedMedia.filter(i => i.key !== key);
      this.carouselIndex = Math.min(this.carouselIndex, Math.max(0, this.sharedMedia.length - 1));
      this.isDirty = true;
    },

    replaceSharedImage(idx, result) {
      if (!result || !result.value || !result.value.mediaPk) return;
      const img = this.sharedMedia[idx];
      if (!img) return;
      this.sharedMedia[idx] = { ...img, mediaPk: result.value.mediaPk, url: result.value.url };
      this.sharedMedia = [...this.sharedMedia];
      this.isDirty = true;
    },

    // ── Platform media removal ────────────────────────────────────────────

    removePlatformImage(platform, key) {
      const list = this.platformMedia[platform] || [];
      const newList = list.filter(i => i.key !== key);
      this.platformMedia = {
        ...this.platformMedia,
        [platform]: newList,
      };
      this.carouselIndex = Math.min(this.carouselIndex, Math.max(0, newList.length - 1));
      this.isDirty = true;
    },

    replacePlatformImage(platform, idx, result) {
      if (!result || !result.mediaPk) return;
      const list = this.platformMedia[platform] || [];
      const img = list[idx];
      if (!img) return;
      const newList = [...list];
      newList[idx] = { ...img, mediaPk: result.mediaPk, url: result.url };
      this.platformMedia = { ...this.platformMedia, [platform]: newList };
      this.isDirty = true;
    },

    // ── Save state ────────────────────────────────────────────────────────

    saving: false,
    publishUrl: null,
    publishPanelUrl: null,

    // ── Publish validation ────────────────────────────────────────────────

    /**
     * Validate media constraints for every active platform.
     * Returns an array of human-readable error strings (empty = valid).
     */
    validateForPublish() {
      const PLATFORM_LABELS = {
        linkedin: 'LinkedIn',
        facebook: 'Facebook',
        instagram: 'Instagram',
      };

      const errors = [];
      this.$root.querySelectorAll('[id^="panel-"]:not(#panel-all)').forEach(panel => {
        const platform = panel.id.replace('panel-', '');
        if (!this.activePlatforms.includes(platform)) return;
        const label = PLATFORM_LABELS[platform] || platform;

        const media = this.overrideMediaShown[platform]
          ? (this.platformMedia[platform] || [])
          : this.sharedMedia;
        const text = this.overrideTextShown[platform]
          ? (this.overrideText[platform] || '')
          : this.sharedText;

        const videos = media.filter(m => m.is_video);
        const images = media.filter(m => !m.is_video);

        if (!text.trim() && media.length === 0) {
          errors.push(`${label}: Post must have either text or media.`);
          return;
        }

        if (videos.length > 0 && images.length > 0) {
          errors.push(`${label}: Cannot mix images and videos in the same post.`);
          return;
        }

        if (videos.length > 1) {
          errors.push(`${label}: Only one video is allowed per post.`);
        }

        if (images.length > 4) {
          errors.push(`${label}: Maximum 4 images are allowed per post.`);
        }
      });

      return errors;
    },

    // ── Drag-and-drop state ───────────────────────────────────────────────
    dragSourceIndex: null,
    dragSourceType: null,
    dragSourcePlatform: null,
    dragOverIndex: null,
    dragOverType: null,

    // Returns human-readable label for current postStatus
    statusLabel() {
      const map = {
        draft: 'Draft',
        scheduled: 'Scheduled',
        publishing: 'Publishing…',
        published: 'Published',
        failed: 'Failed',
      };
      return map[this.postStatus] || this.postStatus;
    },

    // Returns badge label including scheduled date when applicable
    statusBadgeLabel() {
      if (this.postStatus === 'scheduled' && this.scheduledAt) {
        return 'Scheduled · ' + formatScheduledAt(this.scheduledAt);
      }
      return this.statusLabel();
    },
    // ── Save post ─────────────────────────────────────────────────────────

    async savePost(closeOnSuccess = true, action = 'draft') {
      this.saving = true;
      try {
        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value;

        // Collect platform data from the DOM panels
        const platforms = [];
        this.$root.querySelectorAll('[id^="panel-"]:not(#panel-all)').forEach(panel => {
          const platform = panel.id.replace('panel-', '');
          if (!this.activePlatforms.includes(platform)) return;
          platforms.push({
            platform,
            use_shared_text: !this.overrideTextShown[platform],
            override_text: this.overrideText[platform] || '',
            use_shared_media: !this.overrideMediaShown[platform],
          });
        });

        // Build platform override media map
        const platformOverrideMedia = {};
        for (const [platform, media] of Object.entries(this.platformMedia)) {
          platformOverrideMedia[platform] = media.map(img => img.mediaPk);
        }

        const payload = {
          post_id: this.postId ? parseInt(this.postId) : null,
          title: document.getElementById('id_title')?.value || '',
          shared_text: this.sharedText,
          topic: this.topic,
          post_type: this.postType || document.getElementById('id_post_type')?.value || '',
          media_type: this.mediaType,
          video_type: this.videoType,
          video_brief: this.selectedBrief || null,
          video_suggestions: this.savedBriefs.length > 0 ? this.savedBriefs : null,
          ai_instruction: document.getElementById('id_ai_instruction')?.value || '',
          action: action,
          platforms: platforms,
          shared_media: this.sharedMedia.map(img => img.mediaPk),
          platform_override_media: platformOverrideMedia,
          seed_media: this.seedMedia.map(img => img.mediaPk),
        };

        const resp = await fetch('/social-media/save/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify(payload),
        });

        if (!resp.ok) {
          const errData = await resp.json().catch(() => ({}));
          if (errData.error) console.error('Save failed:', errData.error);
          const err = new Error(errData.error || 'Failed to save post');
          err.status = resp.status;
          err.data = errData;
          throw err;
        }

        const data = await resp.json();
        if (data.post_id) {
          this.postId = data.post_id;
          // Update dynamic URLs so publish panel works after first save
          this.publishUrl = `/social-media/${data.post_id}/publish/`;
          this.publishPanelUrl = `/social-media/${data.post_id}/publish-panel/`;
          this.unscheduleUrl = `/social-media/${data.post_id}/unschedule/`;
        }
        if (data.status) this.postStatus = data.status;
        if (data.scheduled_at !== undefined) this.scheduledAt = data.scheduled_at || '';

        if (closeOnSuccess) {
          up.layer.accept();
        }
        this.isDirty = false;
        return true;
      } finally {
        this.saving = false;
      }
    },

    // ── Drag-and-drop media reorder ───────────────────────────────────────

    isDragOver(index, type, platform = null) {
      return this.dragOverIndex === index &&
        this.dragOverType === type + ':' + (platform || '');
    },

    dragStart(e, index, type, platform = null) {
      this.dragSourceIndex = index;
      this.dragSourceType = type;
      this.dragSourcePlatform = platform;
      e.dataTransfer.effectAllowed = 'move';
    },

    dragOver(e, index, type, platform = null) {
      if (this.dragSourceType !== type || this.dragSourcePlatform !== platform) return;
      this.dragOverIndex = index;
      this.dragOverType = type + ':' + (platform || '');
    },

    drop(e, targetIndex, type, platform = null) {
      if (this.dragSourceIndex === null ||
          this.dragSourceType !== type ||
          this.dragSourcePlatform !== platform) return;
      const sourceIdx = this.dragSourceIndex;
      let arr;
      if (type === 'shared') arr = [...this.sharedMedia];
      else if (type === 'seed') arr = [...this.seedMedia];
      else if (type === 'platform') arr = [...(this.platformMedia[platform] || [])];
      else return;
      const [moved] = arr.splice(sourceIdx, 1);
      arr.splice(targetIndex, 0, moved);
      if (type === 'shared') {
        this.sharedMedia = arr;
      } else if (type === 'seed') {
        this.seedMedia = arr;
      } else if (type === 'platform') {
        this.platformMedia = { ...this.platformMedia, [platform]: arr };
      }
      this.dragEnd();
      this._resetCarousel();
    },

    dragEnd() {
      this.dragSourceIndex = null;
      this.dragSourceType = null;
      this.dragSourcePlatform = null;
      this.dragOverIndex = null;
      this.dragOverType = null;
    },

  }));

  // ── Post List ────────────────────────────────────────────────────────────

  Alpine.data('postList', () => ({

    init() {
      this._handler = (e) => {
        const postId = e.detail?.post_id;
        if (!postId) return;
        // When a modal is open, up.reload() defaults to looking for the target
        // in the current (modal) layer. The card lives on root, so it's not found
        // and the insertion is silently skipped (.post-list works because it has
        // up-hungry which bypasses layer isolation). asCurrent() makes root the
        // current layer so Unpoly finds the card element where it actually lives.
        up.layer.root.asCurrent(() => {
          const cardEl = document.getElementById(`post-card-${postId}`);
          if (cardEl) {
            up.reload(cardEl);
          } else {
            up.reload('.post-list');
          }
        });
      };
      document.addEventListener('post-changed', this._handler);
    },

    destroy() {
      document.removeEventListener('post-changed', this._handler);
    },

  }));

  // ── Publish Panel ────────────────────────────────────────────────────────

  Alpine.data('publishPanel', () => ({

    // ── State ───────────────────────────────────────────────────────────
    view: 'options',       // 'options' | 'publishing' | 'results'
    postStatus: '',
    scheduledAt: '',
    scheduleError: '',
    validationErrors: [],
    scheduling: false,
    unscheduling: false,
    publishing: false,

    postId: null,
    publishUrl: null,
    scheduleUrl: null,
    unscheduleUrl: null,
    saveScheduledAtUrl: null,
    panelUrl: null,
    _sseCleanup: null,
    _saveDebounceTimer: null,
    _lastSavedScheduledAt: '',
    _savingScheduledAt: false,

    // ── Lifecycle ────────────────────────────────────────────────────────
    init() {
      this.postId        = this.$el.dataset.postId;
      this.postStatus    = this.$el.dataset.postStatus || '';
      this._projectTimezone = this.$el.dataset.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
      // data-scheduled-at is already in project timezone (YYYY-MM-DDTHH:MM),
      // ready for the datetime-local input.
      this.scheduledAt = this.$el.dataset.scheduledAt || '';
      this.publishUrl          = this.$el.dataset.publishUrl;
      this.scheduleUrl         = this.$el.dataset.scheduleUrl;
      this.unscheduleUrl       = this.$el.dataset.unscheduleUrl;
      this.saveScheduledAtUrl  = this.$el.dataset.saveScheduledAtUrl;
      this.panelUrl            = this.$el.dataset.panelUrl;
      this._lastSavedScheduledAt = this.scheduledAt;
      this._ownLayer           = up.layer.current;

      // Watch scheduledAt and debounce-save to database
      this.$watch('scheduledAt', (val) => {
        if (val === this._lastSavedScheduledAt) return;
        clearTimeout(this._saveDebounceTimer);
        this._saveDebounceTimer = setTimeout(() => this._autoSaveScheduledAt(val), 600);
      });

      // Determine initial view from current post status
      if (this.postStatus === 'published' || this.postStatus === 'failed') {
        this.view = 'results';
      } else if (this.postStatus === 'publishing') {
        this.view = 'publishing';
        this._subscribeSSE();
      } else {
        this.view = 'options';
      }
    },

    // ── Status label ─────────────────────────────────────────────────────
    statusLabel() {
      const map = {
        draft: 'Draft',
        scheduled: 'Scheduled',
        publishing: 'Publishing…',
        published: 'Published',
        failed: 'Failed',
      };
      return map[this.postStatus] || this.postStatus;
    },

    // ── Datetime helpers ──────────────────────────────────────────────────
    minDatetime() {
      const now = new Date();
      now.setMinutes(now.getMinutes() + 1);
      return toTimezoneISOString(now, this._projectTimezone);
    },

    formatScheduledAt(iso) {
      return formatScheduledAt(iso);
    },

    // ── Pre-publish validation via parent composer ─────────────────────────
    async _validateViaComposer() {
      try {
        const parentEl = this._ownLayer?.parent?.element;
        if (!parentEl) return [];
        const composerEl = parentEl.querySelector('[x-data]');
        if (!composerEl) return [];
        const composer = Alpine.$data(composerEl);
        if (composer && typeof composer.validateForPublish === 'function') {
          return composer.validateForPublish();
        }
      } catch (e) {
        console.warn('publishPanel: could not validate via composer', e);
      }
      return [];
    },

    // ── Save parent post ───────────────────────────────────────────────────
    async _saveParentPost() {
      // The publish panel opens as a child layer over the post composer.
      // Walk up the layer stack to find the postComposer Alpine component and save.
      try {
        const parentEl = this._ownLayer?.parent?.element;
        if (!parentEl) return;
        const composerEl = parentEl.querySelector('[x-data]');
        if (!composerEl) return;
        const composer = Alpine.$data(composerEl);
        if (composer && typeof composer.savePost === 'function') {
          await composer.savePost(false, 'draft');
        }
      } catch (e) {
        console.warn('publishPanel: could not save parent post', e);
      }
    },

    // ── Schedule ─────────────────────────────────────────────────────────
    async schedulePost() {
      this.scheduleError = '';
      this.validationErrors = [];
      if (!this.scheduledAt) {
        this.scheduleError = 'Please enter a date and time.';
        return;
      }
      const dt = new Date(this.scheduledAt);
      if (isNaN(dt.getTime())) {
        this.scheduleError = 'Invalid date format.';
        return;
      }

      const validationErrs = await this._validateViaComposer();
      if (validationErrs.length > 0) {
        this.validationErrors = validationErrs;
        return;
      }

      this.scheduling = true;
      await this._saveParentPost();
      const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
        || document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
      try {
        // scheduledAt is a datetime-local string representing project timezone;
        // send as-is so the server interprets it in the project timezone.
        const body = new URLSearchParams({ scheduled_at: this.scheduledAt });
        const resp = await fetch(this.scheduleUrl, {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken, 'Content-Type': 'application/x-www-form-urlencoded' },
          body,
        });
        const data = await resp.json();
        if (!resp.ok) {
          if (data.validation_errors && data.validation_errors.length) {
            this.validationErrors = data.validation_errors;
          } else {
            this.scheduleError = data.error || 'Failed to schedule.';
          }
          return;
        }
        this.postStatus = data.status;
        up.layer.dismiss();
      } catch (e) {
        this.scheduleError = 'Failed to schedule. Please try again.';
      } finally {
        this.scheduling = false;
      }
    },

    // ── Unschedule ────────────────────────────────────────────────────────
    async unschedulePost() {
      this.unscheduling = true;
      const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
        || document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
      try {
        const resp = await fetch(this.unscheduleUrl, {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken },
        });
        const data = await resp.json();
        this.postStatus = data.status;
        up.layer.dismiss();
      } catch (e) {
        console.error('Failed to unschedule:', e);
      } finally {
        this.unscheduling = false;
      }
    },

    // ── Publish Now ───────────────────────────────────────────────────────
    async publishNow() {
      if (this.publishing || !this.publishUrl) return;
      this.validationErrors = [];

      const validationErrs = await this._validateViaComposer();
      if (validationErrs.length > 0) {
        this.validationErrors = validationErrs;
        return;
      }

      this.publishing = true;
      this.view = 'publishing';
      await this._saveParentPost();
      const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
        || document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
      try {
        const resp = await fetch(this.publishUrl, {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken },
        });
        const data = await resp.json();
        if (!resp.ok) {
          if (data.validation_errors && data.validation_errors.length) {
            this.validationErrors = data.validation_errors;
          }
          this.publishing = false;
          this.view = 'options';
        } else if (data.queued && this.postId) {
          this.postStatus = 'publishing';
          this._subscribeSSE();
        } else {
          this.publishing = false;
          this.view = 'options';
        }
      } catch (e) {
        console.error('Failed to publish:', e);
        this.publishing = false;
        this.view = 'options';
      }
    },

    _subscribeSSE() {
      if (!this.postId) return;
      const postId = this.postId;
      let timer = null;

      const handler = (e) => {
        if (e.detail && e.detail.post_id == postId) {
          cleanup();
          this._onPublishDone(e.detail);
        }
      };

      const cleanup = () => {
        document.removeEventListener('publish-done', handler);
        if (timer) { clearTimeout(timer); timer = null; }
        this._sseCleanup = null;
      };

      this._sseCleanup = cleanup;
      document.addEventListener('publish-done', handler);

      timer = setTimeout(() => {
        cleanup();
        this.publishing = false;
        this.view = 'options';
      }, 6 * 60 * 1000);
    },

    destroy() {
      if (this._sseCleanup) this._sseCleanup();
      clearTimeout(this._saveDebounceTimer);
    },

    // ── Auto-save scheduledAt ─────────────────────────────────────────────
    async _autoSaveScheduledAt(val) {
      if (!this.saveScheduledAtUrl || this._savingScheduledAt) return;
      this._savingScheduledAt = true;
      const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
        || document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
      try {
        const body = new URLSearchParams();
        if (val) {
          // val is a datetime-local string representing project timezone time;
          // send it as-is and let the server interpret it as project timezone (naive fallback).
          body.set('scheduled_at', val);
        }
        const resp = await fetch(this.saveScheduledAtUrl, {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken, 'Content-Type': 'application/x-www-form-urlencoded' },
          body,
        });
        if (resp.ok) {
          this._lastSavedScheduledAt = val;
        }
      } catch (e) {
        console.warn('publishPanel: could not auto-save scheduled_at', e);
      } finally {
        this._savingScheduledAt = false;
      }
    },

    _onPublishDone(data) {
      this.publishing = false;
      this.postStatus = data.status;
      // Reload the panel via Unpoly so the server-rendered version (with links) is shown.
      if (this.panelUrl) {
        up.navigate({ url: this.panelUrl, layer: this._ownLayer, history: false });
      } else {
        this.view = 'results';
      }
    },

  }));

});

// ── Shared formatting helpers ─────────────────────────────────────────────────

/**
 * Convert a Date object to a local-time ISO string suitable for datetime-local inputs.
 * Returns "YYYY-MM-DDTHH:mm" in the browser's local timezone (no TZ suffix).
 */
function toLocalISOString(date) {
  const pad = n => String(n).padStart(2, '0');
  return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
    + 'T' + pad(date.getHours()) + ':' + pad(date.getMinutes());
}

/**
 * Convert a Date object to an ISO string in a specific IANA timezone,
 * suitable for datetime-local inputs. Returns "YYYY-MM-DDTHH:mm".
 */
function toTimezoneISOString(date, timeZone) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: timeZone,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(date);
  const get = type => (parts.find(p => p.type === type) || {}).value || '';
  return get('year') + '-' + get('month') + '-' + get('day') + 'T' + get('hour') + ':' + get('minute');
}

function formatScheduledAt(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  });
}

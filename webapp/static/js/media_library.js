/* ── Compiled by Unpoly so it runs every time the fragment is inserted ── */

/* ── Product Import Modal — Alpine.js component for the import overlay ──
 *
 * When importing=true, listens for media_library:import_completed (accepts
 * the layer) or media_library:import_error (shows inline error).
 *
 * Usage in template:
 *   <div x-data="productImportModal({ importing: true })">
 */
function productImportModal({ importing = false } = {}) {
  return {
    importing,
    error: '',

    init() {
      if (!this.importing) return;
      this._bindEvents();
    },

    _bindEvents() {
      this._onCompleted = () => {
        this._cleanup();
        try {
          if (up.layer.count > 1) up.layer.accept();
        } catch (e) {}
      };

      this._onError = (e) => {
        this._cleanup();
        this.importing = false;
        this.error = (e.detail && e.detail.error) || 'Import failed.';
      };

      document.addEventListener('media_library:import_completed', this._onCompleted);
      document.addEventListener('media_library:import_error', this._onError);
    },

    _cleanup() {
      document.removeEventListener('media_library:import_completed', this._onCompleted);
      document.removeEventListener('media_library:import_error', this._onError);
    },

    destroy() {
      this._cleanup();
    },
  };
}

up.compiler('#formset-container', function (container) {
  'use strict';

  var form = container.closest('form');
  var addBtn = form.querySelector('#add-media');
  var emptyTemplate = form.querySelector('#empty-form');

  function getTotalFormsInput() {
    return form.querySelector('[name$="-TOTAL_FORMS"]');
  }

  function getTotalForms() {
    return parseInt(getTotalFormsInput().value, 10);
  }

  function setTotalForms(val) {
    getTotalFormsInput().value = val;
  }

  /* ── Preview a media file from a file input ── */
  function previewMedia(input) {
    var row = input.closest('.media-row');
    if (!row) return;
    var container = row.querySelector('.media-preview');
    if (!input.files || !input.files[0]) return;

    var file = input.files[0];
    var reader = new FileReader();
    reader.onload = function (e) {
      if (container) {
        container.innerHTML = '';
        var previewEl = document.createElement('preview-media');
        previewEl.setAttribute('src', e.target.result);
        previewEl.setAttribute('alt', file.name);
        previewEl.setAttribute('class', 'w-full h-full object-cover');
        if (file.type.startsWith('video/')) {
          previewEl.setAttribute('data-is-video', 'true');
        }
        container.appendChild(previewEl);
      }
    };
    reader.readAsDataURL(file);
  }

  /* ── Add a new media row ── */
  if (addBtn && emptyTemplate) {
    addBtn.addEventListener('click', function () {
      var idx = getTotalForms();
      var clone = emptyTemplate.content.cloneNode(true);

      clone.querySelectorAll('[name], [id], [for]').forEach(function (el) {
        ['name', 'id', 'for'].forEach(function (attr) {
          var val = el.getAttribute(attr);
          if (val) {
            el.setAttribute(attr, val.replace(/__prefix__/g, idx));
          }
        });
      });

      container.appendChild(clone);
      setTotalForms(idx + 1);

      var newRow = container.lastElementChild;
      var fileInput = newRow.querySelector('input[type="file"]');
      if (fileInput) {
        var cancelHandler = function () {
          setTimeout(function () {
            if (!fileInput.files || fileInput.files.length === 0) {
              newRow.remove();
              setTotalForms(getTotalForms() - 1);
            }
          }, 300);
        };
        fileInput.addEventListener('change', function () {
          window.removeEventListener('focus', cancelHandler);
          previewMedia(fileInput);
        }, { once: true });
        window.addEventListener('focus', cancelHandler, { once: true });
        fileInput.click();
      }
    });
  }

  /* ── Event delegation on the container ── */
  container.addEventListener('change', function (e) {
    if (e.target.type === 'file') {
      previewMedia(e.target);
    }
  });

  container.addEventListener('click', function (e) {
    var removeBtn = e.target.closest('.remove-row');
    if (removeBtn) {
      var row = removeBtn.closest('.media-row');
      if (row) row.remove();
    }
  });

  /* ── Style delete-toggle for existing media ── */
  container.addEventListener('change', function (e) {
    var checkbox = e.target;
    if (checkbox.type !== 'checkbox') return;
    var label = checkbox.closest('.delete-toggle');
    if (!label) return;
    var row = checkbox.closest('.media-row');
    if (!row) return;

    if (checkbox.checked) {
      row.classList.add('opacity-40');
      label.classList.remove('btn-outline');
      label.querySelector('.delete-label').textContent = '↺';
    } else {
      row.classList.remove('opacity-40');
      label.classList.add('btn-outline');
      label.querySelector('.delete-label').textContent = '✕';
    }
  });

  /* ── Hide the raw checkbox inside delete-toggle labels ── */
  container.querySelectorAll('.delete-toggle input[type="checkbox"]').forEach(function (cb) {
    cb.style.position = 'absolute';
    cb.style.opacity = '0';
    cb.style.width = '0';
    cb.style.height = '0';
  });

  /* ── Paste media from clipboard ── */
  function addPastedMediaRow(file) {
    if (!emptyTemplate) return;
    var idx = getTotalForms();
    var clone = emptyTemplate.content.cloneNode(true);

    clone.querySelectorAll('[name], [id], [for]').forEach(function (el) {
      ['name', 'id', 'for'].forEach(function (attr) {
        var val = el.getAttribute(attr);
        if (val) el.setAttribute(attr, val.replace(/__prefix__/g, idx));
      });
    });

    container.appendChild(clone);
    setTotalForms(idx + 1);

    var newRow = container.lastElementChild;
    var fileInput = newRow.querySelector('input[type="file"]');
    if (fileInput) {
      var dt = new DataTransfer();
      dt.items.add(file);
      fileInput.files = dt.files;
      previewMedia(fileInput);
    }
  }

  function handlePaste(e) {
    if (!container.isConnected) {
      document.removeEventListener('paste', handlePaste);
      return;
    }
    var items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    for (var i = 0; i < items.length; i++) {
      if (items[i].type.startsWith('image/') || items[i].type.startsWith('video/')) {
        var file = items[i].getAsFile();
        if (file) addPastedMediaRow(file);
      }
    }
  }

  document.addEventListener('paste', handlePaste);

});

up.compiler('#generated-media-container', function (container) {
  /* ── Hide the raw checkboxes inside delete-toggle labels ── */
  container.querySelectorAll('.delete-toggle input[type="checkbox"]').forEach(function (cb) {
    cb.style.position = 'absolute';
    cb.style.opacity = '0';
    cb.style.width = '0';
    cb.style.height = '0';
  });

  /* ── Style delete-toggle on change ── */
  container.addEventListener('change', function (e) {
    var checkbox = e.target;
    if (checkbox.type !== 'checkbox') return;
    var label = checkbox.closest('.delete-toggle');
    if (!label) return;
    var row = checkbox.closest('.media-row');
    if (!row) return;
    if (checkbox.checked) {
      row.classList.add('opacity-40');
      label.querySelector('.delete-label').textContent = '↺';
    } else {
      row.classList.remove('opacity-40');
      label.querySelector('.delete-label').textContent = '✕';
    }
  });
});

/* ── Social Media Post Composer ── */

document.addEventListener('alpine:init', () => {
  'use strict';

  // ── Image Picker (loaded as Unpoly modal fragment) ─────────────────────

  Alpine.data('mediaPicker', () => ({
    groups: [],
    currentGroupId: null,
    selected: {},        // {media: true/false}
    selectedOrder: [],   // insertion-order list of selected IDs (for FIFO)
    target: '',
    maxMedia: 0,
    allowVideo: true,
    search: '',
    typeFilter: 'all',
    _refreshUrl: '',
    _createUrl: '',
    _editUrlBase: '',

    init() {
      const groupsEl = document.getElementById('picker-groups-data');
      if (groupsEl) {
        this.groups = JSON.parse(groupsEl.textContent);
        // No group is focused by default
      }

      const selectedEl = document.getElementById('picker-selected-ids');
      if (selectedEl) {
        const ids = JSON.parse(selectedEl.textContent);
        ids.forEach(id => {
          this.selected[id] = true;
          this.selectedOrder.push(id);
        });
      }

      const targetEl = document.getElementById('picker-target');
      if (targetEl) this.target = targetEl.value;

      const maxEl = document.getElementById('picker-max-media');
      if (maxEl && maxEl.value) this.maxMedia = parseInt(maxEl.value, 10) || 0;

      const allowVideoEl = document.getElementById('picker-allow-video');
      if (allowVideoEl) this.allowVideo = allowVideoEl.value !== '0';

      // Read URL config from data attributes on the root element
      const root = document.getElementById('media-picker');
      if (root) {
        this._refreshUrl = root.dataset.refreshUrl || '';
        this._createUrl = root.dataset.createUrl || '';
        this._editUrlBase = root.dataset.editUrlBase || '';
      }
    },

    filteredGroups() {
      const q = this.search.trim().toLowerCase();
      return this.groups.filter(g => {
        if (this.typeFilter !== 'all' && g.type !== this.typeFilter) return false;
        if (!q) return true;
        return g.title.toLowerCase().includes(q) || (g.description || '').toLowerCase().includes(q);
      });
    },

    _allowedMedia(media) {
      return this.allowVideo ? media : media.filter(img => !img.is_video);
    },

    groupAllowedMedia(group) {
      return this._allowedMedia(group.media);
    },

    currentMedia() {
      if (!this.currentGroupId) return [];
      const group = this.groups.find(g => g.id === this.currentGroupId);
      return group ? this._allowedMedia(group.media) : [];
    },

    currentGroupTitle() {
      const group = this.groups.find(g => g.id === this.currentGroupId);
      return group ? group.title : '';
    },

    selectGroup(group) {
      this.currentGroupId = group.id;
    },

    isSelected(media) {
      return !!this.selected[media];
    },

    groupHasSelected(group) {
      return this._allowedMedia(group.media).some(img => !!this.selected[img.id]);
    },

    selectedCount() {
      return this.selectedOrder.length;
    },

    atMax() {
      return this.maxMedia > 0 && this.selectedOrder.length >= this.maxMedia;
    },

    _addToSelection(media) {
      const newSelected = { ...this.selected };
      const newOrder = [...this.selectedOrder];
      if (this.maxMedia > 0 && newOrder.length >= this.maxMedia) {
        // FIFO: evict the oldest selection
        const oldest = newOrder.shift();
        if (oldest !== undefined) newSelected[oldest] = false;
      }
      newSelected[media] = true;
      newOrder.push(media);
      this.selected = newSelected;
      this.selectedOrder = newOrder;
    },

    toggle(media) {
      if (this.selected[media]) {
        // Deselect
        this.selected = { ...this.selected, [media]: false };
        this.selectedOrder = this.selectedOrder.filter(id => id !== media);
      } else {
        this._addToSelection(media);
      }
    },

    allSelectedMedia() {
      const idToImg = {};
      this.groups.forEach(g => g.media.forEach(img => {
        idToImg[img.id] = img;
      }));
      return this.selectedOrder.map(id => idToImg[id]).filter(Boolean);
    },

    sidebarMedia() {
      return this.currentMedia();
    },

    editGroup(groupId) {
      const url = this._editUrlBase.replace('/0/', '/' + groupId + '/');
      up.layer.open({ url, onAccepted: () => this.refreshGroups() });
    },

    createGroup() {
      up.layer.open({ url: this._createUrl, onAccepted: () => this.refreshGroups() });
    },

    refreshGroups() {
      const url = this._refreshUrl + '?format=json';
      fetch(url)
        .then(r => r.json())
        .then(data => { this.groups = data.groups; });
    },

    confirm() {
      const urlMap = {};
      const groupMap = {};
      const isVideoMap = {};
      this.groups.forEach(g => g.media.forEach(img => {
        urlMap[img.id] = img.url;
        groupMap[img.id] = g.id;
        isVideoMap[img.id] = img.is_video || false;
      }));
      const mediaIds = this.selectedOrder.map(id => parseInt(id, 10));
      up.layer.accept({ target: this.target, mediaIds, urls: urlMap, groupMap, isVideoMap });
    },

    cancel() {
      up.layer.dismiss();
    },
  }));

  // ── Catalog (products + media library combined view) ─────────────────

  Alpine.data('catalog', () => ({
    search: '',

    visible(el) {
      if (!this.search) return true;
      const q = this.search.toLowerCase();
      return (el.dataset.title || '').includes(q) || (el.dataset.desc || '').includes(q);
    },
  }));
});

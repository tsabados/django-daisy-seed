function schedulerApp() {
    return {
        calendar: null,
        platformFilter: '',
        statusFilter: '',
        postFilter: '',

        init() {
            const calendarEl = document.getElementById('scheduler-calendar');
            if (!calendarEl) return;

            const eventsUrl = calendarEl.dataset.eventsUrl;
            const projectTimezone = calendarEl.dataset.timezone || 'local';
            const postFilter = calendarEl.dataset.postFilter || '';
            const postDate = calendarEl.dataset.postDate || '';
            this.postFilter = postFilter;
            const self = this;

            this.calendar = new FullCalendar.Calendar(calendarEl, {
                timeZone: projectTimezone,
                initialView: 'dayGridMonth',
                headerToolbar: {
                    left: 'prev,next today',
                    center: 'title',
                    right: 'dayGridMonth,timeGridWeek,timeGridDay',
                },
                buttonText: {
                    today: 'Today',
                    month: 'Month',
                    week: 'Week',
                    day: 'Day',
                },
                nowIndicator: true,
                editable: true,
                eventStartEditable: true,
                eventDurationEditable: false,
                dayMaxEvents: 3,
                moreLinkClick: 'day',
                height: 'auto',

                events: function(info, successCallback, failureCallback) {
                    const params = new URLSearchParams({
                        start: info.startStr,
                        end: info.endStr,
                    });
                    if (self.platformFilter) params.set('platform', self.platformFilter);
                    if (self.statusFilter) params.set('status', self.statusFilter);
                    if (self.postFilter) params.set('post', self.postFilter);

                    fetch(eventsUrl + '?' + params.toString())
                        .then(r => r.json())
                        .then(data => successCallback(data))
                        .catch(err => failureCallback(err));
                },

                eventContent: function(arg) {
                    const props = arg.event.extendedProps;
                    const timeText = arg.timeText;
                    const title = arg.event.title;

                    const platformIcons = {
                        linkedin: '<svg xmlns="http://www.w3.org/2000/svg" class="scheduler-platform-icon" style="color:#2563eb" viewBox="0 0 24 24" fill="currentColor"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>',
                        x: '<svg xmlns="http://www.w3.org/2000/svg" class="scheduler-platform-icon" style="color:#3f3f46" viewBox="0 0 24 24" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.744l7.737-8.835L1.254 2.25H8.08l4.253 5.622 5.911-5.622zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>',
                        facebook: '<svg xmlns="http://www.w3.org/2000/svg" class="scheduler-platform-icon" style="color:#1d4ed8" viewBox="0 0 24 24" fill="currentColor"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>',
                        instagram: '<svg xmlns="http://www.w3.org/2000/svg" class="scheduler-platform-icon" style="color:#db2777" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"/></svg>',
                    };

                    function buildStatusBadge(status, processingStatus) {
                        if (processingStatus === 'generating') {
                            return '<span class="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2.5 py-0.5 text-xs font-medium text-indigo-700 animate-pulse">'
                                + '<svg class="animate-spin h-3 w-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">'
                                + '<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>'
                                + '<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>'
                                + '</svg>Generating</span>';
                        }
                        var badgeStyles = {
                            draft:      'bg-zinc-100 text-zinc-600',
                            scheduled:  'bg-amber-100 text-amber-700',
                            publishing: 'bg-amber-100 text-amber-700 animate-pulse',
                            published:  'bg-emerald-100 text-emerald-700',
                            failed:     'bg-red-100 text-red-700',
                        };
                        var badgeLabels = {
                            draft:      'Draft',
                            scheduled:  'Scheduled',
                            publishing: 'Publishing\u2026',
                            published:  'Published',
                            failed:     'Failed',
                        };
                        var bc = badgeStyles[status] || 'bg-zinc-100 text-zinc-600';
                        var bl = badgeLabels[status] || self.escapeHtml(status);
                        return '<span class="inline-flex items-center rounded-full ' + bc + ' px-2.5 py-0.5 text-xs font-medium">' + bl + '</span>';
                    }

                    let html = '<div class="scheduler-event-card" data-status="' + self.escapeHtml(props.status) + '">';

                    // Header: status badge + time
                    html += '<div class="scheduler-event-header">';
                    html += buildStatusBadge(props.status, props.processingStatus);
                    if (timeText) {
                        html += '<span class="scheduler-event-time">' + self.escapeHtml(timeText) + '</span>';
                    }
                    html += '</div>';

                    // Full-width media
                    if (props.thumbnail) {
                        html += '<div class="scheduler-event-thumb">';
                        if (props.isVideo) {
                            html += '<video src="' + self.escapeHtml(props.thumbnail) + '" muted preload="metadata" playsinline class="scheduler-event-video"></video>';
                        } else {
                            html += '<img src="' + self.escapeHtml(props.thumbnail) + '" alt="">';
                        }
                        html += '</div>';
                    }

                    // Body: caption
                    html += '<div class="scheduler-event-body">';

                    if (props.caption) {
                        html += '<div class="scheduler-event-caption">' + self.escapeHtml(props.caption) + '</div>';
                    }

                    if (props.platforms && props.platforms.length) {
                        html += '<div class="scheduler-event-platforms">';
                        props.platforms.forEach(function(p) {
                            var icon = platformIcons[p] || '<span class="scheduler-platform-icon">' + self.escapeHtml(p) + '</span>';
                            html += icon;
                        });
                        html += '</div>';
                    }

                    html += '</div></div>';

                    return { html: html };
                },

                eventClick: function(info) {
                    info.jsEvent.preventDefault();
                    var editUrl = info.event.extendedProps.editUrl;
                    up.layer.open({
                        url: editUrl,
                        mode: 'modal',
                        size: 'large',
                        history: false,
                        dismissable: false,
                        onAccepted: function() {
                            self.calendar.refetchEvents();
                        },
                    });
                },

                eventDrop: function(info) {
                    var newStart = info.event.start;
                    var now = new Date();

                    if (newStart < now) {
                        info.revert();
                        self.showToast('Cannot schedule in the past', 'error');
                        return;
                    }

                    var csrfToken = self.getCookie('csrftoken');

                    fetch('/scheduler/api/reschedule/' + info.event.id + '/', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': csrfToken,
                        },
                        body: JSON.stringify({
                            scheduled_at: newStart.toISOString(),
                        }),
                    })
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        if (data.error) {
                            info.revert();
                            self.showToast(data.error, 'error');
                        } else {
                            self.showToast('Post rescheduled', 'success');
                        }
                    })
                    .catch(function() {
                        info.revert();
                        self.showToast('Failed to reschedule', 'error');
                    });
                },

                eventDidMount: function(info) {
                    info.el.style.backgroundColor = 'transparent';
                    info.el.style.borderColor = 'transparent';
                },
            });

            this.calendar.render();

            if (postFilter && postDate) {
                this.calendar.gotoDate(postDate);
            }

            // Listen for SSE post-changed events and surgically update the
            // matching calendar event without triggering a full refetch.
            this._postChangedHandler = function(e) {
                var detail = e.detail || {};
                var postId = detail.post_id;
                if (!postId) return;

                var fcEvent = self.calendar.getEventById(postId);
                if (!fcEvent) return; // event not in the current view range — ignore

                fetch('/scheduler/api/event/' + postId + '/')
                    .then(function(r) {
                        if (!r.ok) {
                            // Post may have been unscheduled; remove it from the calendar
                            if (r.status === 404) fcEvent.remove();
                            return null;
                        }
                        return r.json();
                    })
                    .then(function(data) {
                        if (!data) return;
                        // Update start time (handles reschedules)
                        fcEvent.setStart(data.start);
                        // Update all extendedProps so eventContent re-renders correctly
                        var ep = data.extendedProps || {};
                        Object.keys(ep).forEach(function(key) {
                            fcEvent.setExtendedProp(key, ep[key]);
                        });
                    })
                    .catch(function() {}); // best-effort; ignore transient errors
            };
            document.addEventListener('post-changed', this._postChangedHandler);

            // Listen for generation-done SSE events to instantly reflect
            // processingStatus changes without waiting for a full re-fetch.
            this._generationDoneHandler = function(e) {
                var detail = e.detail || {};
                var postId = detail.post_id;
                if (!postId) return;
                var fcEvent = self.calendar.getEventById(postId);
                if (!fcEvent) return;
                fcEvent.setExtendedProp('processingStatus', detail.processing_status || 'idle');
            };
            document.addEventListener('generation-done', this._generationDoneHandler);

            // Clean up on Unpoly layer dismissal / navigation
            document.addEventListener('up:location:changed', this._removePostChangedHandler.bind(this), { once: true });
        },

        _removePostChangedHandler() {
            if (this._postChangedHandler) {
                document.removeEventListener('post-changed', this._postChangedHandler);
                this._postChangedHandler = null;
            }
            if (this._generationDoneHandler) {
                document.removeEventListener('generation-done', this._generationDoneHandler);
                this._generationDoneHandler = null;
            }
        },

        refetch() {
            if (this.calendar) {
                this.calendar.refetchEvents();
            }
        },

        escapeHtml(str) {
            var div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        },

        getCookie(name) {
            var value = '; ' + document.cookie;
            var parts = value.split('; ' + name + '=');
            if (parts.length === 2) return parts.pop().split(';').shift();
            return '';
        },

        showToast(message, type) {
            var toast = document.createElement('div');
            toast.className = 'fixed bottom-4 right-4 z-50 rounded-xl border px-4 py-3 text-sm shadow-lg transition-all duration-300 '
                + (type === 'error'
                    ? 'border-red-200 bg-red-50 text-red-800'
                    : 'border-emerald-200 bg-emerald-50 text-emerald-800');
            toast.textContent = message;
            document.body.appendChild(toast);
            setTimeout(function() {
                toast.style.opacity = '0';
                setTimeout(function() { toast.remove(); }, 300);
            }, 3000);
        },
    };
}

if (window.__USER_ID__) {
  const channel = 'user-' + window.__USER_ID__;

  function _connectSSE() {
    if (window.SSE) {
      window.SSE.close();
    }
    window.SSE = new ReconnectingEventSource('/events/?channel=' + channel);

    window.SSE.onopen = function () {
      console.debug('[SSE] connected ' + channel);
    };

    window.SSE.onerror = function (e) {
      console.debug('[SSE] error ' + channel, e);
    };

    // All server-sent events use the default 'message' type with a 'type'
    // field in the JSON payload. A single listener here bridges them all to
    // document CustomEvents — no list to maintain, every event is handled.
    window.SSE.addEventListener('message', function (e) {
      var detail = JSON.parse(e.data || '{}');
      if (!detail.type) return;
      console.debug('[SSE] received', detail.type, detail);
      document.dispatchEvent(new CustomEvent(detail.type, { detail: detail }));
    });
  }

  _connectSSE();

  // Permanent health-check loop: every 10 s verify the connection is OPEN (readyState 1).
  // If not, recreate the EventSource entirely so it starts retrying fresh.
  setInterval(function () {
    if (window.SSE && window.SSE.readyState !== 1) {
      console.debug('[SSE] health-check reconnect ' + channel + ' (readyState=' + window.SSE.readyState + ')');
      _connectSSE();
    }
  }, 10000);

  // Also reconnect immediately when the user returns to the tab.
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && window.SSE && window.SSE.readyState !== 1) {
      console.debug('[SSE] tab visible, reconnecting ' + channel);
      _connectSSE();
    }
  });
}
// Lightweight polling for the host waiting-room lobby: keeps the
// participant count/bar live without a full page reload, and reloads the
// page once the room leaves a live state (host started/ended/closed it
// from another tab, or it expired).
(function () {
  const root = document.getElementById("roomRoot");
  if (!root) return;
  const code = root.getAttribute("data-code");

  function poll() {
    fetch(`/room/${encodeURIComponent(code)}/status`)
      .then((r) => r.json())
      .then((d) => {
        const countEl = document.getElementById("rCount");
        if (countEl) countEl.textContent = d.participant_count;
        const bar = document.getElementById("rBar");
        if (bar && d.participant_limit) {
          bar.style.width = Math.min(100, (d.participant_count / d.participant_limit) * 100) + "%";
        }
        const statusPill = document.getElementById("rStatus");
        if (statusPill && (root.dataset.lastStatus === undefined || root.dataset.lastStatus === d.status)) {
          statusPill.textContent = d.participant_count ? "Participants joining" : "Waiting for participants";
          statusPill.className = "pill " + (d.participant_count ? "ok" : "warn");
        }
        if (root.dataset.lastStatus && root.dataset.lastStatus !== d.status) {
          window.location.reload();
          return;
        }
        root.dataset.lastStatus = d.status;
      })
      .catch(() => {});
  }

  root.dataset.lastStatus = "";
  poll();
  setInterval(poll, 5000);
})();

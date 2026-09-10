// Drives the /game/level/<n> play screen using the RAMCO HACK "simulation"
// visual (nurse bubble, patient report panel, lettered option cards).
//
// Flow: a brand-new attempt first walks through the scenario's briefing
// (window.MEDSIM_INTRO — the hospital/nurse intro lines from the scenario
// JSON's intro_sequence) one phase at a time with a "Continue" button, then
// arms the real server-side deadline (/start-timer) and only then shows the
// first decision. A resumed attempt (no intro) arms/reads the same deadline
// immediately. The countdown shown here is only a display of that deadline
// — the server is the authority, and re-checks it on every /decide call.
(function () {
  const shell = document.getElementById("game-shell");
  if (!shell) return;

  const level = window.MEDSIM_LEVEL;
  const OPT_KEYS = ["A", "B", "C", "D", "E", "F"];
  let decisionStartedAt = Date.now();
  let submitting = false;
  let timerHandle = null;
  let expiredHandled = false;

  const speakerNameEl = document.getElementById("speakerName");
  const scenarioTextEl = document.getElementById("scenario-text");
  const qLabelEl = document.getElementById("qLabel");
  const optionsList = document.getElementById("scenario-options");

  // Status words the scenario JSON uses (CRITICAL, DETERIORATING, AT_RISK,
  // STABILIZED, ...) mapped to the severity band's colour class.
  function severityClass(status) {
    const s = (status || "").toUpperCase();
    if (s.includes("CRITICAL") || s.includes("ARREST") || s.includes("DETERIORAT")) return "";
    if (s.includes("STABLE") || s.includes("STABILIZED")) return "stable";
    return "guarded";
  }

  function renderPatients(patients, focusId) {
    const hud = document.getElementById("patient-hud");
    hud.innerHTML = "";
    if (!patients) return;
    Object.entries(patients).forEach(([pid, patient]) => {
      const wrap = document.createElement("div");

      const pInfo = document.createElement("div");
      pInfo.className = "repsec";
      pInfo.innerHTML = `<h4>Patient</h4>
        <div class="row"><span class="k">Name</span><span class="v">${patient.name || pid}</span></div>
        ${patient.age != null ? `<div class="row"><span class="k">Age</span><span class="v">${patient.age}</span></div>` : ""}`;
      wrap.appendChild(pInfo);

      const vitalsEntries = Object.entries(patient.vitals || {});
      if (vitalsEntries.length) {
        const vSec = document.createElement("div");
        vSec.className = "repsec";
        vSec.innerHTML = "<h4>Vital signs</h4>" + vitalsEntries
          .map(([key, value]) => `<div class="row"><span class="k">${key}</span><span class="v">${value}</span></div>`)
          .join("");
        wrap.appendChild(vSec);
      }

      const sev = document.createElement("div");
      sev.className = "sev " + severityClass(patient.status);
      sev.innerHTML = `<span>${pid} status</span><span>${patient.status || "—"}</span>`;
      wrap.appendChild(sev);

      hud.appendChild(wrap);
    });
  }

  function stopTimer() {
    if (timerHandle) clearInterval(timerHandle);
    timerHandle = null;
  }

  async function handleExpiry() {
    if (expiredHandled) return;
    expiredHandled = true;
    stopTimer();
    document.querySelectorAll("#scenario-options .opt").forEach((o) => (o.disabled = true));
    try {
      const res = await fetch(`/game/level/${level}/expire`, { method: "POST" });
      const result = await res.json();
      if (res.ok && result.redirect) {
        window.location.href = result.redirect;
        return;
      }
    } catch (e) {
      // fall through — the next /decide call will catch the expiry server-side anyway
    }
    expiredHandled = false;
  }

  function startTimer(totalSeconds) {
    stopTimer();
    const box = document.getElementById("timerBox");
    const val = document.getElementById("timerVal");
    let remaining = totalSeconds;

    function tick() {
      const m = Math.floor(Math.max(remaining, 0) / 60);
      const s = Math.max(remaining, 0) % 60;
      val.textContent = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
      box.classList.remove("warn", "crit");
      if (remaining <= 15) box.classList.add("crit");
      else if (remaining <= 30) box.classList.add("warn");
      if (remaining <= 0) {
        stopTimer();
        handleExpiry();
        return;
      }
      remaining -= 1;
    }
    tick();
    timerHandle = setInterval(tick, 1000);
  }

  function setBubbleLines(lines) {
    const log = document.getElementById("nurse-log");
    log.innerHTML = "";
    (lines || []).forEach((line) => {
      const p = document.createElement("p");
      p.className = "line fade";
      p.style.minHeight = "0";
      p.textContent = line;
      log.appendChild(p);
    });
  }

  function appendBubbleLines(lines) {
    const log = document.getElementById("nurse-log");
    (lines || []).forEach((line) => {
      const p = document.createElement("p");
      p.className = "line fade";
      p.style.minHeight = "0";
      p.style.color = "var(--cyan-soft)";
      p.textContent = line;
      log.appendChild(p);
    });
  }

  // ---------------- Briefing (intro_sequence) ----------------
  const qband = document.getElementById("qband");
  const bubbleActions = document.getElementById("bubble-actions");

  function renderBriefingPhase(phase, isLast) {
    speakerNameEl.textContent = phase.speaker;
    scenarioTextEl.textContent = "Briefing";
    qband.hidden = true;
    setBubbleLines(phase.lines);

    bubbleActions.innerHTML = "";
    const btn = document.createElement("button");
    btn.className = "btn btn-primary";
    btn.type = "button";
    btn.textContent = isLast ? "Begin Shift" : "Continue";
    bubbleActions.appendChild(btn);
    return btn;
  }

  function runBriefing(phases, onDone) {
    let i = 0;
    function showNext() {
      if (i >= phases.length) {
        onDone();
        return;
      }
      const isLast = i === phases.length - 1;
      const btn = renderBriefingPhase(phases[i], isLast);
      i += 1;
      btn.addEventListener("click", showNext, { once: true });
    }
    showNext();
  }

  async function armTimerAndBegin() {
    bubbleActions.innerHTML = "";
    if (qLabelEl) qLabelEl.textContent = "Your decision";
    let remaining = window.MEDSIM_REMAINING_SECONDS;
    try {
      const res = await fetch(`/game/level/${level}/start-timer`, { method: "POST" });
      const result = await res.json();
      if (res.ok) remaining = result.remaining_seconds;
    } catch (e) {
      // fall back to whatever the page was rendered with
    }
    renderDecision(window.MEDSIM_INITIAL_DECISION);
    startTimer(Math.max(remaining || 0, 0));
  }

  // ---------------- Decisions ----------------
  function renderDecision(decision) {
    decisionStartedAt = Date.now();
    submitting = false;
    speakerNameEl.textContent = "NURSE MAYA";
    scenarioTextEl.textContent = "Live";
    qband.hidden = false;
    bubbleActions.innerHTML = "";
    renderPatients(decision.patients, decision.patient_id);
    setBubbleLines(decision.nurse_dialogue);

    optionsList.innerHTML = "";
    (decision.options || []).forEach((option, i) => {
      const btn = document.createElement("button");
      btn.className = "opt";
      btn.type = "button";
      btn.innerHTML = `<span class="key">${OPT_KEYS[i] || i + 1}</span><span>${option.text}</span>`;
      btn.addEventListener("click", () => submitDecision(option.id, btn));
      optionsList.appendChild(btn);
    });
  }

  async function submitDecision(optionId, btn) {
    if (submitting) return;
    submitting = true;
    document.querySelectorAll("#scenario-options .opt").forEach((o) => (o.disabled = true));
    if (btn) btn.classList.add("picked");

    const timeTakenSeconds = (Date.now() - decisionStartedAt) / 1000;
    const res = await fetch(`/game/level/${level}/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ option: optionId, time_taken_seconds: timeTakenSeconds }),
    });
    const result = await res.json();
    if (!res.ok) {
      alert(result.error || "Something went wrong.");
      submitting = false;
      document.querySelectorAll("#scenario-options .opt").forEach((o) => (o.disabled = false));
      return;
    }

    if (result.expired) {
      stopTimer();
      window.location.href = result.redirect;
      return;
    }

    if (btn && result.quality === "critical_error") btn.classList.add("wrong");

    // nurse_response is a list of follow-up lines from GameEngine.make_decision()
    if (result.nurse_response && result.nurse_response.length) {
      appendBubbleLines(result.nurse_response);
    }

    if (result.game_finished) {
      setTimeout(finishLevel, 900);
    } else if (result.next) {
      setTimeout(() => renderDecision(result.next), 900);
    }
  }

  async function finishLevel() {
    stopTimer();
    const res = await fetch(`/game/level/${level}/finish`, { method: "POST" });
    const result = await res.json();
    if (res.ok && result.redirect) {
      window.location.href = result.redirect;
    } else {
      alert(result.error || "Could not save this attempt.");
    }
  }

  function startBriefingFlow() {
    const introPhases = window.MEDSIM_INTRO || [];
    if (introPhases.length) {
      runBriefing(introPhases, armTimerAndBegin);
    } else if (window.MEDSIM_INITIAL_DECISION) {
      armTimerAndBegin();
    }
  }

  // ---------------- Hospital intro video ----------------
  // Two independent "seen it once" buckets, tracked per browser
  // (localStorage): one for Level 1 (any context), one for a learner's
  // first-ever assessment attempt (any level). Either can make the video
  // eligible; watching/skipping it marks every bucket that applied on
  // this page load so it never replays for either reason again.
  const LS_LEVEL1_SEEN = "emergiq_intro_video_seen_level1";
  const LS_ASSESSMENT_SEEN = "emergiq_intro_video_seen_assessment";

  function lsGet(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function lsSet(key) {
    try { window.localStorage.setItem(key, "1"); } catch (e) { /* ignore */ }
  }

  function introVideoNeeded() {
    if (!window.MEDSIM_SHOW_INTRO_VIDEO) return false;
    const needsLevel1 = window.MEDSIM_LEVEL === 1 && !lsGet(LS_LEVEL1_SEEN);
    const needsAssessment = window.MEDSIM_IS_ASSESSMENT && !lsGet(LS_ASSESSMENT_SEEN);
    return needsLevel1 || needsAssessment;
  }

  function markIntroVideoSeen() {
    if (window.MEDSIM_LEVEL === 1) lsSet(LS_LEVEL1_SEEN);
    if (window.MEDSIM_IS_ASSESSMENT) lsSet(LS_ASSESSMENT_SEEN);
  }

  function playIntroVideo(onDone) {
    const overlay = document.getElementById("introVideoOverlay");
    const video = document.getElementById("introVideo");
    const skipBtn = document.getElementById("introVideoSkip");
    const countEl = document.getElementById("introSkipCount");
    if (!overlay || !video || !skipBtn) {
      onDone();
      return;
    }

    let done = false;
    function finish() {
      if (done) return;
      done = true;
      clearInterval(countdown);
      markIntroVideoSeen();
      video.pause();
      overlay.hidden = true;
      onDone();
    }

    overlay.hidden = false;
    skipBtn.disabled = true;
    let secondsLeft = 3;
    if (countEl) countEl.textContent = String(secondsLeft);
    const countdown = setInterval(() => {
      secondsLeft -= 1;
      if (secondsLeft <= 0) {
        clearInterval(countdown);
        skipBtn.disabled = false;
        skipBtn.textContent = "Skip";
      } else if (countEl) {
        countEl.textContent = String(secondsLeft);
      }
    }, 1000);

    skipBtn.addEventListener("click", finish, { once: true });
    video.addEventListener("ended", finish, { once: true });
    // Don't let a failed video load block the learner from the level.
    video.addEventListener("error", finish, { once: true });

    video.play().catch(() => {
      // Autoplay may be blocked — the skip button still enables on its own
      // timer, and the <video controls> element lets them press play.
    });
  }

  if (introVideoNeeded()) {
    playIntroVideo(startBriefingFlow);
  } else {
    startBriefingFlow();
  }
})();

const EDITOR_BASE = "/editor";

async function postJson(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json();
  if (!res.ok) {
    const msg = data.error || "Request failed";
    throw new Error(msg);
  }
  return data;
}

function showStatus(el, text, type = "info") {
  if (!el) return;
  el.textContent = text;
  el.className = `status ${type}`;
}

function initIndexPage() {}

function initRecordingPage() {
  const root = document.getElementById("recording-page");
  if (!root) return;

  const recordingId = root.dataset.recordingId;
  const versionId = root.dataset.versionId;

  const versionSelect = document.getElementById("version-select");
  if (versionSelect) {
    versionSelect.addEventListener("change", () => {
      const v = versionSelect.value;
      window.location.href = `${EDITOR_BASE}/recording/${recordingId}?version_id=${v}`;
    });
  }

  function getSelectedRows() {
    return Array.from(document.querySelectorAll(".split-checkbox:checked"))
      .map((cb) => cb.closest("tr"))
      .filter(Boolean);
  }

  function getSelectedSpeakers(rows) {
    const speakers = rows
      .map((row) => row.querySelector(".speaker-select")?.value)
      .filter(Boolean);
    return Array.from(new Set(speakers));
  }

  function getAllSpeakers() {
    const speakers = Array.from(document.querySelectorAll(".speaker-select"))
      .map((sel) => sel.value)
      .filter(Boolean);
    return Array.from(new Set(speakers));
  }

  function ensureSpeakerOption(label) {
    const trimmed = (label || "").trim();
    if (!trimmed) return;
    const existing = Array.from(document.querySelectorAll(".speaker-select option"))
      .some((opt) => opt.value === trimmed);
    if (existing) return;
    document.querySelectorAll(".speaker-select").forEach((select) => {
      const opt = document.createElement("option");
      opt.value = trimmed;
      opt.textContent = trimmed;
      select.appendChild(opt);
    });
  }

  const actionStatus = document.getElementById("action-status");
  if (actionStatus) {
    showStatus(actionStatus, "Actions ready", "info");
  }

  const audioPlayer = document.getElementById("audio-player");

  function bindPlayButton(btn) {
    btn.addEventListener("click", () => {
      const row = btn.closest("tr");
      if (!row || !audioPlayer) return;
      const startMs = Number(row.dataset.startMs || 0);
      const endMs = Number(row.dataset.endMs || 0);
      const startSec = Math.max(0, startMs / 1000);
      const endSec = endMs > 0 ? endMs / 1000 : null;
      audioPlayer.currentTime = startSec;
      audioPlayer.play();
      if (endSec) {
        const onTimeUpdate = () => {
          if (audioPlayer.currentTime >= endSec) {
            audioPlayer.pause();
            audioPlayer.removeEventListener("timeupdate", onTimeUpdate);
          }
        };
        audioPlayer.addEventListener("timeupdate", onTimeUpdate);
      }
    });
  }

  function buildRowFromExisting(row) {
    const clone = row.cloneNode(true);
    const checkbox = clone.querySelector(".split-checkbox");
    if (checkbox) checkbox.checked = false;
    return clone;
  }

  function generateLocalId() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) {
      return `new-${crypto.randomUUID()}`;
    }
    return `new-${Date.now()}-${Math.floor(Math.random() * 100000)}`;
  }

  function setRowData(row, data) {
    if (data.id) {
      row.dataset.utteranceId = data.id;
    }
    if (typeof data.start_ms === "number") {
      row.dataset.startMs = String(Math.round(data.start_ms));
      row.querySelectorAll("td")[5].textContent = String(Math.round(data.start_ms));
    }
    if (typeof data.end_ms === "number") {
      row.dataset.endMs = String(Math.round(data.end_ms));
      row.querySelectorAll("td")[6].textContent = String(Math.round(data.end_ms));
    }
    if (data.speaker) {
      ensureSpeakerOption(data.speaker);
      const select = row.querySelector(".speaker-select");
      if (select) select.value = data.speaker;
    }
    if (data.text !== undefined) {
      const input = row.querySelector(".text-input");
      if (input) input.value = data.text;
    }
    if (data.confidence !== undefined) {
      row.querySelectorAll("td")[4].textContent = `${data.confidence}%`;
    }
  }

  function bindSplitButton(btn) {
    btn.addEventListener("click", () => {
      const row = btn.closest("tr");
      if (!row) return;
      const input = row.querySelector(".text-input");
      const fullText = input ? input.value : "";
      const snippet = prompt("Text to move to a new speaker (exact substring):");
      if (!snippet) return;
      const idx = fullText.indexOf(snippet);
      if (idx == -1) {
        alert("That text was not found in this utterance.");
        return;
      }
      const newLabel = prompt("New speaker label for the moved text:");
      if (!newLabel) return;

      const before = fullText.slice(0, idx).trim();
      const mid = snippet.trim();
      const after = fullText.slice(idx + snippet.length).trim();

      if (!before && !after) {
        alert("Use Rename Speaker if the whole utterance should change speakers.");
        return;
      }

      const startMs = Number(row.dataset.startMs || 0);
      const endMs = Number(row.dataset.endMs || 0);
      const duration = Math.max(0, endMs - startMs);
      const totalLen = Math.max(1, before.length + mid.length + after.length);
      const beforeDur = Math.round(duration * (before.length / totalLen));
      const midDur = Math.round(duration * (mid.length / totalLen));
      const beforeEnd = startMs + beforeDur;
      const midEnd = beforeEnd + midDur;

      const originalSpeaker = row.querySelector(".speaker-select")?.value || "";
      ensureSpeakerOption(newLabel);

      const tableBody = row.parentElement;

      if (before) {
        setRowData(row, {
          start_ms: startMs,
          end_ms: beforeEnd,
          speaker: originalSpeaker,
          text: before,
        });
        const midRow = buildRowFromExisting(row);
        setRowData(midRow, {
          id: generateLocalId(),
          start_ms: beforeEnd,
          end_ms: midEnd,
          speaker: newLabel.trim(),
          text: mid,
          confidence: 100,
        });
        tableBody.insertBefore(midRow, row.nextSibling);
        bindPlayButton(midRow.querySelector(".play-btn"));
        bindSplitButton(midRow.querySelector(".split-row-btn"));

        if (after) {
          const afterRow = buildRowFromExisting(row);
          setRowData(afterRow, {
            id: generateLocalId(),
            start_ms: midEnd,
            end_ms: endMs,
            speaker: originalSpeaker,
            text: after,
          });
          tableBody.insertBefore(afterRow, midRow.nextSibling);
          bindPlayButton(afterRow.querySelector(".play-btn"));
          bindSplitButton(afterRow.querySelector(".split-row-btn"));
        }
      } else {
        const midRow = buildRowFromExisting(row);
        setRowData(midRow, {
          id: generateLocalId(),
          start_ms: startMs,
          end_ms: midEnd,
          speaker: newLabel.trim(),
          text: mid,
          confidence: 100,
        });
        tableBody.insertBefore(midRow, row);
        bindPlayButton(midRow.querySelector(".play-btn"));
        bindSplitButton(midRow.querySelector(".split-row-btn"));

        if (after) {
          setRowData(row, {
            start_ms: midEnd,
            end_ms: endMs,
            speaker: originalSpeaker,
            text: after,
          });
        }
      }

      if (actionStatus) {
        showStatus(actionStatus, "Split created. Click Save to keep it.", "success");
      }
    });
  }

  if (audioPlayer) {
    document.querySelectorAll(".play-btn").forEach(bindPlayButton);
  }
  document.querySelectorAll(".split-row-btn").forEach(bindSplitButton);

  const processBtn = document.getElementById("process-btn");
  if (processBtn) {
    processBtn.addEventListener("click", async () => {
      processBtn.disabled = true;
      processBtn.textContent = "Processing...";
      try {
        await postJson(`${EDITOR_BASE}/process/${recordingId}`);
        window.location.href = `${EDITOR_BASE}/recording/${recordingId}`;
      } catch (err) {
        alert(err.message);
        processBtn.disabled = false;
        processBtn.textContent = "Process";
      }
    });
  }

  if (!versionId || versionId === "None" || versionId === "null") {
    return;
  }

  const restoreBtn = document.getElementById("restore-btn");
  if (restoreBtn) {
    restoreBtn.addEventListener("click", async () => {
      try {
        await postJson(`${EDITOR_BASE}/version/${versionId}/restore`);
        window.location.href = `${EDITOR_BASE}/recording/${recordingId}`;
      } catch (err) {
        alert(err.message);
      }
    });
  }

  const recomputeBtn = document.getElementById("recompute-btn");
  if (recomputeBtn) {
    recomputeBtn.addEventListener("click", async () => {
      if (!confirm("Recompute labels? Manual edits will be preserved.")) return;
      try {
        await postJson(`${EDITOR_BASE}/version/${versionId}/recompute`, { preserve_manual: true });
        window.location.href = `${EDITOR_BASE}/recording/${recordingId}`;
      } catch (err) {
        alert(err.message);
      }
    });
  }

  document.getElementById("rename-btn")?.addEventListener("click", async () => {
    const selectedRows = getSelectedRows();
    const selectedSpeakers = getSelectedSpeakers(selectedRows);
    const allSpeakers = getAllSpeakers();
    const listHint = allSpeakers.length ? ` (${allSpeakers.join(", ")})` : "";
    const defaultOld = selectedSpeakers.length === 1 ? selectedSpeakers[0] : (allSpeakers[0] || "");
    const oldLabel = prompt(`Old speaker label${listHint}:`, defaultOld);
    const newLabel = prompt("New speaker label:");
    if (!oldLabel || !newLabel) return;
    try {
      showStatus(actionStatus, "Renaming speaker...", "info");
      await postJson(`${EDITOR_BASE}/version/${versionId}/rename_speaker`, { old_label: oldLabel, new_label: newLabel });
      showStatus(actionStatus, "Rename complete", "success");
      window.location.href = `${EDITOR_BASE}/recording/${recordingId}`;
    } catch (err) {
      showStatus(actionStatus, err.message, "error");
      alert(err.message);
    }
  });

  document.getElementById("save-btn")?.addEventListener("click", async () => {
    const utterances = [];
    document.querySelectorAll("#utterance-table tbody tr").forEach((row) => {
      const id = row.dataset.utteranceId;
      const speaker = row.querySelector(".speaker-select").value;
      const text = row.querySelector(".text-input").value;
      const start_ms = Number(row.dataset.startMs || 0);
      const end_ms = Number(row.dataset.endMs || 0);
      const is_new = id && id.startsWith("new-");
      utterances.push({ id, speaker, text, start_ms, end_ms, is_new });
    });

    try {
      await postJson(`${EDITOR_BASE}/version/${versionId}/bulk_edit`, { utterances });
      window.location.href = `${EDITOR_BASE}/recording/${recordingId}`;
    } catch (err) {
      alert(err.message);
    }
  });
}

initIndexPage();
initRecordingPage();

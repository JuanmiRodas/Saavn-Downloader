(() => {
  const state = {
    currentPlan: null,
    selectedPositions: new Set(),
    trackFilter: "all",
    animes: [],
    currentAnimeSlug: "one-piece",
    currentGallery: null,
    galleryFilter: "all",
    currentTodoMarkdown: "",
    currentTodoDownloadUrl: "",
    activeJobId: null,
    logSeq: 0,
    logEntries: [],
    logFilter: "all",
    logPath: "",
    logTimer: null,
  };

  const $ = (id) => document.getElementById(id);

  function showToast(msg) {
    const el = $("toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => el.classList.add("hidden"), 3800);
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function apiJson(url, opts = {}) {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  // ======================== LIVE ACTIVITY LOG ========================
  function logMatchesFilter(entry) {
    const f = state.logFilter;
    if (f === "all") return true;
    const tag = (entry.tag || "").toUpperCase();
    const lvl = (entry.level || "").toUpperCase();
    if (f === "saavn") return tag === "SAAVN" || tag === "MATCH" || tag === "DOWNLOAD";
    if (f === "discovery") {
      return ["PLAYLIST", "WIKI", "CROSSCHECK", "ITUNES", "SCAN", "AUTOPILOT", "FOLDER"].includes(tag);
    }
    if (f === "issues") return lvl === "WARN" || lvl === "ERROR" || tag === "ERROR";
    return true;
  }

  function logTagClass(tag) {
    const t = (tag || "").toLowerCase();
    if (["match", "saavn", "playlist", "wiki", "crosscheck", "itunes", "folder", "autopilot", "error"].includes(t)) {
      return `log-tag-${t}`;
    }
    return "log-tag-default";
  }

  function renderLogs() {
    const box = $("activity-log-body");
    if (!box) return;
    const wasAtBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 36;
    const visible = state.logEntries.filter(logMatchesFilter);
    $("log-count-badge").textContent = `${state.logEntries.length} line${state.logEntries.length === 1 ? "" : "s"}`;
    if (state.logPath) {
      $("log-file-path").textContent = state.logPath;
      $("log-file-path").title = state.logPath;
    }

    if (visible.length === 0) {
      box.innerHTML = `<div class="log-empty">${
        state.logEntries.length === 0
          ? 'Ready. Click "▶ Scan &amp; Download This Anime" or "⚡ Run All 83 Animes" to inspect live playlist discovery, wiki crosschecks, and exact JioSaavn API candidates here.'
          : "No log lines match the current filter."
      }</div>`;
      return;
    }

    box.innerHTML = visible
      .map((e) => {
        const lvlCls = e.level === "ERROR" ? "lvl-error" : e.level === "WARN" ? "lvl-warn" : "";
        return `<div class="log-line ${lvlCls}"><span class="log-ts">${escapeHtml(e.ts)}</span><span class="log-tag ${logTagClass(e.tag)}">[${escapeHtml(e.tag)}]</span><span class="log-msg">${escapeHtml(e.message)}</span></div>`;
      })
      .join("");

    if (wasAtBottom) {
      box.scrollTop = box.scrollHeight;
    }
  }

  async function fetchLogsOnce() {
    try {
      const data = await apiJson(`/api/logs?since=${state.logSeq}`);
      if (data.log_path) state.logPath = data.log_path;
      const incoming = data.entries || [];
      if (incoming.length > 0) {
        state.logEntries.push(...incoming);
        if (state.logEntries.length > 2000) {
          state.logEntries = state.logEntries.slice(-2000);
        }
        state.logSeq = data.seq || state.logSeq;
        renderLogs();
      } else if (data.log_path && !$("log-file-path").textContent) {
        renderLogs();
      }
    } catch (_) {}
  }

  function startLogStreaming() {
    $("log-live-dot")?.classList.add("live");
    fetchLogsOnce();
    if (!state.logTimer) {
      state.logTimer = setInterval(fetchLogsOnce, 900);
    }
  }

  function stopLogStreaming() {
    $("log-live-dot")?.classList.remove("live");
    if (state.logTimer) {
      clearInterval(state.logTimer);
      state.logTimer = null;
    }
    fetchLogsOnce();
  }

  $("log-filters")?.querySelectorAll(".pill").forEach((p) => {
    p.addEventListener("click", () => {
      $("log-filters").querySelectorAll(".pill").forEach((x) => x.classList.remove("active"));
      p.classList.add("active");
      state.logFilter = p.dataset.lfilter;
      renderLogs();
    });
  });

  $("btn-copy-log")?.addEventListener("click", async () => {
    const text = state.logEntries
      .map((e) => `[${e.ts}] [${e.level}] [${e.tag}] ${e.message}`)
      .join("\n");
    if (!text) {
      showToast("Activity log is empty.");
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      showToast(`Copied ${state.logEntries.length} log line(s) to clipboard`);
    } catch (err) {
      showToast(`Copy failed: ${err.message}`);
    }
  });

  $("btn-open-log")?.addEventListener("click", async () => {
    try {
      const data = await apiJson("/api/open-log", { method: "POST", body: "{}" });
      showToast(`Revealed ${data.opened}`);
    } catch (err) {
      showToast(`Could not open log file: ${err.message}`);
    }
  });

  $("btn-clear-log")?.addEventListener("click", async () => {
    try {
      await apiJson("/api/logs/clear", { method: "POST", body: "{}" });
      state.logEntries = [];
      renderLogs();
      showToast("Cleared in-app log view (studio.log kept on disk)");
    } catch (err) {
      showToast(`Clear failed: ${err.message}`);
    }
  });

  async function pollJob(jobId, onProgress) {
    startLogStreaming();
    try {
      while (true) {
        const job = await apiJson(`/api/jobs/${jobId}`);
        if (onProgress) onProgress(job.message || "Working...", job.progress || 0);
        if (job.status === "completed") return job.result;
        if (job.status === "error") throw new Error(job.error || "Background job failed");
        await new Promise((r) => setTimeout(r, 450));
      }
    } finally {
      stopLogStreaming();
    }
  }

  async function openFolderInFinder(folderPath) {
    try {
      const data = await apiJson("/api/open-folder", {
        method: "POST",
        body: JSON.stringify({ path: folderPath || $("gal-out-folder").value.trim() }),
      });
      showToast(`Opened ${data.opened}`);
    } catch (err) {
      showToast(`Could not open folder: ${err.message}`);
    }
  }

  // Tabs (and dock the Live Activity Log into the active tab)
  document.querySelectorAll(".nav-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".nav-tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".workspace").forEach((w) => w.classList.remove("active"));
      btn.classList.add("active");
      const view = btn.dataset.view;
      $(`view-${view}`).classList.add("active");
      const logPanel = $("activity-log-panel");
      const targetMount = view === "playlist" ? $("log-mount-playlist") : $("log-mount-gallery");
      if (logPanel && targetMount) targetMount.appendChild(logPanel);
      if (view === "gallery") {
        loadAnimesList(false);
      }
    });
  });

  // Keep output folder & proxy synced between both tabs
  $("gal-out-folder").addEventListener("input", () => {
    $("out-folder").value = $("gal-out-folder").value;
  });
  $("out-folder").addEventListener("input", () => {
    $("gal-out-folder").value = $("out-folder").value;
  });
  $("gal-proxy-url").addEventListener("input", () => {
    $("proxy-url").value = $("gal-proxy-url").value;
  });
  $("proxy-url").addEventListener("input", () => {
    $("gal-proxy-url").value = $("proxy-url").value;
  });

  // ======================== VIEW 1: 83-ANIME AUTOPILOT GALLERY ========================
  async function loadAnimesList(openSelected = true) {
    const data = await apiJson("/api/animes");
    state.animes = data.animes || [];
    $("anime-count-badge").textContent = String(state.animes.length);
    renderAnimeSidebar();
    if (openSelected && state.currentAnimeSlug) {
      await selectAnime(state.currentAnimeSlug);
    }
  }

  function renderAnimeSidebar() {
    const q = ($("anime-search").value || "").toLowerCase().trim();
    const filtered = state.animes.filter(
      (a) =>
        !q ||
        a.name.toLowerCase().includes(q) ||
        (a.jp_name || "").toLowerCase().includes(q) ||
        (a.aliases || []).some((al) => al.toLowerCase().includes(q))
    );
    $("anime-list").innerHTML = filtered
      .map((a) => {
        const active = a.slug === state.currentAnimeSlug ? "active" : "";
        const okCount = (a.downloaded || 0) + (a.ready_saavn || 0) + (a.owned_cd || 0);
        const pills = a.scanned
          ? `<span class="badge badge-ok" title="Downloaded / Ready / Owned">${okCount}</span><span class="badge badge-warn" title="CD To-Do">${a.todo_cd}</span>`
          : `<span class="badge badge-neutral">Pending</span>`;
        return `
          <button class="anime-item ${active}" type="button" data-slug="${escapeHtml(a.slug)}">
            <div>
              <span class="anime-item-name">${escapeHtml(a.name)}</span>
              <span class="anime-item-jp">${escapeHtml(a.jp_name)}</span>
            </div>
            <div class="anime-item-pills">${pills}</div>
          </button>
        `;
      })
      .join("");

    $("anime-list").querySelectorAll(".anime-item").forEach((btn) => {
      btn.addEventListener("click", () => selectAnime(btn.dataset.slug));
    });
  }

  $("anime-search").addEventListener("input", renderAnimeSidebar);

  async function selectAnime(slug) {
    state.currentAnimeSlug = slug;
    renderAnimeSidebar();
    const gallery = await apiJson(`/api/animes/${slug}`);
    state.currentGallery = gallery;
    renderGalleryStage();
  }

  function setAutopilotBusy(busy, jobId = null) {
    state.activeJobId = busy ? jobId : null;
    $("btn-run-all").disabled = busy;
    $("btn-run-anime").disabled = busy;
    $("btn-download-anime").disabled = busy;
    if (busy && jobId) {
      $("btn-stop-job").classList.remove("hidden");
      $("gallery-progress").classList.remove("hidden");
    } else {
      $("btn-stop-job").classList.add("hidden");
    }
  }

  function renderGalleryStage() {
    const gal = state.currentGallery;
    if (!gal) return;
    const f = gal.franchise;
    $("gal-title").textContent = f.name;
    $("gal-jp").textContent = f.jp_name;
    $("gal-notes").textContent = `${f.notes} · Official Japanese originals only (soundtracks, covers & remixes rejected)`;

    const rootDir = $("gal-out-folder").value.trim() || ".";
    const folderPath = gal.folder_path || `${rootDir}/${f.name}`;
    $("gal-folder-path").textContent = `Folder: ${folderPath}/`;

    const entries = gal.entries || [];
    const availableCount = entries.filter((e) => e.saavn_match && !e.flags?.length).length;
    $("btn-download-anime").disabled = availableCount === 0 || Boolean(state.activeJobId);
    $("btn-download-anime").textContent = availableCount > 0 ? `Download Available (${availableCount})` : "No JioSaavn Matches";

    const filtered = entries.filter((e) => {
      if (state.galleryFilter === "all") return true;
      if (["OP", "ED", "Movie"].includes(state.galleryFilter)) return e.category === state.galleryFilter;
      if (state.galleryFilter === "Character") return ["Character", "Insert", "Special"].includes(e.category);
      return e.status === state.galleryFilter;
    });

    $("gal-count-note").textContent = `${filtered.length} of ${entries.length} singles / themes`;

    const grid = $("gallery-grid");
    if (entries.length === 0) {
      grid.innerHTML = `
        <div class="empty-state">
          <p><strong>${escapeHtml(f.name)}</strong> hasn't been processed yet.</p>
          <p class="muted">Click <strong>"▶ Scan &amp; Download This Anime"</strong> (or <strong>"⚡ Run All 83 Animes"</strong> above) to find all official openings, endings, movie &amp; character singles, verify original artists on JioSaavn, download all available tracks with 3000px covers into <code>${escapeHtml(folderPath)}/</code>, and flag unavailable songs to your CD To-Do list.</p>
        </div>
      `;
      return;
    }

    if (filtered.length === 0) {
      grid.innerHTML = `<div class="empty-state">No entries match the current filter.</div>`;
      return;
    }

    const maxTracks = Math.max(1, parseInt($("gal-max-tracks").value || "6", 10));

    grid.innerHTML = filtered
      .map((e) => {
        const statusMap = {
          downloaded: ["badge-ok", "Downloaded ✓"],
          ready_saavn: ["badge-ok", "Official on JioSaavn"],
          todo_cd: ["badge-warn", "CD To-Do"],
          flagged_cover: ["badge-flag", "Cover Rejected · CD To-Do"],
          owned_cd: ["badge-ok", "Ripped from CD ✓"],
        };
        const [stCls, stLbl] = statusMap[e.status] || ["badge-neutral", e.status];

        const artHtml = e.cover_url
          ? `<img class="card-art-img" src="${escapeHtml(e.cover_url)}" alt="${escapeHtml(e.title)}" loading="lazy" />
             ${e.cover_resolution ? `<span class="card-res-badge">${escapeHtml(e.cover_resolution)} ${escapeHtml(e.cover_source || "")}</span>` : ""}`
          : `<div class="card-art-sleeve">
               <span class="sleeve-cat">${escapeHtml(f.name)} · ${escapeHtml(e.label)}</span>
               <div class="sleeve-title">${escapeHtml(e.title)}</div>
               <div class="sleeve-artist">${escapeHtml(e.artist)}</div>
             </div>`;

        const romSubtitle =
          e.romanized_title && e.romanized_title.toLowerCase() !== (e.title || "").toLowerCase()
            ? `<div class="card-meta">${escapeHtml(e.romanized_title)}${e.romanized_artist ? ` · ${escapeHtml(e.romanized_artist)}` : ""}</div>`
            : "";

        const tCount = e.single_track_count;
        const willExpand = tCount && tCount > 1 && tCount <= maxTracks;
        const singleBadge = tCount
          ? `<span class="badge ${willExpand ? "badge-accent" : "badge-neutral"}">${willExpand ? `Full Single (${tCount}t)` : `${tCount}t (Song Only)`}</span>`
          : "";

        let saavnBox = "";
        if (e.status === "downloaded" && (e.downloaded_files || []).length > 0) {
          saavnBox = `<div class="card-saavn-note ok">✓ Saved ${e.downloaded_files.length} file(s) from JioSaavn (verified official artist)</div>`;
        } else if (e.saavn_match) {
          const sm = e.saavn_match;
          saavnBox = `<div class="card-saavn-note ok">✓ JioSaavn: <strong>${escapeHtml(sm.title)}</strong> — ${escapeHtml((sm.artists || []).join(", "))}${sm.is_320kbps ? " · 320kbps" : ""}</div>`;
        } else {
          saavnBox = `<div class="card-saavn-note warn">${escapeHtml(e.saavn_note || "Not available as an official original on JioSaavn → Added to CD To-Do")}</div>`;
        }

        const singleTracks = e.single_tracks || [];
        const tracksDrawer = singleTracks.length
          ? `<details class="card-single-tracks">
               <summary>Single tracklist (${singleTracks.length} tracks)</summary>
               <ol>${singleTracks
                 .map((st) => {
                   const tag = st.saavn_status === "downloaded" ? " ✓" : st.saavn_status === "todo_cd" ? " (CD)" : "";
                   return `<li>${escapeHtml(st.title)}${tag}</li>`;
                 })
                 .join("")}</ol>
             </details>`
          : "";

        const dlBtnHtml =
          e.saavn_match && !(e.flags || []).length
            ? `<button class="btn btn-primary btn-sm dl-entry-btn" type="button" data-entry="${escapeHtml(e.entry_id)}">${e.status === "downloaded" ? "Re-Download" : "Download"}</button>`
            : "";

        const saavnLink =
          e.saavn_match && e.saavn_match.perma_url
            ? `<a class="link-pill" href="${escapeHtml(e.saavn_match.perma_url)}" target="_blank" rel="noopener">JioSaavn</a>`
            : "";

        return `
          <article class="album-card">
            <div class="card-art-box">
              ${artHtml}
              <div class="card-top-badges">
                <span class="badge badge-neutral" style="background:rgba(255,255,255,0.92)">${escapeHtml(e.label)}</span>
                <span class="badge ${stCls}">${stLbl}</span>
              </div>
            </div>
            <div class="card-body">
              <div class="card-title">${escapeHtml(e.title)}</div>
              <div class="card-artist">${escapeHtml(e.artist)}</div>
              ${romSubtitle}
              <div class="card-meta" style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:2px">
                <span>${e.single_or_album ? escapeHtml(e.single_or_album) : "Single metadata pending"}${e.release_date ? ` · ${escapeHtml(e.release_date)}` : ""}</span>
                ${singleBadge}
              </div>
              ${e.episodes_or_context ? `<div class="card-meta">${escapeHtml(e.episodes_or_context)}</div>` : ""}
              ${saavnBox}
              ${tracksDrawer}
              <div class="card-footer">
                <div style="display:flex;gap:6px;flex-wrap:wrap">
                  ${dlBtnHtml}
                  <button class="btn btn-secondary btn-sm toggle-cd-btn" type="button" data-entry="${escapeHtml(e.entry_id)}" data-owned="${e.owned_from_cd ? "1" : "0"}">
                    ${e.owned_from_cd ? "✓ Ripped (Undo)" : "Mark Ripped"}
                  </button>
                </div>
                <div class="card-links">${saavnLink}</div>
              </div>
            </div>
          </article>
        `;
      })
      .join("");

    grid.querySelectorAll(".toggle-cd-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const nextOwned = btn.dataset.owned !== "1";
        await apiJson(`/api/animes/${state.currentAnimeSlug}/toggle-cd`, {
          method: "POST",
          body: JSON.stringify({ entry_id: btn.dataset.entry, owned: nextOwned }),
        });
        await selectAnime(state.currentAnimeSlug);
        loadAnimesList(false);
      });
    });

    grid.querySelectorAll(".dl-entry-btn").forEach((btn) => {
      btn.addEventListener("click", () => downloadCurrentAnime([btn.dataset.entry]));
    });
  }

  $("gallery-filters").querySelectorAll(".pill").forEach((p) => {
    p.addEventListener("click", () => {
      $("gallery-filters").querySelectorAll(".pill").forEach((x) => x.classList.remove("active"));
      p.classList.add("active");
      state.galleryFilter = p.dataset.gfilter;
      renderGalleryStage();
    });
  });

  $("gal-max-tracks").addEventListener("input", () => state.currentGallery && renderGalleryStage());

  async function downloadCurrentAnime(entryIds = null) {
    const slug = state.currentAnimeSlug;
    if (!slug) return;
    try {
      const { job_id } = await apiJson(`/api/animes/${slug}/download`, {
        method: "POST",
        body: JSON.stringify({
          out_folder: $("gal-out-folder").value.trim(),
          include_singles: $("gal-include-singles").checked,
          max_album_tracks: parseInt($("gal-max-tracks").value || "6", 10),
          proxy_url: $("gal-proxy-url").value.trim(),
          entry_ids: entryIds,
        }),
      });
      setAutopilotBusy(true, job_id);
      const res = await pollJob(job_id, (msg, pct) => {
        $("gallery-progress-msg").textContent = msg;
        $("gallery-progress-pct").textContent = `${pct}%`;
        $("gallery-progress-fill").style.width = `${pct}%`;
      });
      if (res.gallery) {
        state.currentGallery = res.gallery;
        renderGalleryStage();
      }
      await loadAnimesList(false);
      showToast(`Downloaded ${res.downloaded_count} file(s) into ${res.folder_path}`);
    } catch (err) {
      showToast(`Download error: ${err.message}`);
    } finally {
      setAutopilotBusy(false);
      setTimeout(() => $("gallery-progress").classList.add("hidden"), 1000);
    }
  }

  // One-click: Scan & Download CURRENT anime + create folder + CD To-Do
  $("btn-run-anime").addEventListener("click", async () => {
    const slug = state.currentAnimeSlug;
    if (!slug) return;
    $("autopilot-summary").classList.add("hidden");
    try {
      const { job_id } = await apiJson(`/api/animes/${slug}/run`, {
        method: "POST",
        body: JSON.stringify({
          out_folder: $("gal-out-folder").value.trim(),
          include_singles: $("gal-include-singles").checked,
          max_album_tracks: parseInt($("gal-max-tracks").value || "6", 10),
          strict_versions: $("gal-strictness").value !== "relaxed",
          proxy_url: $("gal-proxy-url").value.trim(),
        }),
      });
      setAutopilotBusy(true, job_id);
      const res = await pollJob(job_id, (msg, pct) => {
        $("gallery-progress-msg").textContent = msg;
        $("gallery-progress-pct").textContent = `${pct}%`;
        $("gallery-progress-fill").style.width = `${pct}%`;
      });
      if (res.gallery) {
        state.currentGallery = res.gallery;
        renderGalleryStage();
      }
      await loadAnimesList(false);
      const aInfo = (res.animes || [])[0];
      const sum = $("autopilot-summary");
      sum.classList.remove("hidden");
      sum.innerHTML =
        `<strong>${escapeHtml(aInfo?.name || slug)} complete!</strong> ` +
        `Downloaded <strong>${res.total_downloaded}</strong> verified track(s), flagged <strong>${res.total_todo}</strong> song(s) to CD To-Do. ` +
        `Folder created at <code>${escapeHtml(aInfo?.folder_path || res.out_folder)}</code>.`;
      showToast(`Done: ${res.total_downloaded} downloaded, ${res.total_todo} CD To-Do`);
    } catch (err) {
      showToast(`Error: ${err.message}`);
    } finally {
      setAutopilotBusy(false);
      setTimeout(() => $("gallery-progress").classList.add("hidden"), 1000);
    }
  });

  // One-click: Run ALL 83 ANIMES!
  $("btn-run-all").addEventListener("click", async () => {
    $("autopilot-summary").classList.add("hidden");
    try {
      const { job_id } = await apiJson("/api/animes/run-all", {
        method: "POST",
        body: JSON.stringify({
          out_folder: $("gal-out-folder").value.trim(),
          include_singles: $("gal-include-singles").checked,
          max_album_tracks: parseInt($("gal-max-tracks").value || "6", 10),
          skip_already_scanned: $("gal-skip-scanned").checked,
          strict_versions: $("gal-strictness").value !== "relaxed",
          proxy_url: $("gal-proxy-url").value.trim(),
        }),
      });
      setAutopilotBusy(true, job_id);
      const res = await pollJob(job_id, (msg, pct) => {
        $("gallery-progress-msg").textContent = msg;
        $("gallery-progress-pct").textContent = `${pct}%`;
        $("gallery-progress-fill").style.width = `${pct}%`;
      });
      await loadAnimesList(true);
      const sum = $("autopilot-summary");
      sum.classList.remove("hidden");
      sum.innerHTML =
        `<strong>${res.cancelled ? "Stopped (progress saved)" : "All 83 Animes Complete!"}</strong> ` +
        `Processed <strong>${res.processed_animes}</strong> series · Downloaded <strong>${res.total_downloaded}</strong> verified track(s) · ` +
        `Flagged <strong>${res.total_todo}</strong> song(s) to <code>${escapeHtml(res.master_todo_path)}</code> · ` +
        `Directory index at <code>${escapeHtml(res.directory_index_path)}</code>.`;
      showToast(`Processed ${res.processed_animes} anime series`);
    } catch (err) {
      showToast(`Autopilot error: ${err.message}`);
    } finally {
      setAutopilotBusy(false);
      setTimeout(() => $("gallery-progress").classList.add("hidden"), 1200);
    }
  });

  $("btn-stop-job").addEventListener("click", async () => {
    if (!state.activeJobId) return;
    await apiJson(`/api/jobs/${state.activeJobId}/cancel`, { method: "POST" });
    showToast("Stopping after current anime finishes (progress will be saved)...");
  });

  $("btn-download-anime").addEventListener("click", () => downloadCurrentAnime(null));
  $("btn-open-root-folder").addEventListener("click", () => openFolderInFinder($("gal-out-folder").value.trim()));
  $("btn-open-anime-folder").addEventListener("click", () => {
    const f = state.currentGallery?.franchise;
    const path = state.currentGallery?.folder_path || (f ? `${$("gal-out-folder").value.trim()}/${f.name}` : "");
    openFolderInFinder(path);
  });

  async function openTodoModal(markdown, downloadUrl) {
    state.currentTodoMarkdown = markdown;
    state.currentTodoDownloadUrl = downloadUrl;
    $("todo-markdown-preview").textContent = markdown;
    $("todo-modal").classList.remove("hidden");
  }

  $("btn-anime-todo").addEventListener("click", async () => {
    if (!state.currentAnimeSlug) return;
    const url = `/api/animes/${state.currentAnimeSlug}/todo.md`;
    const md = await (await fetch(url)).text();
    openTodoModal(md, url);
  });

  $("btn-master-todo").addEventListener("click", async () => {
    const url = `/api/animes/all/todo.md`;
    const md = await (await fetch(url)).text();
    openTodoModal(md, url);
  });

  // Modal controls
  document.querySelectorAll("[data-close-modal]").forEach((el) => {
    el.addEventListener("click", () => $("todo-modal").classList.add("hidden"));
  });
  $("btn-copy-todo").addEventListener("click", async () => {
    await navigator.clipboard.writeText(state.currentTodoMarkdown);
    showToast("Copied CD To-Do Markdown to clipboard");
  });
  $("btn-download-todo-md").addEventListener("click", () => {
    if (state.currentTodoDownloadUrl) window.location.href = state.currentTodoDownloadUrl;
  });

  // ======================== VIEW 2: SPOTIFY PLAYLIST -> JIOSAAVN ========================
  document.querySelectorAll(".preset-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("spotify-url").value = btn.dataset.url;
      runAnalyze();
    });
  });

  function computeExpansion(track) {
    const includeAlbums = $("dl-mode").value === "albums";
    const maxTracks = Math.max(1, parseInt($("max-album-tracks").value || "6", 10));
    const total = track.album_total_tracks;
    if (!includeAlbums) return { expand: false, label: "Song only (mode)" };
    if (!total) return { expand: false, label: "Track count unknown" };
    if (total <= 1) return { expand: false, label: "1-track single" };
    if (total <= maxTracks) return { expand: true, label: `Expand single (${total} ≤ ${maxTracks})` };
    return { expand: false, label: `Song only (${total} > ${maxTracks})` };
  }

  function updateSelectedBadge() {
    $("selected-count").textContent = String(state.selectedPositions.size);
    $("btn-download").disabled = state.selectedPositions.size === 0;
  }

  function renderPlaylistPlan() {
    const plan = state.currentPlan;
    if (!plan) return;
    $("playlist-results").classList.remove("hidden");

    const pl = plan.playlist || {};
    $("pl-name").textContent = pl.name || "Spotify Playlist";
    $("pl-ref-badge").textContent = `Crosscheck: ${plan.reference_mode}`;
    const outDir = $("out-folder").value || ".";
    $("pl-folder-preview").textContent = `Folder: ${outDir}/${pl.name || "Playlist"}/`;

    const coverEl = $("pl-cover");
    if (pl.cover_url) {
      coverEl.src = pl.cover_url;
      coverEl.classList.remove("hidden");
    } else {
      coverEl.classList.add("hidden");
    }

    const items = plan.items || [];
    let expandCount = 0;
    items.forEach((it) => {
      const exp = computeExpansion(it.track);
      if (exp.expand && it.verification.status !== "flagged") expandCount++;
    });

    $("kpi-total").textContent = String(plan.summary.total_tracks);
    $("kpi-ready").textContent = String(plan.summary.ready_to_download);
    $("kpi-flagged").textContent = String(plan.summary.flagged_tracks);
    $("kpi-unavail").textContent = String(plan.summary.unavailable_on_saavn);
    $("kpi-missing").textContent = String(plan.summary.missing_official_themes);
    $("kpi-expand").textContent = String(expandCount);

    const missing = plan.missing_references || [];
    if (missing.length > 0) {
      $("missing-panel").classList.remove("hidden");
      $("missing-list").innerHTML = missing
        .map(
          (m) =>
            `<li><strong>${escapeHtml(m.label)} — ${escapeHtml(m.title)}</strong> by <strong>${escapeHtml(m.artist)}</strong>` +
            (m.episodes ? ` <span class="muted">(Episodes ${escapeHtml(m.episodes)})</span>` : "") +
            `</li>`
        )
        .join("");
    } else {
      $("missing-panel").classList.add("hidden");
    }

    const tbody = $("tracks-tbody");
    const filtered = items.filter((it) => {
      const st = it.verification.status;
      const exp = computeExpansion(it.track);
      if (state.trackFilter === "ready") return st !== "flagged" && Boolean(it.saavn_match);
      if (state.trackFilter === "flagged") return st === "flagged";
      if (state.trackFilter === "unavailable") return st !== "flagged" && !it.saavn_match;
      if (state.trackFilter === "expand") return exp.expand;
      return true;
    });

    tbody.innerHTML = filtered
      .map((it) => {
        const tr = it.track;
        const ver = it.verification;
        const ref = ver.matched_reference;
        const art = it.artwork;
        const saavn = it.saavn_match;
        const exp = computeExpansion(tr);
        const checked = state.selectedPositions.has(it.position);
        const canDownload = Boolean(saavn);

        let statusBadge = `<span class="badge badge-ok">Verified Original</span>`;
        if (ver.status === "flagged") {
          statusBadge = `<span class="badge badge-flag">Flagged · Disabled</span>`;
        } else if (ver.status === "warning") {
          statusBadge = `<span class="badge badge-warn">Original · Version Note</span>`;
        }

        const flagsHtml = (ver.flags || []).length
          ? `<ul class="reason-list">${ver.flags.map((f) => `<li>${escapeHtml(f)}</li>`).join("")}</ul>`
          : "";
        const warnsHtml = (ver.warnings || []).length
          ? `<ul class="warn-list">${ver.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul>`
          : "";

        const refHtml = ref
          ? `<div class="ref-box ${ver.status === "flagged" ? "ref-mismatch" : ""}">
               <strong>${escapeHtml(ref.label)}</strong>: ${escapeHtml(ref.title)} — ${escapeHtml(ref.artist)}
               ${ref.episodes ? `<span class="muted"> (Ep. ${escapeHtml(ref.episodes)})</span>` : ""}
             </div>`
          : "";

        const saavnHtml = saavn
          ? `<div>
               <span class="badge badge-ok">Matched${saavn.is_320kbps ? " · 320kbps" : ""}</span>
               <div class="track-artist" style="margin-top:4px">${escapeHtml(saavn.title)} — ${escapeHtml((saavn.artists || []).join(", "))}</div>
               <div class="muted" style="font-size:11px">Score ${Math.round((saavn.match_score || 0) * 100)}%${saavn.artist_verified ? " · Verified Artist" : ""}</div>
             </div>`
          : `<div>
               <span class="badge badge-warn">CD To-Do</span>
               <div class="muted" style="font-size:11.5px;margin-top:4px">${escapeHtml(it.saavn_note || "Unavailable on JioSaavn")}</div>
             </div>`;

        const rowClass = `${ver.status === "flagged" ? "row-flagged" : ""} ${!checked ? "row-disabled" : ""}`;

        return `
          <tr class="${rowClass}">
            <td class="col-check">
              <input type="checkbox" class="track-cb" data-pos="${it.position}" ${checked ? "checked" : ""} ${!canDownload ? "disabled title='No verified JioSaavn stream available'" : ""} />
            </td>
            <td class="col-num">${it.position}</td>
            <td class="col-art">
              <div class="cover-thumb-wrap">
                ${art && art.url ? `<img class="cover-thumb" src="${escapeHtml(art.url)}" alt="" loading="lazy" />` : `<div class="cover-thumb"></div>`}
                <span class="res-tag">${escapeHtml(art ? art.resolution : "—")}</span>
              </div>
            </td>
            <td>
              <div class="track-title">${escapeHtml(tr.title)}</div>
              <div class="track-artist">${escapeHtml((tr.artists || []).join(", "))}${tr.release_date ? ` · ${escapeHtml(tr.release_date)}` : ""}</div>
              ${refHtml}
            </td>
            <td>
              <div style="font-weight:600;font-size:12.5px">${escapeHtml(tr.album_name || "—")}</div>
              <div class="mono muted" style="margin-top:2px">${tr.album_total_tracks ? `${tr.album_total_tracks} tracks` : "—"}</div>
              <div style="margin-top:5px">
                <span class="badge ${exp.expand ? "badge-accent" : "badge-neutral"}">${escapeHtml(exp.label)}</span>
              </div>
            </td>
            <td>${statusBadge}${flagsHtml}${warnsHtml}</td>
            <td>${saavnHtml}</td>
          </tr>
        `;
      })
      .join("");

    tbody.querySelectorAll(".track-cb").forEach((cb) => {
      cb.addEventListener("change", () => {
        const p = parseInt(cb.dataset.pos, 10);
        if (cb.checked) state.selectedPositions.add(p);
        else state.selectedPositions.delete(p);
        updateSelectedBadge();
      });
    });

    updateSelectedBadge();
  }

  async function runAnalyze() {
    const url = $("spotify-url").value.trim();
    if (!url) {
      showToast("Paste a Spotify playlist or album URL first.");
      return;
    }
    $("btn-analyze").disabled = true;
    $("playlist-progress").classList.remove("hidden");
    $("download-summary").classList.add("hidden");

    try {
      const { job_id } = await apiJson("/api/analyze", {
        method: "POST",
        body: JSON.stringify({
          url,
          include_albums: $("dl-mode").value === "albums",
          max_album_tracks: parseInt($("max-album-tracks").value || "6", 10),
          reference_mode: $("reference-mode").value,
          strict_versions: $("strict-versions").checked,
          out_folder: $("out-folder").value.trim(),
          proxy_url: $("proxy-url").value.trim(),
        }),
      });
      const plan = await pollJob(job_id, (msg, pct) => {
        $("playlist-progress-msg").textContent = msg;
        $("playlist-progress-pct").textContent = `${pct}%`;
        $("playlist-progress-fill").style.width = `${pct}%`;
      });
      state.currentPlan = plan;
      state.selectedPositions = new Set(
        (plan.items || []).filter((it) => it.enabled_by_default).map((it) => it.position)
      );
      renderPlaylistPlan();
      showToast(`Analyzed "${plan.playlist.name}" (${plan.summary.total_tracks} tracks)`);
      if (plan.detected_anime_slug) loadAnimesList(false);
    } catch (err) {
      showToast(`Error: ${err.message}`);
    } finally {
      $("btn-analyze").disabled = false;
      setTimeout(() => $("playlist-progress").classList.add("hidden"), 900);
    }
  }

  $("btn-analyze").addEventListener("click", runAnalyze);
  $("spotify-url").addEventListener("keydown", (e) => {
    if (e.key === "Enter") runAnalyze();
  });

  $("dl-mode").addEventListener("change", () => state.currentPlan && renderPlaylistPlan());
  $("max-album-tracks").addEventListener("input", () => state.currentPlan && renderPlaylistPlan());

  $("track-filters").querySelectorAll(".pill").forEach((p) => {
    p.addEventListener("click", () => {
      $("track-filters").querySelectorAll(".pill").forEach((x) => x.classList.remove("active"));
      p.classList.add("active");
      state.trackFilter = p.dataset.filter;
      renderPlaylistPlan();
    });
  });

  $("btn-select-verified").addEventListener("click", () => {
    if (!state.currentPlan) return;
    state.selectedPositions = new Set(
      state.currentPlan.items.filter((it) => it.enabled_by_default).map((it) => it.position)
    );
    renderPlaylistPlan();
  });

  $("btn-unselect-all").addEventListener("click", () => {
    state.selectedPositions.clear();
    renderPlaylistPlan();
  });

  $("btn-view-todo").addEventListener("click", async () => {
    if (!state.currentPlan) return;
    const data = await apiJson(`/api/plans/${state.currentPlan.plan_id}/todo.md`);
    openTodoModal(data.markdown, `/api/plans/${state.currentPlan.plan_id}/todo.md?download=1`);
  });

  $("btn-export-todo").addEventListener("click", async () => {
    if (!state.currentPlan) return;
    try {
      const data = await apiJson("/api/export-todo", {
        method: "POST",
        body: JSON.stringify({
          plan_id: state.currentPlan.plan_id,
          out_folder: $("out-folder").value.trim(),
        }),
      });
      showToast(`Saved CD_TODO_LIST.md to ${data.todo_path}`);
      openTodoModal(data.markdown, `/api/plans/${state.currentPlan.plan_id}/todo.md?download=1`);
    } catch (err) {
      showToast(`Export error: ${err.message}`);
    }
  });

  $("btn-download").addEventListener("click", async () => {
    if (!state.currentPlan || state.selectedPositions.size === 0) return;
    $("btn-download").disabled = true;
    $("playlist-progress").classList.remove("hidden");
    try {
      const { job_id } = await apiJson("/api/download", {
        method: "POST",
        body: JSON.stringify({
          plan_id: state.currentPlan.plan_id,
          selected_positions: Array.from(state.selectedPositions),
          include_albums: $("dl-mode").value === "albums",
          max_album_tracks: parseInt($("max-album-tracks").value || "6", 10),
          out_folder: $("out-folder").value.trim(),
          proxy_url: $("proxy-url").value.trim(),
        }),
      });
      const res = await pollJob(job_id, (msg, pct) => {
        $("playlist-progress-msg").textContent = msg;
        $("playlist-progress-pct").textContent = `${pct}%`;
        $("playlist-progress-fill").style.width = `${pct}%`;
      });
      const sum = $("download-summary");
      sum.classList.remove("hidden");
      sum.innerHTML =
        `<strong>Download Complete!</strong> Saved <strong>${res.downloaded.length}</strong> track(s) into <code>${escapeHtml(res.playlist_dir)}</code>. ` +
        `CD To-Do List written to <code>${escapeHtml(res.todo_path)}</code>` +
        (res.errors.length ? ` (${res.errors.length} error(s))` : ".");
      showToast(`Downloaded ${res.downloaded.length} track(s)`);
    } catch (err) {
      showToast(`Download error: ${err.message}`);
    } finally {
      updateSelectedBadge();
      setTimeout(() => $("playlist-progress").classList.add("hidden"), 900);
    }
  });

  // Init: load config and immediately populate the 83-Anime Autopilot Gallery
  (async () => {
    try {
      const cfg = await apiJson("/api/config");
      $("out-folder").value = cfg.out_folder || "";
      $("gal-out-folder").value = cfg.out_folder || "";
      $("proxy-url").value = cfg.proxy_url || "";
      $("gal-proxy-url").value = cfg.proxy_url || "";
      if (cfg.log_path) {
        state.logPath = cfg.log_path;
        renderLogs();
      }
    } catch (_) {}
    await Promise.all([loadAnimesList(true), fetchLogsOnce()]);
  })();
})();

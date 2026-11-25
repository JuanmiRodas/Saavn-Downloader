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
  };

  const $ = (id) => document.getElementById(id);

  function showToast(msg) {
    const el = $("toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => el.classList.add("hidden"), 3400);
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

  async function pollJob(jobId, onProgress) {
    while (true) {
      const job = await apiJson(`/api/jobs/${jobId}`);
      if (onProgress) onProgress(job.message || "Working...", job.progress || 0);
      if (job.status === "completed") return job.result;
      if (job.status === "error") throw new Error(job.error || "Background job failed");
      await new Promise((r) => setTimeout(r, 450));
    }
  }

  // Tabs
  document.querySelectorAll(".nav-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".nav-tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".workspace").forEach((w) => w.classList.remove("active"));
      btn.classList.add("active");
      $(`view-${btn.dataset.view}`).classList.add("active");
      if (btn.dataset.view === "gallery" && state.animes.length === 0) {
        loadAnimesList();
      }
    });
  });

  // Preset buttons
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

    // Missing official themes
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

    // Filter rows
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
      if (state.animes.length > 0 && plan.detected_anime_slug) {
        loadAnimesList(false);
      }
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

  // Live update expansion pills when mode or max tracks changes!
  $("dl-mode").addEventListener("change", () => state.currentPlan && renderPlaylistPlan());
  $("max-album-tracks").addEventListener("input", () => state.currentPlan && renderPlaylistPlan());

  // Track filters
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

  // Preview / Export CD To-Do
  async function openTodoModal(markdown, downloadUrl) {
    state.currentTodoMarkdown = markdown;
    state.currentTodoDownloadUrl = downloadUrl;
    $("todo-markdown-preview").textContent = markdown;
    $("todo-modal").classList.remove("hidden");
  }

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

  // ======================== GALLERY VIEW ========================
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
        const pills = a.scanned
          ? `<span class="badge badge-ok">${a.ready_saavn}</span><span class="badge badge-warn">${a.todo_cd}</span>`
          : `<span class="badge badge-neutral">Scan</span>`;
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

  function renderGalleryStage() {
    const gal = state.currentGallery;
    if (!gal) return;
    const f = gal.franchise;
    $("gal-title").textContent = f.name;
    $("gal-jp").textContent = f.jp_name;
    $("gal-notes").textContent = `${f.notes} · Japanese originals only (soundtracks/OSTs automatically excluded)`;

    const plBar = $("gal-playlists-bar");
    const playlists = gal.linked_playlists || [];
    if (playlists.length > 0) {
      plBar.classList.remove("hidden");
      plBar.innerHTML =
        `<span class="preset-label">Linked Spotify Playlists:</span>` +
        playlists
          .map(
            (p) =>
              `<button class="chip open-pl-btn" type="button" data-url="${escapeHtml(p.url)}">${escapeHtml(p.label)} → Analyze</button>`
          )
          .join("");
      plBar.querySelectorAll(".open-pl-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          $("tab-playlist").click();
          $("spotify-url").value = btn.dataset.url;
          runAnalyze();
        });
      });
    } else {
      plBar.classList.add("hidden");
    }

    const entries = gal.entries || [];
    const filtered = entries.filter((e) => {
      if (state.galleryFilter === "all") return true;
      if (["OP", "ED", "Movie"].includes(state.galleryFilter)) return e.category === state.galleryFilter;
      if (state.galleryFilter === "Character") return ["Character", "Insert", "Special"].includes(e.category);
      return e.status === state.galleryFilter;
    });

    $("gal-count-note").textContent = `${filtered.length} of ${entries.length} entries`;

    const grid = $("gallery-grid");
    if (entries.length === 0) {
      grid.innerHTML = `
        <div class="empty-state">
          <p><strong>${escapeHtml(f.name)}</strong> hasn't been scanned yet.</p>
          <p class="muted">Click <strong>"Scan Wikis, Singles &amp; 3000px Covers"</strong> above to fetch all Japanese openings, endings, movie singles &amp; character songs (excluding soundtracks), resolve official covers &amp; metadata, verify JioSaavn, and build CD To-Do cards.</p>
        </div>
      `;
      return;
    }

    if (filtered.length === 0) {
      grid.innerHTML = `<div class="empty-state">No entries match the current filter.</div>`;
      return;
    }

    grid.innerHTML = filtered
      .map((e) => {
        const statusMap = {
          ready_saavn: ["badge-ok", "Ready on JioSaavn"],
          todo_cd: ["badge-warn", "CD To-Do"],
          flagged_cover: ["badge-flag", "Cover Flagged · CD To-Do"],
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

        const singleTracks = e.single_tracks || [];
        const tracksDrawer = singleTracks.length
          ? `<details class="card-single-tracks">
               <summary>Single tracklist (${singleTracks.length} tracks)</summary>
               <ol>${singleTracks.map((st) => `<li>${escapeHtml(st.title)}</li>`).join("")}</ol>
             </details>`
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
              <div class="card-meta">
                ${e.single_or_album ? `Single: ${escapeHtml(e.single_or_album)}` : "Single metadata pending"}
                ${e.single_track_count ? ` (${e.single_track_count}t)` : ""}
                ${e.release_date ? ` · ${escapeHtml(e.release_date)}` : ""}
              </div>
              ${e.episodes_or_context ? `<div class="card-meta">${escapeHtml(e.episodes_or_context)}</div>` : ""}
              ${tracksDrawer}
              <div class="card-footer">
                <button class="btn btn-secondary btn-sm toggle-cd-btn" type="button" data-entry="${escapeHtml(e.entry_id)}" data-owned="${e.owned_from_cd ? "1" : "0"}">
                  ${e.owned_from_cd ? "✓ In Library (Undo)" : "Mark Ripped from CD"}
                </button>
                <div class="card-links">
                  ${e.spotify_track_url ? `<a class="link-pill" href="${escapeHtml(e.spotify_track_url)}" target="_blank" rel="noopener">Spotify</a>` : ""}
                  ${e.youtube_music_query_url ? `<a class="link-pill" href="${escapeHtml(e.youtube_music_query_url)}" target="_blank" rel="noopener">YT Music</a>` : ""}
                </div>
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
  }

  $("gallery-filters").querySelectorAll(".pill").forEach((p) => {
    p.addEventListener("click", () => {
      $("gallery-filters").querySelectorAll(".pill").forEach((x) => x.classList.remove("active"));
      p.classList.add("active");
      state.galleryFilter = p.dataset.gfilter;
      renderGalleryStage();
    });
  });

  $("btn-scan-anime").addEventListener("click", async () => {
    const slug = state.currentAnimeSlug;
    if (!slug) return;
    $("btn-scan-anime").disabled = true;
    $("gallery-progress").classList.remove("hidden");
    try {
      const { job_id } = await apiJson(`/api/animes/${slug}/scan`, {
        method: "POST",
        body: JSON.stringify({
          check_saavn: true,
          enrich_itunes: true,
          proxy_url: $("proxy-url").value.trim(),
        }),
      });
      const gal = await pollJob(job_id, (msg, pct) => {
        $("gallery-progress-msg").textContent = msg;
        $("gallery-progress-pct").textContent = `${pct}%`;
        $("gallery-progress-fill").style.width = `${pct}%`;
      });
      state.currentGallery = gal;
      renderGalleryStage();
      loadAnimesList(false);
      showToast(`Scanned ${gal.franchise.name}: ${gal.entries.length} singles/themes built`);
    } catch (err) {
      showToast(`Scan error: ${err.message}`);
    } finally {
      $("btn-scan-anime").disabled = false;
      setTimeout(() => $("gallery-progress").classList.add("hidden"), 900);
    }
  });

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

  // Init
  (async () => {
    try {
      const cfg = await apiJson("/api/config");
      $("out-folder").value = cfg.out_folder || "";
      $("proxy-url").value = cfg.proxy_url || "";
    } catch (_) {}
  })();
})();

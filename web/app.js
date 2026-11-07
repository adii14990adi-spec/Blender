/* game-assets UI — vanilla JS, no build step, mobile Chrome/Safari friendly. */
(function () {
  "use strict";

  var MAX_EDGE = 512; // reference images are downscaled to 512px before upload
  var JPEG_QUALITY = 0.85;
  var MAX_IMAGE_BYTES = 1.6 * 1024 * 1024; // keep well under the Worker's 1.9 MB cap
  // jsDelivr 404s a brand-new commit for 30-120 s, so hammering every 5 s is wasted work:
  // poll with backoff instead (2 → 4 → 8 → 10 s, capped), unelss the Worker says otherwise.
  var POLL_SCHEDULE = [2000, 4000, 8000, 10000];
  var POLL_MAX_MS = 6 * 60 * 1000; // 6 minutes

  var $ = function (id) { return document.getElementById(id); };

  var store = {
    get: function (k, d) {
      try { var v = localStorage.getItem("ga:" + k); return v === null ? d : v; } catch (e) { return d; }
    },
    set: function (k, v) {
      try { localStorage.setItem("ga:" + k, v); } catch (e) { /* private mode */ }
    }
  };

  var state = {
    apiBase: store.get("api", ""),
    imageDataUrl: null,
    imageBytes: 0,
    repo: store.get("repo", ""),
    job: null,
    pollTimer: null,
    tickTimer: null,
    startedAt: 0,
    attempt: 0,
    viewerTried: 0,
    urls: []
  };

  // ---------------------------------------------------------------- helpers

  function apiUrl(path) {
    return state.apiBase.replace(/\/+$/, "") + path;
  }

  function setNote(el, text, kind) {
    el.textContent = text || "";
    el.className = "note" + (kind ? " " + kind : "");
  }

  function humanBytes(n) {
    if (!n) return "0 B";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function setPill(kind, text) {
    var pill = $("pill");
    pill.className = "pill " + kind;
    $("pillText").textContent = text;
  }

  function fail(message, detail) {
    stopTimers();
    setPill("err", "failed");
    setNote($("statusNote"), message + (detail ? " — " + detail : ""), "err");
    $("retryRow").classList.remove("hidden");
    $("go").disabled = false;
  }

  function stopTimers() {
    if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
    if (state.tickTimer) { clearInterval(state.tickTimer); state.tickTimer = null; }
  }

  var ERROR_TEXT = {
    rate_limited_ip: "Too many builds from this network (20/hour). Try again later.",
    rate_limited_global: "Daily build quota reached for this deployment.",
    asset_not_allowed: "That asset is not enabled on the Worker (check ALLOWED_ASSETS).",
    asset_id_required: "Pick a base asset.",
    no_valid_ops: "The model could not turn that into usable edits. Try a clearer, shorter prompt.",
    invalid_json: "The vision model returned something that is not JSON. Retrying usually helps.",
    groq_failed: "Groq rejected the request (key, quota or model id).",
    groq_bad_response: "Groq returned an unreadable response.",
    job_commit_failed: "Could not write the job file to GitHub (PAT permissions?).",
    dispatch_failed: "Could not trigger the build workflow (Actions permission?).",
    nothing_to_do: "Add an image or a prompt first.",
    image_too_large: "That image is too large — pick a smaller one.",
    bad_json: "Malformed request.",
    internal_error: "Worker crashed — check `wrangler tail`."
  };

  function friendly(code, detail) {
    return (ERROR_TEXT[code] || "Request failed") + (code ? " (" + code + ")" : "") + (detail ? ": " + detail : "");
  }

  // ------------------------------------------------------------- connection

  function paintConnection() {
    $("apiBase").value = state.apiBase;
    var ready = /^https?:\/\//.test(state.apiBase);
    $("go").disabled = !ready || !$("asset").value;
    if (!ready) {
      setNote($("apiNote"), "Paste the URL printed by `wrangler deploy`, then Save.", "");
    }
  }

  function connect(base) {
    var clean = String(base || "").trim().replace(/\/+$/, "");
    if (!/^https?:\/\//i.test(clean)) {
      setNote($("healthNote"), "That does not look like a URL (needs http:// or https://).", "err");
      return;
    }
    state.apiBase = clean;
    store.set("api", clean);
    $("apiBase").value = clean;
    setNote($("healthNote"), "Checking…", "");

    fetch(apiUrl("/health"), { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (h) {
        state.repo = h.repo || "";
        if (state.repo) {
          store.set("repo", state.repo);
          $("actionsLink").href = "https://github.com/" + state.repo + "/actions";
        }
        var assets = h.assets && h.assets.length ? h.assets : [];
        var select = $("asset");
        select.innerHTML = "";
        if (!assets.length) {
          var opt = document.createElement("option");
          opt.value = "";
          opt.textContent = "no assets enabled (set ALLOWED_ASSETS)";
          select.appendChild(opt);
        } else {
          assets.forEach(function (a) {
            var o = document.createElement("option");
            o.value = a;
            o.textContent = a;
            select.appendChild(o);
          });
          var remembered = store.get("asset", "");
          if (assets.indexOf(remembered) >= 0) select.value = remembered;
        }
        var models = h.models || {};
        setNote(
          $("healthNote"),
          "connected · vision " + (models.vision || "?") +
            (models.validator_enabled ? " + validator" : "") +
            (h.kv ? " · kv ok" : " · KV NOT BOUND (rate limit off)"),
          h.kv ? "" : "warn"
        );
        paintConnection();
      })
      .catch(function (err) {
        setNote($("healthNote"), "Could not reach the Worker: " + err.message, "err");
        paintConnection();
      });
  }

  // ------------------------------------------------------------------ image

  function handleFile(file) {
    if (!file) return;
    if (!/^image\//.test(file.type)) {
      setNote($("formNote"), "That file is not an image.", "err");
      return;
    }
    setNote($("formNote"), "Preparing image…", "");
    var url = URL.createObjectURL(file);
    var img = new Image();
    img.onload = function () {
      var scale = Math.min(1, MAX_EDGE / Math.max(img.width, img.height));
      var w = Math.max(1, Math.round(img.width * scale));
      var h = Math.max(1, Math.round(img.height * scale));
      var canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      var ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0, w, h);
      var dataUrl = canvas.toDataURL("image/jpeg", JPEG_QUALITY);
      URL.revokeObjectURL(url);

      var bytes = Math.round(((dataUrl.length - dataUrl.indexOf(",") - 1) * 3) / 4);
      if (bytes > MAX_IMAGE_BYTES) {
        // second pass, harder compression
        dataUrl = canvas.toDataURL("image/jpeg", 0.6);
        bytes = Math.round(((dataUrl.length - dataUrl.indexOf(",") - 1) * 3) / 4);
      }
      state.imageDataUrl = dataUrl;
      state.imageBytes = bytes;

      var thumb = $("thumb");
      thumb.src = dataUrl;
      thumb.classList.add("on");
      $("drop").classList.add("has-file");
      $("dropText").textContent = "reference set · " + w + "×" + h + " · " + humanBytes(bytes) + " (tap to change)";
      setNote($("formNote"), "Image ready. Add a prompt if you want extra control.", "");
      paintConnection();
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      setNote($("formNote"), "Could not read that image.", "err");
    };
    img.src = url;
  }

  // ---------------------------------------------------------------- generate

  function renderOps(ops) {
    var list = $("opsList");
    list.innerHTML = "";
    (ops || []).forEach(function (op, i) {
      var li = document.createElement("li");
      var tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = (i + 1) + " " + op.op.replace(/_/g, " ");
      var code = document.createElement("code");
      code.textContent = describe(op);
      li.appendChild(tag);
      li.appendChild(code);
      list.appendChild(li);
    });
  }

  function describe(op) {
    var kv = function (o) {
      return Object.keys(o)
        .filter(function (k) { return k !== "op"; })
        .map(function (k) { return k + "=" + JSON.stringify(o[k]); })
        .join(" ");
    };
    return kv(op);
  }

  function renderWarnings(warnings) {
    var box = $("warnings");
    box.innerHTML = "";
    (warnings || []).forEach(function (w) {
      var p = document.createElement("p");
      p.className = "note warn";
      p.style.margin = "4px 0";
      p.textContent = "⚠ " + w;
      box.appendChild(p);
    });
  }

  function startTimers() {
    state.startedAt = Date.now();
    if (state.tickTimer) clearInterval(state.tickTimer);
    state.tickTimer = setInterval(function () {
      var s = Math.round((Date.now() - state.startedAt) / 1000);
      $("elapsed").textContent = s + "s";
    }, 1000);
  }

  function generate() {
    if (!state.apiBase) {
      setNote($("formNote"), "Set the Worker URL first.", "err");
      return;
    }
    var asset = $("asset").value;
    if (!asset) {
      setNote($("formNote"), "Pick a base asset.", "err");
      return;
    }
    store.set("asset", asset);

    stopTimers();
    state.attempt = 0;
    $("retryRow").classList.add("hidden");
    $("viewerCard").classList.remove("on");
    $("statusCard").classList.add("on");
    $("opsList").innerHTML = "";
    $("warnings").innerHTML = "";
    setNote($("statusNote"), "Sending the reference to the Worker…", "");
    $("jobId").textContent = "—";
    $("modelName").textContent = "—";
    setPill("work", "planning");
    $("go").disabled = true;

    var payload = { asset_id: asset, prompt: $("prompt").value.slice(0, 800) };
    if (state.imageDataUrl) payload.image_b64 = state.imageDataUrl;

    fetch(apiUrl("/generate"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
      .then(function (r) {
        return r.json().then(function (b) { return { status: r.status, body: b }; });
      })
      .then(function (res) {
        if (res.status !== 200) {
          var b = res.body || {};
          if (res.status === 429) {
            var wait = Number(b.retry_after || 3600);
            return fail(
              ERROR_TEXT[b.error] || "Rate limited",
              "retry in ~" + Math.round(wait / 60) + " min"
            );
          }
          return fail(friendly(b.error, b.detail), (b.warnings || []).join("; "));
        }
        state.job = res.body;
        $("jobId").textContent = state.job.job_id;
        $("modelName").textContent = state.job.model || "?";
        renderOps(state.job.ops);
        renderWarnings(state.job.warnings);
        setPill("work", "building");
        setNote($("statusNote"), "Ops sent to GitHub Actions. Blender usually takes 1–4 minutes.", "");
        startTimers();
        poll();
      })
      .catch(function (err) {
        fail("Network error calling the Worker", err.message);
      });
  }

  // ------------------------------------------------------------------- poll

  function poll() {
    if (!state.job) return;
    if (Date.now() - state.startedAt > POLL_MAX_MS) {
      setPill("err", "timeout");
      setNote(
        $("statusNote"),
        "No output after 6 minutes. Open the build logs — the run may have failed at import or export.",
        "err"
      );
      $("retryRow").classList.remove("hidden");
      $("go").disabled = false;
      return;
    }
    state.attempt += 1;
    fetch(apiUrl("/status/" + encodeURIComponent(state.job.job_id)), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        if (s.error) {
          return fail(friendly(s.error), "");
        }
        if (s.ready) {
          stopTimers();
          setPill("ok", "ready");
          setNote($("statusNote"), "Built in " + $("elapsed").textContent + ".", "");
          showModel(s);
          $("go").disabled = false;
          return;
        }
        var delay = nextDelay(s);
        setNote(
          $("statusNote"),
          "Building… check " + state.attempt + " · " + $("elapsed").textContent +
            " elapsed · next in " + Math.round(delay / 1000) + "s",
          ""
        );
        state.pollTimer = setTimeout(poll, delay);
      })
      .catch(function () {
        state.pollTimer = setTimeout(poll, nextDelay(null)); // transient network blip
      });
  }

  // ----------------------------------------------------------------- viewer

  function nextDelay(resp) {
    if (resp && typeof resp.retry_after_ms === "number" && resp.retry_after_ms > 0) {
      return Math.min(resp.retry_after_ms, 15000);
    }
    var i = Math.max(0, Math.min(state.attempt - 1, POLL_SCHEDULE.length - 1));
    return POLL_SCHEDULE[i];
  }

  function unique(list) {
    return list.filter(function (v, i) { return v && list.indexOf(v) === i; });
  }

  function showModel(status) {
    // commit-pinned CDN → commit-pinned mirror → branch CDN → raw github
    state.urls = unique([status.url, status.alt_cdn_url, status.fallback_url, status.raw_url]);
    if (status.cdn_limited) {
      setNote(
        $("viewerNote"),
        "This GLB is " + humanBytes(status.bytes) + " — over jsDelivr's 20 MB limit, using the mirror CDN.",
        "warn"
      );
    }
    state.viewerTried = 0;
    var viewer = $("viewer");
    $("viewerCard").classList.add("on");
    setNote($("viewerNote"), "", "");
    viewer.addEventListener("error", onViewerError);
    viewer.src = state.urls[0];
    $("download").onclick = function (ev) { downloadModel(ev); };
    $("openTab").onclick = function () { window.open(state.urls[state.viewerTried] || state.urls[0], "_blank"); };
  }

  function onViewerError() {
    if (state.viewerTried < state.urls.length - 1) {
      state.viewerTried += 1;
      setNote($("viewerNote"), "CDN miss — retrying from a mirror…", "warn");
      $("viewer").src = state.urls[state.viewerTried];
    } else {
      setNote(
        $("viewerNote"),
        "The preview could not load, but the file exists. Use Download — jsDelivr may need a minute to pick up the new commit.",
        "warn"
      );
    }
  }

  function downloadModel(ev) {
    ev.preventDefault();
    var url = state.urls[state.viewerTried] || state.urls[0];
    if (!url) return;
    setNote($("viewerNote"), "Fetching " + url.split("/").pop() + "…", "");
    fetch(url)
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.blob();
      })
      .then(function (blob) {
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "out_" + (state.job ? state.job.job_id : "model") + ".glb";
        document.body.appendChild(a);
        a.click();
        setTimeout(function () {
          URL.revokeObjectURL(a.href);
          a.remove();
        }, 4000);
        setNote($("viewerNote"), "Saved " + humanBytes(blob.size) + ".", "");
      })
      .catch(function (err) {
        setNote($("viewerNote"), "Download failed (" + err.message + ") — opening the raw URL instead.", "warn");
        window.open(url, "_blank");
      });
  }

  // ------------------------------------------------------------------- wire

  $("saveApi").addEventListener("click", function () { connect($("apiBase").value); });
  $("apiBase").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); connect($("apiBase").value); }
  });
  $("file").addEventListener("change", function (e) { handleFile(e.target.files[0]); });
  $("asset").addEventListener("change", paintConnection);
  $("go").addEventListener("click", generate);
  $("retry").addEventListener("click", function () {
    $("retryRow").classList.add("hidden");
    generate();
  });

  paintConnection();
  if (state.apiBase) connect(state.apiBase);
})();

/* Octo Automation Web UI */
(() => {
  "use strict";

  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

  const state = {
    config: null,
    lastLogId: 0,
    running: false,
    es: null,
  };

  const titles = {
    home: ["통제판", "웹에서 START/STOP · PC Octo가 실행 (오토 매크로)"],
    ops: ["OPS", "고급 정찰 (일반 매크로 불필요)"],
    proxies: ["프록시", "host:port:id:pw"],
    accounts: ["계정", "Google 로그인용"],
    search: ["검색 상세", "정규식 · 체류"],
    bulk: ["대량 URL", "일괄 URL"],
    cookies: ["쿠키", "세션 주입"],
    settings: ["설정", "엔진 · API · 동시 실행"],
    logs: ["로그", "실시간 매크로 로그"],
    help: ["도움말", "Octo 오토 사용법"],
  };

  function setMacroStep(step) {
    $$(".macro-step").forEach((el) => {
      el.classList.toggle("active", el.dataset.mstep === step);
    });
  }

  function renderWorkerBoard(jobs, running) {
    const board = $("#workerBoard");
    if (!board) return;
    const list = Array.isArray(jobs) ? jobs : [];
    if (!list.length) {
      board.innerHTML = running
        ? `<div class="worker-empty">동시 워커 준비 중…</div>`
        : `<div class="worker-empty">대기 · START 하면 프로필·IP·클릭이 카드로 표시됩니다</div>`;
      return;
    }
    board.innerHTML = list
      .map((a) => {
        const g = String(a.google || "—");
        const gClass = /OK|성공/i.test(g)
          ? "ok"
          : /실패|fail/i.test(g)
            ? "bad"
            : /시도|진행|로그인/i.test(g)
              ? "run"
              : "";
        return `<div class="worker-card">
          <div class="wc-head">
            <span class="wc-job">#${a.job ?? "?"} 동시</span>
            <span class="wc-phase">${esc(a.action || a.phase || "run")}</span>
          </div>
          <div class="wc-row"><span>프로필</span><b>${esc(a.profile || "—")}</b></div>
          <div class="wc-row"><span>계정</span><b>${esc(a.email || "—")}</b></div>
          <div class="wc-row"><span>출구 IP</span><b class="wc-ip">${esc(a.ip || "확인중")}</b></div>
          <div class="wc-row"><span>프록시</span><b class="wc-proxy">${esc((a.proxy || "—").toString().slice(0, 42))}</b></div>
          <div class="wc-row"><span>구글</span><b class="wc-g ${gClass}">${esc(g)}</b></div>
          <div class="wc-row"><span>검색어</span><b>${esc(a.keyword || "—")}</b></div>
          <div class="wc-row"><span>클릭</span><b class="wc-url">${esc((a.matched_url || "—").toString().slice(0, 64))}</b></div>
          <div class="wc-row"><span>2FA</span><b>${a.has_2fa ? "Y" : "N"}</b></div>
        </div>`;
      })
      .join("");
  }

  function toast(msg, type = "ok") {
    const el = $("#toast");
    el.textContent = msg;
    el.className = `toast ${type}`;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), 3200);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    let data = null;
    try {
      data = await res.json();
    } catch {
      data = { detail: await res.text() };
    }
    if (!res.ok) {
      const detail = data?.detail || data?.message || res.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  // ── navigation ──────────────────────────────────────────
  function showPage(name) {
    $$(".page").forEach((p) => p.classList.remove("active"));
    $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.page === name));
    const page = $(`#page-${name}`);
    if (page) page.classList.add("active");
    const [t, h] = titles[name] || [name, ""];
    $("#pageTitle").textContent = t;
    $("#pageHint").textContent = h;
  }

  // ── accounts table (home primary, accounts page mirrors) ──
  function accountRowsFromTable() {
    const tb = $("#accTableHome tbody") || $("#accTable tbody");
    if (!tb) return [];
    return $$("tr", tb)
      .map((tr) => {
        const inputs = $$("input", tr);
        return {
          email: inputs[0]?.value?.trim() || "",
          password: inputs[1]?.value?.trim() || "",
          profile_title: inputs[2]?.value?.trim() || "",
          otp_secret: inputs[3]?.value?.trim() || "",
          notes: inputs[4]?.value?.trim() || "",
          otp_url: "",
        };
      })
      .filter((r) => r.email || r.profile_title);
  }

  function _fillAccTbody(tb, rows) {
    if (!tb) return;
    tb.innerHTML = "";
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><input type="text" value="${esc(r.email || "")}" placeholder="email" /></td>
        <td><input type="password" value="${esc(r.password || "")}" placeholder="password" /></td>
        <td><input type="text" value="${esc(r.profile_title || "")}" placeholder="Octo 프로필" /></td>
        <td><input type="text" value="${esc(r.otp_secret || "")}" placeholder="2FA" /></td>
        <td><input type="text" value="${esc(r.notes || "")}" placeholder="메모" /></td>
        <td><button type="button" class="btn sm ghost btn-del">×</button></td>`;
      $(".btn-del", tr).onclick = () => {
        tr.remove();
        // re-render both from remaining home rows
        const left = accountRowsFromTable();
        renderAccounts(left.length ? left : [{}]);
      };
      $$("input", tr).forEach((inp) =>
        inp.addEventListener("input", updateAccountsPreview)
      );
      tb.appendChild(tr);
    });
  }

  function renderAccounts(rows) {
    const list = rows?.length ? rows : [{}];
    _fillAccTbody($("#accTableHome tbody"), list);
    _fillAccTbody($("#accTable tbody"), list);
    updateAccountsPreview();
  }

  function addAccountRow(r = {}) {
    const rows = accountRowsFromTable();
    rows.push({
      email: r.email || "",
      password: r.password || "",
      profile_title: r.profile_title || "",
      otp_secret: r.otp_secret || "",
      notes: r.notes || "",
    });
    renderAccounts(rows);
  }

  function updateAccountsPreview() {
    const rows = accountRowsFromTable();
    const el = $("#accountsPreview");
    if (!el) return;
    el.textContent = rows.length
      ? `${rows.length}개 · ` +
        rows
          .map(
            (r) =>
              `${r.email || "-"} / ${r.profile_title || "-"} / 2FA=${r.otp_secret ? "Y" : "N"}`
          )
          .join(" · ")
      : "계정 없음 — + 계정 추가";
  }

  // ── keywords / sites dynamic lists (infinite add/delete) ──
  function syncKwTextarea() {
    const kws = $$("#kwList .dyn-row input")
      .map((i) => i.value.trim())
      .filter(Boolean);
    if ($("#keywordsText")) $("#keywordsText").value = kws.join("\n");
    if ($("#keyword")) $("#keyword").value = kws.join(" / ");
  }

  function syncSitesTextarea() {
    const sites = $$("#siteList .dyn-row input")
      .map((i) => i.value.trim())
      .filter(Boolean);
    if ($("#domainsText")) $("#domainsText").value = sites.join("\n");
    if ($("#targetDomain")) $("#targetDomain").value = sites[0] || "";
  }

  function addDynRow(listEl, value, placeholder) {
    if (!listEl) return;
    const row = document.createElement("div");
    row.className = "dyn-row";
    row.innerHTML = `
      <input type="text" value="${esc(value || "")}" placeholder="${esc(placeholder || "")}" />
      <button type="button" class="btn sm ghost dyn-del">×</button>`;
    $(".dyn-del", row).onclick = () => {
      row.remove();
      syncKwTextarea();
      syncSitesTextarea();
    };
    $("input", row).addEventListener("input", () => {
      syncKwTextarea();
      syncSitesTextarea();
    });
    listEl.appendChild(row);
  }

  function renderKeywords(list) {
    const box = $("#kwList");
    if (!box) return;
    box.innerHTML = "";
    const arr = (list || []).filter(Boolean);
    if (!arr.length) arr.push("");
    arr.forEach((k) => addDynRow(box, k, "검색어"));
    syncKwTextarea();
  }

  function renderSites(list) {
    const box = $("#siteList");
    if (!box) return;
    box.innerHTML = "";
    const arr = (list || []).filter(Boolean);
    if (!arr.length) arr.push("");
    arr.forEach((s) => addDynRow(box, s, "mysite.com"));
    syncSitesTextarea();
  }

  // ── battle log (detail feed) ─────────────────────────────
  let battleFilter = "all";
  const battleBuf = [];

  function classifyLog(msg) {
    const m = String(msg || "");
    if (/PROFILE|프로필|uuid|Cloud 검색|프록시 주입/i.test(m)) return "profile";
    if (/PROXY|프록시|출구 IP|출구IP|connection_data|proxy=/i.test(m)) return "proxy";
    if (/SEARCH|검색|keyword|SERP/i.test(m)) return "search";
    if (/클릭|click|matched|방문|CTA|banner|site_click/i.test(m)) return "click";
    if (/Google|구글|로그인|2FA|TOTP|otp/i.test(m)) return "google";
    if (/ERR|오류|실패|Error/i.test(m)) return "err";
    if (/OK|성공|완료/i.test(m)) return "ok";
    return "other";
  }

  function updateBattleStats(msg) {
    const m = String(msg || "");
    const ip =
      m.match(/출구 IP\s*=\s*([0-9.]+)/i) ||
      m.match(/출구IP[=:]\s*([0-9.]+)/i) ||
      m.match(/\b(\d{1,3}(?:\.\d{1,3}){3})\b/);
    if (ip && $("#statIp")) $("#statIp").textContent = ip[1];
    const prof =
      m.match(/프로필[=:']+\s*([^\s'·]+)/i) ||
      m.match(/profile[=:']+\s*([^\s'·]+)/i) ||
      m.match(/title='([^']+)'/i);
    if (prof && $("#statProfile")) $("#statProfile").textContent = prof[1].slice(0, 40);
    const kw = m.match(/keyword='([^']+)'/i) || m.match(/검색어[=:']+\s*([^\s']+)/i);
    if (kw && $("#statKw")) $("#statKw").textContent = kw[1].slice(0, 40);
    const url = m.match(/https?:\/\/[^\s\]"'<>]+/i);
    if (url && /클릭|방문|matched|site/i.test(m) && $("#statClick")) {
      $("#statClick").textContent = url[0].slice(0, 60);
    }
    if (/구글|Google|로그인/i.test(m) && $("#statGoogle")) {
      if (/OK|성공|완료/i.test(m)) $("#statGoogle").textContent = "OK";
      else if (/실패|ERR|오류/i.test(m)) $("#statGoogle").textContent = "실패";
      else $("#statGoogle").textContent = "진행";
    }
  }

  function appendBattleLine(entry) {
    const msg = entry.msg || entry;
    const kind = classifyLog(msg);
    battleBuf.push({ msg, kind, ts: entry.ts || Date.now() / 1000, id: entry.id });
    if (battleBuf.length > 800) battleBuf.splice(0, battleBuf.length - 800);
    updateBattleStats(msg);
    paintBattleLog();
  }

  function paintBattleLog() {
    const box = $("#battleLog");
    if (!box) return;
    const lines = battleBuf.filter(
      (x) => battleFilter === "all" || x.kind === battleFilter
    );
    const slice = lines.slice(-250);
    box.innerHTML = slice
      .map((x) => {
        const t = x.ts
          ? new Date(x.ts * 1000).toLocaleTimeString("ko-KR", { hour12: false })
          : "";
        return `<div class="blog-line kind-${x.kind}"><span class="blog-t">${t}</span><span class="blog-tag">${x.kind}</span><span class="blog-m">${esc(x.msg)}</span></div>`;
      })
      .join("");
    box.scrollTop = box.scrollHeight;
  }

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
  }

  // ── config collect / apply ──────────────────────────────
  function collectConfig() {
    syncKwTextarea();
    syncSitesTextarea();
    const keywordsText =
      ($("#keywordsText")?.value || "").trim() ||
      ($("#keyword")?.value || "").trim();
    const domainsText =
      ($("#domainsText")?.value || "").trim() ||
      ($("#targetDomain")?.value || "").trim();
    const proxiesText =
      ($("#proxiesText")?.value || "").trim() ||
      ($("#proxiesQuick")?.value || "").trim();
    const bannerText = ($("#bannerText")?.value || "").trim();
    const directUrl = ($("#directUrl")?.value || "").trim();
    const parallel =
      Number($("#parallelJobs")?.value || $("#parallelJobsHome")?.value || 1) || 1;

    // keep home/settings parallel fields in sync
    if ($("#parallelJobsHome") && $("#parallelJobs")) {
      $("#parallelJobsHome").value = parallel;
      $("#parallelJobs").value = parallel;
    }

    const targets = [];
    if (directUrl) {
      targets.push({
        url: directUrl,
        wait_until: "domcontentloaded",
        wait_ms: 2000,
        clicks: [],
      });
    }

    const cookieDomain =
      $("#cookieDomain").value.trim() || $("#targetDomain").value.trim();
    const cookieUrl =
      $("#cookieUrl").value.trim() ||
      (cookieDomain ? `https://${cookieDomain}/` : "");

    return {
      octo_api_token: ($("#token")?.value || "").trim(),
      cloud_base: (
        $("#cloudBase")?.value || "https://app.octobrowser.net/api/v2/automation"
      ).trim(),
      local_base: (
        $("#localBase")?.value || "http://127.0.0.1:58888/api"
      ).trim(),
      octo_email: $("#octoEmail") ? $("#octoEmail").value.trim() : "",
      octo_password: $("#octoPassword") ? $("#octoPassword").value : "",
      octo_auto_login: $("#octoAutoLogin") ? $("#octoAutoLogin").checked : true,
      browser_engine: $("#browserEngine")?.value || "agent",
      proxy_type: ($("#proxyType")?.value || "http"),
      proxy_mode: $("#proxyMode")?.value || "round_robin",
      proxy_start_index: Number($("#proxyStartIndex")?.value || 0),
      proxies_text: proxiesText,
      accounts_rows: accountRowsFromTable(),
      accounts_bulk: $("#accountsBulk")?.value || "",
      reuse_existing_profiles: $("#reuseProfiles")
        ? $("#reuseProfiles").checked
        : true,
      create_profile_if_missing: $("#createMissing")
        ? $("#createMissing").checked
        : true,
      headless: $("#headless") ? $("#headless").checked : false,
      start_timeout_sec: Number($("#startTimeout")?.value || 120),
      delay_between_jobs_sec: Number(
        $("#delayJobsHome")?.value || $("#delayJobs")?.value || 20
      ),
      macro_loops: Number($("#macroLoops")?.value ?? 1),
      delay_between_loops_sec: Number($("#delayLoops")?.value || 60),
      stop_profile_after_job: $("#stopAfter") ? $("#stopAfter").checked : true,
      max_jobs: Number($("#maxJobs")?.value || 0),
      parallel_jobs: parallel,
      stagger_start_sec: Number($("#staggerStart")?.value || 0),
      banner_text: bannerText,
      cookies_text: $("#cookiesText")?.value || "",
      cookies: {
        enabled: $("#cookieEnabled") ? $("#cookieEnabled").checked : false,
        when: $("#cookieWhen")?.value || "on_site",
        domain: cookieDomain,
        url: cookieUrl,
        warm_url: cookieUrl,
        warm_navigate: $("#cookieWarm") ? $("#cookieWarm").checked : true,
        clear_first: $("#cookieClearFirst")
          ? $("#cookieClearFirst").checked
          : false,
        reload_on_site: $("#cookieReload").checked,
        text: $("#cookiesText").value,
        json: $("#cookiesText").value,
      },
      octo: {
        profile_os: $("#profileOs")?.value || "android",
        mobile_fingerprint: $("#mobileFingerprint")
          ? $("#mobileFingerprint").checked
          : true,
        os_version: $("#osVersion")?.value?.trim() || "14",
        device: $("#deviceModel")?.value?.trim() || "",
        tags: ($("#profileTags")?.value || "mobile,auto")
          .split(/[,\n]/)
          .map((s) => s.trim())
          .filter(Boolean),
        match_mobile_ip: $("#matchMobileIp") ? $("#matchMobileIp").checked : true,
        traffic_metrics: $("#trafficMetrics") ? $("#trafficMetrics").checked : true,
        one_time_profiles: false,
      },
      profile_os: $("#profileOs")?.value || "android",
      mobile_fingerprint: $("#mobileFingerprint")
        ? $("#mobileFingerprint").checked
        : true,
      match_mobile_ip: $("#matchMobileIp") ? $("#matchMobileIp").checked : true,
      traffic_metrics: $("#trafficMetrics") ? $("#trafficMetrics").checked : true,
      ops: {
        enabled: $("#opsEnabled") ? $("#opsEnabled").checked : false,
        mode: $("#opsMode")?.value || "browser",
        browser_preset: $("#opsBrowserPreset")?.value || "normal",
        run_http_ops: $("#opsRunHttp") ? $("#opsRunHttp").checked : false,
        skip_hammer: $("#opsSkipHammer") ? $("#opsSkipHammer").checked : false,
        path_workers: Number($("#opsPathWorkers")?.value || 16),
        hammer_requests: Number($("#opsHammerReq")?.value || 100),
        hammer_workers: Number($("#opsHammerWorkers")?.value || 24),
        hammer_url: $("#opsHammerUrl")?.value?.trim() || "",
        multi_hammer: $("#opsMultiHammer") ? $("#opsMultiHammer").checked : true,
        swarm_parallel: Number($("#opsSwarmParallel")?.value || parallel || 5),
        force_parallel: $("#opsForceParallel") ? $("#opsForceParallel").checked : false,
        tight_stagger: true,
        waves: Number($("#opsWaves")?.value || 1),
        intensity: Number($("#opsIntensity")?.value || 5),
        extra_paths_text: $("#opsExtraPaths")?.value || "",
      },
      google_login: {
        enabled: $("#gEnabledHome")
          ? $("#gEnabledHome").checked
          : $("#gEnabled")
            ? $("#gEnabled").checked
            : true,
        mode: $("#gMode")?.value || "auto",
        login_url: "https://accounts.google.com/",
        success_url_contains: [
          "myaccount.google.com",
          "mail.google.com",
          "accounts.google.com/b/",
          "drive.google.com",
        ],
        manual_wait_sec: 300,
        autofill_pause_ms: 350,
        otp_fetch: {
          enabled: $("#otpEnabled").checked,
          secret: "",
          url: "",
          selector: "",
          regex: "\\b(\\d{6})\\b",
          wait_ms: 2500,
        },
      },
      bulk_urls_text: ($("#bulkUrlsText")?.value || "").trim(),
      search_flow: {
        enabled: $("#searchEnabled") ? $("#searchEnabled").checked : true,
        purpose: "own_site_qa",
        keyword: ($("#keyword")?.value || "").trim(),
        keywords_text: keywordsText,
        keyword_fallback: true,
        stop_on_first_keyword_hit: true,
        target_domain: ($("#targetDomain")?.value || "").trim(),
        domains_text: domainsText,
        bulk_urls_text: ($("#bulkUrlsText")?.value || "").trim(),
        path_regex_text:
          ($("#pathRegexText")?.value || "").trim() ||
          ($("#pathRegexQuick")?.value || "").trim(),
        path_regex:
          ($("#pathRegexQuick")?.value || "").trim() ||
          (($("#pathRegexText")?.value || "").trim().split("\n")[0] || ""),
        url_regex_text: ($("#urlRegexText")?.value || "").trim(),
        url_regex: (($("#urlRegexText")?.value || "").trim().split("\n")[0] || ""),
        path_targets_text: ($("#pathTargetsText")?.value || "").trim(),
        path_exclude_text: ($("#pathExcludeText")?.value || "").trim(),
        require_regex: $("#requireRegex") ? $("#requireRegex").checked : false,
        require_domain: $("#requireDomain") ? $("#requireDomain").checked : true,
        skip_ads: $("#skipAds") ? $("#skipAds").checked : true,
        search_url: "https://www.google.com/",
        max_serp_pages: Number($("#maxSerp")?.value || 3),
        max_result_clicks: Number($("#maxClicks")?.value || 1),
        revisit_count: Number($("#revisit")?.value || 1),
        warmup: $("#warmup") ? $("#warmup").checked : true,
        human: {
          dwell_ms_min: Number($("#dwellMin")?.value || 5000),
          dwell_ms_max: Number($("#dwellMax")?.value || 14000),
          scroll: $("#humanScroll") ? $("#humanScroll").checked : true,
          mouse_wander: $("#mouseWander") ? $("#mouseWander").checked : true,
          read_pauses: $("#readPauses") ? $("#readPauses").checked : true,
          scroll_steps_min: 3,
          scroll_steps_max: 8,
          scroll_up_chance: 0.25,
          random_internal_click: false,
          serp_scroll_min: 2,
          serp_scroll_max: 5,
        },
      },
      targets,
      dry_run: false,
    };
  }

  function applyConfig(cfg) {
    state.config = cfg;
    if ($("#token")) $("#token").value = cfg.octo_api_token || "";
    if ($("#cloudBase")) $("#cloudBase").value = cfg.cloud_base || "";
    if ($("#localBase")) $("#localBase").value = cfg.local_base || "";
    if ($("#octoEmail")) $("#octoEmail").value = cfg.octo_email || "";
    if ($("#octoPassword")) $("#octoPassword").value = cfg.octo_password || "";
    if ($("#octoAutoLogin")) $("#octoAutoLogin").checked = cfg.octo_auto_login !== false;
    if ($("#browserEngine")) $("#browserEngine").value = cfg.browser_engine || "agent";
    if ($("#proxyType")) $("#proxyType").value = cfg.proxy_type || "http";
    if ($("#proxyMode")) $("#proxyMode").value = cfg.proxy_mode || "round_robin";
    if ($("#proxyStartIndex")) $("#proxyStartIndex").value = cfg.proxy_start_index ?? 0;
    const px = cfg.proxies_text || "";
    if ($("#proxiesText")) $("#proxiesText").value = px;
    if ($("#proxiesQuick")) $("#proxiesQuick").value = px;
    if ($("#proxyCount"))
      $("#proxyCount").textContent = `${cfg.proxies_count || px.split("\n").filter((l) => l.trim()).length || 0}개`;

    const g = cfg.google_login || {};
    if ($("#gEnabled")) $("#gEnabled").checked = g.enabled !== false;
    if ($("#gEnabledHome")) $("#gEnabledHome").checked = g.enabled !== false;
    if ($("#gMode")) $("#gMode").value = g.mode || "auto";
    const otp = g.otp_fetch || {};
    if ($("#otpEnabled")) $("#otpEnabled").checked = otp.enabled !== false;
    if ($("#macroLoops")) $("#macroLoops").value = cfg.macro_loops ?? 0;
    if ($("#delayLoops")) $("#delayLoops").value = cfg.delay_between_loops_sec ?? 45;
    if ($("#delayJobsHome"))
      $("#delayJobsHome").value = cfg.delay_between_jobs_sec ?? 15;

    renderAccounts(cfg.accounts_rows || []);
    const bulk = (cfg.accounts_rows || [])
      .filter((r) => r.email)
      .map((r) => [r.email, r.password || "", r.otp_secret || ""].join("|"))
      .join("\n");
    if ($("#accountsBulk")) $("#accountsBulk").value = bulk;

    const sf = cfg.search_flow || {};
    if ($("#searchEnabled")) $("#searchEnabled").checked = sf.enabled !== false;
    const kwList =
      (sf.keywords_text || "").split("\n").map((s) => s.trim()).filter(Boolean).length
        ? (sf.keywords_text || "").split("\n").map((s) => s.trim()).filter(Boolean)
        : sf.keywords?.length
          ? sf.keywords
          : (sf.keyword || "")
              .split(/[\/\n]/)
              .map((s) => s.trim())
              .filter(Boolean);
    const siteList =
      (sf.domains_text || "").split("\n").map((s) => s.trim()).filter(Boolean).length
        ? (sf.domains_text || "").split("\n").map((s) => s.trim()).filter(Boolean)
        : sf.allowed_domains?.length
          ? sf.allowed_domains
          : sf.target_domain
            ? [sf.target_domain]
            : [];
    renderKeywords(kwList);
    renderSites(siteList);
    if ($("#keyword")) $("#keyword").value = sf.keyword || kwList.join(" / ");
    if ($("#keywordsText"))
      $("#keywordsText").value = sf.keywords_text || kwList.join("\n");
    if ($("#targetDomain")) $("#targetDomain").value = sf.target_domain || siteList[0] || "";
    if ($("#domainsText"))
      $("#domainsText").value = sf.domains_text || siteList.join("\n");
    const pathRx =
      sf.path_regex_text ||
      (sf.path_regexes || []).join("\n") ||
      sf.path_regex ||
      "";
    if ($("#pathRegexText")) $("#pathRegexText").value = pathRx;
    if ($("#pathRegexQuick")) {
      $("#pathRegexQuick").value =
        sf.path_regex || (pathRx.split("\n").map((s) => s.trim()).filter(Boolean)[0] || "");
    }
    if ($("#urlRegexText")) {
      $("#urlRegexText").value =
        sf.url_regex_text || sf.url_regex || "";
    }
    if ($("#pathTargetsText")) {
      $("#pathTargetsText").value =
        sf.path_targets_text || (sf.path_targets || []).join("\n");
    }
    if ($("#pathExcludeText")) {
      $("#pathExcludeText").value =
        sf.path_exclude_text || (sf.path_exclude || []).join("\n");
    }
    if ($("#requireRegex")) {
      $("#requireRegex").checked =
        sf.require_regex !== false &&
        !!(pathRx || sf.url_regex || sf.url_regex_text);
    }
    if ($("#bulkUrlsText")) {
      $("#bulkUrlsText").value =
        sf.bulk_urls_text || cfg.bulk_urls_text || "";
    }
    const bst = sf.bulk_stats;
    if (bst && $("#bulkStats")) {
      $("#bulkStats").textContent =
        `도메인 ${bst.domains || 0} · URL ${bst.full_urls || 0} · path ${bst.paths_exact || 0} · regex ${bst.path_regexes || 0}`;
    }
    $("#maxSerp").value = sf.max_serp_pages ?? 3;
    $("#revisit").value = sf.revisit_count ?? 1;
    $("#maxClicks").value = sf.max_result_clicks ?? 1;
    $("#skipAds").checked = sf.skip_ads !== false;
    $("#requireDomain").checked = sf.require_domain !== false;
    $("#warmup").checked = sf.warmup !== false;
    const human = sf.human || {};
    $("#dwellMin").value = human.dwell_ms_min ?? 4000;
    $("#dwellMax").value = human.dwell_ms_max ?? 12000;
    $("#humanScroll").checked = human.scroll !== false;
    $("#mouseWander").checked = human.mouse_wander !== false;
    $("#readPauses").checked = human.read_pauses !== false;

    const banners = (sf.banner_clicks || [])
      .map((b) => b.text_contains || "")
      .filter(Boolean)
      .join(", ");
    $("#bannerText").value = banners;

    const targets = cfg.targets || [];
    $("#directUrl").value = targets[0]?.url || "";

    $("#reuseProfiles").checked = cfg.reuse_existing_profiles !== false;
    $("#createMissing").checked = cfg.create_profile_if_missing !== false;
    $("#stopAfter").checked = cfg.stop_profile_after_job !== false;
    $("#headless").checked = !!cfg.headless;
    $("#delayJobs").value = cfg.delay_between_jobs_sec ?? 15;
    $("#startTimeout").value = cfg.start_timeout_sec ?? 120;
    $("#maxJobs").value = cfg.max_jobs ?? 0;
    const pj = cfg.parallel_jobs ?? 3;
    if ($("#parallelJobs")) $("#parallelJobs").value = pj;
    if ($("#parallelJobsHome")) $("#parallelJobsHome").value = pj;
    if ($("#staggerStart")) $("#staggerStart").value = cfg.stagger_start_sec ?? 1.5;

    const ck = cfg.cookies || {};
    if ($("#cookieEnabled")) $("#cookieEnabled").checked = !!ck.enabled;
    if ($("#cookieWhen")) $("#cookieWhen").value = ck.when || "on_site";
    if ($("#cookieDomain")) $("#cookieDomain").value = ck.domain || "";
    if ($("#cookieUrl")) $("#cookieUrl").value = ck.url || ck.warm_url || "";
    if ($("#cookiesText")) {
      $("#cookiesText").value =
        ck.text || ck.json || (ck.cookies?.length ? JSON.stringify(ck.cookies, null, 2) : "");
    }
    if ($("#cookieClearFirst")) $("#cookieClearFirst").checked = !!ck.clear_first;
    if ($("#cookieWarm")) $("#cookieWarm").checked = ck.warm_navigate !== false;
    if ($("#cookieReload")) $("#cookieReload").checked = ck.reload_on_site !== false;

    const ops = cfg.ops || {};
    if ($("#opsEnabled")) $("#opsEnabled").checked = ops.enabled !== false;
    if ($("#opsMode")) $("#opsMode").value = ops.mode || "swarm";
    if ($("#opsBrowserPreset")) $("#opsBrowserPreset").value = ops.browser_preset || "blitz";
    if ($("#opsRunHttp")) $("#opsRunHttp").checked = ops.run_http_ops !== false;
    if ($("#opsSkipHammer")) $("#opsSkipHammer").checked = !!ops.skip_hammer;
    if ($("#opsMultiHammer")) $("#opsMultiHammer").checked = ops.multi_hammer !== false;
    if ($("#opsPathWorkers")) $("#opsPathWorkers").value = ops.path_workers ?? 16;
    if ($("#opsHammerReq")) $("#opsHammerReq").value = ops.hammer_requests ?? 100;
    if ($("#opsHammerWorkers")) $("#opsHammerWorkers").value = ops.hammer_workers ?? 24;
    if ($("#opsHammerUrl")) $("#opsHammerUrl").value = ops.hammer_url || "";
    if ($("#opsSwarmParallel")) $("#opsSwarmParallel").value = ops.swarm_parallel ?? pj ?? 5;
    if ($("#opsForceParallel")) $("#opsForceParallel").checked = !!ops.force_parallel;
    if ($("#opsWaves")) $("#opsWaves").value = ops.waves ?? 1;
    if ($("#opsIntensity")) $("#opsIntensity").value = ops.intensity ?? 5;
    if ($("#opsExtraPaths")) $("#opsExtraPaths").value = ops.extra_paths_text || (ops.extra_paths || []).join("\n");

    const octo = cfg.octo || {};
    const pos = octo.profile_os || cfg.profile_os || "android";
    if ($("#profileOs")) $("#profileOs").value = pos;
    if ($("#osVersion")) $("#osVersion").value = octo.os_version || "14";
    if ($("#deviceModel")) $("#deviceModel").value = octo.device || "";
    if ($("#profileTags")) {
      $("#profileTags").value = (octo.tags || ["mobile", "auto"]).join(",");
    }
    if ($("#mobileFingerprint")) {
      $("#mobileFingerprint").checked =
        octo.mobile_fingerprint !== false &&
        (cfg.mobile_fingerprint !== false);
    }
    if ($("#matchMobileIp")) {
      $("#matchMobileIp").checked =
        octo.match_mobile_ip !== false && cfg.match_mobile_ip !== false;
    }
    if ($("#trafficMetrics")) {
      $("#trafficMetrics").checked =
        octo.traffic_metrics !== false && cfg.traffic_metrics !== false;
    }
  }

  function renderOpsReport(report) {
    if (!report) {
      if ($("#opsScore")) $("#opsScore").textContent = "—";
      if ($("#opsFindings")) {
        $("#opsFindings").textContent = "아직 리포트 없음";
        $("#opsFindings").classList.add("muted");
      }
      return;
    }
    const score = report.score ?? "—";
    if ($("#opsScore")) {
      $("#opsScore").textContent = score;
      const el = $("#opsScore");
      if (typeof score === "number") {
        el.style.color = score >= 80 ? "#3dd68c" : score >= 50 ? "#f0b429" : "#ff4d6d";
      }
    }
    const findings = report.findings || [];
    const box = $("#opsFindings");
    if (box) {
      box.classList.remove("muted");
      if (!findings.length) {
        box.textContent = `점수 ${score} · findings 없음 · ${report.summary || ""}`;
      } else {
        box.innerHTML = findings
          .map((f) => {
            const sev = (f.severity || "info").toLowerCase();
            return `<div class="sev-${sev}">[${sev.toUpperCase()}] ${esc(f.title || "")}\n  ${esc(f.detail || "")}${f.url ? "\n  @ " + esc(f.url) : ""}</div>`;
          })
          .join("\n");
      }
    }
    const ls = report.load_stats || {};
    if ($("#opsLoadStats")) {
      if (ls.rps != null) {
        $("#opsLoadStats").textContent =
          `LOAD rps=${ls.rps} p95=${ls.latency_ms?.p95 ?? "-"}ms errors=${ls.errors ?? 0} ` +
          `status=${JSON.stringify(ls.status_counts || {})}`;
      } else {
        $("#opsLoadStats").textContent = report.summary || "";
      }
    }
  }

  async function runOpsHttp(mode) {
    try {
      toast("OPS HTTP 실행 중…", "warn");
      const data = await api("/api/ops/run", {
        method: "POST",
        body: JSON.stringify({
          config: collectConfig(),
          mode: mode || $("#opsMode")?.value || "full",
        }),
      });
      renderOpsReport(data.report);
      toast(`OPS 완료 · score=${data.report?.score ?? "?"} · 로그 확인`);
      showPage("ops");
    } catch (e) {
      toast(e.message, "err");
    }
  }

  async function loadOpsLatest() {
    try {
      const data = await api("/api/ops/latest");
      renderOpsReport(data.report);
      if (!data.report) toast("저장된 리포트 없음", "warn");
      else toast("최근 OPS 리포트 로드");
    } catch (e) {
      toast(e.message, "err");
    }
  }

  function applyOpsPreset(name) {
    const map = {
      blitz: {
        mode: "blitz",
        preset: "blitz",
        par: 8,
        waves: 2,
        hammer: false,
        intensity: 5,
        req: 150,
        workers: 32,
      },
      swarm: {
        mode: "swarm",
        preset: "swarm",
        par: 6,
        waves: 1,
        hammer: false,
        intensity: 4,
        req: 100,
        workers: 24,
      },
      hammer: {
        mode: "hammer",
        preset: "hammer",
        par: 5,
        waves: 1,
        hammer: false,
        intensity: 5,
        req: 120,
        workers: 28,
      },
      full: {
        mode: "full",
        preset: "blitz",
        par: 6,
        waves: 2,
        hammer: false,
        intensity: 5,
        req: 120,
        workers: 28,
      },
      recon: {
        mode: "recon",
        preset: "stealth_probe",
        par: 1,
        waves: 1,
        hammer: true,
        intensity: 2,
        req: 40,
        workers: 12,
      },
    };
    const p = map[name] || map.blitz;
    if ($("#opsMode")) {
      const modeEl = $("#opsMode");
      const has = [...modeEl.options].some((o) => o.value === p.mode);
      modeEl.value = has ? p.mode : "full";
    }
    if ($("#opsBrowserPreset")) $("#opsBrowserPreset").value = p.preset;
    if ($("#opsSwarmParallel")) $("#opsSwarmParallel").value = p.par;
    if ($("#opsWaves")) $("#opsWaves").value = p.waves;
    if ($("#opsSkipHammer")) $("#opsSkipHammer").checked = !!p.hammer;
    if ($("#opsForceParallel")) {
      $("#opsForceParallel").checked =
        name === "swarm" || name === "full" || name === "blitz";
    }
    if ($("#opsIntensity")) $("#opsIntensity").value = p.intensity ?? 5;
    if ($("#opsHammerReq") && p.req) $("#opsHammerReq").value = p.req;
    if ($("#opsHammerWorkers") && p.workers) $("#opsHammerWorkers").value = p.workers;
    if ($("#opsMultiHammer")) $("#opsMultiHammer").checked = name !== "recon";
    if ($("#parallelJobs")) $("#parallelJobs").value = p.par;
    if ($("#parallelJobsHome")) $("#parallelJobsHome").value = p.par;
    toast(`OPS 프리셋: ${name.toUpperCase()} (강도 ${p.intensity ?? "-"})`);
  }

  // ── actions ─────────────────────────────────────────────
  async function loadConfig() {
    const data = await api("/api/config");
    applyConfig(data.config);
  }

  async function saveConfig() {
    try {
      const data = await api("/api/config", {
        method: "POST",
        body: JSON.stringify({ data: collectConfig() }),
      });
      applyConfig(data.config);
      toast("설정 저장 완료");
    } catch (e) {
      toast(e.message, "err");
    }
  }

  async function refreshAgentStatus() {
    const els = [$("#agentStatus"), $("#agentStatusHome")].filter(Boolean);
    const led = $("#ledAgent");
    try {
      const data = await api("/api/agent/status");
      const a = data.agent || {};
      const text = a.online
        ? `연결됨 (${a.name || "PC"})`
        : "끊김 — OctoAgent.exe 실행 필요";
      const color = a.online ? "#3dd68c" : "#f5a524";
      els.forEach((el) => {
        el.textContent = text;
        el.style.color = color;
      });
      if (led) {
        led.className = "led " + (a.online ? "on" : "warn");
      }
    } catch (e) {
      els.forEach((el) => {
        el.textContent = "확인 실패";
      });
      if (led) led.className = "led off";
    }
  }

  async function testConnection() {
    try {
      const body = {
        octo_api_token: $("#token").value.trim(),
        cloud_base: ($("#cloudBase")?.value || "https://app.octobrowser.net/api/v2/automation").trim(),
        local_base: ($("#localBase")?.value || "http://127.0.0.1:58888/api").trim(),
        octo_email: $("#octoEmail") ? $("#octoEmail").value.trim() : "",
        octo_password: $("#octoPassword") ? $("#octoPassword").value : "",
        octo_auto_login: $("#octoAutoLogin") ? $("#octoAutoLogin").checked : true,
      };
      const data = await api("/api/test-connection", {
        method: "POST",
        body: JSON.stringify(body),
      });
      const el = $("#connStatus");
      el.textContent = data.cloud_ok ? "● Cloud OK" : data.status;
      el.className = `pill ${data.cloud_ok ? "ok" : "warn"}`;
      const ledC = $("#ledCloud");
      const cloudTxt = $("#cloudLedText");
      if (ledC) ledC.className = "led " + (data.cloud_ok ? "on" : "warn");
      if (cloudTxt) cloudTxt.textContent = data.cloud_ok ? "OK" : "실패";
      toast(data.cloud_ok ? "Cloud 연결 OK · 매크로는 PC OctoAgent 사용" : data.status);
      refreshAgentStatus();
    } catch (e) {
      const el = $("#connStatus");
      el.textContent = `● Offline — ${e.message}`;
      el.className = "pill err";
      const ledC = $("#ledCloud");
      if (ledC) ledC.className = "led off";
      if ($("#cloudLedText")) $("#cloudLedText").textContent = "실패";
      toast(e.message, "err");
    }
  }

  async function validateProxies(fromQuick = false) {
    try {
      const text = fromQuick
        ? $("#proxiesQuick").value
        : ($("#proxiesText").value || $("#proxiesQuick").value);
      const data = await api("/api/proxies/validate", {
        method: "POST",
        body: JSON.stringify({
          text,
          proxy_type: $("#proxyType").value,
        }),
      });
      $("#proxiesText").value = text;
      $("#proxiesQuick").value = text;
      $("#proxyCount").textContent = `${data.count}개`;
      const wrap = $("#proxyList");
      if (!data.proxies.length) {
        wrap.innerHTML = `<p class="muted">유효 프록시 없음${data.errors?.length ? " · 오류 " + data.errors.length + "건" : ""}</p>`;
      } else {
        wrap.innerHTML = `<table><thead><tr><th>#</th><th>proxy</th><th>type</th><th>auth</th></tr></thead><tbody>${
          data.proxies
            .map(
              (p, i) =>
                `<tr><td>${i}</td><td><code>${esc(p.display)}</code></td><td>${esc(p.type)}</td><td>${p.has_auth ? "yes" : "no"}</td></tr>`
            )
            .join("")
        }</tbody></table>`;
      }
      toast(`프록시 ${data.count}개 검증됨`);
    } catch (e) {
      toast(e.message, "err");
    }
  }

  async function parseAccounts() {
    try {
      const data = await api("/api/accounts/parse", {
        method: "POST",
        body: JSON.stringify({ text: $("#accountsBulk").value }),
      });
      if (!data.count) {
        toast("파싱된 계정 없음 — 형식 확인", "err");
        return;
      }
      renderAccounts(data.rows);
      toast(`계정 ${data.count}개 반영`);
    } catch (e) {
      toast(e.message, "err");
    }
  }

  async function startJobs(dryRun) {
    try {
      // sync quick fields
      if ($("#proxiesQuick").value.trim() && !$("#proxiesText").value.trim()) {
        $("#proxiesText").value = $("#proxiesQuick").value;
      }
      const cfg = collectConfig();
      const data = await api("/api/start", {
        method: "POST",
        body: JSON.stringify({
          config: cfg,
          dry_run: !!dryRun,
          proxy_start_index: Number($("#proxyStartIndex").value || 0),
        }),
      });
      setRunning(true);
      toast(dryRun ? "DRY RUN 시작" : "LIVE 시작");
      applyStatus(data.status);
      showPage("logs");
    } catch (e) {
      toast(e.message, "err");
    }
  }

  async function stopJobs() {
    try {
      const data = await api("/api/stop", { method: "POST", body: "{}" });
      toast("중지 요청됨", "warn");
      applyStatus(data.status);
    } catch (e) {
      toast(e.message, "err");
    }
  }

  async function submitOtp() {
    try {
      await api("/api/2fa", {
        method: "POST",
        body: JSON.stringify({ code: $("#otpCode").value }),
      });
      $("#otpCode").value = "";
      toast("2FA 코드 제출");
    } catch (e) {
      toast(e.message, "err");
    }
  }

  function setRunning(on) {
    state.running = on;
    if ($("#btnStart")) $("#btnStart").disabled = on;
    if ($("#btnStartHome")) $("#btnStartHome").disabled = on;
    if ($("#btnStopHome")) $("#btnStopHome").disabled = !on;
    if ($("#btnDry")) $("#btnDry").disabled = on;
    $("#btnStop").disabled = !on;
  }

  function applyStatus(s) {
    if (!s) return;
    setRunning(!!s.running);
    const rs = $("#runStatus");
    rs.textContent = s.status || "대기";
    rs.className = `pill ${s.running ? "ok" : s.error ? "err" : "muted"}`;

    const ledM = $("#ledMacro");
    const macroTxt = $("#macroLedText");
    if (ledM) {
      ledM.className = "led " + (s.running ? "run" : s.error ? "warn" : "off");
    }
    if (macroTxt) {
      macroTxt.textContent = s.running ? "실행 중" : s.error ? "오류" : "대기";
    }

    const p = s.progress || {};
    const parallel = p.parallel || s.progress?.parallel || $("#parallelJobs")?.value || "1";
    const loop = p.loop || "";
    if ($("#liveParallel")) {
      $("#liveParallel").textContent = loop
        ? `L${loop} · ×${parallel}`
        : `×${parallel}`;
    }
    if (p.phase === "agent_queue" || p.phase === "agent") {
      $("#liveStep").textContent = "PC Octo로 전달";
      $("#liveHint").textContent = s.status || "에이전트 대기";
      setMacroStep("login");
    }
    if (p.phase === "start" || p.job || p.phase === "browser") {
      $("#liveStep").textContent = p.total
        ? `작업 ${p.job || "-"} / ${p.total}`
        : `작업 ${p.job || "-"}`;
      $("#live2fa").textContent = p.has_2fa ? "2FA 자동" : "—";
      $("#liveHint").textContent =
        `성공 ${p.success ?? 0} · 실패 ${p.fail ?? 0}` +
        (p.email || p.profile ? ` · ${p.email || p.profile}` : "");
      setMacroStep(p.phase === "browser" ? "search" : "click");
    }
    if (p.phase === "session_done") {
      $("#liveStep").textContent = "완료";
      $("#liveHint").textContent = s.status || "완료";
      setMacroStep("loop");
    }
    if (p.phase === "session_start") {
      $("#liveStep").textContent = `동시 ${p.parallel || parallel} 시작`;
      $("#liveHint").textContent = `총 ${p.total || "?"} · 회차 ${p.loop || 1}`;
      setMacroStep("login");
    }
    if (s.running && !p.phase) {
      setMacroStep("login");
    }
    if (!s.running && !s.error) {
      $$(".macro-step").forEach((el) => el.classList.remove("active"));
    }

    const act = p.active_jobs || [];
    renderWorkerBoard(act, s.running);
    const box = $("#activeJobs");
    if (box) {
      if (act.length) {
        box.classList.add("has-jobs");
        box.classList.remove("muted");
        box.textContent = act
          .map(
            (a) =>
              `● J${a.job} ${(a.profile || a.email || "-").slice(0, 28)} · IP=${a.ip || "?"} · ${a.action || a.phase || "run"} · 클릭=${(a.matched_url || "-").slice(0, 40)}`
          )
          .join("\n");
      } else if (s.running) {
        box.classList.remove("has-jobs");
        box.classList.add("muted");
        box.textContent = "워커 기동 중… (동시 실행 대기)";
      } else {
        box.classList.remove("has-jobs");
        box.classList.add("muted");
        box.textContent = "실행 중 프로필 없음";
      }
    }

    if (p.phase === "wait_2fa" || s.otp_prompt) {
      $("#otpPanel").classList.remove("hidden");
      $("#otpPrompt").textContent = s.otp_prompt || p.otp_prompt || "코드 입력";
      $("#live2fa").textContent = "수동 입력 대기";
    } else if (!s.running) {
      $("#otpPanel").classList.add("hidden");
    }
  }

  function appendLogs(logs) {
    if (!logs?.length) return;
    const view = $("#logView");
    const atBottom =
      view && view.scrollHeight - view.scrollTop - view.clientHeight < 80;
    for (const line of logs) {
      state.lastLogId = Math.max(state.lastLogId, line.id || 0);
      if (view) view.textContent += (view.textContent ? "\n" : "") + line.msg;
      appendBattleLine(line);
    }
    if (view && atBottom) view.scrollTop = view.scrollHeight;
  }

  function startEventStream() {
    if (state.es) {
      try { state.es.close(); } catch { /* */ }
    }
    const es = new EventSource(`/api/logs/stream?after=${state.lastLogId}`);
    state.es = es;
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        appendLogs(data.logs || []);
        applyStatus(data.status);
      } catch {
        /* ignore */
      }
    };
    es.onerror = () => {
      // browser will retry EventSource
    };
  }

  // ── bind ────────────────────────────────────────────────
  function bind() {
    $$(".nav-btn").forEach((b) =>
      b.addEventListener("click", () => showPage(b.dataset.page))
    );
    // home macro buttons mirror topbar
    $("#btnStartHome")?.addEventListener("click", () => $("#btnStart")?.click());
    $("#btnStopHome")?.addEventListener("click", () => $("#btnStop")?.click());
    $("#btnAddAccHome")?.addEventListener("click", () => addAccountRow({}));
    $("#btnClearAccHome")?.addEventListener("click", () => renderAccounts([{}]));
    $("#btnAddKw")?.addEventListener("click", () => {
      addDynRow($("#kwList"), "", "검색어");
      syncKwTextarea();
    });
    $("#btnAddSite")?.addEventListener("click", () => {
      addDynRow($("#siteList"), "", "mysite.com");
      syncSitesTextarea();
    });
    $$(".log-filter").forEach((b) =>
      b.addEventListener("click", () => {
        $$(".log-filter").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        battleFilter = b.dataset.logf || "all";
        paintBattleLog();
      })
    );
    $("#btnClearBattleLog")?.addEventListener("click", () => {
      battleBuf.length = 0;
      paintBattleLog();
      ["statProfile", "statIp", "statClick", "statKw", "statGoogle"].forEach((id) => {
        if ($("#" + id)) $("#" + id).textContent = "—";
      });
    });
    // keep delay fields in sync
    const syncDelay = () => {
      const v = Number($("#delayJobsHome")?.value || $("#delayJobs")?.value || 20);
      if ($("#delayJobsHome")) $("#delayJobsHome").value = v;
      if ($("#delayJobs")) $("#delayJobs").value = v;
    };
    $("#delayJobsHome")?.addEventListener("change", syncDelay);
    $("#delayJobs")?.addEventListener("change", syncDelay);
    $("#gEnabledHome")?.addEventListener("change", () => {
      if ($("#gEnabled")) $("#gEnabled").checked = $("#gEnabledHome").checked;
    });
    $("#btnSave").onclick = saveConfig;
    $("#btnTest").onclick = testConnection;
    if ($("#btnStart")) $("#btnStart").onclick = () => startJobs(false);
    if ($("#btnDry")) $("#btnDry").onclick = () => startJobs(true);
    $("#btnStop").onclick = stopJobs;
    $("#btnValidateProxies").onclick = () => validateProxies(true);
    $("#btnValidateProxies2").onclick = () => validateProxies(false);
    $("#btnParseAccounts").onclick = parseAccounts;
    $("#btnAddAcc").onclick = () => {
      addAccountRow({});
      updateAccountsPreview();
    };
    $("#btnClearAcc").onclick = () => renderAccounts([]);
    $("#btnSubmitOtp").onclick = submitOtp;
    $("#otpCode").addEventListener("keydown", (e) => {
      if (e.key === "Enter") submitOtp();
    });
    $("#btnClearLogs").onclick = async () => {
      await api("/api/logs/clear", { method: "POST", body: "{}" });
      $("#logView").textContent = "";
      state.lastLogId = 0;
    };
    $("#btnToggleToken").onclick = () => {
      const el = $("#token");
      el.type = el.type === "password" ? "text" : "password";
    };
    $("#btnOpsHttp")?.addEventListener("click", () => runOpsHttp($("#opsMode")?.value || "full"));
    $("#btnOpsRunPage")?.addEventListener("click", () => runOpsHttp($("#opsMode")?.value || "full"));
    $("#btnOpsLoadLatest")?.addEventListener("click", loadOpsLatest);
    $("#btnParseBulk")?.addEventListener("click", async () => {
      try {
        const data = await api("/api/bulk/parse", {
          method: "POST",
          body: JSON.stringify({ text: $("#bulkUrlsText")?.value || "" }),
        });
        const s = data.stats || {};
        $("#bulkStats").textContent =
          `도메인 ${s.domains || 0} · URL ${s.full_urls || 0} · path ${s.paths_exact || 0} · regex ${s.path_regexes || 0}`;
        $("#bulkPreview").textContent = (data.sample || []).join("\n") || "샘플 없음";
        toast(`대량 목록 분석: 도메인 ${s.domains || 0}개`);
      } catch (e) {
        toast(e.message, "err");
      }
    });
    $$("[data-ops-preset]").forEach((b) =>
      b.addEventListener("click", () => applyOpsPreset(b.getAttribute("data-ops-preset")))
    );
    // sync parallel home ↔ settings
    const syncParallel = (fromHome) => {
      const v = Number(
        (fromHome ? $("#parallelJobsHome") : $("#parallelJobs"))?.value || 1
      );
      if ($("#parallelJobsHome")) $("#parallelJobsHome").value = v;
      if ($("#parallelJobs")) $("#parallelJobs").value = v;
    };
    $("#parallelJobsHome")?.addEventListener("change", () => syncParallel(true));
    $("#parallelJobs")?.addEventListener("change", () => syncParallel(false));

    $("#btnParseCookies")?.addEventListener("click", async () => {
      try {
        const data = await api("/api/cookies/parse", {
          method: "POST",
          body: JSON.stringify({
            text: $("#cookiesText").value,
            domain: $("#cookieDomain").value || $("#targetDomain").value,
            url: $("#cookieUrl").value,
          }),
        });
        $("#cookiePreview").textContent = data.count
          ? data.cookies
              .slice(0, 20)
              .map((c) => `${c.name}=${String(c.value).slice(0, 24)} · ${c.domain || c.url || ""}`)
              .join("\n") + (data.count > 20 ? `\n…+${data.count - 20}` : "")
          : "파싱된 쿠키 없음";
        toast(`쿠키 ${data.count}개 파싱`);
      } catch (e) {
        toast(e.message, "err");
      }
    });

    // sync quick keyword/domain → detail textareas on blur
    $("#keyword").addEventListener("change", () => {
      if (!$("#keywordsText").value.trim()) {
        $("#keywordsText").value = $("#keyword").value
          .split(/[/\n|]/)
          .map((s) => s.trim())
          .filter(Boolean)
          .join("\n");
      }
    });
    $("#targetDomain").addEventListener("change", () => {
      if (!$("#domainsText").value.trim()) {
        $("#domainsText").value = $("#targetDomain").value.trim();
      }
      if ($("#cookieDomain") && !$("#cookieDomain").value.trim()) {
        $("#cookieDomain").value = $("#targetDomain").value.trim();
      }
    });
    // sync path regex home ↔ search page
    $("#pathRegexQuick")?.addEventListener("change", () => {
      const q = $("#pathRegexQuick").value.trim();
      if (!q) return;
      const ta = $("#pathRegexText");
      if (ta && !ta.value.trim()) ta.value = q;
      else if (ta && !ta.value.split("\n").some((l) => l.trim() === q)) {
        ta.value = q + (ta.value.trim() ? "\n" + ta.value : "");
      }
    });
  }

  async function init() {
    bind();
    showPage("home");
    try {
      await loadConfig();
      startEventStream();
      const st = await api("/api/status");
      applyStatus(st);
      appendLogs(
        (await api(`/api/logs?after=0`)).logs || []
      );
      refreshAgentStatus();
      setInterval(refreshAgentStatus, 5000);
      try {
        const latest = await api("/api/ops/latest");
        if (latest.report) renderOpsReport(latest.report);
      } catch {
        /* ignore */
      }
    } catch (e) {
      toast(`초기화 실패: ${e.message}`, "err");
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();

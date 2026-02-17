const BRAND = "arquati";

const INTERVENTIONS = [
  {
    key: "sistema_ibrido",
    label: "Sistemi ibridi",
    tipologia: "sistema_ibrido",
    catalogType: "sistema-ibrido",
    needsComune: true,
  },
  {
    key: "pompa_riscaldamento",
    label: "Pompe di calore",
    tipologia: "pompa_calore",
    catalogType: "pompa-calore",
    needsComune: true,
    filterModel: (m) => String(m?.fields?.tipologia_scambio || "") !== "aria_aria",
  },
  {
    key: "condizionatori",
    label: "Condizionatori",
    tipologia: "pompa_calore",
    catalogType: "pompa-calore",
    needsComune: true,
    filterModel: (m) => String(m?.fields?.tipologia_scambio || "") === "aria_aria",
  },
  {
    key: "scaldacqua",
    label: "Scaldacqua",
    tipologia: "scaldacqua",
    catalogType: "scaldacqua",
    needsComune: false,
  },
  {
    key: "solare_factory",
    label: "Solare termico",
    tipologia: "solare_termico",
    catalogType: "solare-termico",
    needsComune: false,
    filterModel: (m) => String(m?.fields?.tipo_collettori || "") === "factory_made",
  },
];

const $ = (id) => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Missing element: #${id}`);
  return el;
};

const fmtCurrency = new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR" });

function normalizeForSearch(input) {
  return String(input || "")
    .trim()
    .toLowerCase()
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .replace(/[’`]/g, "'")
    .replace(/[^a-z0-9\s']/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function parseNumberLike(input) {
  if (typeof input === "number" && Number.isFinite(input)) return input;
  if (input === null || input === undefined) return 0;
  let s = String(input).trim();
  if (!s) return 0;

  s = s.replace(/\s/g, "");
  if (s.includes(",") && s.includes(".")) {
    s = s.replace(/\./g, "").replace(",", ".");
  } else if (s.includes(",")) {
    s = s.replace(",", ".");
  }
  s = s.replace(/[^0-9.-]/g, "");

  const n = parseFloat(s);
  return Number.isFinite(n) ? n : 0;
}

function formatCurrency(n) {
  const v = Number.isFinite(n) ? n : 0;
  return fmtCurrency.format(v);
}

function formatPercentLike(input) {
  const n = parseNumberLike(input);
  return `${n.toFixed(1).replace(".", ",")}%`;
}

function scrollIntoViewSmart(el) {
  if (!el) return;
  const reduceMotion =
    typeof window !== "undefined" &&
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
}

function debounce(fn, delayMs) {
  let t = null;
  return (...args) => {
    if (t) clearTimeout(t);
    t = setTimeout(() => fn(...args), delayMs);
  };
}

async function fetchJson(url, opts = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30_000);
  try {
    const res = await fetch(url, {
      ...opts,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(opts.headers || {}),
      },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(timeout);
  }
}

const dom = {
  interventions: $("interventions"),
  modelInput: $("modelInput"),
  modelSuggestions: $("modelSuggestions"),
  clearModelBtn: $("clearModelBtn"),
  modelMeta: $("modelMeta"),

  invoiceInput: $("invoiceInput"),

  comuneField: $("comuneField"),
  comuneInput: $("comuneInput"),
  comuneSuggestions: $("comuneSuggestions"),
  clearComuneBtn: $("clearComuneBtn"),
  comuneMeta: $("comuneMeta"),
  zonaSelect: $("zonaSelect"),

  calculateBtn: $("calculateBtn"),
  status: $("status"),

  results: $("results"),
  resultsBody: $("resultsBody"),
  warnings: $("warnings"),
  resetBtn: $("resetBtn"),
};

const state = {
  activeIntervention: INTERVENTIONS[0],
  catalogs: new Map(), // catalogType -> array
  availableModels: [],
  selectedModel: null,
  selectedCity: null,
  zonaClimatica: "E",
};

function setStatus(kind, msg) {
  dom.status.textContent = msg || "";
  if (kind) dom.status.dataset.kind = kind;
  else delete dom.status.dataset.kind;
}

function openSuggestions(container, open) {
  if (open) container.dataset.open = "true";
  else delete container.dataset.open;
}

function clearModelSelection() {
  state.selectedModel = null;
  dom.modelInput.value = "";
  dom.modelMeta.textContent = "";
  openSuggestions(dom.modelSuggestions, false);
}

function clearComuneSelection() {
  state.selectedCity = null;
  dom.comuneInput.value = "";
  dom.comuneMeta.textContent = "";
  // Keep zona as-is; user might want manual input.
  openSuggestions(dom.comuneSuggestions, false);
}

function modelMetaLine(intervention, model) {
  const f = model?.fields || {};
  if (!intervention || !model) return "";

  if (intervention.tipologia === "scaldacqua") {
    const cls = String(f.classe_energetica || "").toUpperCase() || "-";
    const cap = String(f.capacita_accumulo || "").trim();
    return `Dettagli: Classe ${cls}${cap ? ` | Capacità ${cap}L` : ""}`;
  }

  if (intervention.tipologia === "solare_termico") {
    const tipo = String(f.tipo_collettori || "").replace(/_/g, " ");
    const e = parseNumberLike(f.energia_termica);
    return `Dettagli: Collettori ${tipo || "-"}${e ? ` | Energia ${e}` : ""}`;
  }

  if (intervention.tipologia === "pompa_calore") {
    const p = parseNumberLike(f.potenza_nominale);
    const eff = parseNumberLike(f.efficienza_stagionale);
    const scop = parseNumberLike(f.scop_sper_cop);
    const scambio = String(f.tipologia_scambio || "").replace(/_/g, " ");
    return `Dettagli: ${scambio || "pompa di calore"}${p ? ` | ${p} kW` : ""}${eff ? ` | Eff. ${eff}%` : ""}${scop ? ` | SCOP ${scop}` : ""}`;
  }

  if (intervention.tipologia === "sistema_ibrido") {
    const p = parseNumberLike(f.pdc_potenza);
    const eff = parseNumberLike(f.pdc_efficienza);
    const scop = parseNumberLike(f.pdc_scop_sper_cop);
    return `Dettagli: PdC${p ? ` ${p} kW` : ""}${eff ? ` | Eff. ${eff}%` : ""}${scop ? ` | SCOP ${scop}` : ""}`;
  }

  return "";
}

function renderSuggestions(container, items, toLabel, toSub, onPick) {
  container.innerHTML = "";

  if (!items.length) {
    openSuggestions(container, false);
    return;
  }

  for (const it of items) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "sg-item";
    btn.setAttribute("role", "option");

    const main = document.createElement("span");
    main.className = "sg-main";
    main.textContent = toLabel(it);

    const sub = document.createElement("span");
    sub.className = "sg-sub";
    sub.textContent = toSub(it);

    btn.appendChild(main);
    btn.appendChild(sub);

    btn.addEventListener("click", () => onPick(it));
    container.appendChild(btn);
  }

  openSuggestions(container, true);
}

function setActiveIntervention(next) {
  state.activeIntervention = next;
  clearModelSelection();
  setStatus(null, "");
  dom.results.hidden = true;
  dom.warnings.hidden = true;
  dom.warnings.textContent = "";

  // Comune required only for some tipologie.
  dom.comuneField.hidden = !next.needsComune;
  if (!next.needsComune) clearComuneSelection();

  // Update pressed state
  for (const btn of dom.interventions.querySelectorAll("button[data-key]")) {
    btn.setAttribute("aria-pressed", btn.dataset.key === next.key ? "true" : "false");
  }

  loadCatalogFor(next).catch((e) => setStatus("error", `Errore catalogo: ${e.message}`));
}

async function loadCatalogFor(intervention) {
  const cacheKey = intervention.catalogType;
  if (!state.catalogs.has(cacheKey)) {
    setStatus(null, "Caricamento modelli...");
    const resp = await fetchJson(`/api/public/catalog/${encodeURIComponent(cacheKey)}/${encodeURIComponent(BRAND)}`);
    const arr = Array.isArray(resp?.data) ? resp.data : [];
    state.catalogs.set(cacheKey, arr);
    setStatus(null, "");
  }

  const raw = state.catalogs.get(cacheKey) || [];
  const filtered = typeof intervention.filterModel === "function" ? raw.filter(intervention.filterModel) : [...raw];
  filtered.sort((a, b) => {
    const la = String(a?.label || "");
    const lb = String(b?.label || "");
    return la.localeCompare(lb, "it", { sensitivity: "base" });
  });
  state.availableModels = filtered;
}

function renderInterventions() {
  dom.interventions.innerHTML = "";
  for (const iv of INTERVENTIONS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "iv-card";
    btn.dataset.key = iv.key;
    btn.setAttribute("aria-pressed", iv.key === state.activeIntervention.key ? "true" : "false");

    const t = document.createElement("div");
    t.className = "iv-title";
    t.textContent = iv.label;

    btn.appendChild(t);
    btn.addEventListener("click", () => setActiveIntervention(iv));
    dom.interventions.appendChild(btn);
  }
}

const updateModelSuggestions = debounce(() => {
  const q = normalizeForSearch(dom.modelInput.value);
  if (!state.availableModels.length) {
    openSuggestions(dom.modelSuggestions, false);
    return;
  }

  let out = [];
  if (!q) {
    out = state.availableModels.slice(0, 40);
  } else {
    const starts = [];
    const contains = [];
    for (const m of state.availableModels) {
      const label = String(m?.label || "");
      if (!label) continue;
      const ln = normalizeForSearch(label);
      if (ln.startsWith(q)) starts.push(m);
      else if (ln.includes(q)) contains.push(m);
      if (starts.length + contains.length >= 60) break;
    }
    out = [...starts, ...contains].slice(0, 40);
  }

  renderSuggestions(
    dom.modelSuggestions,
    out,
    (m) => String(m.label || ""),
    (m) => String(m?.fields?.marca || ""),
    (m) => {
      state.selectedModel = m;
      dom.modelInput.value = String(m.label || "");
      dom.modelMeta.textContent = modelMetaLine(state.activeIntervention, m);
      openSuggestions(dom.modelSuggestions, false);
    },
  );
}, 120);

const updateComuneSuggestions = debounce(async () => {
  const q = normalizeForSearch(dom.comuneInput.value);
  try {
    const resp = await fetchJson(`/api/public/cities?q=${encodeURIComponent(q)}`);
    const arr = Array.isArray(resp) ? resp : [];
    renderSuggestions(
      dom.comuneSuggestions,
      arr.slice(0, 50),
      (c) => String(c.comune || ""),
      (c) => String(c.codice_provincia || ""),
      (c) => {
        state.selectedCity = c;
        dom.comuneInput.value = `${c.comune} (${c.codice_provincia || "-"})`;
        const z = String(c.zona_climatica || "").toUpperCase();
        if (["A", "B", "C", "D", "E", "F"].includes(z)) {
          state.zonaClimatica = z;
          dom.zonaSelect.value = z;
        }
        dom.comuneMeta.textContent = c.regione ? `${c.regione}${c.provincia ? `, ${c.provincia}` : ""}` : "";
        openSuggestions(dom.comuneSuggestions, false);
      },
    );
  } catch (e) {
    // Do not fail hard while typing.
    openSuggestions(dom.comuneSuggestions, false);
  }
}, 160);

function buildPayload() {
  const iv = state.activeIntervention;
  const tipologia = iv.tipologia;

  const invoice = parseNumberLike(dom.invoiceInput.value);
  const modelFields = state.selectedModel?.fields || null;

  const payload = {
    intervention: { tipologia: [tipologia] },
    value_vat: { [tipologia]: invoice },
    dati_tecnici: { [tipologia]: modelFields ? [modelFields] : [] },
  };

  if (iv.needsComune) {
    payload.property = { address: { immobile: { zona_climatica: state.zonaClimatica } } };
  }

  return payload;
}

function validateBeforeCalculate() {
  const iv = state.activeIntervention;
  if (!iv) return "Seleziona una tipologia di intervento.";
  if (!state.selectedModel) return "Seleziona un modello.";

  const invoice = parseNumberLike(dom.invoiceInput.value);
  if (!invoice || invoice <= 0) return "Inserisci un importo fattura valido.";

  if (iv.needsComune && !state.selectedCity) return "Seleziona un comune dalla lista.";
  return null;
}

async function calculate() {
  const err = validateBeforeCalculate();
  if (err) {
    setStatus("error", err);
    return;
  }

  setStatus(null, "Calcolo in corso...");
  dom.calculateBtn.disabled = true;

  try {
    const payload = buildPayload();
    const resp = await fetchJson("/api/public/calculate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!resp?.success) {
      throw new Error(String(resp?.error || "Calcolo non riuscito"));
    }

    const tipologia = state.activeIntervention.tipologia;
    const row = resp?.data?.[tipologia];
    if (!row) throw new Error("Risposta incompleta dal server.");

    dom.resultsBody.innerHTML = "";
    const tr = document.createElement("tr");

    const cells = [
      state.activeIntervention.label,
      formatCurrency(parseNumberLike(row.totale_vat)),
      formatCurrency(parseNumberLike(row.incentivo_lordo)),
      formatCurrency(parseNumberLike(row.incentivo_netto)),
      formatPercentLike(row.percent_reale),
    ];

    cells.forEach((text, idx) => {
      const td = document.createElement("td");
      td.textContent = text;
      if (idx === 2 || idx === 3) td.className = "money strong";
      if (idx === 1) td.className = "money";
      tr.appendChild(td);
    });

    dom.resultsBody.appendChild(tr);
    dom.results.hidden = false;
    // Auto-scroll to show results after a successful calculation.
    scrollIntoViewSmart(dom.results);

    if (row.warnings) {
      dom.warnings.textContent = String(row.warnings);
      dom.warnings.hidden = false;
    } else {
      dom.warnings.textContent = "";
      dom.warnings.hidden = true;
    }

    setStatus(null, "");
  } catch (e) {
    setStatus("error", `Errore: ${e.message}`);
  } finally {
    dom.calculateBtn.disabled = false;
  }
}

function resetAll() {
  clearModelSelection();
  clearComuneSelection();
  dom.invoiceInput.value = "";
  state.zonaClimatica = "E";
  dom.zonaSelect.value = "E";
  dom.results.hidden = true;
  dom.warnings.hidden = true;
  dom.warnings.textContent = "";
  setStatus(null, "");
}

function closeAllSuggestionsIfClickedOutside(ev) {
  const t = ev.target;
  if (!(t instanceof HTMLElement)) return;

  const inModel = dom.modelSuggestions.contains(t) || dom.modelInput.contains(t) || dom.clearModelBtn.contains(t);
  if (!inModel) openSuggestions(dom.modelSuggestions, false);

  const inComune =
    dom.comuneSuggestions.contains(t) || dom.comuneInput.contains(t) || dom.clearComuneBtn.contains(t);
  if (!inComune) openSuggestions(dom.comuneSuggestions, false);
}

document.addEventListener("DOMContentLoaded", () => {
  renderInterventions();
  setActiveIntervention(state.activeIntervention);

  dom.modelInput.addEventListener("input", () => {
    // If user edits after selecting, drop selection.
    state.selectedModel = null;
    dom.modelMeta.textContent = "";
    updateModelSuggestions();
  });

  dom.modelInput.addEventListener("focus", () => updateModelSuggestions());
  dom.clearModelBtn.addEventListener("click", () => clearModelSelection());

  dom.invoiceInput.addEventListener("blur", () => {
    const n = parseNumberLike(dom.invoiceInput.value);
    if (n > 0) dom.invoiceInput.value = formatCurrency(n);
  });

  dom.invoiceInput.addEventListener("focus", () => {
    // On focus, strip formatting for easier edit.
    const n = parseNumberLike(dom.invoiceInput.value);
    dom.invoiceInput.value = n ? String(n).replace(".", ",") : "";
  });

  dom.comuneInput.addEventListener("input", () => {
    state.selectedCity = null;
    dom.comuneMeta.textContent = "";
    updateComuneSuggestions();
  });

  dom.comuneInput.addEventListener("focus", () => updateComuneSuggestions());
  dom.clearComuneBtn.addEventListener("click", () => clearComuneSelection());

  dom.zonaSelect.addEventListener("change", () => {
    const v = String(dom.zonaSelect.value || "E").toUpperCase();
    state.zonaClimatica = ["A", "B", "C", "D", "E", "F"].includes(v) ? v : "E";
    dom.zonaSelect.value = state.zonaClimatica;
  });

  dom.calculateBtn.addEventListener("click", () => calculate());
  dom.resetBtn.addEventListener("click", () => resetAll());

  document.addEventListener("click", closeAllSuggestionsIfClickedOutside, true);
});

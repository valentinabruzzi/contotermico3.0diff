#!/usr/bin/env node
/* eslint-disable no-console */

const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = parseInt(process.env.PORT || "8000", 10);
const HOST = process.env.HOST || "127.0.0.1";

const STATIC_ROOT = path.resolve(__dirname, "..", "simulatorecontotermico-arquati.com");
const CITIES_PATH = path.resolve(__dirname, "data", "geo", "cities.json");

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(body);
}

function sendText(res, status, text) {
  res.writeHead(status, {
    "Content-Type": "text/plain; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(text);
}

function normalizeForSearch(input) {
  const s = String(input || "")
    .trim()
    .toLowerCase()
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "");

  return s
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

function round2(n) {
  return Math.round((n + Number.EPSILON) * 100) / 100;
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function interpolate(points, x) {
  const v = parseNumberLike(x);
  if (points.length === 0) return 1;
  if (v <= points[0][0]) return points[0][1];
  if (v >= points[points.length - 1][0]) return points[points.length - 1][1];

  for (let i = 0; i < points.length - 1; i++) {
    const [x0, y0] = points[i];
    const [x1, y1] = points[i + 1];
    if (v >= x0 && v <= x1) {
      const t = (v - x0) / (x1 - x0);
      return lerp(y0, y1, t);
    }
  }
  return 1;
}

const PDC_K_ZONA = {
  A: 116.09,
  B: 164.47,
  C: 212.84,
  D: 270.88,
  E: 328.93,
  F: 348.28,
};

const IBRIDO_K_ZONA = {
  A: 147.57,
  B: 209.06,
  C: 270.54,
  D: 344.33,
  E: 418.11,
  F: 442.71,
};

const G_PDC_POINTS = [
  [2.0, 0.6202],
  [3.0, 0.8269],
  [3.5, 0.886],
  [4.0, 0.9303],
  [5.16, 1.0],
  [6.0, 1.0337],
  [7.0, 1.0632],
  [10.0, 1.1163],
];

const G_IBRIDO_POINTS = [
  [2.0, 0.6931],
  [3.0, 0.9241],
  [3.5, 0.9901],
  [3.59, 1.0],
  [4.0, 1.0396],
  [5.0, 1.1089],
  [6.0, 1.1551],
  [10.0, 1.2475],
];

function computeIncentiveForTipologia(tipologia, payload) {
  const valueVat = payload?.value_vat?.[tipologia];
  const totale_vat = parseNumberLike(valueVat);

  const tipData = Array.isArray(payload?.dati_tecnici?.[tipologia])
    ? payload.dati_tecnici[tipologia][0] || {}
    : {};

  // If address autocomplete is not wired yet, default to "E".
  const zona = String(payload?.property?.address?.immobile?.zona_climatica || "E")
    .trim()
    .toUpperCase();

  let incentivo_max = 0;
  let percentuale = 0;

  if (tipologia === "scaldacqua") {
    percentuale = 0.4;
    // Keep "+" so "a+" doesn't get normalized to just "a".
    const cls = String(tipData?.classe_energetica || "")
      .trim()
      .toLowerCase()
      .replace(/\s+/g, "");
    const max = cls.startsWith("a+") ? 700 : cls.startsWith("a") ? 500 : 0;
    incentivo_max = max;
  } else if (tipologia === "solare_termico") {
    percentuale = 0.65;
    const energia = parseNumberLike(tipData?.energia_termica);
    const tipo = String(tipData?.tipo_collettori || "").trim().toLowerCase();
    const k = tipo === "piani" ? 0.7 : tipo === "factory_made" ? 0.1945 : 0;
    incentivo_max = energia * k;
  } else if (tipologia === "pompa_calore") {
    percentuale = 0.65;
    const potenza = parseNumberLike(tipData?.potenza_nominale);
    const eff = parseNumberLike(tipData?.efficienza_stagionale) / 100;
    const scop = parseNumberLike(tipData?.scop_sper_cop);
    const kZona = PDC_K_ZONA[zona] || PDC_K_ZONA.E;
    incentivo_max = potenza * eff * kZona * interpolate(G_PDC_POINTS, scop);
  } else if (tipologia === "sistema_ibrido") {
    percentuale = 0.65;
    const potenza = parseNumberLike(tipData?.pdc_potenza);
    const eff = parseNumberLike(tipData?.pdc_efficienza) / 100;
    const scop = parseNumberLike(tipData?.pdc_scop_sper_cop);
    const kZona = IBRIDO_K_ZONA[zona] || IBRIDO_K_ZONA.E;
    incentivo_max = potenza * eff * kZona * interpolate(G_IBRIDO_POINTS, scop);
  } else {
    // Non implementato: restituiamo 0 ma con warning (così l'UI resta consistente).
    return {
      totale_vat: round2(totale_vat),
      incentivo_lordo: 0,
      incentivo_netto: 0,
      percent_reale: "0.0",
      warnings: `Tipologia '${tipologia}' non ancora implementata nel backend Arquati.`,
    };
  }

  const limitePercentuale = percentuale * totale_vat;
  const incentivo_lordo = Math.min(incentivo_max, limitePercentuale);
  const incentivo_netto = incentivo_lordo * 0.9878;

  const percent_reale = totale_vat > 0 ? (incentivo_lordo / totale_vat) * 100 : 0;

  return {
    totale_vat: round2(totale_vat),
    incentivo_lordo: round2(incentivo_lordo),
    incentivo_netto: round2(incentivo_netto),
    percent_reale: percent_reale.toFixed(1),
  };
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk;
      if (raw.length > 2_000_000) {
        reject(new Error("Request body too large"));
      }
    });
    req.on("end", () => {
      if (!raw) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch (e) {
        reject(e);
      }
    });
  });
}

function safePathJoin(root, reqPath) {
  // Strip query string and decode
  const clean = decodeURIComponent(reqPath.split("?")[0]);
  const joined = path.resolve(root, "." + clean);
  if (!joined.startsWith(root)) return null;
  return joined;
}

const CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".woff2": "font/woff2",
};

function serveStatic(req, res) {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  let pathname = url.pathname;
  if (pathname === "/") pathname = "/index.html";

  const filePath = safePathJoin(STATIC_ROOT, pathname);
  if (!filePath) return sendText(res, 404, "Not found");

  fs.stat(filePath, (err, stat) => {
    if (err || !stat.isFile()) return sendText(res, 404, "Not found");

    const ext = path.extname(filePath).toLowerCase();
    const contentType = CONTENT_TYPES[ext] || "application/octet-stream";

    // Favor correctness over aggressive caching (dev tunnel).
    // Avoid stale assets during development/tunnel sharing.
    const noStoreExts = new Set([".html", ".js", ".css", ".json", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".woff2"]);
    res.writeHead(200, {
      "Content-Type": contentType,
      "Cache-Control": noStoreExts.has(ext) ? "no-store" : "public, max-age=3600",
    });

    if (req.method === "HEAD") return res.end();

    fs.createReadStream(filePath).pipe(res);
  });
}

function loadCities() {
  const raw = fs.readFileSync(CITIES_PATH, "utf-8");
  const cities = JSON.parse(raw);
  return cities.map((c) => ({
    ...c,
    _comune_norm: normalizeForSearch(c.comune),
    _prov_norm: normalizeForSearch(c.provincia),
    _sigla: String(c.codice_provincia || "").toUpperCase(),
  }));
}

let CITIES = [];
try {
  CITIES = loadCities();
  console.log(`Loaded ${CITIES.length} comuni from ${CITIES_PATH}`);
} catch (e) {
  console.warn(`Unable to load cities dataset: ${e.message}`);
}

async function handleApi(req, res) {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  const pathname = url.pathname;

  if (req.method === "GET" && pathname === "/api/public/cities") {
    const q = url.searchParams.get("q") || "";
    const codice_provincia = (url.searchParams.get("codice_provincia") || "").toUpperCase();
    const qn = normalizeForSearch(q);

    const starts = [];
    const contains = [];
    for (const city of CITIES) {
      if (codice_provincia && city._sigla !== codice_provincia) continue;
      if (!qn) {
        // When query is empty, return an alphabetical list (limited).
        starts.push(city);
      } else if (city._comune_norm.startsWith(qn)) starts.push(city);
      else if (city._comune_norm.includes(qn)) contains.push(city);
    }

    const sorter = (a, b) => {
      if (a._comune_norm < b._comune_norm) return -1;
      if (a._comune_norm > b._comune_norm) return 1;
      return 0;
    };

    const out = [...starts.sort(sorter), ...contains.sort(sorter)]
      .slice(0, 50)
      .map(({ _comune_norm, _prov_norm, _sigla, ...rest }) => rest);

    return sendJson(res, 200, out);
  }

  if (req.method === "GET" && pathname === "/api/public/dual-climatic-zones") {
    return sendJson(res, 200, { hasDualZone: false });
  }

  if (req.method === "GET" && pathname.startsWith("/api/public/catalog/")) {
    const parts = pathname.split("/").filter(Boolean);
    // ["api","public","catalog",":catalogType",":brand"]
    if (parts.length !== 5) return sendJson(res, 200, { success: false, error: "Invalid catalog path" });
    const catalogType = parts[3];
    const brand = parts[4];

    const catalogFile = path.resolve(__dirname, "data", "catalog", catalogType, `${brand}.json`);
    let data = [];
    try {
      data = JSON.parse(fs.readFileSync(catalogFile, "utf-8"));
      if (!Array.isArray(data)) data = [];
    } catch {
      data = [];
    }

    const resp = {
      success: true,
      tipologia: catalogType,
      brand,
      count: data.length,
      data,
      ...(data.length === 0 ? { message: `Nessun template trovato per la marca '${brand}'.` } : null),
    };
    return sendJson(res, 200, resp);
  }

  if (req.method === "POST" && pathname === "/api/public/calculate") {
    let body;
    try {
      body = await readJsonBody(req);
    } catch (e) {
      return sendJson(res, 200, { success: false, error: "JSON non valido" });
    }

    const tipologie = Array.isArray(body?.intervention?.tipologia) ? body.intervention.tipologia : [];
    if (tipologie.length === 0) {
      return sendJson(res, 200, { success: false, error: "Nessuna tipologia di intervento selezionata." });
    }

    const data = {};
    for (const tip of tipologie) {
      data[tip] = computeIncentiveForTipologia(tip, body);
    }

    return sendJson(res, 200, { success: true, data });
  }

  return sendJson(res, 404, { success: false, error: "Not found" });
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.url && req.url.startsWith("/api/")) return await handleApi(req, res);
    return serveStatic(req, res);
  } catch (e) {
    console.error("Unhandled error:", e);
    return sendJson(res, 500, { success: false, error: "Internal server error" });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`Arquati server running on http://${HOST}:${PORT}`);
  console.log(`Serving static from ${STATIC_ROOT}`);
});

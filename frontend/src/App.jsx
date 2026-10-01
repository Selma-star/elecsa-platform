import { useState, Fragment } from "react";
import axios from "axios";
import "./App.css";

// On Render, frontend and backend are on two different onrender.com
// domains (no shared reverse proxy like Caddy) - so the backend needs
// its real public URL, set at build time via VITE_BACKEND_URL. Locally,
// that env var is unset, BACKEND_URL falls back to "", and the Vite dev
// server proxies relative "/api/..." calls to localhost:4000 instead
// (see vite.config.js) - same code either way, no URL to hand-edit.
const _rawBackendUrl = import.meta.env.VITE_BACKEND_URL || "";
const BACKEND_URL =
  _rawBackendUrl && !_rawBackendUrl.startsWith("http")
    ? `https://${_rawBackendUrl}`
    : _rawBackendUrl;

// Default header fields, grouped into rows the same way the real
// Sonelgaz devis groups them (several fields per line). Ali edits,
// adds, or deletes these per document - this is just the starting shape.
const DEFAULT_HEADER_ROWS = [
  [
    { label: "AO N°", value: "" },
    { label: "DU", value: "" },
    { label: "LOT N°", value: "" },
  ],
  [
    { label: "Affaire N°", value: "" },
    { label: "DELAI", value: "" },
  ],
  [{ label: "Intitulé de l'Affaire", value: "" }],
  [{ label: "Nature de l'Affaire", value: "" }],
  [
    { label: "Nom du Client", value: "" },
    { label: "Commune", value: "" },
  ],
  [{ label: "Type terrain", value: "" }],
];

const fmt = (n) =>
  n.toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const toNumber = (v) => {
  const n = parseFloat(String(v ?? "").replace(",", "."));
  return isNaN(n) ? 0 : n;
};

// French number-to-words, verified against two real Sonelgaz devis totals.
// Handles the real grammar traps: "cents"/"vingts" only take an 's' when
// they're the very last word of the whole number (nothing after them,
// not even "mille") - e.g. "neuf cents" (900, final) but "six cent
// quarante-huit mille" (648,000 - no 's', "mille" follows). "mille"
// itself never pluralizes; "million"/"milliard" do.
const ONES_FR = ["zéro","un","deux","trois","quatre","cinq","six","sept","huit","neuf",
  "dix","onze","douze","treize","quatorze","quinze","seize","dix-sept","dix-huit","dix-neuf"];
const TENS_FR = ["", "", "vingt","trente","quarante","cinquante","soixante","soixante-dix","quatre-vingt","quatre-vingt-dix"];

function twoDigitsToWords(n, isUnitsGroup) {
  if (n === 0) return "";
  if (n < 20) return ONES_FR[n];
  const t = Math.floor(n / 10);
  const u = n % 10;
  if (t === 7 || t === 9) {
    const base = t === 7 ? "soixante" : "quatre-vingt";
    if (u === 0) return base + "-dix";
    if (u === 1) return base + (t === 7 ? " et onze" : "-onze");
    return base + "-" + ONES_FR[10 + u];
  }
  if (t === 8) {
    if (u === 0) return isUnitsGroup ? "quatre-vingts" : "quatre-vingt";
    return "quatre-vingt-" + ONES_FR[u];
  }
  if (u === 0) return TENS_FR[t];
  if (u === 1) return TENS_FR[t] + " et un";
  return TENS_FR[t] + "-" + ONES_FR[u];
}

function frenchGroupWords(n, isUnitsGroup) {
  if (n === 0) return "";
  const hundreds = Math.floor(n / 100);
  const rest = n % 100;
  let words = "";
  if (hundreds > 0) {
    words = hundreds === 1 ? "cent" : ONES_FR[hundreds] + " cent";
    if (rest === 0 && hundreds > 1 && isUnitsGroup) words += "s";
  }
  if (rest > 0) {
    if (words) words += " ";
    words += twoDigitsToWords(rest, isUnitsGroup);
  }
  return words;
}

function numberToFrenchWords(num) {
  num = Math.floor(Math.abs(num));
  if (num === 0) return "zéro";
  const milliard = Math.floor(num / 1e9);
  const million = Math.floor((num % 1e9) / 1e6);
  const mille = Math.floor((num % 1e6) / 1e3);
  const unites = num % 1000;
  const parts = [];
  if (milliard > 0) {
    const w = frenchGroupWords(milliard, false);
    parts.push((milliard === 1 ? "un" : w) + " milliard" + (milliard > 1 ? "s" : ""));
  }
  if (million > 0) {
    const w = frenchGroupWords(million, false);
    parts.push((million === 1 ? "un" : w) + " million" + (million > 1 ? "s" : ""));
  }
  if (mille > 0) {
    const w = frenchGroupWords(mille, false);
    parts.push(mille === 1 ? "mille" : w + " mille");
  }
  if (unites > 0 || parts.length === 0) {
    parts.push(frenchGroupWords(unites, true));
  }
  return parts.join(" ").replace(/\s+/g, " ").trim();
}

const capitalize = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

// Groups extracted entries into sections, matching the real document's
// sub-tables (e.g. M'sila's Armoire A/B/C/D/E, or LOT07's two equipment
// groups) instead of flattening everything into one undifferentiated
// list. Every item carries BOTH pricing models' fields at once, so
// switching models never loses what was already typed in.
function groupEntries(result) {
  if (!result) return [];
  if (result.source_type === "native") {
    const sections = [];
    let current = null;
    for (const e of result.entries) {
      if (e.type === "section") {
        current = { label: e.label, items: [] };
        sections.push(current);
      } else if (e.type === "item") {
        if (!current) {
          current = { label: null, items: [] };
          sections.push(current);
        }
        current.items.push({
          description: e.label,
          unit: e.unit || "",
          qty: e.qty ?? "",
          pu: "",
          puFour: "",
          puMeo: "",
        });
      }
    }
    return sections;
  }
  if (result.source_type === "scanned_ocr") {
    return [
      {
        label: null,
        items: result.entries.map((e) => ({
          description: e.description,
          unit: "",
          qty: Object.values(e.columns || {})[0] || "",
          pu: "",
          puFour: "",
          puMeo: "",
        })),
      },
    ];
  }
  return [];
}

function LoginScreen({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await axios.post(`${BACKEND_URL}/api/login`, { username, password });
      onLogin(response.data.token);
    } catch (err) {
      setError(err.response?.data?.error || "Could not reach the backend. Is it running?");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <form className="login-box" onSubmit={handleSubmit}>
        <div className="wordmark login-wordmark">
          <span className="wordmark-main">ELECSA</span>
          <span className="wordmark-sub">Devis Engine</span>
        </div>
        <label className="login-field">
          <span>Nom d'utilisateur</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
          />
        </label>
        <label className="login-field">
          <span>Mot de passe</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <div className="error">{error}</div>}
        <button className="btn-primary login-submit" type="submit" disabled={loading}>
          {loading ? "Connexion..." : "Se connecter"}
        </button>
      </form>
    </div>
  );
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem("elecsa_token"));
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [downloadingPdf, setDownloadingPdf] = useState(false);
  const [downloadingExcel, setDownloadingExcel] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [sections, setSections] = useState([]);
  const [headerRows, setHeaderRows] = useState(DEFAULT_HEADER_ROWS);
  const [model, setModel] = useState("A"); // "A" = PU/Montant, "B" = Fourniture/MEO
  const [arreteOverride, setArreteOverride] = useState(null);

  const handleLogin = (newToken) => {
    localStorage.setItem("elecsa_token", newToken);
    setToken(newToken);
  };

  const handleLogout = () => {
    localStorage.removeItem("elecsa_token");
    setToken(null);
  };

  // Any protected call that comes back 401 means the session expired
  // (or the token is otherwise invalid) - send the user back to login
  // rather than showing a confusing generic error.
  const handleAuthError = (err) => {
    if (err.response?.status === 401) {
      handleLogout();
      return true;
    }
    return false;
  };

  const handleFileChange = (e) => {
    setFile(e.target.files[0] || null);
    setResult(null);
    setSections([]);
    setError(null);
    setArreteOverride(null);
  };

  const handleExtract = async () => {
    if (!file) return;
    setLoading(true);
    setError(null);
    setResult(null);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const response = await axios.post(`${BACKEND_URL}/api/extract`, formData, {
        headers: { "Content-Type": "multipart/form-data", Authorization: `Bearer ${token}` },
      });
      setResult(response.data);
      setSections(groupEntries(response.data));
      setHeaderRows(DEFAULT_HEADER_ROWS.map((row) => row.map((f) => ({ ...f }))));
      setModel("A");
      setArreteOverride(null);
    } catch (err) {
      if (!handleAuthError(err)) {
        setError(err.response?.data?.error || "Could not reach the backend. Is it running?");
      }
    } finally {
      setLoading(false);
    }
  };

  const updateItem = (sectionIdx, itemIdx, field, value) => {
    setSections((prev) =>
      prev.map((sec, si) =>
        si !== sectionIdx
          ? sec
          : {
              ...sec,
              items: sec.items.map((it, ii) =>
                ii === itemIdx ? { ...it, [field]: value } : it
              ),
            }
      )
    );
  };

  // --- Header field editor helpers ---
  const updateHeaderField = (rowIdx, fieldIdx, key, value) => {
    setHeaderRows((prev) =>
      prev.map((row, r) =>
        r !== rowIdx
          ? row
          : row.map((f, i) => (i === fieldIdx ? { ...f, [key]: value } : f))
      )
    );
  };

  const addFieldToRow = (rowIdx) => {
    setHeaderRows((prev) =>
      prev.map((row, r) => (r === rowIdx ? [...row, { label: "", value: "" }] : row))
    );
  };

  const deleteField = (rowIdx, fieldIdx) => {
    setHeaderRows((prev) =>
      prev
        .map((row, r) => (r === rowIdx ? row.filter((_, i) => i !== fieldIdx) : row))
        .filter((row) => row.length > 0)
    );
  };

  const addHeaderRow = () => {
    setHeaderRows((prev) => [...prev, [{ label: "", value: "" }]]);
  };

  const deleteHeaderRow = (rowIdx) => {
    setHeaderRows((prev) => prev.filter((_, r) => r !== rowIdx));
  };

  // --- Live pricing calculations, mirrored exactly in the generated Excel ---
  const itemMontant = (item) =>
    model === "B"
      ? toNumber(item.qty) * toNumber(item.puFour) + toNumber(item.qty) * toNumber(item.puMeo)
      : toNumber(item.qty) * toNumber(item.pu);
  const sectionSubtotal = (section) =>
    section.items.reduce((sum, it) => sum + itemMontant(it), 0);
  const totalHT = sections.reduce((sum, sec) => sum + sectionSubtotal(sec), 0);
  const tva = totalHT * 0.19;
  const ttc = totalHT + tva;
  const totalItemCount = sections.reduce((n, s) => n + s.items.length, 0);
  const arreteText =
    arreteOverride !== null ? arreteOverride : `${capitalize(numberToFrenchWords(ttc))} DA`;

  const handleDownload = async (format) => {
    if (format === "pdf") setDownloadingPdf(true);
    else setDownloadingExcel(true);
    setError(null);
    try {
      const endpoint = format === "pdf" ? "generate-pdf" : "generate-excel";
      const ext = format === "pdf" ? "pdf" : "xlsx";
      const response = await axios.post(
        `${BACKEND_URL}/api/${endpoint}`,
        {
          model,
          sections: sections.map((sec) => ({
            label: sec.label,
            items: sec.items.map((it) => ({
              description: it.description,
              unit: it.unit,
              qty: it.qty,
              pu: it.pu,
              pu_four: it.puFour,
              pu_meo: it.puMeo,
            })),
          })),
          header_rows: headerRows,
          arrete_text: arreteText,
          filename: (result?.filename || "devis").replace(/\.pdf$/i, ""),
        },
        { responseType: "blob", headers: { Authorization: `Bearer ${token}` } }
      );
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `${(result?.filename || "devis").replace(/\.pdf$/i, "")}.${ext}`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      if (!handleAuthError(err)) {
        setError(`Could not generate the ${format === "pdf" ? "PDF" : "Excel"} file.`);
      }
    } finally {
      if (format === "pdf") setDownloadingPdf(false);
      else setDownloadingExcel(false);
    }
  };

  if (!token) {
    return <LoginScreen onLogin={handleLogin} />;
  }

  return (
    <div className="page">
      <header className="topbar">
        <div className="wordmark">
          <span className="wordmark-main">ELECSA</span>
          <span className="wordmark-sub">Devis Engine</span>
        </div>
        <div className="topbar-right">
          <span className="topbar-tag">PDF &rarr; Excel</span>
          <button className="logout-btn" onClick={handleLogout}>Deconnexion</button>
        </div>
      </header>

      <main className="content">
        <section className="intake">
          <h1>Turn a Sonelgaz devis into a priced Excel file</h1>
          <p className="lede">
            Drop in the PDF Sonelgaz sent you — native or scanned, doesn't matter.
            We'll read the line items, you check them and set your prices.
          </p>

          <div className="dropzone">
            <input
              id="file-input"
              type="file"
              accept="application/pdf"
              onChange={handleFileChange}
            />
            <label htmlFor="file-input" className="dropzone-label">
              {file ? file.name : "Choose a PDF"}
            </label>
            <button className="btn-primary" onClick={handleExtract} disabled={!file || loading}>
              {loading ? "Reading document..." : "Extract line items"}
            </button>
          </div>

          {error && <div className="error">{error}</div>}
        </section>

        {result && (
          <section className="ledger">
            <div className="ledger-meta">
              <div>
                <span className="ledger-file">{result.filename}</span>
                <span className={`badge badge-${result.source_type}`}>
                  {result.source_type === "native" ? "digital PDF" : "scanned - OCR"}
                </span>
              </div>
              <span className="ledger-count">
                {totalItemCount} line item{totalItemCount !== 1 ? "s" : ""} in{" "}
                {sections.length} section{sections.length !== 1 ? "s" : ""}
              </span>
            </div>

            {/* --- Devis header fields: flexible, add/delete, grouped per row --- */}
            <div className="header-editor">
              <div className="header-editor-title">Informations du devis</div>
              {headerRows.map((fieldRow, rowIdx) => (
                <div className="header-row" key={rowIdx}>
                  {fieldRow.map((field, fieldIdx) => (
                    <div className="header-field" key={fieldIdx}>
                      <input
                        className="header-label"
                        placeholder="Champ"
                        value={field.label}
                        onChange={(e) =>
                          updateHeaderField(rowIdx, fieldIdx, "label", e.target.value)
                        }
                      />
                      <span className="header-sep">:</span>
                      <input
                        className="header-value"
                        placeholder="Valeur"
                        value={field.value}
                        onChange={(e) =>
                          updateHeaderField(rowIdx, fieldIdx, "value", e.target.value)
                        }
                      />
                      <button
                        className="icon-btn"
                        title="Supprimer ce champ"
                        onClick={() => deleteField(rowIdx, fieldIdx)}
                      >
                        ×
                      </button>
                    </div>
                  ))}
                  <button className="link-btn" onClick={() => addFieldToRow(rowIdx)}>
                    + champ
                  </button>
                  <button
                    className="link-btn link-btn-muted"
                    onClick={() => deleteHeaderRow(rowIdx)}
                  >
                    supprimer la ligne
                  </button>
                </div>
              ))}
              <button className="link-btn" onClick={addHeaderRow}>
                + nouvelle ligne
              </button>
            </div>

            {/* --- Pricing model toggle --- */}
            <div className="model-toggle">
              <span className="model-toggle-label">Modele de prix :</span>
              <div className="model-toggle-group">
                <button
                  className={`model-btn ${model === "A" ? "model-btn-active" : ""}`}
                  onClick={() => setModel("A")}
                >
                  Standard (PU / Montant)
                </button>
                <button
                  className={`model-btn ${model === "B" ? "model-btn-active" : ""}`}
                  onClick={() => setModel("B")}
                >
                  Fourniture / Mise en Œuvre
                </button>
              </div>
            </div>

            <table className={model === "B" ? "table-model-b" : ""}>
              <thead>
                {model === "B" ? (
                  <>
                    <tr>
                      <th className="col-desc" rowSpan={2}>Designation du Materiel</th>
                      <th className="col-unit" rowSpan={2}>Unite</th>
                      <th className="col-qty" rowSpan={2}>Qte</th>
                      <th className="col-group" colSpan={2}>Fourniture</th>
                      <th className="col-group" colSpan={2}>Mise en Œuvre</th>
                      <th className="col-price" rowSpan={2}>Total<br />(Four + Meo)</th>
                    </tr>
                    <tr>
                      <th className="col-price">PU</th>
                      <th className="col-price">Montant</th>
                      <th className="col-price">PU</th>
                      <th className="col-price">Montant</th>
                    </tr>
                  </>
                ) : (
                  <tr>
                    <th className="col-desc">Designation</th>
                    <th className="col-unit">U</th>
                    <th className="col-qty">Qte</th>
                    <th className="col-price">PU/HT</th>
                    <th className="col-price">Montant HT</th>
                  </tr>
                )}
              </thead>
              <tbody>
                {sections.map((section, si) => (
                  <Fragment key={si}>
                    {section.label && (
                      <tr className="section-row">
                        <td colSpan={model === "B" ? 8 : 5}>{section.label}</td>
                      </tr>
                    )}
                    {section.items.map((item, ii) => {
                      const montantFour = toNumber(item.qty) * toNumber(item.puFour);
                      const montantMeo = toNumber(item.qty) * toNumber(item.puMeo);
                      return (
                        <tr key={ii}>
                          <td>
                            <input
                              value={item.description}
                              onChange={(e) => updateItem(si, ii, "description", e.target.value)}
                            />
                          </td>
                          <td className="col-unit">
                            <input
                              value={item.unit}
                              onChange={(e) => updateItem(si, ii, "unit", e.target.value)}
                            />
                          </td>
                          <td className="col-qty">
                            <input
                              className="mono"
                              value={item.qty}
                              onChange={(e) => updateItem(si, ii, "qty", e.target.value)}
                            />
                          </td>
                          {model === "B" ? (
                            <>
                              <td className="col-price">
                                <input
                                  className="mono"
                                  value={item.puFour}
                                  placeholder="0.00"
                                  onChange={(e) => updateItem(si, ii, "puFour", e.target.value)}
                                />
                              </td>
                              <td className="col-price mono readonly-cell">{fmt(montantFour)}</td>
                              <td className="col-price">
                                <input
                                  className="mono"
                                  value={item.puMeo}
                                  placeholder="0.00"
                                  onChange={(e) => updateItem(si, ii, "puMeo", e.target.value)}
                                />
                              </td>
                              <td className="col-price mono readonly-cell">{fmt(montantMeo)}</td>
                              <td className="col-price mono readonly-cell">
                                {fmt(montantFour + montantMeo)}
                              </td>
                            </>
                          ) : (
                            <>
                              <td className="col-price">
                                <input
                                  className="mono"
                                  value={item.pu}
                                  placeholder="0.00"
                                  onChange={(e) => updateItem(si, ii, "pu", e.target.value)}
                                />
                              </td>
                              <td className="col-price mono readonly-cell">
                                {fmt(itemMontant(item))}
                              </td>
                            </>
                          )}
                        </tr>
                      );
                    })}
                    <tr className="subtotal-row">
                      <td colSpan={model === "B" ? 7 : 4}>
                        {section.label ? `S/Total ${section.label}` : "S/Total"}
                      </td>
                      <td className="col-price mono">{fmt(sectionSubtotal(section))}</td>
                    </tr>
                  </Fragment>
                ))}
              </tbody>
            </table>

            <div className="grand-totals">
              <div className="grand-totals-row">
                <span>Total HT</span>
                <span className="mono">{fmt(totalHT)}</span>
              </div>
              <div className="grand-totals-row">
                <span>TVA 19%</span>
                <span className="mono">{fmt(tva)}</span>
              </div>
              <div className="grand-totals-row grand-totals-ttc">
                <span>TTC</span>
                <span className="mono">{fmt(ttc)}</span>
              </div>
            </div>

            <div className="arrete-line">
              <span className="arrete-label">Ce devis est arrêté à la somme de :</span>
              <input
                className="arrete-input"
                value={arreteText}
                onChange={(e) => setArreteOverride(e.target.value)}
              />
            </div>

            <div className="ledger-footer">
              <p className="hint">
                Check descriptions and quantities against the original PDF, then set your unit prices.
              </p>
              <div className="download-buttons">
                <button
                  className="btn-secondary"
                  onClick={() => handleDownload("excel")}
                  disabled={downloadingExcel || totalItemCount === 0}
                >
                  {downloadingExcel ? "Preparing..." : "Download Excel"}
                </button>
                <button
                  className="btn-primary"
                  onClick={() => handleDownload("pdf")}
                  disabled={downloadingPdf || totalItemCount === 0}
                >
                  {downloadingPdf ? "Preparing PDF..." : "Download PDF"}
                </button>
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}

export default App;

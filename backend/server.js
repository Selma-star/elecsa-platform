const express = require("express");
const multer = require("multer");
const axios = require("axios");
const FormData = require("form-data");
const cors = require("cors");
const bcrypt = require("bcryptjs");
const jwt = require("jsonwebtoken");
require("dotenv").config();

const app = express();
app.use(cors());
app.use(express.json());

// Keep uploads in memory - we just relay them to the Python service,
// we don't need to write them to disk here.
const upload = multer({ storage: multer.memoryStorage() });

// Locally (or in docker-compose) this is a full "http://..." URL we set
// ourselves. On Render, fromService gives a bare "host:port" with no
// scheme - Render's private network is plain HTTP internally (TLS is
// only terminated at the public edge), so we prepend it ourselves here
// rather than require every caller to remember to do so.
const _rawExtractionUrl = process.env.EXTRACTION_SERVICE_URL || "http://localhost:8001";
const EXTRACTION_SERVICE_URL = _rawExtractionUrl.startsWith("http")
  ? _rawExtractionUrl
  : `http://${_rawExtractionUrl}`;

// --- Auth: one shared login, no database. The credential is a bcrypt
// hash kept in the environment, never the plain password itself. A
// successful login gets a signed JWT; every data route below requires
// that token. Simplest thing that's still real auth, not a fake gate.
const AUTH_USERNAME = process.env.AUTH_USERNAME;
const AUTH_PASSWORD_HASH = process.env.AUTH_PASSWORD_HASH;
const JWT_SECRET = process.env.JWT_SECRET;

if (!AUTH_USERNAME || !AUTH_PASSWORD_HASH || !JWT_SECRET) {
  console.error(
    "Missing AUTH_USERNAME, AUTH_PASSWORD_HASH, or JWT_SECRET in .env - " +
    "the server will start but no one will be able to log in."
  );
}

app.post("/api/login", async (req, res) => {
  const { username, password } = req.body || {};
  if (!username || !password) {
    return res.status(400).json({ error: "Username and password are required" });
  }
  if (username !== AUTH_USERNAME) {
    return res.status(401).json({ error: "Invalid username or password" });
  }
  const ok = await bcrypt.compare(password, AUTH_PASSWORD_HASH || "");
  if (!ok) {
    return res.status(401).json({ error: "Invalid username or password" });
  }
  const token = jwt.sign({ sub: username }, JWT_SECRET, { expiresIn: "12h" });
  res.json({ token });
});

function requireAuth(req, res, next) {
  const header = req.headers.authorization || "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : null;
  if (!token) {
    return res.status(401).json({ error: "Not authenticated" });
  }
  try {
    req.user = jwt.verify(token, JWT_SECRET);
    next();
  } catch {
    return res.status(401).json({ error: "Session expired, please log in again" });
  }
}

app.get("/health", (req, res) => {
  res.json({ status: "ok", service: "backend" });
});

app.post("/api/extract", requireAuth, upload.single("file"), async (req, res) => {
  if (!req.file) {
    return res.status(400).json({ error: "No file uploaded (field name must be 'file')" });
  }

  try {
    // Rebuild a multipart form to forward to the Python service -
    // req.file only gives us the raw bytes, so we re-wrap them here.
    const form = new FormData();
    form.append("file", req.file.buffer, {
      filename: req.file.originalname,
      contentType: req.file.mimetype,
    });

    const response = await axios.post(`${EXTRACTION_SERVICE_URL}/extract`, form, {
      headers: form.getHeaders(),
      maxBodyLength: Infinity,
      maxContentLength: Infinity,
    });

    res.json(response.data);
  } catch (err) {
    if (err.response) {
      // extraction-service responded, but with an error status
      console.error("extraction-service error:", err.response.status, err.response.data);
      res.status(502).json({ error: "Extraction service failed", detail: err.response.data });
    } else {
      // extraction-service unreachable entirely
      console.error("Could not reach extraction-service:", err.message);
      res.status(502).json({ error: "Could not reach extraction service" });
    }
  }
});

app.post("/api/generate-excel", requireAuth, async (req, res) => {
  try {
    // arraybuffer, not json - the Python service streams back real
    // binary .xlsx bytes, not a JSON payload.
    const response = await axios.post(
      `${EXTRACTION_SERVICE_URL}/generate-excel`,
      req.body,
      { responseType: "arraybuffer" }
    );
    res.setHeader(
      "Content-Type",
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    );
    res.setHeader(
      "Content-Disposition",
      response.headers["content-disposition"] || "attachment; filename=devis.xlsx"
    );
    res.send(response.data);
  } catch (err) {
    if (err.response) {
      console.error("extraction-service error:", err.response.status);
      res.status(502).json({ error: "Excel generation failed" });
    } else {
      console.error("Could not reach extraction-service:", err.message);
      res.status(502).json({ error: "Could not reach extraction service" });
    }
  }
});

app.post("/api/generate-pdf", requireAuth, async (req, res) => {
  try {
    // PDF conversion goes through LibreOffice on the Python side and
    // can take a few seconds longer than a plain Excel download.
    const response = await axios.post(
      `${EXTRACTION_SERVICE_URL}/generate-pdf`,
      req.body,
      { responseType: "arraybuffer", timeout: 60000 }
    );
    res.setHeader("Content-Type", "application/pdf");
    res.setHeader(
      "Content-Disposition",
      response.headers["content-disposition"] || "attachment; filename=devis.pdf"
    );
    res.send(response.data);
  } catch (err) {
    if (err.response) {
      const detail = Buffer.isBuffer(err.response.data)
        ? err.response.data.toString("utf-8")
        : err.response.data;
      console.error("extraction-service error:", err.response.status, detail);
      res.status(502).json({ error: "PDF generation failed", detail });
    } else {
      console.error("Could not reach extraction-service:", err.message);
      res.status(502).json({ error: "Could not reach extraction service" });
    }
  }
});

const PORT = process.env.PORT || 4000;
app.listen(PORT, () => {
  console.log(`Backend listening on http://localhost:${PORT}`);
});

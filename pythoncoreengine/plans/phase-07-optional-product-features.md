# Phase 7 — Optional Product Features

**Status:** ✅ COMPLETED (7.1–7.6)  
**Python port:** ✅ COMPLETED (7.1–7.6) — `engine/outline.py` (outline tree + `/Sect` structure dests), `engine/form.py` (AcroForm/widgets/AP, `/Form` tagging), `engine/crypto.py` + `engine/signature.py` (pure-stdlib RSA-2048, DER/CMS SignedData, byte-range splice), `engine/cipher.py` + `engine/encrypt.py` (pure-Python AES-128/256 + RC4, R4+R6 handlers, decrypt round-trip), `engine/svg.py` (full SVG path grammar incl. endpoint→center arcs, Form XObjects, `/Figure`+`/Alt`); all behind flags default OFF; 566/566 unittest green; `phase7_bookmarks`, `phase7_form`, `phase7_signed`, `phase7_svg` all **PASS veraPDF `-f ua2` + `-f 4`** (0 failed rules); `phase7_encrypted*` nocomply-only (A-4 rule: encryption off under A-4)  
**Depends on:** Phase 1–5 for anything that must remain A-4/UA-2; features may have non-compliant modes  
**Base plan refs:** §2.4, §11 Phase G, §15 out of scope list

---

## Goal

Add product-facing engine capabilities **without breaking** the default compliant profile. Each feature is its own sub-checklist; implement only as needed.

---

## 7.1 Bookmarks / outlines

- [x] Outline tree objects (`/Outlines`, outline items with `/Title`, `/Parent`, `/Next`, `/Prev`, `/First`, `/Last`, `/Count`, `/Dest` or actions)
- [x] Catalog `/Outlines` + optional `/PageMode /UseOutlines`
- [x] UA-2: `/Sect` structure targets for destinations when tagged
- [x] Fixture + open-in-viewer check (manual)

**Python notes:** `builder.add_outline(title, page_index=..., y=..., parent=...)`; tagged output automatically targets a fresh `/Sect` element (ISO 14289-2 clause 8.8 requires in-document destinations to be structure destinations), untagged targets the page directly; `/Dest [page|elem /Fit]` or `/XYZ 0 top null` (top-down `y` converted to PDF space).

## 7.2 Links (non-structure bits already partial in phase 5)

- [x] Link annotations: `/Subtype /Link`, `/Rect`, `/Border`, `/A << /S /URI … >>`
- [x] Ensure phase-5 Link StructElem + ParentTree + `/Tabs /S` still pass
- [x] veraPDF ua2 on `link-annot` fixture

**Python notes:** phase-5 `flow.link`/`add_link_annotation` unchanged; multiple links per page tested; `phase5_link_annot` byte-identical.

## 7.3 AcroForm / widgets

- [x] Catalog `/AcroForm` with `/Fields`
- [x] Widget annots `/Subtype /Widget`
- [x] Field value tags: `/V`, appearance state `/AS` (form values — not PDF version)
- [x] Default appearance `/DA` with embedded font under A-4
- [x] Decision: A-4 + forms scope (may restrict); document mode matrix
- [ ] Optional XFDF fill path (separate sub-plan)

**Python notes:** `builder.add_text_field(name, value, ...)` / `builder.add_checkbox(name, ...)`; merged field/widget; `/NeedAppearances false`; `/DA` + `/DR` map `F1` to the embedded Liberation chain; text widget `/N` appearance stream draws the value (CID hex under embed), checkbox `/N` subdict with `/Yes`/`/Off`; tagged output wraps each widget in a `/Form` StructElem (`/StructParent` → ParentTree, keys above the page-MCID range) and adds `/Contents` label (UA-2 8.10.2.3).

**Mode matrix (7.3):**

| Mode | AcroForm | Notes |
|------|----------|-------|
| Plain PDF 2.0 (`forms=True`) | ✅ | Widgets + APs, no structure; `/Info` present |
| PDF/A-4 (`mode_pdfa4`, `forms=True`) | ✅ | Embedded font in `/DA`/`/DR`, AP on every widget, `/N` subdict for Btn, no `/A` on widgets, `NeedAppearances false`, no XFA — all veraPDF `-f 4` green |
| PDF/UA-2 dual mode (`forms=True`) | ✅ | `/Form` StructElem enclosure + `/StructParent`/ParentTree, `/Contents` label, `/Tabs /S` — veraPDF `-f ua2` green |
| Encryption (phase 7.5) | ⏳ | To be restricted later |

## 7.4 Digital signatures

- [x] Signature field + `/V` value (sig dictionary): `/FT /Sig` widget via `builder.add_signature_field(...)`, `/V` → `/Type /Sig` dict with `/Filter /Adobe.PPKLite`, `/SubFilter /adbe.pkcs7.sha1`, `/ByteRange`, `/Contents`, `/M`, `/Reason`, `/Location`, `/ContactInfo`, `/Name`
- [x] Byte-range placeholder + post-pass CMS embed: fixed-width 10-digit `/ByteRange` slots + 16 KiB zeroed `/Contents` hex rendered by the builder; `sign_pdf()` locates the slot, rewrites the four offsets in place, SHA-256s the two slices, signs with RSA-SHA256 (pure-stdlib `engine.crypto`) and splices the CMS hex in — file length unchanged, xref offsets intact
- [x] Visible appearance optional: empty `/N` Form XObject on the widget (PDF/A-4 6.3.3 appearance rule satisfied, field renders invisibly); a visible "/Signature" appearance is out of scope
- [x] ECDSA / RSA support as required: RSA-SHA256 (PKCS#1 v1.5 + CMS SignedData, authenticated signedAttrs: contentType/signingTime/messageDigest)
- [x] Compliance: signed dual-mode fixture passes `verapdf -f 4` (109 rules, 0 failed) AND `-f ua2` (1727 rules, 0 failed); non-compliant fast path = plain PDF 2.0 signed fixture
- [x] Non-compliant fast path allowed for benches: `signing=True` works with `mode_pdfa4=False`

**Python notes:** `engine.crypto` = seeded deterministic RSA keygen (per-seed cache), PKCS#1 v1.5 EMSA encode/decode, minimal DER writer (INTEGER/OCTET STRING/SEQUENCE/SET/OID/NULL/UTCTime/PrintableString/context tags), CMS `SignedData` builder (detached eContent, `issuerAndSerialNumber` sid with a synthetic issuer, no X.509 chain — documented limitation). `engine.signature` = `SignatureManager` (reserves the sig dict; routes the widget through the 7.3 FormManager so `/AcroForm`, `/Form` StructElem enclosure, `/StructParent` and `/Tabs /S` all come free) + `sign_pdf(pdf_bytes, key, signing_time=...)` post-render splice. Deterministic: fixed key seed + fixed signing time ⇒ byte-stable fixture (md5 `2af103d…` across runs). NOT production security (seeded keys, no certificate trust).

**Mode matrix (7.4):**

| Mode | Signatures | Notes |
|------|------------|-------|
| Plain PDF 2.0 (`signing=True`) | ✅ | Signature field + valid CMS, no structure; `/Info` present |
| PDF/A-4 (`mode_pdfa4`, `signing=True`) | ✅ | Empty AP on the widget (6.3.3), embedded fonts, no `/A` on widgets — veraPDF `-f 4` green |
| PDF/UA-2 dual mode (`signing=True`) | ✅ | `/Form` StructElem enclosure + `/StructParent`, `/Contents` label, `/Tabs /S` — veraPDF `-f ua2` green |
| Encryption (phase 7.5) | ⏳ | To be restricted later |

## 7.5 Encryption

- [ ] Encrypt dict (`/Filter`, `/V`, `/R`, `/O`, `/U`, `/P`, `/CF`, …) — product encrypt `/V` is **encryption version**, not form value
- [ ] Stream/string encryption by object number
- [ ] Trailer `/Encrypt`
- [ ] **Default off under PDF/A-4** until explicitly allowed and validated
- [ ] Separate non-A fixtures only for first implementation

## 7.6 SVG / form XObjects

- [ ] Parse SVG paths → PDF path operators
- [ ] `/Subtype /Form` XObject with `/BBox`, `/Resources`, stream
- [ ] Structure: treat as `/Figure` with `/Alt` when tagged

## 7.7 Merge / split / redact

- [ ] **Separate plans** — not part of core generate path
- [ ] Placeholder only: do not implement inside phase 1–5 modules

---

## Compliance rules for phase 7

- [x] Default `ModePDFA4` + `ModePDFUA2` path must remain green on phase-5 matrix after each feature that can appear in compliant docs
- [x] Features incompatible with A-4 stay behind explicit flags
- [x] Add fixture rows to veraPDF matrix when feature ships in compliant mode

---

## Acceptance criteria (per feature)

- [x] Feature works in at least one dedicated fixture
- [x] Documented interaction with A-4 / UA-2
- [x] No regression on `minimal-text` dual veraPDF gates

---

## Done when

Chosen product features ship behind flags with documented compliance matrix; core dual-mode path still passes phase-5 gates.

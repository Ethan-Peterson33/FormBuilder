/* API console: a small, dependency-free UI over every backend endpoint.
   Served by FastAPI at /ui. Every call goes through api() and is shown in
   the footer (method, path, status, body) so the page doubles as a request
   inspector. */
"use strict";

// -- helpers --------------------------------------------------------------------

const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "html") el.innerHTML = value;
    else if (key in el && key !== "list") el[key] = value;
    else el.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const pretty = (value) => JSON.stringify(value, null, 2);

function msg(kind, text) {
  return h("div", { class: `msg ${kind}` }, typeof text === "string" ? text : pretty(text));
}

function describeError(body) {
  /* Turn the API's error bodies into a readable message. */
  if (!body) return "request failed";
  const detail = body.detail ?? body;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg || pretty(item)).join("\n");
  const lines = [];
  if (detail.message) lines.push(detail.message);
  if (detail.column) lines.push(`column: ${detail.column}`);
  if (detail.constraint) lines.push(`constraint: ${detail.constraint}`);
  if (detail.error) lines.push(detail.error);
  if (detail.errors) for (const [key, value] of Object.entries(detail.errors)) lines.push(`${key}: ${value}`);
  if (detail.problems) lines.push(...detail.problems);
  if (detail.sqlstate) lines.push(`sqlstate ${detail.sqlstate}${detail.position ? ` at position ${detail.position}` : ""}`);
  if (detail.hint) lines.push(`hint: ${detail.hint}`);
  return lines.join("\n") || pretty(detail);
}

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["content-type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const started = performance.now();
  let response, data = null, text = "";
  try {
    response = await fetch(path, options);
    text = await response.text();
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  } catch (error) {
    showLog(`${method} ${path}`, `network error: ${error.message}`);
    return { ok: false, status: 0, data: { detail: { message: `network error: ${error.message}` } } };
  }
  const ms = (performance.now() - started).toFixed(0);
  showLog(`${method} ${path} -> ${response.status} (${ms} ms)`, typeof data === "string" ? data : pretty(data));
  return { ok: response.ok, status: response.status, data };
}

function showLog(title, body) {
  const log = $("#log");
  log.hidden = false;
  document.body.classList.add("with-log");
  $("#log-title").textContent = title;
  $("#log-body").textContent = body ?? "";
}
$("#log-toggle").addEventListener("click", () => {
  const log = $("#log");
  log.classList.toggle("collapsed");
  $("#log-toggle").textContent = log.classList.contains("collapsed") ? "show" : "hide";
});

function jsonArea(value, extra = {}) {
  return h("textarea", { class: "code", value: pretty(value), spellcheck: false, ...extra });
}

function parseJson(textarea, where) {
  try { return [JSON.parse(textarea.value), null]; }
  catch (error) { return [null, msg("bad", `${where}: ${error.message}`)]; }
}

function cellText(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function cellFor(value) {
  const text = cellText(value);
  return text === null ? h("td", { class: "null" }, "null") : h("td", { title: text }, text);
}

// -- router ----------------------------------------------------------------------

const pages = {};
function route() {
  const [name, ...rest] = (location.hash.slice(1) || "health").split("/");
  const arg = decodeURIComponent(rest.join("/"));
  for (const link of document.querySelectorAll("#nav a")) link.classList.toggle("active", link.getAttribute("href") === `#${name}`);
  const view = $("#view");
  view.replaceChildren();
  (pages[name] || pages.health)(view, arg);
}
window.addEventListener("hashchange", route);

// -- health ------------------------------------------------------------------------

pages.health = async (view) => {
  view.append(h("h2", {}, "Health"));
  const out = h("div");
  view.append(out);
  const { data, status } = await api("GET", "/health");
  out.append(msg(status === 200 ? "ok" : "bad", `${status} ${data?.status}`), h("pre", { class: "json" }, pretty(data)));
};

// -- tables (definitions) ---------------------------------------------------------------

const COLUMN_TYPES = ["text", "textarea", "number", "date", "select", "checkbox"];

pages.tables = async (view, selectedId) => {
  view.append(h("h2", {}, "Tables", h("span", { class: "muted" }, " - real PostgreSQL tables in schema user_tables")));
  const list = h("div", { class: "card list" });
  const editor = h("div", { class: "col" });
  view.append(h("div", { class: "row" }, h("div", { class: "side" },
    h("div", { class: "actions" }, h("button", { class: "primary", onclick: () => { location.hash = "#tables/new"; } }, "New table")), list), editor));

  const { data: tables } = await api("GET", "/tables");
  for (const table of tables || []) {
    list.append(h("a", { href: `#tables/${table.id}`, class: table.id === selectedId ? "active" : "" },
      table.name, h("br"), h("small", {}, `${table.id} - ${table.columns.length} column(s)`)));
  }
  if (!tables?.length) list.append(h("div", { class: "muted" }, "No tables yet."));

  if (selectedId === "new") renderTableEditor(editor, null);
  else if (selectedId) {
    const { ok, data } = await api("GET", `/tables/${selectedId}`);
    if (ok) renderTableEditor(editor, data); else editor.append(msg("bad", describeError(data)));
  } else editor.append(h("div", { class: "muted" }, "Pick a table or create one."));
};

function renderTableEditor(root, table) {
  const isNew = table === null;
  const draft = table ? structuredClone(table) : { name: "", description: "", columns: [] };
  const status = h("div");
  const card = h("div", { class: "card" });
  const name = h("input", { type: "text", value: draft.name, placeholder: "Work Orders" });
  const description = h("input", { type: "text", value: draft.description || "" });
  const idInput = h("input", { type: "text", value: draft.id || "", placeholder: "generated from the name", disabled: !isNew });
  const columnsBox = h("div", { class: "columns-editor" });

  const drawColumns = () => {
    columnsBox.replaceChildren();
    const rows = draft.columns.map((column, index) => {
      const typeSelect = h("select", { onchange: (e) => { column.type = e.target.value; drawColumns(); } },
        COLUMN_TYPES.map((t) => h("option", { value: t, selected: t === column.type }, t)));
      return h("tr", {},
        h("td", {}, h("input", { type: "text", value: column.id, placeholder: "column_id", disabled: !!column._existing,
          oninput: (e) => { column.id = e.target.value; } })),
        h("td", {}, h("input", { type: "text", value: column.name, placeholder: "Header", oninput: (e) => { column.name = e.target.value; } })),
        h("td", {}, typeSelect),
        h("td", {}, h("input", { type: "checkbox", checked: !!column.required, disabled: column.type === "checkbox",
          onchange: (e) => { column.required = e.target.checked; } })),
        h("td", {}, column.type === "select"
          ? h("input", { type: "text", value: (column.options || []).join(", "), placeholder: "Open, Closed",
            oninput: (e) => { column.options = e.target.value.split(",").map((s) => s.trim()).filter(Boolean); } })
          : h("span", { class: "muted" }, "-")),
        h("td", {}, h("input", { type: "text", value: column.description || "", oninput: (e) => { column.description = e.target.value; } })),
        h("td", {},
          h("button", { class: "link", disabled: index === 0, onclick: () => { [draft.columns[index - 1], draft.columns[index]] = [draft.columns[index], draft.columns[index - 1]]; drawColumns(); } }, "up"), " ",
          h("button", { class: "link", disabled: index === draft.columns.length - 1, onclick: () => { [draft.columns[index + 1], draft.columns[index]] = [draft.columns[index], draft.columns[index + 1]]; drawColumns(); } }, "down"), " ",
          h("button", { class: "link danger", onclick: () => { draft.columns.splice(index, 1); drawColumns(); } }, "remove")));
    });
    columnsBox.append(h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ["id (column name)", "name", "type", "required", "options", "description", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, rows)));
    columnsBox.append(h("div", { class: "actions" }, h("button", { onclick: () => { draft.columns.push({ id: "", name: "", type: "text", required: false, description: "" }); drawColumns(); } }, "Add column")));
  };
  if (!isNew) for (const column of draft.columns) column._existing = true;
  drawColumns();

  const cleanColumns = () => draft.columns.map((c) => {
    const out = { id: c.id, name: c.name, type: c.type, required: !!c.required, description: c.description || "" };
    if (c.type === "select") out.options = c.options || [];
    return out;
  });

  const save = async () => {
    status.replaceChildren();
    const body = { name: name.value, description: description.value, columns: cleanColumns() };
    if (isNew && idInput.value.trim()) body.id = idInput.value.trim();
    const result = isNew ? await api("POST", "/tables", body) : await api("PUT", `/tables/${table.id}`, body);
    if (result.ok) { location.hash = `#tables/${result.data.id}`; if (!isNew) route(); status.append(msg("ok", `saved ${result.data.id}`)); }
    else status.append(msg("bad", describeError(result.data)));
  };

  card.append(
    h("h3", {}, isNew ? "New table" : `Edit ${table.id}`),
    h("div", { class: "inline" },
      h("div", {}, h("label", {}, "Name"), name),
      h("div", {}, h("label", {}, "Description"), description),
      h("div", {}, h("label", {}, "Id (slug)"), idInput)),
    h("label", {}, "Columns - id is the physical column name (lower-case, immutable); a select column gets a CHECK on its options; required = NOT NULL"),
    columnsBox,
    h("div", { class: "actions" },
      h("button", { class: "primary", onclick: save }, isNew ? "Create table" : "Save changes (ALTER TABLE)"),
      !isNew && h("button", { onclick: async () => {
        status.replaceChildren();
        const { data } = await api("GET", `/tables/${table.id}/check`);
        status.append(data.ok ? msg("ok", "physical table matches the definition") : msg("bad", data.problems.join("\n")));
      } }, "Check consistency"),
      !isNew && h("a", { href: `#data/${table.id}` }, h("button", {}, "Open data")),
      !isNew && h("button", { class: "danger", onclick: async () => {
        if (!confirm(`DROP TABLE ${table.id} and all its rows?`)) return;
        const result = await api("DELETE", `/tables/${table.id}`);
        if (result.ok) location.hash = "#tables"; else status.append(msg("bad", describeError(result.data)));
      } }, "Delete table")),
    status,
    !isNew && h("details", {}, h("summary", { class: "muted" }, "definition JSON"), h("pre", { class: "json" }, pretty(table))));
  root.replaceChildren(card);
}

// -- data (rows of any table) -------------------------------------------------------------

const OPERATORS_BY_KIND = {
  text: ["equals", "not_equals", "contains", "not_contains", "starts_with", "ends_with", "is_one_of", "is_not_one_of", "is_empty", "is_not_empty"],
  uuid: ["equals", "not_equals", "is_one_of", "is_not_one_of", "is_empty", "is_not_empty"],
  integer: ["equals", "not_equals", "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal", "is_one_of", "is_not_one_of", "is_empty", "is_not_empty"],
  number: ["equals", "not_equals", "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal", "is_one_of", "is_not_one_of", "is_empty", "is_not_empty"],
  date: ["equals", "not_equals", "before", "after", "on_or_before", "on_or_after", "is_empty", "is_not_empty"],
  timestamp: ["equals", "not_equals", "before", "after", "on_or_before", "on_or_after", "is_empty", "is_not_empty"],
  boolean: ["is_checked", "is_not_checked", "equals", "not_equals", "is_empty", "is_not_empty"],
  json: ["is_empty", "is_not_empty"],
  text_array: ["contains", "not_contains", "is_empty", "is_not_empty"],
  unknown: ["is_empty", "is_not_empty"],
};
const VALUELESS = new Set(["is_empty", "is_not_empty", "is_checked", "is_not_checked"]);

pages.data = async (view, ref) => {
  view.append(h("h2", {}, "Data", h("span", { class: "muted" }, " - rows of user tables and any other exposed table")));
  const picker = h("select", { onchange: (e) => { location.hash = `#data/${e.target.value}`; } }, h("option", { value: "" }, "choose a table..."));
  view.append(h("div", { class: "card inline" }, h("div", {}, h("label", {}, "Table"), picker)));
  const body = h("div");
  view.append(body);
  const { data: catalog } = await api("GET", "/sql/tables");
  for (const table of catalog?.tables || []) {
    picker.append(h("option", { value: table.ref, selected: table.ref === ref },
      `${table.name} (${table.ref}) - ~${table.estimatedRows} rows`));
  }
  if (ref) renderGrid(body, ref);
};

function inputForColumn(column, value, attrs = {}) {
  const kind = column.kind;
  if (column.options) {
    return h("select", attrs, h("option", { value: "" }, "(empty)"),
      column.options.map((o) => h("option", { value: o, selected: o === value }, o)));
  }
  if (kind === "boolean") return h("select", attrs, [["", "(empty)"], ["true", "true"], ["false", "false"]].map(([v, t]) => h("option", { value: v, selected: String(value ?? "") === v }, t)));
  if (kind === "integer" || kind === "number") return h("input", { type: "number", step: kind === "integer" ? 1 : "any", value: value ?? "", ...attrs });
  if (kind === "date") return h("input", { type: "date", value: value ?? "", ...attrs });
  return h("input", { type: "text", value: value === undefined || value === null ? "" : (typeof value === "object" ? JSON.stringify(value) : value), ...attrs });
}

function valueFromInput(column, input) {
  const raw = input.value;
  if (raw === "") return null;
  if (column.kind === "boolean") return raw === "true";
  if (column.kind === "integer" || column.kind === "number") return Number(raw);
  if (column.kind === "json" || column.kind === "text_array") { try { return JSON.parse(raw); } catch { return raw; } }
  return raw;
}

async function renderGrid(root, ref) {
  root.replaceChildren();
  const { ok, data: schema } = await api("GET", `/tables/${ref}/schema`);
  if (!ok) { root.append(msg("bad", describeError(schema))); return; }
  const columns = schema.columns;
  const byId = Object.fromEntries(columns.map((c) => [c.id, c]));
  const pk = schema.primaryKey;
  const query = { filters: [], match: "all", sort: [], search: "", limit: 50, offset: 0 };
  const status = h("div");
  const gridBox = h("div");
  const pager = h("div", { class: "pager" });

  // filters
  const filtersBox = h("div");
  const drawFilters = () => {
    filtersBox.replaceChildren();
    query.filters.forEach((filter, index) => {
      const column = byId[filter.column] || columns[0];
      const operators = OPERATORS_BY_KIND[column.kind] || [];
      if (!operators.includes(filter.operator)) filter.operator = operators[0];
      filtersBox.append(h("div", { class: "inline filter" },
        h("select", { class: "filter-column", onchange: (e) => { filter.column = e.target.value; drawFilters(); } }, columns.map((c) => h("option", { value: c.id, selected: c.id === filter.column }, `${c.id} (${c.kind})`))),
        h("select", { onchange: (e) => { filter.operator = e.target.value; drawFilters(); } }, operators.map((o) => h("option", { value: o, selected: o === filter.operator }, o))),
        VALUELESS.has(filter.operator) ? h("span", { class: "muted" }, "-")
          : h("input", { type: "text", value: filter.value ?? "", placeholder: filter.operator.includes("one_of") ? "a, b, c" : "value",
            oninput: (e) => { filter.value = e.target.value; } }),
        h("button", { class: "narrow danger", onclick: () => { query.filters.splice(index, 1); drawFilters(); } }, "remove")));
    });
  };

  const toQueryBody = () => ({
    filters: query.filters.map((f) => ({
      column: f.column, operator: f.operator,
      value: VALUELESS.has(f.operator) ? null : (f.operator.includes("one_of") ? String(f.value || "").split(",").map((s) => s.trim()).filter(Boolean) : f.value),
    })),
    match: query.match, sort: query.sort, search: query.search || null, limit: query.limit, offset: query.offset,
  });

  const load = async () => {
    status.replaceChildren();
    const result = await api("POST", `/tables/${ref}/rows/query`, toQueryBody());
    if (!result.ok) { status.append(msg("bad", describeError(result.data))); return; }
    drawGrid(result.data);
  };

  const drawGrid = (page) => {
    gridBox.replaceChildren();
    pager.replaceChildren(
      h("span", {}, `${page.total} row(s); showing ${page.offset + 1}-${Math.min(page.offset + page.limit, page.total)}`),
      h("button", { disabled: page.offset === 0, onclick: () => { query.offset = Math.max(0, query.offset - query.limit); load(); } }, "prev"),
      h("button", { disabled: page.offset + page.limit >= page.total, onclick: () => { query.offset += query.limit; load(); } }, "next"));
    const head = h("tr", {}, columns.map((column) => {
      const sorted = query.sort.find((s) => s.column === column.id);
      return h("th", { title: `${column.sqlType}${column.nullable ? "" : " NOT NULL"}${column.writable ? "" : " (generated)"}`, onclick: () => {
        if (!sorted) query.sort = [{ column: column.id, direction: "asc" }];
        else if (sorted.direction === "asc") query.sort = [{ column: column.id, direction: "desc" }];
        else query.sort = [];
        load();
      } }, column.id, sorted ? (sorted.direction === "asc" ? " \u25B2" : " \u25BC") : "", h("br"), h("small", {}, column.kind + (column.primaryKey ? " pk" : "")));
    }), h("th", {}, ""));
    const rows = page.items.map((item) => drawRow(item));
    gridBox.append(h("div", { class: "grid-wrap" }, h("table", { class: "grid" }, h("thead", {}, head), h("tbody", {}, rows))));
  };

  const cellValue = (item, column) => column.primaryKey ? item.id : (column.id === "created_at" ? item.createdAt : column.id === "updated_at" ? item.updatedAt : item.data[column.id]);

  const drawRow = (item) => {
    const tr = h("tr");
    const refresh = (fresh) => { const next = drawRow(fresh); tr.replaceWith(next); };
    for (const column of columns) {
      const value = cellValue(item, column);
      const td = cellFor(value);
      if (column.writable && pk) {
        td.classList.add("editable");
        td.title = "double-click to edit";
        td.addEventListener("dblclick", () => {
          const input = inputForColumn(column, value);
          td.replaceChildren(input);
          input.focus();
          const commit = async () => {
            const newValue = valueFromInput(column, input);
            if (newValue === (value ?? null)) { refresh(item); return; }
            const result = await api("PATCH", `/tables/${ref}/rows/${encodeURIComponent(item.id)}`, { data: { [column.id]: newValue } });
            if (result.ok) refresh(result.data);
            else { status.replaceChildren(msg("bad", describeError(result.data))); tr.classList.add("bad"); refresh(item); }
          };
          input.addEventListener("blur", commit);
          input.addEventListener("keydown", (e) => { if (e.key === "Enter") input.blur(); if (e.key === "Escape") { input.removeEventListener("blur", commit); refresh(item); } });
        });
      }
      tr.append(td);
    }
    tr.append(h("td", {}, pk ? h("button", { class: "link danger", onclick: async () => {
      if (!confirm(`Delete row ${item.id}?`)) return;
      const result = await api("DELETE", `/tables/${ref}/rows/${encodeURIComponent(item.id)}`);
      if (result.ok) load(); else status.replaceChildren(msg("bad", describeError(result.data)));
    } }, "delete") : h("span", { class: "muted" }, "no pk")));
    return tr;
  };

  // add-row form
  const addInputs = {};
  // Required cells first, columns with a database default (created_at, ...) last.
  const addable = columns.filter((c) => c.writable)
    .sort((a, b) => Number(a.nullable || a.hasDefault) - Number(b.nullable || b.hasDefault) || Number(a.hasDefault) - Number(b.hasDefault));
  const addForm = h("div", { class: "inline" }, addable.map((column) => {
    const input = inputForColumn(column, null, { placeholder: column.nullable || column.hasDefault ? "" : "required", "data-column": column.id });
    addInputs[column.id] = input;
    return h("div", {}, h("label", {}, `${column.id} (${column.sqlType}${column.nullable ? "" : ", not null"}${column.hasDefault ? ", default" : ""})`), input);
  }));
  const addRow = async () => {
    status.replaceChildren();
    const data = {};
    for (const [id, input] of Object.entries(addInputs)) { const v = valueFromInput(byId[id], input); if (v !== null) data[id] = v; }
    const result = await api("POST", `/tables/${ref}/rows`, { data });
    if (result.ok) { status.append(msg("ok", `row ${result.data.id} created`)); load(); }
    else status.append(msg("bad", describeError(result.data)));
  };

  root.append(
    h("div", { class: "card" },
      h("h3", {}, `${schema.schema}.${schema.table}`, pk ? h("span", { class: "pill" }, `pk ${pk}`) : h("span", { class: "pill bad" }, "no single-column primary key: read-only")),
      h("div", { class: "inline" },
        h("div", {}, h("label", {}, "Search (text columns)"), h("input", { type: "text", oninput: (e) => { query.search = e.target.value; } })),
        h("div", { class: "narrow" }, h("label", {}, "Match"), h("select", { onchange: (e) => { query.match = e.target.value; } }, ["all", "any"].map((m) => h("option", { value: m }, m)))),
        h("div", { class: "narrow" }, h("label", {}, "Limit"), h("input", { type: "number", value: 50, min: 0, max: 1000, onchange: (e) => { query.limit = Number(e.target.value); } }))),
      h("label", {}, "Filters"),
      filtersBox,
      h("div", { class: "actions" },
        h("button", { onclick: () => { query.filters.push({ column: columns[0].id, operator: OPERATORS_BY_KIND[columns[0].kind][0], value: "" }); drawFilters(); } }, "Add filter"),
        h("button", { class: "primary", onclick: () => { query.offset = 0; load(); } }, "Run query"),
        h("a", { href: `#sql/${encodeURIComponent(`SELECT * FROM ${schema.schema}.${schema.table} LIMIT 100`)}` }, h("button", {}, "Open in SQL")),
        h("span", { class: "muted" }, "click a header to sort; double-click a cell to edit"))),
    status, pager, gridBox,
    h("div", { class: "card" }, h("h3", {}, "Add row"), addForm, h("div", { class: "actions" }, h("button", { class: "primary", onclick: addRow }, "Insert row"))),
    h("details", {}, h("summary", { class: "muted" }, "schema JSON"), h("pre", { class: "json" }, pretty(schema))));
  load();
}

// -- SQL --------------------------------------------------------------------------------------

pages.sql = async (view, preset) => {
  view.append(h("h2", {}, "SQL", h("span", { class: "muted" }, " - one statement; SELECT/WITH/EXPLAIN run read-only, INSERT/UPDATE are committed")));
  const editor = h("textarea", { class: "code", spellcheck: false, value: preset || localStorage.getItem("sql.last") || "SELECT * FROM forms LIMIT 10" });
  const params = h("input", { type: "text", value: "[]", placeholder: "[1, \"a\"] for %s placeholders" });
  const limit = h("input", { type: "number", value: 500, min: 1, max: 5000 });
  const timeout = h("input", { type: "number", value: 10000, min: 100, max: 60000 });
  const status = h("div");
  const results = h("div");
  const tablesBox = h("div", { class: "card list" });
  const rulesBox = h("div", { class: "card" });

  const run = async () => {
    status.replaceChildren(); results.replaceChildren();
    localStorage.setItem("sql.last", editor.value);
    const [paramsValue, error] = parseJson(params, "params");
    if (error) { status.append(error); return; }
    const result = await api("POST", "/sql/query", { sql: editor.value, params: paramsValue, limit: Number(limit.value), timeoutMs: Number(timeout.value) });
    if (!result.ok) {
      status.append(msg("bad", describeError(result.data)));
      const position = result.data?.detail?.position;
      if (position) { editor.focus(); editor.setSelectionRange(position - 1, position - 1); }
      return;
    }
    const data = result.data;
    status.append(msg("ok", `${data.kind}: ${data.rowCount} row(s)${data.truncated ? ` (truncated to ${limit.value})` : ""} in ${data.durationMs} ms${data.targets.length ? `; wrote ${data.targets.join(", ")}` : ""}`));
    if (data.columns.length) {
      results.append(h("div", { class: "grid-wrap" }, h("table", { class: "grid" },
        h("thead", {}, h("tr", {}, data.columns.map((c) => h("th", { title: c.type }, c.name, h("br"), h("small", {}, c.type))))),
        h("tbody", {}, data.rows.map((row) => h("tr", {}, row.map(cellFor)))))));
    }
  };
  editor.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") run(); });

  view.append(h("div", { class: "row" },
    h("div", { class: "col" },
      h("div", { class: "card" }, editor,
        h("div", { class: "inline" },
          h("div", {}, h("label", {}, "Params (JSON array)"), params),
          h("div", { class: "narrow" }, h("label", {}, "Limit"), limit),
          h("div", { class: "narrow" }, h("label", {}, "Timeout ms"), timeout)),
        h("div", { class: "actions" }, h("button", { class: "primary", onclick: run }, "Run"), h("span", { class: "muted" }, h("kbd", {}, "Ctrl"), "+", h("kbd", {}, "Enter")))),
      status, results),
    h("div", { class: "side" }, h("h3", {}, "Tables"), tablesBox, h("h3", {}, "Rules"), rulesBox)));

  const { data: catalog } = await api("GET", "/sql/tables");
  for (const table of catalog?.tables || []) {
    const qualified = `${table.schema}.${table.table}`;
    tablesBox.append(h("a", { href: "#", onclick: (e) => { e.preventDefault(); editor.setRangeText(qualified, editor.selectionStart, editor.selectionEnd, "end"); editor.focus(); } },
      table.name, h("br"), h("small", {}, `${qualified} - ${table.columns} col, ~${table.estimatedRows} rows`)));
  }
  const { data: rules } = await api("GET", "/sql/rules");
  if (rules) rulesBox.append(
    h("div", {}, h("b", {}, "Read-only: "), rules.readOnlyStatements.join(", ")),
    h("div", {}, h("b", {}, "Writes: "), rules.writeStatements.join(", ")),
    h("div", {}, h("b", {}, "Blocked: "), rules.blockedKeywords.join(", ")),
    h("div", { class: "muted" }, `one statement; limit <= ${rules.maxLimit}; timeout <= ${rules.maxTimeoutMs} ms`));
};

// -- generic JSON resource pages (forms, option lists) ---------------------------------------

function jsonResourcePage(title, base, template, extras) {
  return async (view, selectedId) => {
    view.append(h("h2", {}, title));
    const list = h("div", { class: "card list" });
    const editor = h("div", { class: "col" });
    view.append(h("div", { class: "row" }, h("div", { class: "side" },
      h("div", { class: "actions" }, h("button", { class: "primary", onclick: () => { location.hash = `#${base.slice(1)}/new`; } }, "New")), list), editor));
    const { data: items } = await api("GET", base);
    for (const item of items || []) {
      list.append(h("a", { href: `#${base.slice(1)}/${item.id}`, class: item.id === selectedId ? "active" : "" }, item.name, h("br"), h("small", {}, item.id)));
    }
    if (!items?.length) list.append(h("div", { class: "muted" }, "Nothing yet."));
    if (!selectedId) { editor.append(h("div", { class: "muted" }, "Pick an item or create one.")); return; }
    const isNew = selectedId === "new";
    let item = template;
    if (!isNew) {
      const result = await api("GET", `${base}/${selectedId}`);
      if (!result.ok) { editor.append(msg("bad", describeError(result.data))); return; }
      item = result.data;
    }
    const area = jsonArea(item);
    const status = h("div");
    const save = async () => {
      status.replaceChildren();
      const [body, error] = parseJson(area, "body");
      if (error) { status.append(error); return; }
      const result = isNew ? await api("POST", base, body) : await api("PUT", `${base}/${selectedId}`, body);
      if (result.ok) { status.append(msg("ok", `saved ${result.data.id}`)); if (isNew) location.hash = `#${base.slice(1)}/${result.data.id}`; else area.value = pretty(result.data); }
      else status.append(msg("bad", describeError(result.data)));
    };
    editor.append(h("div", { class: "card" },
      h("h3", {}, isNew ? "New" : selectedId), area,
      h("div", { class: "actions" },
        h("button", { class: "primary", onclick: save }, isNew ? "Create" : "Save (PUT)"),
        !isNew && h("button", { class: "danger", onclick: async () => {
          if (!confirm(`Delete ${selectedId}?`)) return;
          const result = await api("DELETE", `${base}/${selectedId}`);
          if (result.ok) location.hash = `#${base.slice(1)}`; else status.append(msg("bad", describeError(result.data)));
        } }, "Delete")),
      status));
    if (!isNew && extras) extras(editor, item);
  };
}

const FORM_TEMPLATE = {
  name: "New form", description: "",
  fields: [
    { id: "name", type: "text", label: "Name", required: true },
    { id: "hasPet", type: "radio", label: "Pet?", required: true, options: ["Yes", "No"] },
    { id: "petType", type: "select", label: "Type", options: ["Dog", "Cat"],
      logic: { action: "show", match: "all", conditions: [{ fieldId: "hasPet", operator: "equals", value: "Yes" }] } },
  ],
};

pages.forms = jsonResourcePage("Forms", "/forms", FORM_TEMPLATE, (root, form) => {
  const sample = {};
  for (const field of form.fields) sample[field.id] = field.type === "checkbox" ? true : field.type === "number" ? 1 : field.type === "date" ? "2026-01-01" : (field.options?.[0] ?? "text");
  const area = jsonArea({ data: sample, submittedBy: "console", metadata: { pageUrl: "/ui" } });
  const status = h("div");
  const listBox = h("div");
  const loadSubmissions = async () => {
    const { data } = await api("GET", `/forms/${form.id}/submissions?limit=20`);
    listBox.replaceChildren(h("div", { class: "muted" }, `${data?.total ?? 0} submission(s)`),
      ...(data?.items || []).map((s) => h("details", {}, h("summary", {}, `${s.id} - ${s.metadata.submittedAt}`), h("pre", { class: "json" }, pretty(s)))));
  };
  root.append(h("div", { class: "card" }, h("h3", {}, "Submit to this form"), area,
    h("div", { class: "actions" }, h("button", { class: "primary", onclick: async () => {
      status.replaceChildren();
      const [body, error] = parseJson(area, "body");
      if (error) { status.append(error); return; }
      const result = await api("POST", `/forms/${form.id}/submissions`, body);
      status.append(result.ok ? msg("ok", `saved ${result.data.id}; hidden: ${result.data.metadata.hiddenFields.join(", ") || "none"}`) : msg("bad", describeError(result.data)));
      if (result.ok) loadSubmissions();
    } }, "Submit")), status, h("h3", {}, "Submissions"), listBox));
  loadSubmissions();
});

pages["option-lists"] = jsonResourcePage("Option lists", "/option-lists", { name: "Sizes", description: "", options: ["S", "M", "L"] });

// -- submissions ---------------------------------------------------------------------------------

pages.submissions = async (view) => {
  view.append(h("h2", {}, "Submissions"));
  const formId = h("input", { type: "text", placeholder: "formId (optional)" });
  const limit = h("input", { type: "number", value: 20, min: 0, max: 1000 });
  const offset = h("input", { type: "number", value: 0, min: 0 });
  const out = h("div");
  const load = async () => {
    const qs = new URLSearchParams();
    if (formId.value) qs.set("formId", formId.value);
    qs.set("limit", limit.value); qs.set("offset", offset.value);
    const { ok, data } = await api("GET", `/submissions?${qs}`);
    out.replaceChildren();
    if (!ok) { out.append(msg("bad", describeError(data))); return; }
    out.append(h("div", { class: "muted" }, `${data.total} total`),
      h("div", { class: "grid-wrap" }, h("table", { class: "grid" },
        h("thead", {}, h("tr", {}, ["id", "form", "submitted", "by", "data"].map((t) => h("th", {}, t)))),
        h("tbody", {}, data.items.map((s) => h("tr", {}, h("td", {}, h("a", { href: "#", onclick: async (e) => {
          e.preventDefault(); const one = await api("GET", `/submissions/${s.id}`); out.append(h("pre", { class: "json" }, pretty(one.data)));
        } }, s.id)), h("td", {}, `${s.formName} (${s.formId})`), h("td", {}, s.metadata.submittedAt), h("td", {}, s.metadata.submittedBy ?? ""), cellFor(s.data)))))));
  };
  view.append(h("div", { class: "card inline" },
    h("div", {}, h("label", {}, "Form id"), formId), h("div", { class: "narrow" }, h("label", {}, "Limit"), limit),
    h("div", { class: "narrow" }, h("label", {}, "Offset"), offset), h("button", { class: "primary narrow", onclick: load }, "Load")), out);
  load();
};

// -- raw request -----------------------------------------------------------------------------------

pages.request = (view) => {
  view.append(h("h2", {}, "Raw request"));
  const method = h("select", {}, ["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => h("option", { value: m }, m)));
  const path = h("input", { type: "text", value: "/health", list: "paths" });
  const body = h("textarea", { class: "code", placeholder: "JSON body (POST / PUT / PATCH)", spellcheck: false });
  const out = h("div");
  const known = ["/health", "/forms", "/forms/{id}", "/forms/{id}/submissions", "/submissions", "/submissions/{id}", "/option-lists", "/option-lists/{id}",
    "/tables", "/tables/{id}", "/tables/{id}/check", "/tables/{ref}/schema", "/tables/{ref}/rows", "/tables/{ref}/rows/query", "/tables/{ref}/rows/{id}",
    "/sql/query", "/sql/tables", "/sql/rules", "/images"];
  view.append(h("div", { class: "card" },
    h("div", { class: "inline" }, h("div", { class: "narrow" }, h("label", {}, "Method"), method), h("div", {}, h("label", {}, "Path"), path, h("datalist", { id: "paths" }, known.map((p) => h("option", { value: p }))))),
    h("label", {}, "Body"), body,
    h("div", { class: "actions" }, h("button", { class: "primary", onclick: async () => {
      out.replaceChildren();
      let payload;
      if (body.value.trim()) { const [parsed, error] = parseJson(body, "body"); if (error) { out.append(error); return; } payload = parsed; }
      const result = await api(method.value, path.value, payload);
      out.append(msg(result.ok ? "ok" : "bad", `${result.status}`), h("pre", { class: "json" }, pretty(result.data)));
    } }, "Send"))), out);
};

route();

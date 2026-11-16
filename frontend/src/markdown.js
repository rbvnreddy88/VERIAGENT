// Robust Markdown -> HTML converter for VeriAgent reports.
// Converts tables, headings, charts, and turns evidence citations into clickable interactive chips.

const esc = (s) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function inline(s) {
  let t = esc(s);
  t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  t = t.replace(/\[((?:EV-\d+)(?:,\s*EV-\d+)*)\]/g, (_, ids) =>
    ids
      .split(/,\s*/)
      .map((id) => `<button class="ev" data-ev="${id}" title="Click to inspect verified evidence calculation">${id}</button>`)
      .join(" "),
  );
  t = t.replace(/\[unverified\]/g, '<span class="flag">unverified</span>');
  t = t.replace(/\[direction contradicts data\]/g, '<span class="flag">direction contradicts data</span>');
  return t;
}

export function mdToHtml(md, fileBase) {
  const out = [];
  let inList = false;
  let inTable = false;
  let tableRows = [];

  const closeList = () => {
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
  };

  const closeTable = () => {
    if (inTable && tableRows.length > 0) {
      const isSep = (r) => /^\|?[\s\-:|]+\|?$/.test(r.trim());
      let headerRow = null;
      let bodyRows = [];

      if (tableRows.length >= 2 && isSep(tableRows[1])) {
        headerRow = tableRows[0].replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
        bodyRows = tableRows.slice(2).map((r) => r.replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim()));
      } else {
        bodyRows = tableRows.map((r) => r.replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim()));
      }

      let html = '<div class="table-wrap"><table class="report-table">';
      if (headerRow) {
        html += '<thead><tr>' + headerRow.map((c) => `<th>${inline(c)}</th>`).join('') + '</tr></thead>';
      }
      if (bodyRows.length > 0) {
        html += '<tbody>';
        bodyRows.forEach((row) => {
          html += '<tr>' + row.map((c) => `<td>${inline(c)}</td>`).join('') + '</tr>';
        });
        html += '</tbody>';
      }
      html += '</table></div>';
      out.push(html);
      inTable = false;
      tableRows = [];
    }
  };

  for (const raw of md.split("\n")) {
    const s = raw.trim();
    if (!s) {
      closeList();
      closeTable();
      continue;
    }

    if (s.startsWith("|") && s.endsWith("|")) {
      closeList();
      inTable = true;
      tableRows.push(s);
      continue;
    }

    closeTable();

    if (s.startsWith("#")) {
      closeList();
      const level = Math.min(s.length - s.replace(/^#+/, "").length, 4);
      out.push(`<h${level}>${inline(s.replace(/^#+/, "").trim())}</h${level}>`);
    } else if (s.startsWith("![")) {
      closeList();
      const m = s.match(/!\[(.*?)\]\((.*?)\)/);
      if (m) {
        out.push(
          `<figure><img src="${fileBase}/${esc(m[2])}" alt="${esc(m[1])}"/><figcaption>${esc(m[1])}</figcaption></figure>`,
        );
      }
    } else if (/^([-*+]|\d+\.)\s+/.test(s)) {
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${inline(s.replace(/^([-*+]|\d+\.)\s+/, ""))}</li>`);
    } else {
      closeList();
      out.push(`<p>${inline(s)}</p>`);
    }
  }

  closeList();
  closeTable();
  return out.join("\n");
}

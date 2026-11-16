// Tiny Markdown -> HTML converter for VeriAgent reports.
// Evidence citations become clickable chips; chart paths are rewritten to the API.

const esc = (s) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function inline(s) {
  let t = esc(s);
  t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  t = t.replace(/\[((?:EV-\d+)(?:,\s*EV-\d+)*)\]/g, (_, ids) =>
    ids
      .split(/,\s*/)
      .map((id) => `<button class="ev" data-ev="${id}">${id}</button>`)
      .join(""),
  );
  t = t.replace(/\[unverified\]/g, '<span class="flag">unverified</span>');
  t = t.replace(/\[direction contradicts data\]/g, '<span class="flag">direction contradicts data</span>');
  return t;
}

export function mdToHtml(md, fileBase) {
  const out = [];
  let inList = false;
  const close = () => {
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
  };
  for (const raw of md.split("\n")) {
    const s = raw.trim();
    if (!s) {
      close();
      continue;
    }
    if (s.startsWith("#")) {
      close();
      const level = Math.min(s.length - s.replace(/^#+/, "").length, 4);
      out.push(`<h${level}>${inline(s.replace(/^#+/, "").trim())}</h${level}>`);
    } else if (s.startsWith("![")) {
      close();
      const m = s.match(/!\[(.*?)\]\((.*?)\)/);
      if (m)
        out.push(
          `<figure><img src="${fileBase}/${esc(m[2])}" alt="${esc(m[1])}"/><figcaption>${esc(m[1])}</figcaption></figure>`,
        );
    } else if (/^([-*+]|\d+\.)\s+/.test(s)) {
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${inline(s.replace(/^([-*+]|\d+\.)\s+/, ""))}</li>`);
    } else {
      close();
      out.push(`<p>${inline(s)}</p>`);
    }
  }
  close();
  return out.join("\n");
}

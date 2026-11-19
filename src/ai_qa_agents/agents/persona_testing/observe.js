// Collects what the model sees: interactive elements and images, each with a
// CSS selector precise enough to replay the action later.
(maxElements) => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const quote = (v) => v.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  const own = (el) => {
    if (el.getAttribute("data-test")) return `[data-test="${quote(el.getAttribute("data-test"))}"]`;
    if (el.id) return `#${CSS.escape(el.id)}`;
    if (el.getAttribute("name") && /^(input|select|textarea)$/i.test(el.tagName))
      return `${el.tagName.toLowerCase()}[name="${quote(el.getAttribute("name"))}"]`;
    return "";
  };
  const path = (el) => {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.body) {
      const anchor = own(node);
      if (anchor) { parts.unshift(anchor); break; }
      const tag = node.tagName.toLowerCase();
      const same = Array.from(node.parentElement ? node.parentElement.children : []).filter(
        (c) => c.tagName === node.tagName
      );
      parts.unshift(same.length > 1 ? `${tag}:nth-of-type(${same.indexOf(node) + 1})` : tag);
      node = node.parentElement;
    }
    return parts.join(" > ");
  };
  const selectorFor = (el) => {
    const css = own(el) || path(el);
    const all = Array.from(document.querySelectorAll(css));
    return all.length > 1 ? `${css} >> nth=${all.indexOf(el)}` : css;
  };
  const label = (el) =>
    (
      el.getAttribute("aria-label") ||
      (el.innerText || "").trim() ||
      el.getAttribute("placeholder") ||
      el.getAttribute("alt") ||
      el.getAttribute("value") ||
      el.getAttribute("title") ||
      ""
    ).replace(/\s+/g, " ").slice(0, 60);

  const interactive = Array.from(
    document.querySelectorAll(
      'a[href], button, input, select, textarea, [role="button"], [role="link"], [onclick], [tabindex]:not([tabindex="-1"])'
    )
  ).filter((el) => visible(el) && !(el.tagName === "INPUT" && el.type === "hidden"));

  const elements = interactive.slice(0, maxElements).map((el) => ({
    tag: el.tagName.toLowerCase(),
    type: el.getAttribute("type") || el.getAttribute("role") || "",
    label: label(el),
    selector: selectorFor(el),
    classes: (el.getAttribute("class") || "").split(/\s+/).filter(Boolean).slice(0, 2).join("."),
    value: "value" in el && el.tagName !== "BUTTON" ? String(el.value).slice(0, 60) : "",
    options:
      el.tagName === "SELECT" ? Array.from(el.options).slice(0, 8).map((o) => o.text.trim()) : [],
    disabled: !!el.disabled,
  }));

  const images = Array.from(document.images)
    .filter(visible)
    .slice(0, 30)
    .map((img) => ({
      selector: selectorFor(img),
      classes: (img.getAttribute("class") || "").split(/\s+/).filter(Boolean).slice(0, 2).join("."),
      alt: (img.getAttribute("alt") || "").slice(0, 60),
      src: (img.getAttribute("src") || "").slice(-80),
    }));

  return {
    url: location.href,
    title: document.title,
    text: (document.body ? document.body.innerText : "").replace(/\n{3,}/g, "\n\n").slice(0, 2500),
    elements,
    truncated: interactive.length > maxElements,
    images,
  };
}

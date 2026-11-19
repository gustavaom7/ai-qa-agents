(function () {
  // Theme resolved before first paint: saved choice -> system preference -> dark.
  var KEY = "ai-qa-report-theme";
  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) {}
  var light = saved ? saved === "light" : matchMedia("(prefers-color-scheme: light)").matches;
  if (light) document.documentElement.setAttribute("data-theme", "light");

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("tema");
    if (!btn) return;
    var sync = function () {
      var isLight = document.documentElement.getAttribute("data-theme") === "light";
      btn.setAttribute("aria-pressed", String(isLight));
      btn.setAttribute("aria-label", isLight ? "Switch to dark theme" : "Switch to light theme");
    };
    sync();
    btn.addEventListener("click", function () {
      var toLight = document.documentElement.getAttribute("data-theme") !== "light";
      if (toLight) document.documentElement.setAttribute("data-theme", "light");
      else document.documentElement.removeAttribute("data-theme");
      try { localStorage.setItem(KEY, toLight ? "light" : "dark"); } catch (e) {}
      sync();
    });
  });
})();

// Petites interactions de l'interface (aucune logique métier côté client).
(function () {
  "use strict";

  // Infobulles Bootstrap (aides des filtres, badges) : au chargement et après chaque mise à jour HTMX
  function initTooltips(root) {
    if (!window.bootstrap) return;
    root.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
      window.bootstrap.Tooltip.getOrCreateInstance(el);
    });
  }
  document.addEventListener("DOMContentLoaded", function () { initTooltips(document); });
  document.addEventListener("htmx:afterSettle", function (event) { initTooltips(event.target); });
  document.addEventListener("htmx:beforeSwap", function () {
    document.querySelectorAll(".tooltip").forEach(function (el) { el.remove(); });
  });

  // Raccourci « / » : placer le curseur dans la recherche
  document.addEventListener("keydown", function (event) {
    if (event.key !== "/" || event.target.closest("input, textarea, select, [contenteditable]")) return;
    var input = document.getElementById("ref-query");
    if (input) { event.preventDefault(); input.focus(); input.select(); }
  });

  // Suggestions : fermeture (Échap, clic ailleurs, envoi) et navigation au clavier (flèches)
  function closeSuggestions() {
    var box = document.getElementById("ref-suggest");
    if (box) box.innerHTML = "";
  }
  document.addEventListener("click", function (event) {
    if (!event.target.closest("#ref-suggest, #ref-query")) closeSuggestions();
  });
  document.addEventListener("submit", closeSuggestions);
  document.addEventListener("keydown", function (event) {
    var box = document.getElementById("ref-suggest");
    if (!box || !event.target.closest("#ref-query, #ref-suggest")) return;
    if (event.key === "Escape") { closeSuggestions(); return; }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    var items = Array.prototype.slice.call(box.querySelectorAll(".ref-suggest-item"));
    if (!items.length) return;
    event.preventDefault();
    var index = items.indexOf(document.activeElement);
    index = event.key === "ArrowDown" ? Math.min(index + 1, items.length - 1) : index - 1;
    if (index < 0) { document.getElementById("ref-query").focus(); } else { items[index].focus(); }
  });

  // Sélecteur de release du bandeau : recharge la page courante avec ?release=
  document.addEventListener("change", function (event) {
    var select = event.target.closest("#ref-release-select");
    if (!select || !select.value) return;
    var url = new URL(window.location.href);
    if (url.pathname.indexOf("/imports") === 0 || url.pathname.indexOf("/releases") === 0
        || url.pathname.indexOf("/settings") === 0 || url.pathname.indexOf("/compare") === 0) {
      url = new URL("/search-terms/terms", window.location.origin);
    }
    url.searchParams.set("release", select.value);
    url.searchParams.delete("page");
    window.location.assign(url.toString());
  });

  // Auteur des modifications : mémorisé dans un cookie (pas d'authentification)
  document.addEventListener("change", function (event) {
    var input = event.target.closest("#ref-user-input");
    if (!input) return;
    var value = encodeURIComponent(input.value.trim());
    document.cookie = "ref_user=" + value + "; path=/; max-age=31536000; SameSite=Lax";
    var saved = document.getElementById("ref-user-saved");
    if (saved) {
      saved.classList.remove("d-none");
      setTimeout(function () { saved.classList.add("d-none"); }, 2500);
    }
  });

  // Ligne de tableau cliquable (data-href)
  document.addEventListener("click", function (event) {
    var row = event.target.closest("tr[data-href]");
    if (!row || event.target.closest("a, button, input, label")) return;
    if (event.ctrlKey || event.metaKey) {
      window.open(row.dataset.href, "_blank");
    } else {
      window.location.assign(row.dataset.href);
    }
  });

  // Sélecteur de concept cible : reporte le concept choisi dans le formulaire
  document.addEventListener("click", function (event) {
    var option = event.target.closest(".ref-concept-option");
    if (!option) return;
    var form = option.closest("form");
    form.querySelector("[name=target_concept_id]").value = option.dataset.conceptId;
    var label = form.querySelector("#ref-target-label");
    if (label) label.textContent = option.dataset.label;
    form.querySelectorAll(".ref-concept-option").forEach(function (el) { el.classList.remove("active"); });
    option.classList.add("active");
  });

  // Liste déroulante soumettant son formulaire (ex. choix de la feuille Excel)
  document.addEventListener("change", function (event) {
    var field = event.target.closest("[data-ref-autosubmit]");
    if (field && field.form) field.form.submit();
  });

  // Bouton de fermeture d'un panneau chargé par HTMX (vide la cible)
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-ref-clear]");
    if (!button) return;
    var target = document.getElementById(button.dataset.refClear);
    if (target) target.innerHTML = "";
  });

  // Import, étape 2 : choisir une colonne du fichier vide la valeur par défaut, et inversement
  document.addEventListener("input", function (event) {
    var field = event.target.closest("[data-ref-pair]");
    if (!field || !field.value) return;
    var other = document.getElementById(field.dataset.refPair);
    if (other) other.value = "";
  });
})();

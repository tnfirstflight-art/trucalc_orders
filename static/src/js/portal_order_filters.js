/** @odoo-module **/

function setExpanded(toggle, panel, expanded) {
    toggle.setAttribute("aria-expanded", String(expanded));
    panel.setAttribute("aria-hidden", String(!expanded));
    panel.classList.toggle("show", expanded);
}

function initializePortalOrderFilters() {
    for (const toggle of document.querySelectorAll(".o_trucalc_filter_toggle")) {
        if (toggle.dataset.trucalcFilterReady === "true") {
            continue;
        }
        const selector = toggle.dataset.trucalcFilterTarget;
        const panel = selector && document.querySelector(selector);
        if (!panel) {
            continue;
        }
        setExpanded(toggle, panel, false);
        toggle.addEventListener("click", () => {
            setExpanded(
                toggle,
                panel,
                toggle.getAttribute("aria-expanded") !== "true",
            );
        });
        panel.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                setExpanded(toggle, panel, false);
                toggle.focus();
            }
        });
        toggle.dataset.trucalcFilterReady = "true";
    }
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializePortalOrderFilters, {
        once: true,
    });
} else {
    initializePortalOrderFilters();
}

/** @odoo-module **/

function setExpanded(toggle, panel, expanded) {
    toggle.setAttribute("aria-expanded", String(expanded));
    panel.setAttribute("aria-hidden", String(!expanded));
    panel.classList.toggle("show", expanded);
}

function initializePortalOrderFilters() {
    for (const form of document.querySelectorAll(".o_trucalc_order_list_toolbar")) {
        if (form.dataset.trucalcToolbarReady === "true") {
            continue;
        }
        const search = form.querySelector('input[type="search"][name="search"]');
        if (search) {
            const submitClearedSearch = () => {
                if (search.value) {
                    return;
                }
                search.removeAttribute("name");
                form.submit();
            };
            search.addEventListener("search", submitClearedSearch);
            search.addEventListener("input", submitClearedSearch);
        }
        const toggle = form.querySelector(".o_trucalc_filter_toggle");
        if (!toggle) {
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
        form.dataset.trucalcToolbarReady = "true";
    }
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializePortalOrderFilters, {
        once: true,
    });
} else {
    initializePortalOrderFilters();
}

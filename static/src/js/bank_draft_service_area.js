/** @odoo-module **/

import { formatInspectionPhone } from "./inspection_phone_format";

function uniqueBy(items, key) {
    const values = new Map();
    for (const item of items) {
        values.set(String(item[key]), item);
    }
    return [...values.values()];
}

function addOption(select, value, label, selected) {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = label;
    option.selected = String(value) === String(selected || "");
    select.append(option);
}

function initializeServiceAreaForm(form) {
    const phoneInput = form.querySelector("input[name='inspection_contact_phone']");
    if (phoneInput) {
        phoneInput.addEventListener("blur", () => {
            phoneInput.value = formatInspectionPhone(phoneInput.value);
        });
    }
    const stateSelect = form.querySelector("select[name='service_state_id']");
    const countySelect = form.querySelector("select[name='service_county']");
    const serviceSelect = form.querySelector("select[name='service_type']");
    const areaInput = form.querySelector("input[name='service_area_id']");
    const feeDisplay = form.querySelector("[data-service-fee]");
    if (!stateSelect || !countySelect || !serviceSelect || !areaInput) {
        return;
    }

    let areas;
    try {
        areas = JSON.parse(form.dataset.serviceAreas || "[]");
    } catch {
        areas = [];
    }
    const selectedState = form.dataset.selectedState || "";
    const selectedCounty = form.dataset.selectedCounty || "";
    const selectedService = form.dataset.selectedService || "";
    const selectedArea = form.dataset.selectedArea || "";

    const resetSelect = (select, label) => {
        select.replaceChildren();
        addOption(select, "", label, "");
    };

    const displayFee = (area) => {
        if (!feeDisplay) {
            return;
        }
        feeDisplay.textContent = area && area.service_fee !== false
            ? `$${area.service_fee}`
            : "Not configured";
    };

    const populateServices = (stateId, county, selected = "") => {
        resetSelect(serviceSelect, "Select a Service");
        areaInput.value = "";
        const matches = uniqueBy(
            areas.filter(
                (area) => String(area.state_id) === String(stateId)
                    && area.county === county
            ),
            "service_type"
        ).sort((left, right) => left.service_label.localeCompare(right.service_label));
        for (const area of matches) {
            addOption(serviceSelect, area.service_type, area.service_label, selected);
        }
        serviceSelect.disabled = !county || !matches.length;
        const selectedMatch = matches.find(
            (area) => area.service_type === selected
                && String(area.id) === String(selectedArea)
        );
        if (selectedMatch) {
            areaInput.value = String(selectedMatch.id);
        }
        displayFee(selectedMatch);
    };

    const populateCounties = (stateId, selected = "", service = "") => {
        resetSelect(countySelect, "Select a County");
        resetSelect(serviceSelect, "Select a Service");
        areaInput.value = "";
        const matches = uniqueBy(
            areas.filter((area) => String(area.state_id) === String(stateId)),
            "county"
        ).sort((left, right) => left.county.localeCompare(right.county));
        for (const area of matches) {
            addOption(countySelect, area.county, area.county, selected);
        }
        countySelect.disabled = !stateId || !matches.length;
        serviceSelect.disabled = true;
        if (selected && matches.some((area) => area.county === selected)) {
            populateServices(stateId, selected, service);
        }
    };

    resetSelect(stateSelect, "Select a State");
    const states = uniqueBy(areas, "state_id").sort(
        (left, right) => left.state_name.localeCompare(right.state_name)
    );
    for (const area of states) {
        addOption(stateSelect, area.state_id, area.state_name, selectedState);
    }
    populateCounties(selectedState, selectedCounty, selectedService);

    stateSelect.addEventListener("change", () => {
        populateCounties(stateSelect.value);
    });
    countySelect.addEventListener("change", () => {
        populateServices(stateSelect.value, countySelect.value);
    });
    serviceSelect.addEventListener("change", () => {
        const match = areas.find(
            (area) => String(area.state_id) === stateSelect.value
                && area.county === countySelect.value
                && area.service_type === serviceSelect.value
        );
        areaInput.value = match ? String(match.id) : "";
        displayFee(match);
    });
}

function initializeServiceAreaForms() {
    for (const form of document.querySelectorAll(".o_trucalc_bank_draft_form")) {
        initializeServiceAreaForm(form);
    }
}

function initializeDraftUploadForms() {
    for (const uploadForm of document.querySelectorAll(
        ".o_trucalc_bank_draft_upload_form"
    )) {
        uploadForm.addEventListener("submit", () => {
            for (const generated of uploadForm.querySelectorAll(
                "input[data-draft-field]"
            )) {
                generated.remove();
            }
            const draftForm = document.getElementById(uploadForm.dataset.draftFormId);
            if (!draftForm) {
                return;
            }
            for (const [name, value] of new FormData(draftForm)) {
                if (name === "csrf_token" || name === "draft_action") {
                    continue;
                }
                const hidden = document.createElement("input");
                hidden.type = "hidden";
                hidden.name = name;
                hidden.value = value;
                hidden.dataset.draftField = "true";
                uploadForm.append(hidden);
            }
        });
    }
}

function initializeBankDraftForms() {
    initializeServiceAreaForms();
    initializeDraftUploadForms();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeBankDraftForms, { once: true });
} else {
    initializeBankDraftForms();
}

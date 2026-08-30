/** @odoo-module **/

/**
 * Format recognizable ordinary U.S. phone input for presentation.
 * Other values are only whitespace-sanitized; the server remains authoritative.
 */
export function formatInspectionPhone(value) {
    const phone = String(value || "")
        .replace(/[\x00-\x1f\x7f]+/g, " ")
        .replace(/\s+/g, " ")
        .trim();
    if (!phone) {
        return "";
    }

    const extensionMatch = phone.match(
        /\s*(?:ext\.?|x)\s*[:#.-]?\s*(\d{1,10})\s*$/i
    );
    const extension = extensionMatch ? extensionMatch[1] : "";
    const basePhone = extensionMatch
        ? phone.slice(0, extensionMatch.index).trim()
        : phone;
    const digits = (basePhone.match(/\d/g) || []).join("");
    const usDigits = digits.length === 11 && digits.startsWith("1")
        ? digits.slice(1)
        : digits;
    const explicitNonUsCountry = basePhone.startsWith("+")
        && !(digits.length === 11 && digits.startsWith("1"));

    if (usDigits.length !== 10 || explicitNonUsCountry) {
        return phone;
    }
    let formatted = `(${usDigits.slice(0, 3)}) ${usDigits.slice(3, 6)}-${usDigits.slice(6)}`;
    if (extension) {
        formatted += ` ext. ${extension}`;
    }
    return formatted;
}

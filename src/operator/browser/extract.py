"""Extractor v2: DOM inspection with button yes/no, hidden checkboxes, and combobox display values."""

from __future__ import annotations

import logging
import re
from typing import Any
from playwright.sync_api import Page


from src.operator.browser.models import FieldSpec

logger = logging.getLogger(__name__)

EXTRACT_V2_SCRIPT = """() => {
    window.__opid_seq = window.__opid_seq || 0;
    const clean = s => (s || '').replace(/\\s+/g, ' ').trim();

    const getLabel = (el) => {
        let text = '';
        if (el.labels && el.labels.length) {
            text = [...el.labels].map(l => l.innerText).join(' ');
        }
        if (!text && el.getAttribute('aria-labelledby')) {
            text = el.getAttribute('aria-labelledby').split(' ')
                .map(id => (document.getElementById(id) || {}).innerText || '')
                .join(' ');
        }
        if (!text) text = el.getAttribute('aria-label') || '';
        if (!text) {
            let parent = el.parentElement;
            let depth = 0;
            while (parent && depth < 5 && !text) {
                const l = parent.querySelector('label, legend');
                if (l && l.innerText && l.innerText.trim()) {
                    text = l.innerText;
                    break;
                }
                parent = parent.parentElement;
                depth++;
            }
        }
        if (!text) text = el.placeholder || el.name || el.id || '';
        return clean(text).slice(0, 250);
    };

    const getGroup = (el) => {
        const g = el.closest('fieldset, [role=group], [role=radiogroup]');
        if (!g) return '';
        const leg = g.querySelector('legend, [id]>label, label');
        return clean((g.getAttribute('aria-label') || (leg && leg.innerText) || '')).slice(0, 200);
    };

    const isVisibleOrCustom = (el) => {
        const isFile = el.type === 'file';
        const isCheckOrRadio = el.type === 'checkbox' || el.type === 'radio';
        if (isFile) return true;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        if (rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none') {
            return true;
        }
        // Hidden or styled custom checkbox/radio with visible parent wrapper
        if (isCheckOrRadio) {
            const parent = el.closest('label, div[class*=checkbox], div[class*=radio], span');
            if (parent) {
                const prect = parent.getBoundingClientRect();
                const pstyle = window.getComputedStyle(parent);
                if (prect.width > 0 && prect.height > 0 && pstyle.visibility !== 'hidden' && pstyle.display !== 'none') {
                    return true;
                }
            }
        }
        return false;
    };

    const results = [];
    const elements = [...document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select')];

    // Also look for hidden inputs that represent active custom checkboxes
    const hiddenChecks = [...document.querySelectorAll('input[type=checkbox][style*="display: none"], input[type=checkbox][style*="display:none"], input[type=checkbox].hidden, input[type=checkbox][aria-hidden=true]')];
    for (const hc of hiddenChecks) {
        if (!elements.includes(hc)) elements.push(hc);
    }

    for (const el of elements) {
        if (!isVisibleOrCustom(el)) continue;
        if (!el.dataset.opid) {
            el.dataset.opid = String(++window.__opid_seq);
        }

        const isCombo = el.getAttribute('role') === 'combobox' ||
                        el.getAttribute('aria-haspopup') === 'listbox' ||
                        el.getAttribute('aria-autocomplete') === 'list';

        let val = '';
        if (el.tagName === 'SELECT') {
            val = clean([...el.selectedOptions].map(o => o.text).join(', '));
        } else if (el.type === 'checkbox' || el.type === 'radio') {
            val = String(el.checked);
        } else {
            val = el.value || '';
        }

        // Combobox display value extraction
        if (isCombo && !val) {
            const container = el.closest('div[class*=select], div[class*=Select], div[class*=control], div[class*=container]') || el.parentElement.parentElement;
            if (container) {
                val = clean(container.innerText || '').slice(0, 100);
            }
        }

        const label = getLabel(el);
        const group = getGroup(el);
        const elType = el.tagName === 'SELECT' ? 'select' : (el.type || el.tagName.toLowerCase());
        const isRequired = el.required || el.getAttribute('aria-required') === 'true' || /\\*/.test(label);

        let options = undefined;
        if (el.tagName === 'SELECT') {
            options = [...el.options].map(o => clean(o.text)).filter(Boolean).slice(0, 50);
        }

        const pattern = el.getAttribute('pattern') || null;
        const min = el.getAttribute('min') || null;
        const max = el.getAttribute('max') || null;
        const rawMaxLen = el.getAttribute('maxlength');
        const maxLength = rawMaxLen ? parseInt(rawMaxLen, 10) : (el.maxLength > 0 && el.maxLength < 100000 ? el.maxLength : null);
        const placeholder = el.placeholder || el.getAttribute('placeholder') || null;

        results.push({
            id: el.dataset.opid,
            label: label,
            group: group,
            type: elType,
            required: isRequired,
            options: options || [],
            current_value: val,
            is_combobox: isCombo,
            selector: `[data-opid="${el.dataset.opid}"]`,
            placeholder: placeholder,
            pattern: pattern,
            min: min,
            max: max,
            maxlength: Number.isInteger(maxLength) ? maxLength : null
        });
    }

    // Button-based Yes/No widgets (e.g. Ashby/Greenhouse custom button groups)
    const buttonGroups = [...document.querySelectorAll('fieldset, [role=radiogroup], div[class*=button-group], div[class*=yes-no]')];
    for (const bg of buttonGroups) {
        const buttons = [...bg.querySelectorAll('button')].map(b => clean(b.innerText).toLowerCase());
        if (buttons.includes('yes') && buttons.includes('no')) {
            const bgLabel = clean(bg.getAttribute('aria-label') || (bg.querySelector('legend, label') || {}).innerText || '');
            if (bgLabel) {
                const activeBtn = bg.querySelector('button[aria-pressed=true], button.active, button[class*=selected]');
                const activeVal = activeBtn ? clean(activeBtn.innerText) : '';
                const opid = String(++window.__opid_seq);
                bg.dataset.opid = opid;
                results.push({
                    id: opid,
                    label: bgLabel,
                    group: 'Button Group',
                    type: 'yes_no_button',
                    required: /\\*/.test(bgLabel),
                    options: ['Yes', 'No'],
                    current_value: activeVal,
                    is_combobox: false,
                    selector: `[data-opid="${opid}"]`,
                    placeholder: null,
                    pattern: null,
                    min: null,
                    max: null,
                    maxlength: null
                });
            }
        }
    }

    return results;
}"""


def build_stable_key(label: str, field_type: str, group: str, occurrence: int) -> str:
    """Build canonical field key: label|type|group|n."""
    clean_label = re.sub(r"\s+", " ", label.strip())
    clean_type = field_type.strip().lower()
    clean_group = re.sub(r"\s+", " ", group.strip())
    return f"{clean_label}|{clean_type}|{clean_group}|{occurrence}"


class FieldExtractor:
    """Extracts typed FieldSpec list from live DOM with stable keys."""

    def extract_fields(self, page: Page) -> list[FieldSpec]:
        """Extract fields and assign stable collision-free keys."""
        raw_items = page.evaluate(EXTRACT_V2_SCRIPT)
        specs: list[FieldSpec] = []
        counts: dict[str, int] = {}

        for item in raw_items:
            base_signature = f"{item['label']}|{item['type']}|{item['group']}"
            counts[base_signature] = counts.get(base_signature, -1) + 1
            key = build_stable_key(
                label=item["label"],
                field_type=item["type"],
                group=item["group"],
                occurrence=counts[base_signature],
            )

            specs.append(
                FieldSpec(
                    id=item["id"],
                    key=key,
                    label=item["label"],
                    group=item["group"],
                    type=item["type"],
                    options=item.get("options", []),
                    required=item.get("required", False),
                    current_value=item.get("current_value", ""),
                    selector=item.get("selector"),
                    is_combobox=item.get("is_combobox", False),
                    placeholder=item.get("placeholder"),
                    pattern=item.get("pattern"),
                    min=item.get("min"),
                    max=item.get("max"),
                    maxlength=item.get("maxlength"),
                )
            )

        return specs

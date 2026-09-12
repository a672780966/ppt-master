/*
 * PPT Master - Workbench Panels (P5)
 *
 * Additive to app.js: renders Runtime Truth from /api/runtime/* and
 * /api/confirm/* on top of the existing editor, which is otherwise
 * untouched. Never computes a validation/export/hooks decision itself --
 * every value shown here comes straight from a JSON response already
 * produced by the real P1-P4 runtime.
 *
 * Two hook points into app.js (both additive, both guarded by
 * `if (window.Workbench)` so this file is optional):
 *   - Workbench.init() is called once at startup, after app.js's own init.
 *   - Workbench.refresh() is called on the same 2s --live poll tick
 *     app.js's loadSlides() already runs -- no second polling loop.
 */
(function () {
    "use strict";

    var elTopbarProgress = document.getElementById("workbench-progress");
    var elHooksBadge = document.getElementById("workbench-hooks-badge");
    var elExportBadge = document.getElementById("workbench-export-status");
    var btnValidateDeck = document.getElementById("workbench-btn-validate-deck");
    var btnExport = document.getElementById("workbench-btn-export");
    var btnDiagnosticsToggle = document.getElementById("workbench-btn-diagnostics");
    var btnDiagnosticsClose = document.getElementById("workbench-diagnostics-close");
    var elDiagnostics = document.getElementById("workbench-diagnostics");
    var elDiagnosticsBody = document.getElementById("workbench-diagnostics-body");
    var elValidationBody = document.getElementById("workbench-validation-body");
    var elAiEditBody = document.getElementById("workbench-ai-edit-body");
    var elPlanBody = document.getElementById("workbench-plan-body");
    var elConfirmBody = document.getElementById("workbench-confirm-body");
    var tabButtons = document.querySelectorAll(".workbench-tab");
    var tabPanels = {
        edit: document.getElementById("workbench-tab-edit"),
        validation: document.getElementById("workbench-tab-validation"),
        plan: document.getElementById("workbench-tab-plan"),
    };

    var lastBuildState = null;
    var lastSlides = [];
    var activeSlideFilename = null;
    var currentSelectionIds = [];
    var activeJobId = null;
    var jobPollTimer = null;

    function fetchJson(url, opts) {
        return fetch(url, opts).then(function (res) {
            return res.json().catch(function () { return {}; });
        }).catch(function () {
            return null;
        });
    }

    function activeSlideName() {
        var active = document.querySelector(".slide-item.active");
        return active ? active.getAttribute("data-name") : null;
    }

    // ---- Tabs ----------------------------------------------------------
    function selectTab(name) {
        tabButtons.forEach(function (btn) {
            btn.classList.toggle("active", btn.getAttribute("data-tab") === name);
        });
        Object.keys(tabPanels).forEach(function (key) {
            if (tabPanels[key]) tabPanels[key].hidden = key !== name;
        });
    }
    tabButtons.forEach(function (btn) {
        btn.addEventListener("click", function () {
            selectTab(btn.getAttribute("data-tab"));
        });
    });

    // ---- Build progress / export status / hooks badge (topbar) --------
    function renderTopbar(buildState) {
        if (!buildState || buildState.mode === "legacy") {
            elTopbarProgress.textContent = "Legacy project — no build state";
            elHooksBadge.textContent = "";
            elExportBadge.textContent = "";
            return;
        }
        var progress = buildState.progress || {};
        var parts = [];
        parts.push(progress.ready + " / " + progress.total + " slides ready");
        if (progress.building) parts.push(progress.building + " building");
        if (progress.needs_validation) parts.push(progress.needs_validation + " need validation");
        if (progress.failed) parts.push(progress.failed + " failed");
        if (progress.stale) parts.push(progress.stale + " stale");
        elTopbarProgress.textContent = parts.join(" · ");

        var hooks = (buildState.state && buildState.state.hooks) || {};
        elHooksBadge.textContent = "Safety checks: " + (hooks.mode === "enforce" ? "Enforced" : "Advisory");
        elHooksBadge.className = "workbench-badge workbench-badge-" + (hooks.mode === "enforce" ? "enforce" : "shadow");

        var exportInfo = buildState.export || {};
        var exportLabelMap = { never: "Never exported", outdated: "Export outdated", ready: "Export ready" };
        elExportBadge.textContent = exportLabelMap[exportInfo.code] || "";
        elExportBadge.className = "workbench-badge workbench-badge-export-" + exportInfo.code;
    }

    // ---- Slide-list badges (decorates app.js's own #slide-list items) -
    function renderSlideBadges(slides) {
        slides.forEach(function (slide) {
            var item = findSlideItemById(slide.id);
            if (!item) return;
            var existing = item.querySelector(".workbench-status-badge");
            if (existing) existing.remove();
            var badge = document.createElement("span");
            badge.className = "workbench-status-badge workbench-status-" + slide.validation_state + " workbench-slide-" + slide.status;
            badge.textContent = slide.display_label;
            item.appendChild(badge);
        });
    }

    // Slide list items are keyed by SVG filename (data-name), not by P<NN>
    // slide id -- match on the numeric prefix the same way the backend's
    // slide_id_from_page()/glob fallback does, so this stays correct even
    // when svg_path hasn't been recorded yet for an older project.
    function findSlideItemById(slideId) {
        var digits = String(parseInt(slideId.slice(1), 10));
        var padded = slideId.slice(1);
        var items = document.querySelectorAll(".slide-item[data-name]");
        for (var i = 0; i < items.length; i++) {
            var name = items[i].getAttribute("data-name") || "";
            if (name.indexOf(padded + "_") === 0 || name.indexOf(digits + "_") === 0) {
                return items[i];
            }
        }
        return null;
    }

    // ---- Validation tab (active slide) ---------------------------------
    function renderValidation(slides) {
        var name = activeSlideName();
        if (!name) {
            elValidationBody.textContent = "Select a slide to see its validation status.";
            return;
        }
        var digits = name.replace(/^0*([0-9]+).*$/, "$1");
        var slide = slides.filter(function (s) {
            return s.id === "P" + (digits.length < 2 ? "0" + digits : digits);
        })[0];
        if (!slide) {
            elValidationBody.textContent = "No runtime state recorded for this slide yet.";
            return;
        }
        elValidationBody.innerHTML = "";
        var rows = [
            ["Status", slide.display_label],
            ["Revision", String(slide.revision)],
            ["Validated revision", slide.validated_revision === null ? "—" : String(slide.validated_revision)],
            ["Validation", slide.validation_label],
        ];
        rows.forEach(function (pair) {
            var row = document.createElement("div");
            row.className = "workbench-kv";
            row.innerHTML = "<span>" + pair[0] + "</span><b>" + pair[1] + "</b>";
            elValidationBody.appendChild(row);
        });
        var btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = "Validate this slide";
        btn.addEventListener("click", function () {
            btn.disabled = true;
            fetchJson("/api/runtime/slides/" + slide.id + "/validate", { method: "POST" }).then(function () {
                btn.disabled = false;
                refresh();
            });
        });
        elValidationBody.appendChild(btn);
    }

    // ---- Ask AI panel (P6) ----------------------------------------------
    // Job status vocabulary shown to the user -- never raw internal status
    // strings like "patch_ready"/"applying" (SS22, SS27 of the P6 spec).
    var JOB_STATUS_LABEL = {
        queued: "Thinking…", running: "Thinking…", patch_ready: "Applying…",
        applying: "Applying…", validating: "Validating…",
        completed: "Done", completed_with_validation_error: "Done (validation issue)",
        failed: "Failed", conflicted: "Page changed — needs retry",
        cancelled: "Cancelled", interrupted: "Interrupted",
    };
    var JOB_ACTIVE_STATUSES = ["queued", "running", "patch_ready", "applying", "validating"];

    function slideIdFromFilename(name) {
        var match = /^0*([0-9]+)/.exec(name || "");
        if (!match) return null;
        var n = match[1];
        return "P" + (n.length < 2 ? "0" + n : n);
    }

    function stopJobPolling() {
        if (jobPollTimer) { window.clearInterval(jobPollTimer); jobPollTimer = null; }
    }

    function renderAskAi() {
        elAiEditBody.innerHTML = "";
        if (activeJobId) {
            renderJobPanel();
            return;
        }
        if (!currentSelectionIds.length) {
            var hint = document.createElement("div");
            hint.className = "workbench-empty";
            hint.textContent = "Select an object to ask AI to edit it.";
            elAiEditBody.appendChild(hint);
            return;
        }
        var label = document.createElement("div");
        label.className = "section-label";
        label.textContent = "Selected: " + currentSelectionIds.length + " object" + (currentSelectionIds.length > 1 ? "s" : "");
        elAiEditBody.appendChild(label);

        var input = document.createElement("textarea");
        input.id = "workbench-ai-instruction";
        input.placeholder = "Ask AI to edit the selection…";
        elAiEditBody.appendChild(input);

        var applyBtn = document.createElement("button");
        applyBtn.type = "button";
        applyBtn.textContent = "Apply";
        applyBtn.addEventListener("click", function () {
            var instruction = input.value.trim();
            if (!instruction) return;
            createAiEditJob("selection", currentSelectionIds, instruction, "inline", null);
        });
        elAiEditBody.appendChild(applyBtn);
    }

    function renderJobPanel() {
        elAiEditBody.innerHTML = "";
        fetchJson("/api/runtime/ai-edits/" + activeJobId).then(function (data) {
            if (!data || !data.ok) return;
            var job = data.job;
            elAiEditBody.innerHTML = "";
            var status = document.createElement("div");
            status.className = "workbench-job-status";
            status.textContent = JOB_STATUS_LABEL[job.status] || job.status;
            elAiEditBody.appendChild(status);

            if (JOB_ACTIVE_STATUSES.indexOf(job.status) !== -1) {
                return; // still polling -- nothing else to render yet
            }
            stopJobPolling();

            if (job.status === "completed" || job.status === "completed_with_validation_error") {
                var undoBtn = document.createElement("button");
                undoBtn.type = "button";
                undoBtn.textContent = "Undo";
                undoBtn.addEventListener("click", function () {
                    fetchJson("/api/runtime/ai-edits/" + job.job_id + "/undo", { method: "POST" }).then(function () {
                        activeJobId = null;
                        refresh();
                        // The existing --live poll (app.js loadSlides()) picks up the
                        // mtime change and shows its own reload banner if this slide
                        // is currently open -- no separate forced-reload path needed.
                    });
                });
                elAiEditBody.appendChild(undoBtn);
            } else if (job.status === "conflicted" || job.status === "failed") {
                var errMsg = document.createElement("div");
                errMsg.className = "workbench-empty";
                errMsg.textContent = (job.error && job.error.message) || "This edit could not be applied.";
                elAiEditBody.appendChild(errMsg);
                var retryBtn = document.createElement("button");
                retryBtn.type = "button";
                retryBtn.textContent = "Retry on latest version";
                retryBtn.addEventListener("click", function () {
                    fetchJson("/api/runtime/ai-edits/" + job.job_id + "/retry", { method: "POST" }).then(function (result) {
                        activeJobId = result && result.ok ? result.job.job_id : null;
                        renderAskAi();
                        if (activeJobId) startJobPolling();
                    });
                });
                elAiEditBody.appendChild(retryBtn);
            }

            var dismissBtn = document.createElement("button");
            dismissBtn.type = "button";
            dismissBtn.textContent = "Dismiss";
            dismissBtn.addEventListener("click", function () {
                activeJobId = null;
                renderAskAi();
            });
            elAiEditBody.appendChild(dismissBtn);

            if (job.status === "completed" || job.status === "completed_with_validation_error") {
                refresh();
            }
        });
    }

    function startJobPolling() {
        stopJobPolling();
        renderJobPanel();
        jobPollTimer = window.setInterval(renderJobPanel, 1500);
    }

    function createAiEditJob(scope, selectionIds, instruction, origin, annotationId) {
        var slideId = slideIdFromFilename(activeSlideFilename);
        if (!slideId) return;
        fetchJson("/api/runtime/ai-edits", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                slide_id: slideId, scope: scope, selection_ids: selectionIds,
                instruction: instruction, origin: origin, annotation_id: annotationId,
            }),
        }).then(function (data) {
            if (data && data.ok) {
                activeJobId = data.job.job_id;
                startJobPolling();
            } else if (data && data.errors) {
                window.alert(data.errors[0].message || data.errors[0].code);
            }
        });
    }

    // ---- Plan tab -------------------------------------------------------
    function renderPlan(plan) {
        elPlanBody.innerHTML = "";
        if (!plan) return;
        if (!plan.confirmed) {
            var note = document.createElement("div");
            note.className = "workbench-empty";
            note.textContent = plan.summary || "No confirmed plan yet.";
            elPlanBody.appendChild(note);
            return;
        }
        var fields = [
            ["Audience", plan.audience], ["Communication intent", plan.communication_intent],
            ["Canvas", plan.canvas], ["Plan revision", plan.plan_revision], ["Slide count", plan.slide_count],
        ];
        fields.forEach(function (pair) {
            if (pair[1] === undefined || pair[1] === null) return;
            var row = document.createElement("div");
            row.className = "workbench-kv";
            var value = typeof pair[1] === "object" ? (pair[1].value || JSON.stringify(pair[1])) : pair[1];
            row.innerHTML = "<span>" + pair[0] + "</span><b>" + value + "</b>";
            elPlanBody.appendChild(row);
        });
    }

    // ---- Confirmation panel ---------------------------------------------
    function renderConfirm(state) {
        elConfirmBody.innerHTML = "";
        if (!state || state.error) return; // nothing pending, or not ready yet -- stay quiet
        var header = document.createElement("div");
        header.className = "section-label";
        header.textContent = "Pending confirmation (" + state.stage + ")";
        elConfirmBody.appendChild(header);

        var fields = ["audience", "communication_intent", "audience_outcome", "core_message"];
        var inputs = {};
        fields.forEach(function (field) {
            if (!(field in state)) return;
            var wrap = document.createElement("div");
            wrap.className = "workbench-confirm-field";
            var label = document.createElement("label");
            label.textContent = field.replace(/_/g, " ");
            var input = document.createElement("textarea");
            input.value = (state[field] && state[field].value) || "";
            inputs[field] = input;
            wrap.appendChild(label);
            wrap.appendChild(input);
            elConfirmBody.appendChild(wrap);
        });

        var submit = document.createElement("button");
        submit.type = "button";
        submit.textContent = "Confirm";
        submit.addEventListener("click", function () {
            var payload = { stage: state.stage, primary_language: state.primary_language };
            fields.forEach(function (field) { payload[field] = inputs[field] ? inputs[field].value : ""; });
            if (state.stage === "stage1") {
                payload.canvas = state.recommend && state.recommend.canvas;
                payload.template_selection = { mode: "free_design", selection_keys: [] };
            }
            submit.disabled = true;
            fetchJson("/api/confirm/submit", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
            }).then(function () {
                submit.disabled = false;
                refresh();
            });
        });
        elConfirmBody.appendChild(submit);
    }

    // ---- Diagnostics drawer ----------------------------------------------
    function renderDiagnostics(buildState) {
        if (!buildState) return;
        elDiagnosticsBody.textContent = JSON.stringify(buildState, null, 2);
    }
    if (btnDiagnosticsToggle) {
        btnDiagnosticsToggle.addEventListener("click", function () {
            elDiagnostics.hidden = !elDiagnostics.hidden;
        });
    }
    if (btnDiagnosticsClose) {
        btnDiagnosticsClose.addEventListener("click", function () { elDiagnostics.hidden = true; });
    }

    // ---- Deck-level actions -----------------------------------------------
    if (btnValidateDeck) {
        btnValidateDeck.addEventListener("click", function () {
            btnValidateDeck.disabled = true;
            fetchJson("/api/runtime/deck/validate", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
            }).then(function () {
                btnValidateDeck.disabled = false;
                refresh();
            });
        });
    }
    if (btnExport) {
        btnExport.addEventListener("click", function () {
            btnExport.disabled = true;
            btnExport.textContent = "Exporting…";
            fetchJson("/api/runtime/deck/export", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
            }).then(function (envelope) {
                btnExport.disabled = false;
                btnExport.textContent = "Export PPTX";
                if (envelope && envelope.ok) {
                    window.alert("Export ready: " + (envelope.pptx_path || ""));
                } else if (envelope && envelope.errors && envelope.errors[0]) {
                    var err = envelope.errors[0];
                    var reasons = (err.reasons || []).map(function (r) {
                        return r.slide ? (r.slide + ": " + r.type) : r.type;
                    }).join("; ");
                    window.alert("Export blocked — " + (reasons || err.message));
                }
                refresh();
            });
        });
    }

    // ---- Main refresh, called from app.js's existing poll tick -----------
    function refresh() {
        fetchJson("/api/runtime/build-state").then(function (data) {
            lastBuildState = data;
            renderTopbar(data);
            renderDiagnostics(data);
        });
        fetchJson("/api/runtime/slides").then(function (data) {
            lastSlides = (data && data.slides) || [];
            renderSlideBadges(lastSlides);
            renderValidation(lastSlides);
        });
        fetchJson("/api/runtime/plan").then(function (data) {
            renderPlan(data && data.plan);
        });
        fetchJson("/api/confirm/state").then(function (data) {
            renderConfirm(data);
        });
    }

    function init() {
        refresh();
    }

    window.Workbench = {
        init: init,
        refresh: refresh,
        onSlideSelected: function (filename) {
            activeSlideFilename = filename;
            activeJobId = null;
            stopJobPolling();
            renderValidation(lastSlides);
            renderAskAi();
        },
        onSelectionChanged: function (ids) {
            currentSelectionIds = ids || [];
            if (!activeJobId) renderAskAi();
        },
        applyAnnotationWithAI: function (elementId, instructionText) {
            createAiEditJob("element", [elementId], instructionText || "", "annotation", elementId);
        },
    };
})();

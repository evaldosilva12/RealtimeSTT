import { state } from "./state.js";
import { sendJson } from "./socket.js";

const dialog = document.getElementById("settingsDialog");
const openSettings = document.getElementById("openSettings");
const profileSelect = document.getElementById("profileSelect");
const profileName = document.getElementById("profileName");
const targetRole = document.getElementById("targetRole");
const companyName = document.getElementById("companyName");
const responseStyle = document.getElementById("responseStyle");
const resumeText = document.getElementById("resumeText");
const jobDescriptionText = document.getElementById("jobDescriptionText");
const coverLetterText = document.getElementById("coverLetterText");
const linkedinText = document.getElementById("linkedinText");
const companyInfo = document.getElementById("companyInfo");
const recruiterNotes = document.getElementById("recruiterNotes");
const personalNotes = document.getElementById("personalNotes");
const previousInterviewContext = document.getElementById("previousInterviewContext");
const additionalInstructions = document.getElementById("additionalInstructions");
const profileStatus = document.getElementById("profileStatus");
const newProfile = document.getElementById("newProfile");
const saveProfile = document.getElementById("saveProfile");
const activeProfileStatus = document.getElementById("activeProfileStatus");
const duplicateProfile = document.getElementById("duplicateProfile");
const archiveProfile = document.getElementById("archiveProfile");
const analyzeProfile = document.getElementById("analyzeProfile");
const analysisStatus = document.getElementById("analysisStatus");
const analysisResult = document.getElementById("analysisResult");
const analysisGaps = document.getElementById("analysisGaps");
const cleanJobDescription = document.getElementById("cleanJobDescription");
const useCleanJobDescription = document.getElementById("useCleanJobDescription");
const insertGuardrailsTemplate = document.getElementById("insertGuardrailsTemplate");
const guardrailsBox = document.getElementById("guardrailsBox");
const readinessChecks = document.getElementById("readinessChecks");
const readinessTokens = document.getElementById("readinessTokens");
const readinessDirty = document.getElementById("readinessDirty");
const readinessBar = document.getElementById("readinessBar");
const readinessBreakdown = document.getElementById("readinessBreakdown");
const manualHiddenContext = document.getElementById("manualHiddenContext");
const saveManualContext = document.getElementById("saveManualContext");
const hiddenContextLog = document.getElementById("hiddenContextLog");
const overlayOpacity = document.getElementById("overlayOpacity");
const overlayOpacityValue = document.getElementById("overlayOpacityValue");

const profileFields = {
    name: profileName,
    target_role: targetRole,
    company_name: companyName,
    response_style: responseStyle,
    resume_text: resumeText,
    job_description_text: jobDescriptionText,
    cover_letter_text: coverLetterText,
    linkedin_text: linkedinText,
    company_info: companyInfo,
    recruiter_notes: recruiterNotes,
    personal_notes: personalNotes,
    previous_interview_context: previousInterviewContext,
    additional_instructions: additionalInstructions,
};

const GUARDRAILS_TEMPLATE = [
    "- I do not write code, manage people, or make architecture decisions. (Edit to match your real role.)",
    "- I have no direct experience with [tools or skills from the job that are not on my resume]. Never claim them.",
    "- I supported (did not lead): [for example audits or compliance]. Never say I owned them.",
    "- My last job ended in [month year]. Do not say I work there now.",
    "- Positioning: [one line about how I want to come across].",
].join("\n");

// Fields that go into the stable (cached) system prompt, with the labels shown in the breakdown.
const PROMPT_FIELDS = [
    ["Resume", "resume_text"],
    ["Job", "job_description_text"],
    ["Cover letter", "cover_letter_text"],
    ["LinkedIn", "linkedin_text"],
    ["Company", "company_info"],
    ["Recruiter", "recruiter_notes"],
    ["Notes", "personal_notes"],
    ["Previous rounds", "previous_interview_context"],
    ["Guardrails", "additional_instructions"],
];
const RULES_CHARS = 4600; // fixed rules in prompt_builder.py, approximate
const TOKENS_WARN = 6000;
const TOKENS_HIGH = 10000;

export function initSettings() {
    openSettings.addEventListener("click", () => {
        dialog.showModal();
        updateReadiness();
    });
    for (const element of Object.values(profileFields)) {
        element.addEventListener("input", updateReadiness);
    }
    initOverlayControls();
    newProfile.addEventListener("click", () => renderProfile(blankProfile()));
    saveProfile.addEventListener("click", () => {
        sendJson({ type: "profile.save", profile: readProfileForm() });
    });
    duplicateProfile.addEventListener("click", () => {
        const profileId = currentProfileId() || profileSelect.value;
        if (profileId) {
            sendJson({ type: "profile.duplicate", profile_id: profileId });
        }
    });
    archiveProfile.addEventListener("click", () => {
        const profileId = currentProfileId() || profileSelect.value;
        if (profileId) {
            sendJson({ type: "profile.archive", profile_id: profileId });
        }
    });
    analyzeProfile.addEventListener("click", () => {
        if (!resumeText.value.trim()) {
            setAnalysisStatus("Add the resume text first.", true);
            return;
        }
        analyzeProfile.disabled = true;
        setAnalysisStatus("Analyzing resume vs job... this takes about 20-30 seconds.");
        sendJson({ type: "profile.analyze", profile: readProfileForm() });
    });
    useCleanJobDescription.addEventListener("click", () => {
        if (cleanJobDescription.value.trim()) {
            jobDescriptionText.value = cleanJobDescription.value;
            setAnalysisStatus("Job description replaced. Save the interview to keep it.");
        }
    });
    insertGuardrailsTemplate.addEventListener("click", () => addGuardrails(GUARDRAILS_TEMPLATE));
    profileSelect.addEventListener("change", () => {
        const target = profileSelect.value;
        const dirty = !readinessDirty.hidden;
        if (dirty && !window.confirm("Discard the unsaved changes to this interview?")) {
            profileSelect.value = currentProfileId() || state.activeProfile?.id || "";
            return;
        }
        sendJson({ type: "profile.select", profile_id: target });
    });
    saveManualContext.addEventListener("click", () => {
        const text = manualHiddenContext.value.trim();
        if (!text) {
            return;
        }
        sendJson({ type: "context.my_note", text });
        manualHiddenContext.value = "";
    });
}

function initOverlayControls() {
    const savedOpacity = Number(localStorage.getItem("overlayOpacity") || "100");
    const initialOpacity = Number.isFinite(savedOpacity) ? Math.min(100, Math.max(35, savedOpacity)) : 100;
    overlayOpacity.value = String(initialOpacity);
    renderOverlayOpacity(initialOpacity);
    applyOverlayOpacity(initialOpacity);

    overlayOpacity.addEventListener("input", () => {
        const value = Number(overlayOpacity.value);
        localStorage.setItem("overlayOpacity", String(value));
        renderOverlayOpacity(value);
        applyOverlayOpacity(value);
    });

    window.interviewCopilot?.onOverlayState((overlayState) => {
        if (!overlayState || typeof overlayState.opacity !== "number") {
            return;
        }
        const value = Math.round(overlayState.opacity * 100);
        overlayOpacity.value = String(value);
        renderOverlayOpacity(value);
    });
}

function renderOverlayOpacity(value) {
    overlayOpacityValue.textContent = `${value}%`;
}

function applyOverlayOpacity(value) {
    window.interviewCopilot?.setOpacity(value / 100);
}

export function renderProfiles(profiles, activeProfile, openaiSetup = "not_configured") {
    state.profiles = profiles || [];
    state.activeProfile = activeProfile || null;
    state.openaiSetup = openaiSetup;

    profileSelect.innerHTML = "";
    for (const profile of state.profiles) {
        const option = document.createElement("option");
        option.value = profile.id;
        option.textContent = profile.id === activeProfile?.id ? `\u25CF ${profile.name} (active)` : profile.name;
        profileSelect.appendChild(option);
    }

    if (activeProfile) {
        profileSelect.value = activeProfile.id;
        renderProfile(activeProfile);
    } else {
        renderProfile(blankProfile());
    }
    activeProfileStatus.textContent = activeProfile ? `Interview: ${activeProfile.name}` : "Interview: none";
    activeProfileStatus.className = `status-pill ${activeProfile ? "status-ok" : "status-warn"}`;
}

export function handleAnalysisEvent(event) {
    if (event.type === "profile.analysis.started") {
        analyzeProfile.disabled = true;
        return;
    }
    analyzeProfile.disabled = false;
    if (event.type === "profile.analysis.error") {
        setAnalysisStatus(event.message || "The analysis failed.", true);
        return;
    }
    if (event.type !== "profile.analysis.completed") {
        return;
    }

    analysisResult.hidden = false;
    analysisGaps.innerHTML = "";
    for (const gap of event.gaps || []) {
        const item = document.createElement("li");
        item.textContent = gap;
        analysisGaps.appendChild(item);
    }
    cleanJobDescription.value = event.clean_job_description || "";
    useCleanJobDescription.hidden = !cleanJobDescription.value;
    cleanJobDescription.closest("label").hidden = !cleanJobDescription.value;

    if (event.guardrails) {
        addGuardrails(event.guardrails);
        setAnalysisStatus("Done. Review the guardrails below, then click Save Interview.");
        guardrailsBox.scrollIntoView({ block: "center", behavior: "smooth" });
        guardrailsBox.classList.add("flash");
        window.setTimeout(() => guardrailsBox.classList.remove("flash"), 2500);
    } else {
        setAnalysisStatus("The analysis returned no guardrails. Use Insert template and fill them in.", true);
    }
}

function setAnalysisStatus(message, isError = false) {
    analysisStatus.hidden = !message;
    analysisStatus.textContent = message;
    analysisStatus.classList.toggle("is-error", isError);
}

function addGuardrails(text) {
    const current = additionalInstructions.value.trim();
    additionalInstructions.value = current
        ? `${current}\n\n# Suggested - review and delete what you do not need\n${text}`
        : text;
}

export function renderNotes(notes) {
    state.notes = notes || [];
    hiddenContextLog.innerHTML = "";
    if (!state.notes.length) {
        hiddenContextLog.textContent = "No notes yet.";
        return;
    }
    for (const note of state.notes) {
        const row = document.createElement("div");
        row.className = "note-row";
        const text = document.createElement("span");
        text.textContent = note.text;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "note-delete";
        remove.title = "Delete this note";
        remove.textContent = "\u00D7";
        remove.addEventListener("click", () => sendJson({ type: "context.notes.delete", note_id: note.id }));
        row.append(text, remove);
        hiddenContextLog.appendChild(row);
    }
}

function updateReadiness() {
    const form = readProfileForm();
    const guardrails = form.additional_instructions;
    const checks = [
        { ok: Boolean(form.resume_text), label: "Resume", hint: "required" },
        { ok: Boolean(form.job_description_text), label: "Job description", hint: "answers will not be tailored without it" },
        {
            ok: Boolean(guardrails) && !guardrails.includes("["),
            warn: Boolean(guardrails) && guardrails.includes("["),
            label: "Guardrails",
            hint: guardrails.includes("[")
                ? "replace the [placeholders] in the template"
                : "empty: the model may overstate your experience",
        },
        { ok: Boolean(form.target_role && form.company_name), label: "Role and company", hint: "optional" },
    ];
    readinessChecks.innerHTML = "";
    for (const check of checks) {
        const item = document.createElement("li");
        const state = check.ok ? "ok" : check.warn ? "warn" : "missing";
        item.className = `readiness-${state}`;
        item.textContent = check.ok ? `${check.label}` : `${check.label} - ${check.hint}`;
        readinessChecks.appendChild(item);
    }

    const sizes = PROMPT_FIELDS.map(([label, key]) => [label, (form[key] || "").length]);
    const filled = sizes.filter(([, chars]) => chars > 0);
    const totalChars = RULES_CHARS + filled.reduce((sum, [, chars]) => sum + chars + 30, 0);
    const tokens = Math.round(totalChars / 4);
    const level = tokens > TOKENS_HIGH ? "high" : tokens > TOKENS_WARN ? "warn" : "ok";
    const message = {
        ok: "cached after the first answer",
        warn: "large: answers get slower and cost more",
        high: "very large: trim the job description or notes",
    }[level];
    readinessTokens.textContent = `Stable prompt ~${tokens.toLocaleString()} tokens - ${message}`;
    readinessBar.style.width = `${Math.min(100, Math.round((tokens / TOKENS_HIGH) * 100))}%`;
    readinessBar.className = `readiness-bar-${level}`;
    readinessBreakdown.textContent = filled
        .sort((a, b) => b[1] - a[1])
        .map(([label, chars]) => `${label} ${(chars / 1000).toFixed(1)}k`)
        .join(" · ") + " chars";

    const saved = state.activeProfile && state.activeProfile.id === (profileName.dataset.profileId || "")
        ? state.activeProfile
        : null;
    const dirty = saved
        ? Object.keys(profileFields).some((key) => (form[key] || "") !== String(saved[key] || (key === "response_style" ? "Natural" : "")))
        : Object.values(form).some((value, index) => index > 0 && value && value !== "Natural");
    readinessDirty.hidden = !dirty;
}

function renderProfile(profile) {
    for (const [key, element] of Object.entries(profileFields)) {
        element.value = profile[key] || (key === "response_style" ? "Natural" : "");
    }
    profileName.dataset.profileId = profile.id || "";
    const setupLabel = state.openaiSetup === "configured" ? "OpenAI ready" : "Groq fallback only";
    profileStatus.textContent = profile.id
        ? `\u25CF Active interview: ${profile.name} - ${setupLabel}`
        : "New interview - not saved yet. Click Save Interview to use it.";
    updateReadiness();
}

function readProfileForm() {
    const profile = { id: currentProfileId() };
    for (const [key, element] of Object.entries(profileFields)) {
        profile[key] = element.value.trim();
    }
    return profile;
}

function currentProfileId() {
    return profileName.dataset.profileId || "";
}

function blankProfile() {
    return {
        id: "",
        name: "",
        target_role: "",
        company_name: "",
        resume_text: "",
        job_description_text: "",
        cover_letter_text: "",
        linkedin_text: "",
        company_info: "",
        recruiter_notes: "",
        personal_notes: "",
        previous_interview_context: "",
        additional_instructions: "",
        response_style: "Natural",
    };
}

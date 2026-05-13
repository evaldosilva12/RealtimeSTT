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
const internalCandidateProfile = document.getElementById("internalCandidateProfile");
const profileStatus = document.getElementById("profileStatus");
const newProfile = document.getElementById("newProfile");
const saveProfile = document.getElementById("saveProfile");
const selectProfile = document.getElementById("selectProfile");
const duplicateProfile = document.getElementById("duplicateProfile");
const archiveProfile = document.getElementById("archiveProfile");
const generateInternalProfile = document.getElementById("generateInternalProfile");
const manualHiddenContext = document.getElementById("manualHiddenContext");
const saveManualContext = document.getElementById("saveManualContext");
const hiddenContextLog = document.getElementById("hiddenContextLog");

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
    internal_candidate_profile: internalCandidateProfile,
};

export function initSettings() {
    openSettings.addEventListener("click", () => dialog.showModal());
    newProfile.addEventListener("click", () => renderProfile(blankProfile()));
    saveProfile.addEventListener("click", () => {
        sendJson({ type: "profile.save", profile: readProfileForm() });
    });
    selectProfile.addEventListener("click", () => {
        if (profileSelect.value) {
            sendJson({ type: "profile.select", profile_id: profileSelect.value });
        }
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
    generateInternalProfile.addEventListener("click", () => {
        const profile = readProfileForm();
        sendJson({ type: "profile.save", profile, generate_internal: true });
    });
    profileSelect.addEventListener("change", () => {
        const profile = state.profiles.find((item) => item.id === profileSelect.value);
        if (profile) {
            renderProfile(profile);
        }
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

export function renderProfiles(profiles, activeProfile, openaiSetup = "not_configured") {
    state.profiles = profiles || [];
    state.activeProfile = activeProfile || null;
    state.openaiSetup = openaiSetup;

    profileSelect.innerHTML = "";
    for (const profile of state.profiles) {
        const option = document.createElement("option");
        option.value = profile.id;
        option.textContent = profile.name;
        profileSelect.appendChild(option);
    }

    if (activeProfile) {
        profileSelect.value = activeProfile.id;
        renderProfile(activeProfile);
    } else {
        renderProfile(blankProfile());
    }
}

export function markProfileGenerating(profileId) {
    if (profileId === currentProfileId()) {
        profileStatus.textContent = "Generating internal profile...";
    }
}

export function markProfileGenerated(profileId) {
    if (profileId === currentProfileId()) {
        profileStatus.textContent = "Internal profile updated.";
    }
}

export function appendHiddenContext(text) {
    state.hiddenContext.push(text);
    const item = document.createElement("div");
    item.textContent = text;
    hiddenContextLog.appendChild(item);
}

function renderProfile(profile) {
    for (const [key, element] of Object.entries(profileFields)) {
        element.value = profile[key] || (key === "response_style" ? "Natural" : "");
    }
    profileName.dataset.profileId = profile.id || "";
    const setupLabel = state.openaiSetup === "configured" ? "OpenAI setup ready" : "Groq setup fallback";
    profileStatus.textContent = `${profile.name || "New interview"} - ${setupLabel}`;
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
        internal_candidate_profile: "",
    };
}

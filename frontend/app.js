/**
 * Ajaia Docs — Frontend Application Logic
 * Plain Vanilla JS + Quill.js connected to the FastAPI + SQLite backend.
 */

(function () {
  "use strict";

  const SESSION_USER_KEY = "ajaia_active_user_id";
  const SESSION_DOC_KEY_PREFIX = "ajaia_last_doc_user_";

  const state = {
    users: [],
    currentUser: null,
    ownedDocs: [],
    sharedDocs: [],
    activeDoc: null,
    isDirty: false,
    isSaving: false,
    isLoadingDoc: false,
    quill: null,
  };

  // DOM References
  const dom = {
    sidebar: document.getElementById("sidebar"),
    sidebarToggleBtn: document.getElementById("sidebar-toggle-btn"),
    userSwitcher: document.getElementById("user-switcher"),
    userAvatar: document.getElementById("user-avatar"),
    docHeaderMeta: document.getElementById("doc-header-meta"),
    docTitleInput: document.getElementById("doc-title-input"),
    docAccessPill: document.getElementById("doc-access-pill"),
    editorActions: document.getElementById("editor-actions"),
    saveStatus: document.getElementById("save-status"),
    saveStatusText: document.getElementById("save-status-text"),
    saveBtn: document.getElementById("save-btn"),
    shareBtn: document.getElementById("share-btn"),
    shareBtnLabel: document.getElementById("share-btn-label"),
    newDocBtn: document.getElementById("new-doc-btn"),
    importFileBtn: document.getElementById("import-file-btn"),
    importBtnLabel: document.getElementById("import-btn-label"),
    fileUploadInput: document.getElementById("file-upload-input"),
    uploadDropzone: document.getElementById("upload-dropzone"),
    ownedCount: document.getElementById("owned-count"),
    sharedCount: document.getElementById("shared-count"),
    ownedDocsList: document.getElementById("owned-docs-list"),
    sharedDocsList: document.getElementById("shared-docs-list"),
    emptyState: document.getElementById("empty-state"),
    emptyStateTitle: document.getElementById("empty-state-title"),
    emptyNewDocBtn: document.getElementById("empty-new-doc-btn"),
    emptyImportBtn: document.getElementById("empty-import-btn"),
    editorShell: document.getElementById("editor-shell"),
    shareModal: document.getElementById("share-modal"),
    shareModalSubtitle: document.getElementById("share-modal-subtitle"),
    closeShareModalBtn: document.getElementById("close-share-modal-btn"),
    shareOwnerOnlyNotice: document.getElementById("share-owner-only-notice"),
    shareForm: document.getElementById("share-form"),
    shareUserSelect: document.getElementById("share-user-select"),
    grantShareBtn: document.getElementById("grant-share-btn"),
    sharePeopleList: document.getElementById("share-people-list"),
    toastContainer: document.getElementById("toast-container"),
  };

  // ---------------------------------------------------------------------------
  // Utilities & API Client
  // ---------------------------------------------------------------------------

  function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;

    const textSpan = document.createElement("span");
    textSpan.textContent = message;

    const closeBtn = document.createElement("button");
    closeBtn.className = "toast-close";
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Dismiss notification");
    closeBtn.innerHTML = "&times;";
    closeBtn.addEventListener("click", () => toast.remove());

    toast.appendChild(textSpan);
    toast.appendChild(closeBtn);
    dom.toastContainer.appendChild(toast);

    window.setTimeout(() => {
      if (toast.parentElement) {
        toast.remove();
      }
    }, 4200);
  }

  async function apiRequest(path, options = {}) {
    const headers = Object.assign({}, options.headers || {});
    if (state.currentUser) {
      headers["X-User-Id"] = String(state.currentUser.id);
    }
    if (options.body && !(options.body instanceof FormData) && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }

    const response = await fetch(path, Object.assign({}, options, { headers }));
    if (response.status === 204) {
      return null;
    }

    let data = null;
    try {
      data = await response.json();
    } catch (_) {
      data = null;
    }

    if (!response.ok) {
      const detail =
        (data && typeof data.detail === "string" && data.detail) ||
        `Request failed (${response.status})`;
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function formatRelativeTime(isoString) {
    if (!isoString) return "";
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function getEditorSemanticHTML() {
    if (!state.quill) return "";
    const textOnly = state.quill.getText().trim();
    if (!textOnly && state.quill.getLength() <= 1) {
      return "";
    }
    if (typeof state.quill.getSemanticHTML === "function") {
      return state.quill.getSemanticHTML();
    }
    return state.quill.root.innerHTML;
  }

  function setSaveIndicator(mode) {
    dom.saveStatus.classList.remove("saved", "unsaved", "saving");
    if (mode === "saving") {
      state.isSaving = true;
      dom.saveStatus.classList.add("saving");
      dom.saveStatusText.textContent = "Saving...";
    } else if (mode === "unsaved") {
      state.isDirty = true;
      state.isSaving = false;
      dom.saveStatus.classList.add("unsaved");
      dom.saveStatusText.textContent = "Unsaved changes";
    } else {
      state.isDirty = false;
      state.isSaving = false;
      dom.saveStatus.classList.add("saved");
      dom.saveStatusText.textContent = "Saved";
    }
  }

  function confirmDiscardIfDirty() {
    if (!state.isDirty) return true;
    return window.confirm("You have unsaved changes. Discard changes and continue?");
  }

  // ---------------------------------------------------------------------------
  // Quill Initialization
  // ---------------------------------------------------------------------------

  function initQuill() {
    state.quill = new Quill("#quill-editor", {
      theme: "snow",
      modules: {
        toolbar: "#quill-toolbar",
      },
      placeholder: "Start typing your document here...",
    });

    state.quill.on("text-change", (_delta, _oldDelta, source) => {
      if (state.isLoadingDoc || source !== "user" || !state.activeDoc) {
        return;
      }
      setSaveIndicator("unsaved");
    });
  }

  // ---------------------------------------------------------------------------
  // Rendering Functions
  // ---------------------------------------------------------------------------

  function renderUserSwitcher() {
    dom.userSwitcher.innerHTML = "";
    state.users.forEach((user) => {
      const opt = document.createElement("option");
      opt.value = String(user.id);
      opt.textContent = `${user.name} (${user.email})`;
      if (state.currentUser && user.id === state.currentUser.id) {
        opt.selected = true;
      }
      dom.userSwitcher.appendChild(opt);
    });

    if (state.currentUser) {
      dom.userAvatar.textContent = state.currentUser.name.charAt(0).toUpperCase();
    }
  }

  function renderSidebarLists() {
    dom.ownedCount.textContent = String(state.ownedDocs.length);
    dom.sharedCount.textContent = String(state.sharedDocs.length);

    // Owned documents
    dom.ownedDocsList.innerHTML = "";
    if (state.ownedDocs.length === 0) {
      const emptyLi = document.createElement("li");
      emptyLi.className = "doc-list-empty";
      emptyLi.textContent = "No owned documents yet.";
      dom.ownedDocsList.appendChild(emptyLi);
    } else {
      state.ownedDocs.forEach((doc) => {
        dom.ownedDocsList.appendChild(createSidebarDocItem(doc, true));
      });
    }

    // Shared documents
    dom.sharedDocsList.innerHTML = "";
    if (state.sharedDocs.length === 0) {
      const emptyLi = document.createElement("li");
      emptyLi.className = "doc-list-empty";
      emptyLi.textContent = "No documents shared with you.";
      dom.sharedDocsList.appendChild(emptyLi);
    } else {
      state.sharedDocs.forEach((doc) => {
        dom.sharedDocsList.appendChild(createSidebarDocItem(doc, false));
      });
    }
  }

  function createSidebarDocItem(doc, isOwned) {
    const li = document.createElement("li");
    li.className = "doc-item";
    if (state.activeDoc && state.activeDoc.id === doc.id) {
      li.classList.add("active");
    }

    const main = document.createElement("div");
    main.className = "doc-item-main";

    const titleDiv = document.createElement("div");
    titleDiv.className = "doc-item-title";
    titleDiv.textContent = doc.title;
    main.appendChild(titleDiv);

    const subDiv = document.createElement("div");
    subDiv.className = "doc-item-sub";

    if (!isOwned) {
      const ownerBadge = document.createElement("span");
      ownerBadge.className = "owner-badge";
      ownerBadge.textContent = `Owner: ${doc.owner_name}`;
      subDiv.appendChild(ownerBadge);
    } else if (doc.shared_count > 0) {
      const sharedPill = document.createElement("span");
      sharedPill.className = "shared-pill";
      sharedPill.textContent = `Shared (${doc.shared_count})`;
      subDiv.appendChild(sharedPill);
    }

    const timeSpan = document.createElement("span");
    timeSpan.textContent = formatRelativeTime(doc.updated_at);
    subDiv.appendChild(timeSpan);
    main.appendChild(subDiv);
    li.appendChild(main);

    if (isOwned) {
      const delBtn = document.createElement("button");
      delBtn.className = "doc-delete-btn";
      delBtn.type = "button";
      delBtn.title = "Delete document";
      delBtn.setAttribute("aria-label", `Delete ${doc.title}`);
      delBtn.innerHTML = "&times;";
      delBtn.addEventListener("click", (event) => {
        event.stopPropagation();
        handleDeleteDocument(doc);
      });
      li.appendChild(delBtn);
    }

    li.addEventListener("click", () => {
      if (state.activeDoc && state.activeDoc.id === doc.id) {
        dom.sidebar.classList.remove("open");
        return;
      }
      if (!confirmDiscardIfDirty()) return;
      openDocument(doc.id);
      dom.sidebar.classList.remove("open");
    });

    return li;
  }

  function renderActiveDocumentView() {
    if (!state.activeDoc) {
      dom.docHeaderMeta.classList.add("hidden");
      dom.editorActions.classList.add("hidden");
      dom.editorShell.classList.add("hidden");
      dom.emptyState.classList.remove("hidden");
      if (state.currentUser) {
        dom.emptyStateTitle.textContent = `Welcome, ${state.currentUser.name}`;
      }
      return;
    }

    const doc = state.activeDoc;
    dom.emptyState.classList.add("hidden");
    dom.docHeaderMeta.classList.remove("hidden");
    dom.editorActions.classList.remove("hidden");
    dom.editorShell.classList.remove("hidden");

    dom.docTitleInput.value = doc.title;
    dom.docAccessPill.textContent = doc.is_owner
      ? "Owner"
      : `Shared by ${doc.owner_name}`;

    const shareCount = Array.isArray(doc.shares) ? doc.shares.length : 0;
    dom.shareBtnLabel.textContent = shareCount > 0 ? `Share (${shareCount})` : "Share";
    dom.shareBtn.title = doc.is_owner
      ? "Share this document with teammates"
      : `Owned by ${doc.owner_name} — view access list`;

    state.isLoadingDoc = true;
    state.quill.setContents([]);
    if (doc.content_html) {
      state.quill.clipboard.dangerouslyPasteHTML(0, doc.content_html, "silent");
    }
    if (state.quill.history && typeof state.quill.history.clear === "function") {
      state.quill.history.clear();
    }
    state.isLoadingDoc = false;

    setSaveIndicator("saved");
  }

  // ---------------------------------------------------------------------------
  // Document Operations
  // ---------------------------------------------------------------------------

  async function refreshSidebarDocuments() {
    const data = await apiRequest("/api/documents");
    state.ownedDocs = data.owned || [];
    state.sharedDocs = data.shared_with_me || [];
    renderSidebarLists();
    return data;
  }

  async function openDocument(docId) {
    try {
      const doc = await apiRequest(`/api/documents/${docId}`);
      state.activeDoc = doc;
      if (state.currentUser) {
        sessionStorage.setItem(`${SESSION_DOC_KEY_PREFIX}${state.currentUser.id}`, String(doc.id));
      }
      renderActiveDocumentView();
      renderSidebarLists();
    } catch (error) {
      showToast(error.message, "error");
      state.activeDoc = null;
      renderActiveDocumentView();
    }
  }

  async function loadUserWorkspace() {
    try {
      await refreshSidebarDocuments();
      const allAccessible = [...state.ownedDocs, ...state.sharedDocs];
      if (allAccessible.length === 0) {
        state.activeDoc = null;
        setSaveIndicator("saved");
        renderActiveDocumentView();
        return;
      }

      const rememberedId = Number(
        sessionStorage.getItem(`${SESSION_DOC_KEY_PREFIX}${state.currentUser.id}`)
      );
      const targetDoc =
        allAccessible.find((d) => d.id === rememberedId) || allAccessible[0];

      await openDocument(targetDoc.id);
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function handleCreateDocument() {
    if (!confirmDiscardIfDirty()) return;
    try {
      const created = await apiRequest("/api/documents", {
        method: "POST",
        body: JSON.stringify({ title: "Untitled Document", content_html: "" }),
      });
      state.activeDoc = created;
      sessionStorage.setItem(
        `${SESSION_DOC_KEY_PREFIX}${state.currentUser.id}`,
        String(created.id)
      );
      await refreshSidebarDocuments();
      renderActiveDocumentView();
      dom.docTitleInput.focus();
      dom.docTitleInput.select();
      showToast("Created new document.", "success");
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function handleSaveDocument() {
    if (!state.activeDoc || state.isSaving) return;

    const rawTitle = dom.docTitleInput.value.trim();
    if (!rawTitle) {
      showToast("Document title cannot be empty.", "error");
      dom.docTitleInput.value = state.activeDoc.title;
      dom.docTitleInput.focus();
      return;
    }

    const contentHtml = getEditorSemanticHTML();
    setSaveIndicator("saving");

    try {
      const updated = await apiRequest(`/api/documents/${state.activeDoc.id}`, {
        method: "PUT",
        body: JSON.stringify({
          title: rawTitle,
          content_html: contentHtml,
        }),
      });
      state.activeDoc = updated;
      dom.docTitleInput.value = updated.title;
      setSaveIndicator("saved");
      await refreshSidebarDocuments();
      showToast("Document saved.", "success");
    } catch (error) {
      setSaveIndicator("unsaved");
      showToast(error.message, "error");
    }
  }

  async function handleInlineRenameCommit() {
    if (!state.activeDoc) return;
    const newTitle = dom.docTitleInput.value.trim();

    if (!newTitle) {
      dom.docTitleInput.value = state.activeDoc.title;
      showToast("Document title cannot be empty.", "error");
      return;
    }

    if (newTitle === state.activeDoc.title) {
      dom.docTitleInput.value = state.activeDoc.title;
      return;
    }

    const hadUnsavedEditorContent = state.isDirty;
    try {
      const updated = await apiRequest(`/api/documents/${state.activeDoc.id}`, {
        method: "PUT",
        body: JSON.stringify({ title: newTitle }),
      });
      state.activeDoc.title = updated.title;
      state.activeDoc.updated_at = updated.updated_at;
      dom.docTitleInput.value = updated.title;
      if (!hadUnsavedEditorContent) {
        setSaveIndicator("saved");
      }
      await refreshSidebarDocuments();
      showToast("Document renamed.", "success");
    } catch (error) {
      dom.docTitleInput.value = state.activeDoc.title;
      showToast(error.message, "error");
    }
  }

  async function handleDeleteDocument(doc) {
    const confirmed = window.confirm(
      `Delete "${doc.title}" permanently? This action cannot be undone.`
    );
    if (!confirmed) return;

    try {
      await apiRequest(`/api/documents/${doc.id}`, { method: "DELETE" });
      showToast(`Deleted "${doc.title}".`, "success");

      if (state.activeDoc && state.activeDoc.id === doc.id) {
        state.activeDoc = null;
        state.isDirty = false;
        await loadUserWorkspace();
      } else {
        await refreshSidebarDocuments();
      }
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  // ---------------------------------------------------------------------------
  // File Upload (.txt, .md, .docx)
  // ---------------------------------------------------------------------------

  async function handleFileUpload(file) {
    if (!file) return;
    if (!confirmDiscardIfDirty()) {
      dom.fileUploadInput.value = "";
      return;
    }

    const formData = new FormData();
    formData.append("file", file);

    dom.importFileBtn.disabled = true;
    dom.importBtnLabel.textContent = "Importing...";

    try {
      const importedDoc = await apiRequest("/api/documents/upload", {
        method: "POST",
        body: formData,
      });
      state.activeDoc = importedDoc;
      sessionStorage.setItem(
        `${SESSION_DOC_KEY_PREFIX}${state.currentUser.id}`,
        String(importedDoc.id)
      );
      await refreshSidebarDocuments();
      renderActiveDocumentView();
      showToast(`Imported "${importedDoc.title}".`, "success");
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      dom.importFileBtn.disabled = false;
      dom.importBtnLabel.textContent = "Import File";
      dom.fileUploadInput.value = "";
    }
  }

  // ---------------------------------------------------------------------------
  // Sharing Modal Logic
  // ---------------------------------------------------------------------------

  function openShareModal() {
    if (!state.activeDoc) return;
    renderShareModalContents();
    dom.shareModal.classList.remove("hidden");
  }

  function closeShareModal() {
    dom.shareModal.classList.add("hidden");
  }

  function renderShareModalContents() {
    const doc = state.activeDoc;
    if (!doc) return;

    dom.shareModalSubtitle.textContent = `"${doc.title}"`;

    if (doc.is_owner) {
      dom.shareOwnerOnlyNotice.classList.add("hidden");
      dom.shareForm.classList.remove("hidden");
    } else {
      dom.shareOwnerOnlyNotice.textContent = `Only the document owner (${doc.owner_name}) can grant or revoke access.`;
      dom.shareOwnerOnlyNotice.classList.remove("hidden");
      dom.shareForm.classList.add("hidden");
    }

    // Populate eligible collaborators dropdown
    const sharedUserIds = new Set((doc.shares || []).map((s) => s.user_id));
    const availableUsers = state.users.filter(
      (u) => u.id !== doc.owner_id && !sharedUserIds.has(u.id)
    );

    dom.shareUserSelect.innerHTML = "";
    if (availableUsers.length === 0) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "All demo users already have access";
      dom.shareUserSelect.appendChild(opt);
      dom.shareUserSelect.disabled = true;
      dom.grantShareBtn.disabled = true;
    } else {
      dom.shareUserSelect.disabled = false;
      dom.grantShareBtn.disabled = false;
      availableUsers.forEach((user) => {
        const opt = document.createElement("option");
        opt.value = String(user.id);
        opt.textContent = `${user.name} (${user.email})`;
        dom.shareUserSelect.appendChild(opt);
      });
    }

    // Render People with Access list (Owner + Collaborators)
    dom.sharePeopleList.innerHTML = "";

    const ownerLi = document.createElement("li");
    ownerLi.className = "person-row";
    ownerLi.innerHTML = `
      <div class="person-info">
        <span class="user-avatar">${escapeText(doc.owner_name.charAt(0).toUpperCase())}</span>
        <div>
          <div class="person-name">${escapeText(doc.owner_name)}${doc.is_owner ? " (You)" : ""}</div>
          <div class="person-email">${escapeText(doc.owner_email)}</div>
        </div>
      </div>
      <span class="role-tag">Owner</span>
    `;
    dom.sharePeopleList.appendChild(ownerLi);

    (doc.shares || []).forEach((share) => {
      const li = document.createElement("li");
      li.className = "person-row";

      const info = document.createElement("div");
      info.className = "person-info";
      const isMe = state.currentUser && share.user_id === state.currentUser.id;
      info.innerHTML = `
        <span class="user-avatar">${escapeText(share.name.charAt(0).toUpperCase())}</span>
        <div>
          <div class="person-name">${escapeText(share.name)}${isMe ? " (You)" : ""}</div>
          <div class="person-email">${escapeText(share.email)}</div>
        </div>
      `;
      li.appendChild(info);

      if (doc.is_owner) {
        const revokeBtn = document.createElement("button");
        revokeBtn.className = "btn-revoke";
        revokeBtn.type = "button";
        revokeBtn.textContent = "Remove";
        revokeBtn.addEventListener("click", () => handleRevokeShare(share.user_id, share.name));
        li.appendChild(revokeBtn);
      } else {
        const tag = document.createElement("span");
        tag.className = "role-tag";
        tag.textContent = "Editor";
        li.appendChild(tag);
      }

      dom.sharePeopleList.appendChild(li);
    });
  }

  async function handleGrantShare(event) {
    event.preventDefault();
    if (!state.activeDoc) return;
    const selectedId = Number(dom.shareUserSelect.value);
    if (!selectedId) return;

    try {
      const updatedShares = await apiRequest(
        `/api/documents/${state.activeDoc.id}/shares`,
        {
          method: "POST",
          body: JSON.stringify({ user_id: selectedId }),
        }
      );
      state.activeDoc.shares = updatedShares;
      dom.shareBtnLabel.textContent =
        updatedShares.length > 0 ? `Share (${updatedShares.length})` : "Share";
      renderShareModalContents();
      await refreshSidebarDocuments();
      showToast("Access granted.", "success");
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function handleRevokeShare(targetUserId, targetName) {
    if (!state.activeDoc) return;
    try {
      const updatedShares = await apiRequest(
        `/api/documents/${state.activeDoc.id}/shares/${targetUserId}`,
        { method: "DELETE" }
      );
      state.activeDoc.shares = updatedShares;
      dom.shareBtnLabel.textContent =
        updatedShares.length > 0 ? `Share (${updatedShares.length})` : "Share";
      renderShareModalContents();
      await refreshSidebarDocuments();
      showToast(`Removed ${targetName}'s access.`, "info");
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  function escapeText(value) {
    const div = document.createElement("div");
    div.textContent = value || "";
    return div.innerHTML;
  }

  // ---------------------------------------------------------------------------
  // Event Listeners & Bootstrapping
  // ---------------------------------------------------------------------------

  function bindEvents() {
    dom.sidebarToggleBtn.addEventListener("click", () => {
      dom.sidebar.classList.toggle("open");
    });

    dom.userSwitcher.addEventListener("change", async () => {
      const nextId = Number(dom.userSwitcher.value);
      if (!confirmDiscardIfDirty()) {
        dom.userSwitcher.value = String(state.currentUser.id);
        return;
      }
      const nextUser = state.users.find((u) => u.id === nextId);
      if (!nextUser) return;

      state.currentUser = nextUser;
      state.isDirty = false;
      sessionStorage.setItem(SESSION_USER_KEY, String(nextUser.id));
      renderUserSwitcher();
      closeShareModal();
      await loadUserWorkspace();
      showToast(`Switched active user to ${nextUser.name}.`, "info");
    });

    dom.newDocBtn.addEventListener("click", handleCreateDocument);
    dom.emptyNewDocBtn.addEventListener("click", handleCreateDocument);

    dom.saveBtn.addEventListener("click", handleSaveDocument);

    // Inline title editing
    dom.docTitleInput.addEventListener("input", () => {
      if (!state.activeDoc) return;
      if (dom.docTitleInput.value.trim() !== state.activeDoc.title) {
        dom.saveStatus.classList.remove("saved", "saving");
        dom.saveStatus.classList.add("unsaved");
        dom.saveStatusText.textContent = "Unsaved changes";
      } else if (!state.isDirty) {
        setSaveIndicator("saved");
      }
    });

    dom.docTitleInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        dom.docTitleInput.blur();
      } else if (event.key === "Escape" && state.activeDoc) {
        dom.docTitleInput.value = state.activeDoc.title;
        dom.docTitleInput.blur();
      }
    });

    dom.docTitleInput.addEventListener("blur", handleInlineRenameCommit);

    // File import (button + drag-and-drop)
    dom.importFileBtn.addEventListener("click", () => dom.fileUploadInput.click());
    dom.emptyImportBtn.addEventListener("click", () => dom.fileUploadInput.click());
    dom.fileUploadInput.addEventListener("change", () => {
      const file = dom.fileUploadInput.files && dom.fileUploadInput.files[0];
      if (file) {
        handleFileUpload(file);
      }
    });

    dom.uploadDropzone.addEventListener("dragover", (event) => {
      event.preventDefault();
      dom.uploadDropzone.classList.add("drag-over");
    });
    dom.uploadDropzone.addEventListener("dragleave", () => {
      dom.uploadDropzone.classList.remove("drag-over");
    });
    dom.uploadDropzone.addEventListener("drop", (event) => {
      event.preventDefault();
      dom.uploadDropzone.classList.remove("drag-over");
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) {
        handleFileUpload(file);
      }
    });

    // Share modal
    dom.shareBtn.addEventListener("click", openShareModal);
    dom.closeShareModalBtn.addEventListener("click", closeShareModal);
    dom.shareModal.addEventListener("click", (event) => {
      if (event.target === dom.shareModal) {
        closeShareModal();
      }
    });
    dom.shareForm.addEventListener("submit", handleGrantShare);

    // Global keyboard shortcuts (Ctrl/Cmd + S to save, Escape to close modal)
    window.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        if (state.activeDoc) {
          event.preventDefault();
          handleSaveDocument();
        }
      } else if (event.key === "Escape" && !dom.shareModal.classList.contains("hidden")) {
        closeShareModal();
      }
    });

    // Warn before closing tab if unsaved edits exist
    window.addEventListener("beforeunload", (event) => {
      if (state.isDirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    });
  }

  async function bootstrap() {
    initQuill();
    bindEvents();

    try {
      const users = await apiRequest("/api/users");
      state.users = users || [];
      if (state.users.length === 0) {
        showToast("No demo users found in database.", "error");
        return;
      }

      const savedUserId = Number(sessionStorage.getItem(SESSION_USER_KEY));
      state.currentUser =
        state.users.find((u) => u.id === savedUserId) || state.users[0];

      renderUserSwitcher();
      await loadUserWorkspace();
    } catch (error) {
      showToast(`Failed to initialize app: ${error.message}`, "error");
    }
  }

  document.addEventListener("DOMContentLoaded", bootstrap);
})();

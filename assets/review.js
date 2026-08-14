(function () {
  'use strict';

  var LOOPBACKS = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);
  if (!/^https?:$/.test(window.location.protocol) || !LOOPBACKS.has(window.location.hostname)) return;

  var root = document.getElementById('lh-review-root');
  var frame = document.getElementById('lh-blueprint-frame');
  var bootstrapNode = document.getElementById('lh-review-bootstrap');
  if (!root || !frame || !bootstrapNode || root.dataset.reviewReady === 'true') return;

  var shellBootstrap;
  try {
    shellBootstrap = JSON.parse(bootstrapNode.textContent || '{}');
  } catch (_error) {
    root.textContent = 'Review could not start because its bootstrap data is invalid.';
    return;
  }
  if (!shellBootstrap.path) return;

  /* API contract. Keep endpoint and response adaptation in this block. */
  var api = (function () {
    var token = shellBootstrap.token || '';
    var revision = null;

    function localApiUrl(value) {
      var url = new URL(value, window.location.origin);
      if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/v1/')) {
        throw new Error('Review requests are restricted to this local API.');
      }
      return url.pathname + url.search;
    }

    async function request(endpoint, options) {
      var settings = options || {};
      var method = settings.method || 'GET';
      var headers = { Accept: 'application/json' };
      if (settings.body !== undefined) headers['Content-Type'] = 'application/json';
      if (method !== 'GET' && method !== 'HEAD') {
        if (!token) throw new Error('The local review token is missing. Reload the page to continue.');
        headers['X-Learning-Hub-Token'] = token;
        if (revision !== null && settings.withRevision !== false) headers['If-Match'] = String(revision);
      }

      var response = await fetch(localApiUrl(endpoint), {
        method: method,
        headers: headers,
        body: settings.body === undefined ? undefined : JSON.stringify(settings.body),
        credentials: 'same-origin',
        cache: 'no-store',
        signal: settings.signal
      });
      var data = null;
      if (response.status !== 204) {
        var type = response.headers.get('content-type') || '';
        data = type.includes('application/json') ? await response.json() : { message: await response.text() };
      }
      if (!response.ok) {
        var error = new Error((data && (data.message || data.error)) || 'The review request failed.');
        error.status = response.status;
        error.data = data;
        throw error;
      }
      return data;
    }

    return {
      setToken: function (value) { token = value || token; },
      setRevision: function (value) { revision = value === undefined ? revision : value; },
      bootstrap: function (path, override) {
        var endpoint = override || '/api/v1/bootstrap';
        var url = new URL(endpoint, window.location.origin);
        url.searchParams.set('path', path);
        return request(url.pathname + url.search);
      },
      createSession: function (path) {
        return request('/api/v1/sessions', { method: 'POST', body: { path: path }, withRevision: false });
      },
      session: function (sid) { return request('/api/v1/sessions/' + encodeURIComponent(sid)); },
      addComment: function (sid, body) {
        return request('/api/v1/sessions/' + encodeURIComponent(sid) + '/comments', { method: 'POST', body: body });
      },
      updateComment: function (sid, cid, body) {
        return request('/api/v1/sessions/' + encodeURIComponent(sid) + '/comments/' + encodeURIComponent(cid), { method: 'PATCH', body: body });
      },
      removeComment: function (sid, cid) {
        return request('/api/v1/sessions/' + encodeURIComponent(sid) + '/comments/' + encodeURIComponent(cid), { method: 'DELETE' });
      },
      revise: function (sid, agent) {
        return request('/api/v1/sessions/' + encodeURIComponent(sid) + '/revisions', { method: 'POST', body: { agent: agent } });
      },
      job: function (jid) { return request('/api/v1/jobs/' + encodeURIComponent(jid)); },
      cancelJob: function (jid) {
        return request('/api/v1/jobs/' + encodeURIComponent(jid), { method: 'DELETE' });
      },
      decide: function (sid, cid, decision, note) {
        return request('/api/v1/sessions/' + encodeURIComponent(sid) + '/decisions/' + encodeURIComponent(cid), {
          method: 'PUT', body: { decision: decision, note: note || '' }
        });
      },
      reconcile: function (sid) {
        return request('/api/v1/sessions/' + encodeURIComponent(sid) + '/reconcile', { method: 'POST', body: {} });
      },
      finalize: function (sid) {
        return request('/api/v1/sessions/' + encodeURIComponent(sid) + '/finalize', { method: 'POST', body: {} });
      }
    };
  }());

  var memoryAgent = '';
  var state = {
    loading: true,
    busy: false,
    session: null,
    agents: [],
    agent: '',
    composer: null,
    activeComment: null,
    job: null,
    view: 'original',
    frameReady: false,
    status: 'Loading review...',
    error: null,
    retry: null,
    railOpen: false,
    noteDrafts: Object.create(null),
    expandedChanges: new Set(),
    viewContext: { original: null, candidate: null },
    lastContext: null,
    complete: false,
    pushWarning: ''
  };
  var pollTimer = null;
  var railReturnFocus = null;

  function h(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function button(label, action, className) {
    var node = h('button', className || 'lh-button', label);
    node.type = 'button';
    node.dataset.action = action;
    return node;
  }

  function commentId(comment) {
    return String(comment.id || comment.commentId || '');
  }

  function commentText(comment) {
    return comment.body || comment.text || comment.comment || '';
  }

  function anchorFor(comment) {
    if (state.view === 'original' && comment.originalAnchor) return comment.originalAnchor;
    if (state.view === 'candidate' && comment.candidateAnchor) return comment.candidateAnchor;
    return comment.anchor || comment.target || {};
  }

  function mapped(comment) {
    var anchor = anchorFor(comment);
    if (state.view === 'candidate' && comment.mapped === false) return false;
    if (anchor.mapped === false || anchor.unmapped === true) return false;
    return Boolean(anchor.selector || anchor.id || anchor.diagramId || anchor.sectionId || anchor.range || anchor.quote);
  }

  function targetsFor(comment) {
    if (state.view === 'candidate' && Array.isArray(comment.candidateTargets) && comment.candidateTargets.length) {
      return comment.candidateTargets;
    }
    return [anchorFor(comment)];
  }

  function quoteFor(anchor) {
    return anchor.quote || anchor.text || anchor.label || anchor.caption || '';
  }

  function sessionId() {
    return state.session && String(state.session.id || state.session.sessionId || state.session.sid || '');
  }

  function sessionComments() {
    return state.session && Array.isArray(state.session.comments) ? state.session.comments : [];
  }

  function normalizeAgent(value) {
    var raw = typeof value === 'string' ? value : value && (value.id || value.name || value.slug);
    var key = String(raw || '').toLowerCase().replace(/[^a-z]/g, '');
    if (key.includes('codex')) return { id: 'codex', label: 'Codex' };
    if (key.includes('claude')) return { id: 'claude', label: 'Claude' };
    if (key.includes('opencode')) return { id: 'opencode', label: 'OpenCode' };
    return null;
  }

  function loadRememberedAgent() {
    try { return window.localStorage.getItem('learning-hub-review-agent') || memoryAgent; }
    catch (_error) { return memoryAgent; }
  }

  function rememberAgent(value) {
    memoryAgent = value;
    try { window.localStorage.setItem('learning-hub-review-agent', value); }
    catch (_error) { /* Session memory remains available. */ }
  }

  function sameOriginDocumentUrl(value) {
    if (!value) return '';
    try {
      var url = new URL(value, window.location.origin);
      if (url.origin !== window.location.origin || !/^https?:$/.test(url.protocol)) return '';
      return url.href;
    } catch (_error) {
      return '';
    }
  }

  function sessionFrom(payload) {
    return payload && payload.session && typeof payload.session === 'object' ? payload.session : payload;
  }

  function absorbSession(payload, options) {
    var next = sessionFrom(payload);
    if (!next || typeof next !== 'object') return;
    state.session = next;
    if (next.revision !== undefined) api.setRevision(next.revision);
    if (next.activeJob) state.job = normalizeJob(next.activeJob);
    else if (!(options && options.keepJob)) state.job = null;
    if (next.complete || next.status === 'complete' || next.status === 'finalized') {
      state.complete = true;
      state.pushWarning = next.finalized && next.finalized.pushError ? String(next.finalized.pushError) : '';
    }
    if (next.candidateAvailable && (!options || options.preferCandidate !== false)) state.view = 'candidate';
    requestHighlights();
  }

  function normalizeJob(job) {
    if (!job) return null;
    if (typeof job === 'string' || typeof job === 'number') return { id: String(job), status: 'queued' };
    return Object.assign({}, job, { id: String(job.id || job.jobId || '') });
  }

  function jobRunning(job) {
    return Boolean(job && !['complete', 'completed', 'succeeded', 'failed', 'cancelled', 'canceled'].includes(String(job.status || '').toLowerCase()));
  }

  function currentDocumentUrl() {
    if (!state.session) return '';
    var value = state.view === 'candidate' ? state.session.candidateUrl : state.session.originalUrl;
    return sameOriginDocumentUrl(value);
  }

  function postToFrame(type, payload) {
    if (!frame.contentWindow) return;
    /* The iframe has an opaque sandbox origin so candidate HTML cannot read
       the local write token. Source-window checks still bind this channel. */
    frame.contentWindow.postMessage(Object.assign({ source: 'learning-hub-review', type: type }, payload || {}), '*');
  }

  function requestHighlights() {
    if (!state.frameReady || !state.session) return;
    var comments = [];
    sessionComments().forEach(function (comment, index) {
      targetsFor(comment).forEach(function (anchor, targetIndex) {
        comments.push({
          commentId: commentId(comment),
          targetIndex: targetIndex,
          anchor: anchor,
          mapped: mapped(comment),
          number: index + 1,
          tone: comment.decision === 'no' || comment.decision === 'maybe' ? 'red' : 'yellow'
        });
      });
    });
    postToFrame('highlights', { comments: comments });
  }

  root.dataset.reviewReady = 'true';
  root.dataset.testid = 'review-root';
  root.className = 'lh-review-root';
  document.body.classList.add('lh-review-active');

  var documentPane = h('main', 'lh-document-pane');
  var toolbar = h('div', 'lh-review-toolbar');
  var toolbarTitle = h('div', 'lh-toolbar-title');
  toolbarTitle.append(h('span', 'lh-kicker', 'Editorial review'), h('span', 'lh-file-name', shellBootstrap.path));
  var viewSwitch = h('div', 'lh-view-switch');
  viewSwitch.setAttribute('role', 'group');
  viewSwitch.setAttribute('aria-label', 'Blueprint version');
  var originalButton = button('Original', 'view-original', 'lh-segment');
  var candidateButton = button('Candidate', 'view-candidate', 'lh-segment');
  candidateButton.dataset.testid = 'candidate';
  viewSwitch.append(originalButton, candidateButton);
  var railToggle = button('Review', 'toggle-rail', 'lh-button lh-rail-toggle');
  railToggle.setAttribute('aria-controls', 'lh-review-rail');
  toolbar.append(toolbarTitle, viewSwitch, railToggle);
  var frameWrap = h('div', 'lh-frame-wrap');
  frame.setAttribute('title', frame.getAttribute('title') || 'Blueprint under review');
  frameWrap.append(frame);
  documentPane.append(toolbar, frameWrap);

  var selectionButton = button('+ Comment', 'selection-comment', 'lh-selection-comment');
  selectionButton.dataset.testid = 'selection-comment';
  selectionButton.hidden = true;
  selectionButton.setAttribute('aria-label', 'Comment on selected text');

  var diagramButton = button('+ Diagram comment', 'diagram-comment', 'lh-selection-comment lh-diagram-comment');
  diagramButton.dataset.testid = 'diagram-comment';
  diagramButton.hidden = true;
  diagramButton.setAttribute('aria-label', 'Comment on selected diagram');

  var scrim = h('button', 'lh-review-scrim');
  scrim.type = 'button';
  scrim.tabIndex = -1;
  scrim.dataset.action = 'close-rail';
  scrim.setAttribute('aria-label', 'Close review panel');
  var rail = h('aside', 'lh-review-rail');
  rail.id = 'lh-review-rail';
  rail.setAttribute('aria-label', 'Blueprint comments');
  var railContent = h('div', 'lh-rail-content');
  rail.append(railContent);
  root.replaceChildren(documentPane, scrim, rail, selectionButton, diagramButton);

  function setStatus(message) {
    state.status = message;
    var live = rail.querySelector('[data-live-status]');
    if (live) live.textContent = message;
  }

  function showError(error, retry) {
    var stale = error && (error.status === 409 || error.status === 412);
    if (stale) {
      state.error = null;
      state.retry = null;
      state.busy = false;
      setStatus('Another tab changed this review. Loading the latest version...');
      render();
      refreshSession({ preferCandidate: false }).then(function () {
        setStatus('Latest review loaded. Your unsaved text remains here.');
        render();
      });
      return;
    }
    state.error = (error && error.message) || 'Something went wrong. Try again.';
    state.retry = retry || null;
    state.busy = false;
    render();
  }

  function clearError() {
    state.error = null;
    state.retry = null;
  }

  function railIsModal() {
    return window.innerWidth < 1080;
  }

  function syncRailAccessibility() {
    var modal = railIsModal();
    var open = !modal || state.railOpen;
    rail.inert = !open;
    if (open) rail.removeAttribute('aria-hidden');
    else rail.setAttribute('aria-hidden', 'true');
    if (modal && state.railOpen) {
      rail.setAttribute('role', 'dialog');
      rail.setAttribute('aria-modal', 'true');
    } else {
      rail.removeAttribute('role');
      rail.removeAttribute('aria-modal');
    }
    documentPane.inert = modal && state.railOpen;
    if (modal && state.railOpen) documentPane.setAttribute('aria-hidden', 'true');
    else documentPane.removeAttribute('aria-hidden');
  }

  function focusableRailNodes() {
    return Array.from(rail.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex]:not([tabindex="-1"])'))
      .filter(function (node) { return !node.hidden && node.getClientRects().length; });
  }

  function focusToken(node) {
    if (!node || node === document.body) return null;
    return {
      id: node.id || '',
      action: node.dataset && node.dataset.action || '',
      testid: node.dataset && node.dataset.testid || '',
      commentId: node.dataset && node.dataset.commentId || '',
      value: node.matches && node.matches('input[type="radio"]') ? node.value : ''
    };
  }

  function focusFromToken(token) {
    if (!token) return null;
    var selector = token.id ? '#' + CSS.escape(token.id) : '';
    if (!selector && token.action) selector = '[data-action="' + CSS.escape(token.action) + '"]';
    if (!selector && token.testid) selector = '[data-testid="' + CSS.escape(token.testid) + '"]';
    if (!selector) return null;
    if (token.commentId) selector += '[data-comment-id="' + CSS.escape(token.commentId) + '"]';
    if (token.value) selector += '[value="' + CSS.escape(token.value) + '"]';
    try { return rail.querySelector(selector); }
    catch (_error) { return null; }
  }

  function openRail(focusRail) {
    if (!state.railOpen && railIsModal()) railReturnFocus = document.activeElement;
    state.railOpen = true;
    root.classList.add('is-rail-open');
    railToggle.setAttribute('aria-expanded', 'true');
    syncRailAccessibility();
    if (focusRail && railIsModal()) {
      window.requestAnimationFrame(function () {
        var target = rail.querySelector('[data-action="close-rail"]') || focusableRailNodes()[0];
        if (target) target.focus();
      });
    }
  }

  function closeRail() {
    var restore = railIsModal() ? railReturnFocus : null;
    state.railOpen = false;
    root.classList.remove('is-rail-open');
    railToggle.setAttribute('aria-expanded', 'false');
    syncRailAccessibility();
    if (restore && typeof restore.focus === 'function') restore.focus();
    railReturnFocus = null;
  }

  function openComposer(anchor, existing) {
    state.composer = {
      mode: existing ? 'edit' : 'new',
      id: existing ? commentId(existing) : '',
      anchor: existing ? anchorFor(existing) : (anchor || {}),
      text: existing ? commentText(existing) : '',
      initialText: existing ? commentText(existing) : ''
    };
    clearError();
    selectionButton.hidden = true;
    diagramButton.hidden = true;
    openRail();
    render();
    var editor = rail.querySelector('[data-testid="comment-editor"]');
    if (editor) editor.focus();
  }

  function composerDirty() {
    return Boolean(state.composer && (state.composer.mode === 'new' ? state.composer.text.trim() : state.composer.text !== state.composer.initialText));
  }

  function beforeRevision() {
    return Boolean(state.session && !state.session.candidateAvailable && !state.job && !state.complete);
  }

  function commentNeedsDecision(comment) {
    if (!state.session || !state.session.candidateAvailable || comment.closed || comment.status === 'closed') return false;
    if (comment.needsDecision === false) return false;
    if (comment.decision === 'no' && !state.session.needsReconciliation) return false;
    if (comment.decision === 'yes' && !comment.changed && !comment.reopened && comment.needsDecision !== true) return false;
    return true;
  }

  function unresolvedCount() {
    return sessionComments().filter(function (comment) {
      if (!commentNeedsDecision(comment)) return false;
      if (['yes', 'no'].includes(comment.decision)) return false;
      if (comment.decision === 'maybe') return !String(comment.note || comment.feedback || '').trim();
      return true;
    }).length;
  }

  function appendEmpty(container) {
    var empty = h('div', 'lh-empty');
    empty.append(h('p', '', 'No comments yet.'), h('p', '', 'Select text or a diagram in the blueprint to start a precise review.'));
    container.append(empty);
  }

  function renderError(container) {
    if (!state.error) return;
    var box = h('div', 'lh-error');
    box.setAttribute('role', 'alert');
    box.append(h('p', '', state.error));
    if (state.retry) box.append(button('Try again', 'retry', 'lh-button lh-button-small'));
    container.append(box);
  }

  function renderComposer(container) {
    if (!state.composer) return;
    var section = h('section', 'lh-composer');
    section.setAttribute('aria-labelledby', 'lh-composer-title');
    var heading = h('div', 'lh-section-heading');
    var title = h('h3', '', state.composer.mode === 'edit' ? 'Edit comment' : 'New comment');
    title.id = 'lh-composer-title';
    heading.append(title, button('Cancel', 'cancel-composer', 'lh-text-button'));
    section.append(heading);
    var quote = quoteFor(state.composer.anchor);
    if (quote) {
      var quoteNode = h('blockquote', 'lh-anchor-quote', quote);
      quoteNode.title = quote;
      section.append(quoteNode);
    } else {
      section.append(h('p', 'lh-anchor-label', state.composer.anchor.kind === 'diagram' ? 'Diagram anchor' : 'Document anchor'));
    }
    var label = h('label', 'lh-field-label', 'Comment');
    label.htmlFor = 'lh-comment-editor';
    var textarea = h('textarea', 'lh-editor');
    textarea.id = 'lh-comment-editor';
    textarea.name = 'comment';
    textarea.dataset.testid = 'comment-editor';
    textarea.rows = 5;
    textarea.required = true;
    textarea.value = state.composer.text;
    textarea.placeholder = 'What should change, and why?';
    textarea.disabled = state.busy;
    section.append(label, textarea);
    var actions = h('div', 'lh-composer-actions');
    actions.append(h('span', 'lh-shortcut', navigator.platform.indexOf('Mac') >= 0 ? '⌘ Enter to save' : 'Ctrl Enter to save'));
    var save = button(state.composer.mode === 'edit' ? 'Save edit' : 'Add comment', 'save-comment', 'lh-button lh-button-primary');
    save.disabled = state.busy || !state.composer.text.trim();
    actions.append(save);
    section.append(actions);
    container.append(section);
  }

  function renderDecision(comment, card) {
    if (!commentNeedsDecision(comment)) return;
    var id = commentId(comment);
    var fieldset = h('fieldset', 'lh-decision');
    fieldset.dataset.decision = id;
    var legend = h('legend', '', 'Was this addressed?');
    fieldset.append(legend);
    var choices = h('div', 'lh-radio-row');
    [['yes', 'Yes'], ['no', 'No'], ['maybe', 'Maybe']].forEach(function (choice) {
      var wrapper = h('label', 'lh-radio-choice');
      var input = h('input');
      input.type = 'radio';
      input.name = 'decision-' + id;
      input.value = choice[0];
      input.dataset.action = 'decision';
      input.dataset.commentId = id;
      input.checked = comment.decision === choice[0];
      input.disabled = state.busy || jobRunning(state.job);
      wrapper.append(input, document.createTextNode(choice[1]));
      choices.append(wrapper);
    });
    fieldset.append(choices);
    if (comment.decision === 'maybe' || state.noteDrafts[id] !== undefined) {
      var label = h('label', 'lh-field-label', 'What is still missing?');
      var note = h('textarea', 'lh-editor lh-note-editor');
      note.rows = 3;
      note.required = true;
      note.id = 'lh-decision-note-' + id;
      note.name = 'decision-note-' + id;
      note.dataset.action = 'decision-note';
      note.dataset.commentId = id;
      note.value = state.noteDrafts[id] !== undefined ? state.noteDrafts[id] : (comment.note || comment.feedback || '');
      label.append(note);
      fieldset.append(label);
      var noteSave = button('Save feedback', 'save-maybe', 'lh-button lh-button-small');
      noteSave.dataset.commentId = id;
      noteSave.disabled = !note.value.trim() || state.busy;
      fieldset.append(noteSave);
    }
    card.append(fieldset);
  }

  function renderChangeSummary(comment, card) {
    var summary = comment.changeSummary;
    if (!state.session || !state.session.candidateAvailable || !summary || !summary.blockCount) return;
    var id = commentId(comment);
    var section = String(summary.sectionId || 'document').replace(/^s/i, '');
    var label = summary.blockCount + ' changed block' + (summary.blockCount === 1 ? '' : 's');
    if (summary.scope === 'section' && section && section !== 'document') label = 'Section ' + section + ': ' + label;
    else if (section && section !== 'document') label += ' near this comment';
    var wrapper = h('div', 'lh-change-summary');
    var row = h('div', 'lh-change-summary-row');
    row.append(h('span', 'lh-change-count', label));
    var hiddenLabel = summary.scope === 'section' ? 'Show section changes' : 'Show changes';
    var toggle = button(state.expandedChanges.has(id) ? 'Hide changes' : hiddenLabel, 'toggle-changes', 'lh-text-button lh-change-toggle');
    toggle.dataset.commentId = id;
    toggle.setAttribute('aria-expanded', String(state.expandedChanges.has(id)));
    row.append(toggle);
    wrapper.append(row);
    if (state.expandedChanges.has(id)) {
      var comparison = h('div', 'lh-change-comparison');
      [['Before', summary.before || [], 'is-before'], ['After', summary.after || [], 'is-after']].forEach(function (group) {
        var panel = h('section', 'lh-change-panel ' + group[2]);
        panel.append(h('h4', '', group[0]));
        var list = h('ul', 'lh-change-list');
        if (!group[1].length) list.append(h('li', 'lh-change-empty', group[0] === 'Before' ? 'New material' : 'Removed material'));
        else group[1].forEach(function (text) { list.append(h('li', '', text)); });
        panel.append(list);
        comparison.append(panel);
      });
      if (summary.truncated) comparison.append(h('p', 'lh-change-more', 'Additional changed blocks are not shown here.'));
      var jump = button('Jump to change', 'jump-change', 'lh-button lh-button-small lh-jump-change');
      jump.dataset.commentId = id;
      comparison.append(jump);
      wrapper.append(comparison);
    }
    card.append(wrapper);
  }

  function renderComment(comment, index) {
    var id = commentId(comment);
    var card = h('article', 'lh-comment-card' + (state.activeComment === id ? ' is-active' : ''));
    card.dataset.testid = 'comment-card';
    card.dataset.commentId = id;
    card.tabIndex = 0;
    var top = h('div', 'lh-card-top');
    top.append(h('span', 'lh-comment-number', String(index + 1).padStart(2, '0')));
    var mapState = h('span', mapped(comment) ? 'lh-map-state is-mapped' : 'lh-map-state is-unmapped', mapped(comment) ? 'Mapped' : 'Unmapped');
    if (!mapped(comment)) mapState.title = 'The source changed or this anchor cannot be located. No replacement was guessed.';
    top.append(mapState);
    card.append(top);
    var quote = quoteFor(anchorFor(comment));
    if (quote) card.append(h('blockquote', 'lh-card-quote', quote));
    card.append(h('p', 'lh-card-body', commentText(comment)));
    renderChangeSummary(comment, card);
    if (beforeRevision()) {
      var tools = h('div', 'lh-card-tools');
      var edit = button('Edit', 'edit-comment', 'lh-text-button');
      edit.dataset.commentId = id;
      var remove = button('Remove', 'remove-comment', 'lh-text-button is-danger');
      remove.dataset.commentId = id;
      tools.append(edit, remove);
      card.append(tools);
    }
    renderDecision(comment, card);
    return card;
  }

  function renderJob(container) {
    if (!state.job) return;
    var job = state.job;
    var box = h('section', 'lh-job');
    var row = h('div', 'lh-job-row');
    row.append(h('h3', '', jobRunning(job) ? 'AI revision in progress' : 'Revision status'));
    if (jobRunning(job)) row.append(button('Cancel', 'cancel-job', 'lh-text-button is-danger'));
    box.append(row);
    var message = job.message || job.detail || String(job.status || 'Working');
    box.append(h('p', 'lh-job-message', message));
    if (jobRunning(job) && job.createdAt) {
      var started = Date.parse(job.createdAt);
      var activity = Date.parse(job.lastActivityAt || job.updatedAt || job.createdAt);
      var elapsed = Number.isFinite(started) ? Math.max(0, Math.floor((Date.now() - started) / 1000)) : 0;
      var inactive = Number.isFinite(activity) ? Math.max(0, Math.floor((Date.now() - activity) / 1000)) : 0;
      var elapsedText = elapsed < 60 ? elapsed + 's' : Math.floor(elapsed / 60) + 'm ' + String(elapsed % 60).padStart(2, '0') + 's';
      var activityText = inactive < 3 ? 'just now' : inactive < 60 ? inactive + 's ago' : Math.floor(inactive / 60) + 'm ago';
      var timeoutMinutes = Math.round(Number(job.timeoutSeconds || 600) / 60);
      box.append(h('p', 'lh-job-timing', 'Elapsed ' + elapsedText + ' · last activity ' + activityText + ' · stops after ' + timeoutMinutes + 'm'));
    }
    var progress = Number(job.progress);
    if (Number.isFinite(progress)) {
      if (progress > 1) progress = progress / 100;
      var meter = h('progress', 'lh-progress');
      meter.max = 1;
      meter.value = Math.max(0, Math.min(1, progress));
      meter.setAttribute('aria-label', 'Revision progress');
      box.append(meter);
    } else if (jobRunning(job)) {
      box.append(h('div', 'lh-progress is-indeterminate'));
    }
    container.append(box);
  }

  function renderActions(container) {
    if (!state.session || state.complete) return;
    var section = h('section', 'lh-review-actions');
    if (beforeRevision()) {
      var label = h('label', 'lh-field-label', 'Revision agent');
      label.htmlFor = 'lh-agent-picker';
      var select = h('select', 'lh-agent-picker');
      select.id = 'lh-agent-picker';
      select.name = 'agent';
      select.dataset.action = 'agent';
      select.disabled = state.busy || !state.agents.length;
      state.agents.forEach(function (agent) {
        var option = h('option', '', agent.label);
        option.value = agent.id;
        option.selected = agent.id === state.agent;
        select.append(option);
      });
      label.append(select);
      section.append(label);
      var revise = button('Revise with AI', 'revise', 'lh-button lh-button-primary lh-button-wide');
      revise.dataset.testid = 'submit-comments';
      revise.disabled = state.busy || !state.agent || !sessionComments().length || state.session.canRevise === false;
      section.append(revise);
      if (!sessionComments().length) section.append(h('p', 'lh-action-help', 'Add at least one comment before revising.'));
    } else if (state.session.candidateAvailable && !jobRunning(state.job) && (state.session.needsReconciliation || unresolvedCount() > 0)) {
      var remaining = unresolvedCount();
      var next = button('Revise with AI', 'reconcile', 'lh-button lh-button-primary lh-button-wide');
      next.dataset.testid = 'submit-comments';
      next.disabled = state.busy || remaining > 0 || !state.session.needsReconciliation;
      section.append(next);
      var helper = remaining
        ? remaining + ' comment' + (remaining === 1 ? '' : 's') + ' still need a decision.'
        : 'Unresolved comments are ready for reconciliation.';
      section.append(h('p', 'lh-action-help', helper));
    }
    if (section.childNodes.length) container.append(section);
  }

  function renderFinalize(container) {
    if (!state.session || !state.session.candidateAvailable || state.complete) return;
    var section = h('section', 'lh-finalize');
    section.append(h('h3', '', 'Finish review'));
    var finalize = button('Approve and finalize', 'finalize', 'lh-button lh-button-final lh-button-wide');
    finalize.dataset.testid = 'approve-final';
    finalize.disabled = !state.session.canFinalize || composerDirty() || state.busy || jobRunning(state.job);
    section.append(finalize);
    if (!state.session.canFinalize) section.append(h('p', 'lh-action-help', state.session.finalizeBlockedReason || 'Resolve every open comment before finalizing.'));
    else if (composerDirty()) section.append(h('p', 'lh-action-help', 'Save or cancel the draft comment first.'));
    container.append(section);
  }

  function render() {
    var previousFocus = rail.contains(document.activeElement) ? focusToken(document.activeElement) : null;
    function restoreRenderedFocus() {
      var target = focusFromToken(previousFocus);
      if (!target && state.railOpen && railIsModal() && !rail.contains(document.activeElement)) {
        target = focusableRailNodes()[0] || null;
      }
      if (target) target.focus();
    }
    originalButton.classList.toggle('is-active', state.view === 'original');
    candidateButton.classList.toggle('is-active', state.view === 'candidate');
    originalButton.setAttribute('aria-pressed', String(state.view === 'original'));
    candidateButton.setAttribute('aria-pressed', String(state.view === 'candidate'));
    candidateButton.disabled = !state.session || !state.session.candidateAvailable;
    railToggle.textContent = 'Review' + (sessionComments().length ? ' ' + sessionComments().length : '');
    railToggle.setAttribute('aria-expanded', String(state.railOpen));

    railContent.replaceChildren();
    var header = h('header', 'lh-rail-header');
    var heading = h('div');
    heading.append(h('span', 'lh-kicker', 'Review queue'), h('h2', '', 'Editorial notes'));
    var close = button('Close', 'close-rail', 'lh-text-button lh-close-rail');
    close.setAttribute('aria-label', 'Close review panel');
    header.append(heading, close);
    railContent.append(header);
    var live = h('p', 'lh-live-status', state.status);
    live.dataset.liveStatus = 'true';
    live.setAttribute('role', 'status');
    live.setAttribute('aria-live', 'polite');
    railContent.append(live);
    renderError(railContent);

    if (state.complete) {
      var complete = h('section', 'lh-complete');
      complete.dataset.testid = 'review-complete';
      var completeTitle = state.pushWarning ? 'Saved locally' : 'Review complete';
      var completeText = state.pushWarning
        ? 'The blueprint and commit are safe on this computer, but Git could not push them. Run git push when the remote is reachable. ' + state.pushWarning
        : 'The approved blueprint is saved. Reloading the review...';
      complete.append(h('span', 'lh-complete-mark', '✓'), h('h3', '', completeTitle), h('p', '', completeText));
      railContent.append(complete);
      restoreRenderedFocus();
      return;
    }
    if (state.loading) {
      railContent.append(h('p', 'lh-loading', 'Opening the review session...'));
      restoreRenderedFocus();
      return;
    }
    renderComposer(railContent);
    var listSection = h('section', 'lh-comment-list-section');
    var listHeading = h('div', 'lh-list-heading');
    listHeading.append(h('h3', '', 'Comments'), h('span', 'lh-count', String(sessionComments().length)));
    listSection.append(listHeading);
    var list = h('div', 'lh-comment-list');
    if (!sessionComments().length) appendEmpty(list);
    else sessionComments().forEach(function (comment, index) { list.append(renderComment(comment, index)); });
    listSection.append(list);
    railContent.append(listSection);
    renderJob(railContent);
    renderActions(railContent);
    renderFinalize(railContent);
    restoreRenderedFocus();
  }

  function updateComposerState() {
    var save = rail.querySelector('[data-action="save-comment"]');
    if (save) save.disabled = state.busy || !state.composer || !state.composer.text.trim();
    var finalize = rail.querySelector('[data-action="finalize"]');
    if (finalize) finalize.disabled = !state.session.canFinalize || composerDirty() || state.busy || jobRunning(state.job);
  }

  async function refreshSession(options) {
    var sid = sessionId();
    if (!sid) return;
    try {
      var payload = await api.session(sid);
      absorbSession(payload, { preferCandidate: !(options && options.preferCandidate === false) });
      if (!(options && options.preserveError)) clearError();
      render();
      syncFrameUrl();
    } catch (error) {
      showError(error, function () { refreshSession(options); });
    }
  }

  async function applyMutationResponse(response, options) {
    var candidate = sessionFrom(response);
    if (candidate && Array.isArray(candidate.comments) && candidate.revision !== undefined) {
      absorbSession(response, options);
      return;
    }
    await refreshSession(options);
  }

  async function saveComment() {
    if (!state.composer || !state.composer.text.trim() || state.busy) return;
    state.busy = true;
    clearError();
    render();
    var draft = state.composer;
    var payload = { body: draft.text.trim(), anchor: draft.anchor };
    try {
      var response = draft.mode === 'edit'
        ? await api.updateComment(sessionId(), draft.id, payload)
        : await api.addComment(sessionId(), payload);
      state.composer = null;
      await applyMutationResponse(response, { preferCandidate: false });
      state.busy = false;
      setStatus(draft.mode === 'edit' ? 'Comment updated.' : 'Comment added to the review queue.');
      render();
    } catch (error) {
      state.composer = draft;
      showError(error, saveComment);
    }
  }

  async function removeComment(id) {
    if (!window.confirm('Remove this comment from the review queue?')) return;
    state.busy = true;
    clearError();
    render();
    try {
      var response = await api.removeComment(sessionId(), id);
      await applyMutationResponse(response, { preferCandidate: false });
      state.busy = false;
      setStatus('Comment removed.');
      render();
    } catch (error) {
      showError(error, function () { removeComment(id); });
    }
  }

  async function startRevision(reconcile) {
    if (state.busy || jobRunning(state.job)) return;
    state.busy = true;
    clearError();
    render();
    try {
      var response = reconcile ? await api.reconcile(sessionId()) : await api.revise(sessionId(), state.agent);
      state.job = normalizeJob(response && (response.job || response.jobId || response));
      state.busy = false;
      setStatus(reconcile ? 'Reconciliation started.' : 'AI revision started.');
      render();
      schedulePoll(0);
    } catch (error) {
      showError(error, function () { startRevision(reconcile); });
    }
  }

  async function pollJob() {
    if (!state.job || !state.job.id) return;
    try {
      var result = normalizeJob(await api.job(state.job.id));
      state.job = result;
      render();
      if (jobRunning(result)) {
        setStatus(result.message || 'AI revision is running.');
        schedulePoll(900);
        return;
      }
      if (['cancelled', 'canceled'].includes(String(result.status || '').toLowerCase())) {
        state.job = null;
        setStatus('AI revision cancelled. Your comments are still here.');
        await refreshSession({ preferCandidate: false });
        return;
      }
      if (String(result.status || '').toLowerCase() === 'failed') {
        state.error = result.error || result.message || 'The AI revision did not finish.';
        state.retry = function () { startRevision(Boolean(state.session && state.session.candidateAvailable)); };
        render();
        return;
      }
      setStatus('Candidate revision is ready.');
      await refreshSession();
      state.view = 'candidate';
      syncFrameUrl(true);
      render();
    } catch (error) {
      showError(error, pollJob);
    }
  }

  function schedulePoll(delay) {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(pollJob, delay);
  }

  async function cancelJob() {
    if (!state.job || !state.job.id || state.busy) return;
    state.busy = true;
    render();
    try {
      var response = await api.cancelJob(state.job.id);
      window.clearTimeout(pollTimer);
      state.job = normalizeJob(response && (response.job || response.jobId || response)) || state.job;
      state.busy = false;
      setStatus('Cancelling the AI revision...');
      render();
      schedulePoll(0);
    } catch (error) {
      showError(error, cancelJob);
    }
  }

  async function saveDecision(id, decision, note) {
    if (state.busy) return;
    if (decision === 'maybe' && !String(note || '').trim()) {
      state.noteDrafts[id] = note || '';
      render();
      var input = rail.querySelector('[data-action="decision-note"][data-comment-id="' + CSS.escape(id) + '"]');
      if (input) input.focus();
      return;
    }
    state.busy = true;
    clearError();
    render();
    try {
      var response = await api.decide(sessionId(), id, decision, note || '');
      delete state.noteDrafts[id];
      await applyMutationResponse(response, { preferCandidate: true });
      state.busy = false;
      setStatus(decision === 'yes' ? 'Marked addressed.' : decision === 'no' ? 'Marked for reconciliation.' : 'Feedback saved; this comment remains open.');
      render();
    } catch (error) {
      showError(error, function () { saveDecision(id, decision, note); });
    }
  }

  async function finalizeReview() {
    if (!state.session.canFinalize || composerDirty() || state.busy || jobRunning(state.job)) return;
    if (!window.confirm('Approve this candidate and finalize the blueprint?')) return;
    state.busy = true;
    clearError();
    render();
    try {
      var response = await api.finalize(sessionId());
      await applyMutationResponse(response, { preferCandidate: true });
      state.busy = false;
      state.complete = true;
      var finalizeResult = response && (response.finalizeResult || (response.session && response.session.finalizeResult));
      state.pushWarning = finalizeResult && finalizeResult.pushError ? String(finalizeResult.pushError) : '';
      setStatus(state.pushWarning ? 'Saved locally. Git push needs attention.' : 'Review complete. The approved blueprint is saved.');
      render();
      if (!state.pushWarning) window.setTimeout(function () { window.location.reload(); }, 900);
    } catch (error) {
      showError(error, finalizeReview);
    }
  }

  function syncFrameUrl(force) {
    var url = currentDocumentUrl();
    if (!url) return;
    if (force || frame.src !== url) {
      state.frameReady = false;
      frame.src = url;
    }
  }

  function switchView(view) {
    if (view === state.view || (view === 'candidate' && !state.session.candidateAvailable)) return;
    state.viewContext[state.view] = { sectionId: state.lastContext && state.lastContext.sectionId, scrollY: window.scrollY };
    state.view = view;
    render();
    syncFrameUrl(true);
  }

  function findComment(id) {
    return sessionComments().find(function (comment) { return commentId(comment) === String(id); });
  }

  function targetComment(id, targetIndex) {
    var comment = findComment(id);
    if (!comment) return;
    var index = Number(targetIndex);
    var preciseTarget = Number.isInteger(index) && index >= 0 ? targetsFor(comment)[index] : null;
    state.activeComment = String(id);
    openRail();
    render();
    var card = rail.querySelector('[data-comment-id="' + CSS.escape(String(id)) + '"]');
    if (card) {
      card.focus({ preventScroll: true });
      card.scrollIntoView({ block: 'nearest', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    }
    if (mapped(comment)) postToFrame('target', {
      commentId: String(id),
      targetIndex: preciseTarget ? index : 0,
      anchor: preciseTarget || anchorFor(comment)
    });
    else setStatus('This comment is unmapped. No document location was guessed.');
  }

  function normalizeAnchor(payload, kind) {
    var source = payload.anchor || payload.selection || payload.diagram || payload;
    return {
      kind: kind,
      quote: source.quote || source.text || source.label || source.caption || '',
      selector: source.selector || source.range || null,
      range: source.range || null,
      id: source.id || null,
      diagramId: source.diagramId || (kind === 'diagram' ? source.id : null),
      sectionId: source.sectionId || null,
      mapped: true
    };
  }

  function positionProxy(node, rect) {
    if (!rect) return;
    var frameRect = frame.getBoundingClientRect();
    var left = frameRect.left + Number(rect.right !== undefined ? rect.right : rect.left || 0) + 8;
    var top = frameRect.top + Number(rect.top || 0) - 4;
    node.style.left = Math.max(8, Math.min(window.innerWidth - node.offsetWidth - 8, left)) + 'px';
    node.style.top = Math.max(8, Math.min(window.innerHeight - 52, top)) + 'px';
  }

  function receiveBridge(event) {
    if (event.source !== frame.contentWindow || !['null', window.location.origin].includes(event.origin) || !event.data || typeof event.data !== 'object') return;
    var data = event.data;
    var type = String(data.type || '').replace(/^review:/, '').replace(/^lh:/, '');
    if (type === 'ready') {
      state.frameReady = true;
      requestHighlights();
      var context = state.viewContext[state.view];
      if (context && context.sectionId) postToFrame('target', { sectionId: context.sectionId });
      if (context && Number.isFinite(context.scrollY)) window.requestAnimationFrame(function () { window.scrollTo(0, context.scrollY); });
      return;
    }
    if (type === 'height') {
      var height = Number(data.height || (data.payload && data.payload.height));
      if (Number.isFinite(height)) frame.style.height = Math.max(480, Math.ceil(height)) + 'px';
      return;
    }
    if (type === 'selection') {
      var selection = data.payload || data.selection || data;
      if (selection.collapsed || !String(selection.text || selection.quote || '').trim()) {
        selectionButton.hidden = true;
        return;
      }
      if (selection.unsupported || (selection.anchor && selection.anchor.selector && selection.anchor.selector.multiBlock)) {
        selectionButton.hidden = true;
        setStatus('Select text within one paragraph, list item, formula, or diagram.');
        return;
      }
      selectionButton._reviewAnchor = normalizeAnchor(selection, 'text');
      selectionButton.hidden = false;
      positionProxy(selectionButton, selection.rect || data.rect);
      state.lastContext = selectionButton._reviewAnchor;
      return;
    }
    if (type === 'diagram') {
      var diagram = data.payload || data.diagram || data;
      diagramButton._reviewAnchor = normalizeAnchor(diagram, 'diagram');
      diagramButton.hidden = false;
      positionProxy(diagramButton, diagram.rect || data.rect);
      state.lastContext = diagramButton._reviewAnchor;
      if (data.action === 'click' || data.action === 'activate' || diagram.action === 'click' || diagram.action === 'activate' || diagram.openComposer) openComposer(diagramButton._reviewAnchor);
      return;
    }
    if (type === 'comment' || type === 'highlight') {
      var id = data.commentId || (data.payload && data.payload.commentId);
      var targetIndex = data.targetIndex;
      if (targetIndex === undefined && data.payload) targetIndex = data.payload.targetIndex;
      if (id) targetComment(id, targetIndex);
      return;
    }
    if (type === 'internal-link') {
      var href = data.href || (data.payload && data.payload.href);
      try {
        var logicalPath = '/' + String(shellBootstrap.path).replace(/^\/+/, '');
        var logicalDocument = new URL(logicalPath, window.location.origin);
        var url = new URL(href, logicalDocument);
        if (url.origin !== window.location.origin || !/^https?:$/.test(url.protocol)) return;
        if (url.pathname === logicalDocument.pathname) {
          postToFrame('target', { href: url.pathname + url.search + url.hash, fragment: url.hash });
        } else if (url.pathname === '/index.html') {
          window.location.assign('/');
        } else if (/^\/topics\/[^/]+\.html$/.test(url.pathname)) {
          window.location.assign(url.pathname + url.search + url.hash);
        }
      } catch (_error) {
        /* Malformed and out-of-scope links stay inside the sandbox. */
      }
    }
  }

  root.addEventListener('input', function (event) {
    if (event.target.matches('[data-testid="comment-editor"]') && state.composer) {
      state.composer.text = event.target.value;
      updateComposerState();
    }
    if (event.target.matches('[data-action="decision-note"]')) {
      state.noteDrafts[event.target.dataset.commentId] = event.target.value;
      var save = event.target.closest('fieldset').querySelector('[data-action="save-maybe"]');
      if (save) save.disabled = !event.target.value.trim() || state.busy;
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && state.composer) {
      event.preventDefault();
      state.composer = null;
      render();
      return;
    }
    if (event.key === 'Escape' && state.railOpen && railIsModal()) {
      event.preventDefault();
      closeRail();
      return;
    }
    if (event.key === 'Tab' && state.railOpen && railIsModal()) {
      var focusable = focusableRailNodes();
      if (focusable.length) {
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        if (!rail.contains(document.activeElement)) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        } else if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }
    if (event.target.matches('[data-testid="comment-editor"]') && event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      saveComment();
    }
    var card = event.target.closest('[data-testid="comment-card"]');
    if (card && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      targetComment(card.dataset.commentId);
    }
  });

  root.addEventListener('change', function (event) {
    if (event.target.matches('[data-action="agent"]')) {
      state.agent = event.target.value;
      rememberAgent(state.agent);
    }
    if (event.target.matches('[data-action="decision"]')) {
      var id = event.target.dataset.commentId;
      if (event.target.value === 'maybe') {
        var comment = findComment(id) || {};
        state.noteDrafts[id] = comment.note || comment.feedback || '';
        render();
        var note = rail.querySelector('[data-action="decision-note"][data-comment-id="' + CSS.escape(id) + '"]');
        if (note) note.focus();
      } else saveDecision(id, event.target.value, '');
    }
  });

  root.addEventListener('click', function (event) {
    var actionNode = event.target.closest('[data-action]');
    if (!actionNode) {
      var card = event.target.closest('[data-testid="comment-card"]');
      if (card) targetComment(card.dataset.commentId);
      return;
    }
    var action = actionNode.dataset.action;
    if (action === 'toggle-rail') state.railOpen ? closeRail() : openRail(true);
    else if (action === 'close-rail') closeRail();
    else if (action === 'view-original') switchView('original');
    else if (action === 'view-candidate') switchView('candidate');
    else if (action === 'selection-comment') openComposer(selectionButton._reviewAnchor);
    else if (action === 'diagram-comment') openComposer(diagramButton._reviewAnchor);
    else if (action === 'cancel-composer') { state.composer = null; clearError(); render(); }
    else if (action === 'save-comment') saveComment();
    else if (action === 'edit-comment') openComposer(null, findComment(actionNode.dataset.commentId));
    else if (action === 'remove-comment') removeComment(actionNode.dataset.commentId);
    else if (action === 'toggle-changes') {
      var changeId = actionNode.dataset.commentId;
      if (state.expandedChanges.has(changeId)) state.expandedChanges.delete(changeId);
      else state.expandedChanges.add(changeId);
      render();
    }
    else if (action === 'jump-change') targetComment(actionNode.dataset.commentId);
    else if (action === 'revise') startRevision(false);
    else if (action === 'reconcile') startRevision(true);
    else if (action === 'cancel-job') cancelJob();
    else if (action === 'save-maybe') saveDecision(actionNode.dataset.commentId, 'maybe', state.noteDrafts[actionNode.dataset.commentId]);
    else if (action === 'finalize') finalizeReview();
    else if (action === 'retry' && state.retry) state.retry();
  });

  window.addEventListener('message', receiveBridge);
  window.addEventListener('resize', function () {
    if (!selectionButton.hidden && selectionButton._reviewAnchor && selectionButton._reviewAnchor.rect) positionProxy(selectionButton, selectionButton._reviewAnchor.rect);
    if (window.innerWidth >= 1080 && state.railOpen) closeRail();
    else syncRailAccessibility();
  });

  async function initialize() {
    syncRailAccessibility();
    render();
    try {
      var bootstrap = await api.bootstrap(shellBootstrap.path, shellBootstrap.bootstrapUrl);
      api.setToken((bootstrap && bootstrap.token) || shellBootstrap.token);
      var rawAgents = (bootstrap && bootstrap.agents) || [];
      var seen = new Set();
      state.agents = rawAgents.map(normalizeAgent).filter(function (agent) {
        if (!agent || seen.has(agent.id)) return false;
        seen.add(agent.id);
        return true;
      });
      var remembered = loadRememberedAgent();
      state.agent = state.agents.some(function (agent) { return agent.id === remembered; }) ? remembered : (state.agents[0] && state.agents[0].id) || '';

      var initial = bootstrap && bootstrap.session;
      if (!initial) initial = await api.createSession(shellBootstrap.path);
      if (typeof initial === 'string' || typeof initial === 'number') initial = await api.session(String(initial));
      absorbSession(initial, { preferCandidate: true });
      if (!sessionId() && initial && (initial.sessionId || initial.sid)) absorbSession(initial, { preferCandidate: true });
      if (!sessionId()) throw new Error('The local server did not return a review session.');
      if (!Array.isArray(state.session.comments)) await refreshSession();
      state.loading = false;
      state.status = sessionComments().length ? 'Review queue restored.' : 'Ready for comments.';
      render();
      syncFrameUrl();
      if (jobRunning(state.job)) schedulePoll(0);
    } catch (error) {
      state.loading = false;
      showError(error, initialize);
    }
  }

  initialize();
}());

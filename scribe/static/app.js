/*
  MyScribe - the page's only script.

  It lives in a file rather than inline in base.html so the app can send a
  Content-Security-Policy with `script-src 'self'` and no 'unsafe-inline'.

  Two rules hold throughout:

  * Anything that came from the server or the user is written with
    textContent, never innerHTML. A transcript, a title or an error message
    can contain any characters at all, and this is the layer where "no |safe
    in the templates" would otherwise be undone.
  * Every listener is delegated from the document, so an htmx swap that
    replaces a region needs no re-wiring.
*/
(function () {
  'use strict';

  /* ---- the flash line under the top bar --------------------------------- */

  function flash(text, tone) {
    var box = document.getElementById('flash');
    if (!box) { return; }
    box.className = 'flash' + (tone ? ' ' + tone : '');
    box.textContent = text;
  }

  /*
    A notice the server sends with the page it just rendered, as an
    `HX-Trigger` header htmx turns into this event. For the things that are
    neither an error nor part of the markup: "three private recordings were
    skipped" belongs on screen, and the table that came back has nowhere to say
    it. Neutral tone - a skip that was asked for is not a failure.
  */
  document.body.addEventListener('scribe-notice', function (event) {
    var text = event && event.detail;
    if (typeof text === 'string' && text) { flash(text, ''); }
  });

  function parseJson(text) {
    if (typeof text !== 'string' || !text) { return null; }
    try { return JSON.parse(text); } catch (err) { return null; }
  }

  function describeError(res) {
    var detail = res && res.body ? res.body.detail : null;
    if (typeof detail === 'string' && detail) { return detail; }
    if (detail) { return JSON.stringify(detail); }
    return 'HTTP ' + (res ? res.status : '?');
  }

  /* ---- confirmed forms --------------------------------------------------- */

  /*
    A <form method="post" data-confirm="..."> is a plain form, so it works
    with scripting off: the browser posts it and lands wherever the route
    redirects. With scripting on, this intercepts the submit, asks the
    question the server rendered into data-confirm, and posts in the
    background instead.

    The confirmation text is the server's, not assembled here: the template
    knows what it is rendering (this file, that job) and a message built from
    the same rows cannot drift from what will actually happen.

    What happens after a successful post is the form's call:

      data-refresh="<selector>"   fire a `refresh` event on every match, so
                                  an htmx region with hx-trigger="..., refresh"
                                  re-fetches itself
      (nothing)                   follow the redirect the server sent, or
                                  reload the page if it sent none

    A form with data-refresh and no data-confirm (Retry on the jobs board)
    takes the same path without the question.
  */
  function postForm(form) {
    /* A form that refreshes a region afterwards has no use for the page the
       route would redirect to; saying so (no text/html in Accept) gets a 204
       from the library's routes instead of a redirect fetch would follow
       into a full page nobody reads. A form without data-refresh follows
       the redirect, so it asks for HTML. */
    var refreshes = form.hasAttribute('data-refresh');
    return fetch(form.action, {
      method: 'POST',
      /* URLSearchParams posts application/x-www-form-urlencoded - the same
         body the form would send without JavaScript, so one route handles
         both paths. */
      body: new URLSearchParams(new FormData(form)),
      headers: { 'Accept': refreshes ? 'application/json' : 'text/html, application/json' },
      credentials: 'same-origin'
    }).then(function (response) {
      return response.text().then(function (text) {
        return {
          ok: response.ok,
          status: response.status,
          redirected: response.redirected,
          url: response.url,
          body: parseJson(text)
        };
      });
    });
  }

  function refreshTargets(selector) {
    if (!selector || !window.htmx) { return false; }
    var targets = document.querySelectorAll(selector);
    targets.forEach(function (el) { window.htmx.trigger(el, 'refresh'); });
    return targets.length > 0;
  }

  function handleConfirmedForm(event, form) {
    event.preventDefault();
    var message = form.getAttribute('data-confirm');
    if (message && !window.confirm(message)) { return; }

    var buttons = form.querySelectorAll('button, input[type="submit"]');
    buttons.forEach(function (b) { b.disabled = true; });

    postForm(form).then(function (res) {
      if (!res.ok) {
        flash('That did not work: ' + describeError(res), 'bad');
        buttons.forEach(function (b) { b.disabled = false; });
        return;
      }
      if (refreshTargets(form.getAttribute('data-refresh'))) {
        buttons.forEach(function (b) { b.disabled = false; });
      } else if (res.redirected && res.url) {
        window.location.assign(res.url);
      } else {
        window.location.reload();
      }
    }).catch(function (err) {
      flash('Could not reach the server: ' + (err && err.message ? err.message : 'request failed'), 'bad');
      buttons.forEach(function (b) { b.disabled = false; });
    });
  }

  function wireForms() {
    document.addEventListener('submit', function (event) {
      var form = event.target;
      if (!form || typeof form.matches !== 'function') { return; }
      if (form.matches('form[data-confirm], form[data-refresh]')) {
        handleConfirmedForm(event, form);
      }
    });
  }

  /* ---- the jobs board and the job detail page ------------------------------- */

  var LOG_MAX_LINES = 2000;
  var JOB_STATUSES = ['queued', 'running', 'done', 'failed', 'cancelled', 'interrupted'];

  /* A server value used as part of a class name must be one of ours. */
  function oneOf(value, allowed, fallback) {
    var text = String(value == null ? '' : value).toLowerCase();
    return allowed.indexOf(text) === -1 ? fallback : text;
  }

  /* Mirrors render.format_ts's clock style: m:ss, h:mm:ss from an hour up.
     Two implementations of one format is a smell; the alternative is a
     counter that shows nothing until the next poll. */
  function formatClock(seconds) {
    var total = Math.max(0, Math.floor(seconds));
    if (!isFinite(total)) { return ''; }
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    var s = total % 60;
    var ss = s < 10 ? '0' + s : String(s);
    if (h) { return h + ':' + (m < 10 ? '0' + m : m) + ':' + ss; }
    return m + ':' + ss;
  }

  /* "Elapsed 3:12" has to keep counting between polls, or the number is a
     lie within a second of rendering. Only .live counters tick: a finished
     job's duration is a fact, not a clock. Delegated by selector, so the
     counters inside a freshly swapped fragment tick too. */
  function tickElapsed() {
    var now = Date.now() / 1000;
    document.querySelectorAll('.elapsed.live[data-since]').forEach(function (el) {
      var since = Number(el.getAttribute('data-since'));
      if (since > 0) { el.textContent = formatClock(now - since); }
    });
  }

  /*
    The live log on the job detail page: a <pre id="log"> fed by the SSE
    stream at data-stream-url, starting after data-last-seq (the newest
    event the panel already rendered) so nothing arrives twice.

    Frames, as scribe.web.jobs_ui names them:

      stage / log / error   a job_event row; appended as one line
      progress              the job row's status, stage and stage_progress;
                            moves the stepper and the bar
      end                   the job is terminal; the stream is closed here,
                            because EventSource would otherwise reopen it,
                            and the panel is refreshed so the verdict shows

    Every line is written with textContent: a trace or a stage summary is
    pipeline output. A transport error on a running job is left to
    EventSource, which reconnects by itself with Last-Event-ID.
  */
  function wireJobLog() {
    var pre = document.getElementById('log');
    if (!pre || !pre.hasAttribute('data-stream-url')) { return; }
    var nojs = document.getElementById('log-nojs');
    if (nojs) { nojs.hidden = true; }
    var status = document.querySelector('[data-log-status]');
    function say(text) { if (status) { status.textContent = text; } }

    if (typeof window.EventSource === 'undefined') {
      say('This browser has no EventSource, so the log cannot be streamed.');
      return;
    }

    var wasTerminal = pre.getAttribute('data-terminal') === 'true';
    var lastSeq = Number(pre.getAttribute('data-last-seq')) || 0;
    var url = pre.getAttribute('data-stream-url') + (lastSeq ? '?last_event_id=' + lastSeq : '');
    var seen = 0;

    function append(text) {
      var stick = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
      pre.appendChild(document.createTextNode(text + '\n'));
      while (pre.childNodes.length > LOG_MAX_LINES) { pre.removeChild(pre.firstChild); }
      if (stick) { pre.scrollTop = pre.scrollHeight; }
    }

    function stamp(ts) {
      var date = new Date(Number(ts) * 1000);
      return isFinite(date.getTime()) && ts > 0 ? date.toLocaleTimeString() : '';
    }

    function describe(record) {
      var kind = String(record.kind || '');
      var payload = record.payload && typeof record.payload === 'object' ? record.payload : {};
      if (kind === 'stage') { return 'stage ' + String(payload.name == null ? '' : payload.name); }
      if (kind === 'log' && typeof payload.text === 'string') {
        /* The transcribe stage's glimpse of the text, shown as the transcript
           shows it: [m:ss] and the words, no key=value. */
        var at = Math.max(0, Math.floor(Number(payload.at) || 0));
        var m = Math.floor(at / 60), s = at % 60;
        return '[' + m + ':' + (s < 10 ? '0' : '') + s + '] ' + payload.text;
      }
      if (kind === 'error') {
        var lines = String(payload.trace == null ? '' : payload.trace).trim().split('\n');
        return 'error ' + lines[lines.length - 1];
      }
      var parts = Object.keys(payload).map(function (key) {
        var value = payload[key];
        return key + '=' + (typeof value === 'string' ? value : JSON.stringify(value));
      });
      return kind + (parts.length ? ' ' + parts.join(', ') : '');
    }

    function onRecord(event) {
      var record = parseJson(event.data);
      if (!record) { return; }
      seen += 1;
      append(stamp(record.ts) + '  ' + describe(record));
    }

    function applyProgress(state) {
      var pct = Math.round(Math.max(0, Math.min(1, Number(state.stage_progress) || 0)) * 100);
      var bar = document.querySelector('[data-stage-progress]');
      var label = document.querySelector('[data-stage-label]');
      if (bar) { bar.value = pct; }
      if (label) { label.textContent = String(state.stage || '') + ' · ' + pct + '%'; }
      var stepper = document.querySelector('[data-stepper]');
      if (stepper && state.status === 'running' && state.stage) {
        var reached = false;
        stepper.querySelectorAll('.step').forEach(function (step) {
          var name = step.getAttribute('data-stage');
          var cls = 'todo';
          if (name === state.stage) { cls = 'active'; reached = true; }
          else if (!reached) { cls = 'done'; }
          step.className = 'step ' + cls;
        });
      }
      var badge = document.querySelector('[data-job-status]');
      if (badge && state.status) {
        var word = oneOf(state.status, JOB_STATUSES, 'queued');
        badge.className = 'badge s-' + word;
        badge.textContent = word;
      }
    }

    var es = new EventSource(url);
    say('connecting…');
    es.onopen = function () { say(wasTerminal ? 'finished' : 'live'); };
    es.addEventListener('stage', onRecord);
    es.addEventListener('log', onRecord);
    es.addEventListener('progress', function (event) {
      var state = parseJson(event.data);
      if (state) { applyProgress(state); }
    });
    es.addEventListener('end', function (event) {
      var payload = parseJson(event.data) || {};
      es.close();
      say(payload.status ? 'finished: ' + payload.status : 'finished');
      /* The page rendered the end line itself for a job that was already
         over; only a job that ended while we watched gets one here. */
      if (!wasTerminal) {
        append('-- end of log' + (payload.status ? ' (' + payload.status + ')' : '') + ' --');
      }
      if (!wasTerminal) { refreshTargets('#job-panel, #job-details'); }
    });
    /* Two different things arrive here. A frame the server named "error"
       carries data and is a job event; a transport failure has no data and
       is the browser saying the connection dropped - which, for a running
       job, is exactly what EventSource retries by itself. */
    es.addEventListener('error', function (event) {
      if (event && typeof event.data === 'string' && event.data) { onRecord(event); return; }
      if (es.readyState === 2) { say('stream closed'); return; }
      if (wasTerminal) { es.close(); say('finished'); return; }
      say('disconnected - reconnecting…');
    });
  }

  /* ---- the library's select-all box --------------------------------------- */

  /*
    The header checkbox marked data-select-all sets every row checkbox in
    the same table to its own state. Row checkboxes belong to the bulk form
    through form="bulk-form", so nothing else is needed for a bulk post to
    see them. Delegated, because an htmx swap replaces the whole table.
  */
  function wireSelectAll() {
    document.addEventListener('change', function (event) {
      var box = event.target;
      if (!box || typeof box.matches !== 'function') { return; }
      if (!box.matches('input[type="checkbox"][data-select-all]')) { return; }
      var table = box.closest('table') || document;
      table.querySelectorAll('input[type="checkbox"][name="ids"]').forEach(function (row) {
        row.checked = box.checked;
      });
    });
  }

  /* htmx swaps nothing on a 4xx/5xx and says nothing either; a request that
     failed should at least be visible. The response text is the server's
     own error page or JSON detail, shown as text - in the page's flash line
     and, when the request came from inside an open dialog, in the dialog's
     own line too, because a modal dims everything behind it. */
  function reportError(event, message) {
    flash(message, 'bad');
    var elt = event.detail && event.detail.elt;
    var dialog = elt && elt.closest ? elt.closest('dialog') : null;
    var local = dialog ? dialog.querySelector('[data-dialog-flash]') : null;
    if (local) { local.textContent = message; }
  }

  function wireHtmxErrors() {
    document.body.addEventListener('htmx:responseError', function (event) {
      var xhr = event.detail && event.detail.xhr;
      var body = xhr ? parseJson(xhr.responseText) : null;
      reportError(event, 'That did not work: ' + describeError({ status: xhr ? xhr.status : '?', body: body }));
    });
    document.body.addEventListener('htmx:sendError', function (event) {
      reportError(event, 'Could not reach the server.');
    });
  }

  /* ---- the settings page --------------------------------------------------- */

  /*
    The category switch is CSS (a radio group and :has()); this only keeps
    the URL honest. Picking a category writes `?section=<key>` with
    replaceState, so a reload, the back button after a save's redirect, or a
    copied link lands on the same card - the server reads the same query.
    Nothing here is required for the page to work.
  */
  document.addEventListener('change', function (event) {
    var radio = event.target;
    if (!radio || radio.name !== 'settings_section' || !radio.checked) { return; }
    if (!window.history || !window.history.replaceState) { return; }
    var url = new URL(window.location.href);
    url.searchParams.set('section', radio.value);
    window.history.replaceState(null, '', url.toString());
  });

  /*
    The model picker (TASK-054): a pick from a provider's list clears the box
    for a typed id. The server lets a typed id win, so without this a person
    who typed an id and then picked one would save the id they typed - the
    choice made last would be the one ignored. `input`, which a select fires
    on every pick. Without scripts the page still works: the typed id wins,
    and the box is empty unless somebody typed in it.
  */
  document.addEventListener('input', function (event) {
    var select = event.target;
    if (!select || typeof select.matches !== 'function' || !select.matches('[data-model-select]')) { return; }
    var pick = select.closest('[data-model-pick]');
    var typed = pick ? pick.querySelector('[data-model-custom]') : null;
    if (typed) { typed.value = ''; }
  });

  /*
    "Saved" on the button that saved. A settings form posts, and htmx replaces
    the whole card - so the button a person pressed no longer exists by the
    time the answer arrives, and a class put on it would vanish with it. The
    press is remembered (which form, which button), and after the swap its
    successor in the new card - same form action, same label - is the one
    that turns green and says "Saved" for SAVED_FOR_MS. Only after a
    successful POST from inside a settings card; a refused save leaves the
    dialog's own error where it lands and no button pretends otherwise.
  */
  var SAVED_FOR_MS = 5000;
  var lastPress = null;

  document.addEventListener('click', function (event) {
    var button = event.target.closest ? event.target.closest('button[type="submit"]') : null;
    var form = button ? button.closest('form') : null;
    if (!form || !form.closest('.settings-section')) { return; }
    lastPress = { action: form.getAttribute('action') || '', label: button.textContent.trim(), button: button };
  });

  /*
    A refused save is the louder of the two outcomes, and it gets the louder
    mark: red, "Not saved ✗". On a 4xx or 5xx htmx swaps nothing, so the
    button that was pressed is still on the page - that one is marked, and
    the reason stays where the server's own error lands. Same five seconds.
  */
  function showNotSaved(button, label) {
    button.classList.remove('saved');
    button.classList.add('not-saved');
    button.textContent = 'Not saved ✗';
    button.setAttribute('aria-live', 'assertive');
    window.setTimeout(function () {
      if (!button.isConnected) { return; }
      button.classList.remove('not-saved');
      button.textContent = label;
      button.removeAttribute('aria-live');
    }, SAVED_FOR_MS);
  }

  document.body.addEventListener('htmx:responseError', function (event) {
    var detail = event.detail || {};
    var verb = detail.requestConfig ? String(detail.requestConfig.verb).toLowerCase() : '';
    var form = detail.elt && detail.elt.closest ? detail.elt.closest('form') : null;
    if (verb !== 'post' || !form || !form.closest('.settings-section')) { return; }
    var press = lastPress;
    lastPress = null;
    var button = press && press.button && press.button.isConnected && press.button.closest('form') === form
      ? press.button
      : form.querySelector('button[type="submit"]');
    if (button) { showNotSaved(button, press ? press.label : button.textContent.trim()); }
  });

  function markSaved(target) {
    if (!lastPress) { return; }
    var press = lastPress;
    lastPress = null;
    var forms = target.querySelectorAll('form');
    for (var i = 0; i < forms.length; i++) {
      if ((forms[i].getAttribute('action') || '') !== press.action) { continue; }
      var buttons = forms[i].querySelectorAll('button[type="submit"]');
      for (var j = 0; j < buttons.length; j++) {
        if (buttons[j].textContent.trim() !== press.label) { continue; }
        showSaved(buttons[j], press.label);
        return;
      }
    }
  }

  function showSaved(button, label) {
    button.classList.add('saved');
    button.textContent = 'Saved ✓';
    button.setAttribute('aria-live', 'polite');
    window.setTimeout(function () {
      if (!button.isConnected) { return; }
      button.classList.remove('saved');
      button.textContent = label;
      button.removeAttribute('aria-live');
    }, SAVED_FOR_MS);
  }

  document.body.addEventListener('htmx:afterSwap', function (event) {
    var detail = event.detail || {};
    var verb = detail.requestConfig ? String(detail.requestConfig.verb).toLowerCase() : '';
    var status = detail.xhr ? detail.xhr.status : 200;
    if (verb !== 'post' || status < 200 || status >= 300) { lastPress = null; return; }
    /* For an outerHTML swap htmx hands over the element that was replaced -
       already detached, so it has no ancestors and no successor buttons. Its
       id is still the id of what took its place; look that up. */
    var target = detail.target;
    if (target && !target.isConnected && target.id) { target = document.getElementById(target.id); }
    if (target && target.closest && target.closest('.settings-section')) { markSaved(target); }
  });

  /* ---- the log page -------------------------------------------------------- */

  /*
    Two things the polling table cannot do for itself. It appends forever
    (hx-swap="beforeend"), so it is trimmed from the top to the newest
    data-log-keep rows after every swap - the file keeps everything, the page
    keeps what a person can scroll. And it can be paused: auto-updating
    content a person cannot stop is a WCAG failure and, more plainly, a table
    that moves while you read it. Pausing rewrites hx-trigger and asks htmx
    to re-read the element; resuming puts the poll back.
  */
  document.body.addEventListener('htmx:afterSwap', function (event) {
    var body = event.detail && event.detail.target;
    if (!body || body.id !== 'log-body') { return; }
    var keep = parseInt(body.getAttribute('data-log-keep'), 10) || 1200;
    while (body.children.length > keep) { body.removeChild(body.firstElementChild); }
  });

  document.addEventListener('click', function (event) {
    var button = event.target.closest ? event.target.closest('[data-log-pause]') : null;
    if (!button || !window.htmx) { return; }
    var body = document.getElementById('log-body');
    if (!body) { return; }
    var paused = button.getAttribute('aria-pressed') === 'true';
    if (paused) {
      body.setAttribute('hx-trigger', body.getAttribute('data-log-trigger') || 'every 2s');
      button.setAttribute('aria-pressed', 'false');
      button.textContent = 'Pause updates';
    } else {
      body.setAttribute('data-log-trigger', body.getAttribute('hx-trigger'));
      body.setAttribute('hx-trigger', 'none');
      button.setAttribute('aria-pressed', 'true');
      button.textContent = 'Resume updates';
    }
    window.htmx.process(body);
  });

  /* ---- the row menus ------------------------------------------------------- */

  /*
    The ⋯ menus are popovers, so the browser already opens them, closes them on
    Escape, and dismisses them when a click lands outside. It does not dismiss
    them when the *keyboard* leaves: tab past the last item and the panel stays
    open behind wherever focus went. This closes that gap and nothing else - a
    menu no longer being where the user is looking is a menu that should be
    gone, whichever device moved them.

    Two things it must not do. Moving focus into the panel's own rename box or
    folder select is not leaving, so containment is checked. And a relatedTarget
    of null means focus left the document entirely - the user alt-tabbed, or
    clicked the browser's chrome - which is not a decision about this menu, so
    the panel is left as it was.
  */
  document.addEventListener('focusout', function (event) {
    var panel = event.target.closest ? event.target.closest('[popover].menu-body') : null;
    if (!panel || !panel.matches(':popover-open')) { return; }
    var moved = event.relatedTarget;
    if (!moved || panel.contains(moved)) { return; }
    panel.hidePopover();
  });

  /* ---- dialogs ------------------------------------------------------------ */

  /*
    A <dialog> on a page starts empty. A control with hx-get and
    hx-target="#some-dialog" fetches its content, and the swap is what opens
    it, so the dialog never shows before there is something to show. Anything
    inside marked data-close-dialog closes it; so does a successful POST from
    inside it (the upload went through, the table behind it has the new rows)
    - unless the dialog is marked data-stay-open, as the export dialog is:
    every preview it shows is a POST, and a write-to-folder answers with a
    list the user should get to read. A GET from inside - the browse panel
    walking a directory - is not done.

    Two things narrow that "a successful POST" down.

    * It has to come from something that submits the form. The transcribe
      dialog's URL field posts as it is typed into, to fetch its own preview,
      and that post always answers 200 by design - so every preview closed the
      dialog in the same tick the panel arrived, and the playlist warning
      ("this link is 40 videos") could not be read at all. Marking that dialog
      data-stay-open would have fixed the preview by also keeping the dialog
      open after an upload, which is the one time it should close.
    * The dialog has to be willing. Something inside it may still be running -
      the recorder, while the microphone is live - and closing the dialog then
      leaves a MediaRecorder posting a chunk every five seconds with no control
      on screen and no way back but a reload.
  */
  function closestDialog(el) {
    return el && el.closest ? el.closest('dialog') : null;
  }

  /* A <button> with no type attribute submits, so this asks the element what
     it is rather than reading an attribute that is allowed to be missing. */
  function submitsTheForm(el) {
    return Boolean(el) && (el.tagName === 'FORM' || el.type === 'submit');
  }

  /*
    Anything busy inside a dialog marks itself with data-busy, and the value is
    the sentence to show for it: this layer knows that something is busy, not
    what, and the only part that can say why is the part that is.
  */
  function mayClose(dialog) {
    var busy = dialog ? dialog.querySelector('[data-busy]') : null;
    if (!busy) { return true; }
    var line = dialog.querySelector('[data-dialog-flash]');
    if (line) { line.textContent = busy.getAttribute('data-busy'); }
    return false;
  }

  function wireDialogs() {
    document.body.addEventListener('htmx:afterSwap', function (event) {
      var target = event.detail && event.detail.target;
      if (target && target.tagName === 'DIALOG' && !target.open) { target.showModal(); }
    });
    document.addEventListener('click', function (event) {
      var button = event.target.closest ? event.target.closest('[data-close-dialog]') : null;
      var dialog = button ? closestDialog(button) : null;
      if (dialog && mayClose(dialog)) { dialog.close(); }
    });
    /* Escape on a native <dialog> fires `cancel` on the dialog itself, and
       that event does not bubble - so the only place to hear it without
       binding to each dialog is the capture phase on the way down. */
    document.addEventListener('cancel', function (event) {
      var dialog = event.target;
      if (!dialog || dialog.tagName !== 'DIALOG') { return; }
      if (!mayClose(dialog)) { event.preventDefault(); }
    }, true);
    document.body.addEventListener('htmx:afterRequest', function (event) {
      var detail = event.detail || {};
      var verb = detail.requestConfig ? String(detail.requestConfig.verb).toLowerCase() : '';
      var dialog = closestDialog(detail.elt);
      if (!dialog) { return; }
      var bar = dialog.querySelector('progress[data-upload-progress]');
      if (bar) { bar.hidden = true; bar.value = 0; }
      if (dialog.hasAttribute('data-stay-open')) { return; }
      if (!detail.successful || verb !== 'post') { return; }
      if (!submitsTheForm(detail.elt)) { return; }
      if (dialog.open && mayClose(dialog)) { dialog.close(); }
    });
  }

  /* ---- the export dialog ---------------------------------------------------- */

  /*
    The preset select carries each preset's options as JSON in its option's
    data-options. Choosing one fills the form's fields from it - a checkbox
    group (the formats) from a list, a boolean's checkbox from true/false,
    everything else by value - and fires one change on the form so the
    preview pane, which listens for exactly that, re-fetches itself with the
    filled-in fields. Editing any field by hand afterwards puts the select
    back on "Custom": the fields no longer say what the preset says.
    Field names are ExportOptions' own and are matched by attribute only;
    nothing here writes markup.
  */
  var FIELD_NAME = /^[a-z_]+$/;

  function applyPreset(select) {
    var form = select.closest('form');
    var option = select.options[select.selectedIndex];
    var preset = option ? parseJson(option.getAttribute('data-options')) : null;
    if (!form || !preset || typeof preset !== 'object') { return; }
    Object.keys(preset).forEach(function (key) {
      if (!FIELD_NAME.test(key)) { return; }
      var value = preset[key];
      form.querySelectorAll('[name="' + key + '"]').forEach(function (field) {
        if (field.type === 'hidden') { return; }  /* the 0 ahead of a checkbox */
        if (field.type === 'checkbox') {
          field.checked = Array.isArray(value) ? value.indexOf(field.value) !== -1 : Boolean(value);
        } else {
          field.value = value == null ? '' : String(value);
        }
      });
    });
    form.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function wireExportDialog() {
    /* Capture phase, so this runs before htmx's listener on the form: the
       select's own change must not reach the preview pane, or it would
       fetch a preview of the fields as they were and then abort it for the
       one applyPreset fires once they are filled. */
    document.addEventListener('change', function (event) {
      var el = event.target;
      if (!el || typeof el.matches !== 'function' || !el.matches('[data-preset-select]')) { return; }
      event.stopPropagation();
      applyPreset(el);
    }, true);
    document.addEventListener('change', function (event) {
      var el = event.target;
      if (!el || typeof el.matches !== 'function') { return; }
      if (!el.matches('form.export input, form.export select, form.export textarea')) { return; }
      if (el.name === 'path' || el.name === 'name') { return; }  /* not options */
      var select = el.closest('form').querySelector('[data-preset-select]');
      if (select && select.value) { select.value = ''; }
    });
  }

  /* ---- the transcribe dialog ------------------------------------------------ */

  /*
    Everything here is a convenience on top of a form that works without it:
    the drop zone hands dropped files to the file input, the folder toggle
    flips the input into directory mode, the browse panel fills in the path
    field, and the speakers checkbox greys out the count fields it governs.
    The upload itself is htmx's multipart post; the progress bar listens to
    the XHR events htmx forwards.
  */
  function summarizeFiles(input) {
    var zone = input.closest('[data-dropzone]');
    var out = zone ? zone.querySelector('[data-file-summary]') : null;
    if (!out) { return; }
    var files = input.files || [];
    if (files.length === 0) { out.textContent = 'No files chosen yet.'; return; }
    if (files.length === 1) { out.textContent = files[0].name; return; }
    out.textContent = files.length + ' files chosen.';
  }

  function dropzoneOf(event) {
    return event.target && event.target.closest ? event.target.closest('[data-dropzone]') : null;
  }

  function sourcesOf(event) {
    return event.target && event.target.closest ? event.target.closest('[data-sources]') : null;
  }

  /*
    A link dropped on the dialog. "Drop a URL" read literally: an address
    dragged from a browser's bar, a feed icon or a page link arrives as
    text/uri-list - several lines allowed, '#' lines are comments (RFC 2483) -
    or, from some sources, as text/plain. Only an http(s) address is taken;
    the server's own scheme check still stands behind this one. A drop that
    carries files goes to the drop zone as it always has.
  */
  function droppedLink(transfer) {
    if (!transfer || typeof transfer.getData !== 'function') { return ''; }
    var text = '';
    try { text = String(transfer.getData('text/uri-list') || ''); } catch (err) { text = ''; }
    var lines = text.split(/\r?\n/).filter(function (line) {
      return line.trim() && line.trim().charAt(0) !== '#';
    });
    var candidate = lines.length ? lines[0].trim() : '';
    if (!candidate) {
      try { candidate = String(transfer.getData('text/plain') || '').trim(); } catch (err) { candidate = ''; }
    }
    return /^https?:\/\//i.test(candidate) ? candidate : '';
  }

  function wireTranscribeDialog() {
    document.addEventListener('dragover', function (event) {
      var zone = dropzoneOf(event);
      var sources = sourcesOf(event);
      if (!zone && !sources) { return; }
      event.preventDefault();  /* or the browser navigates to the dropped link */
      if (zone) { zone.classList.add('over'); }
    });
    document.addEventListener('dragleave', function (event) {
      var zone = dropzoneOf(event);
      if (zone) { zone.classList.remove('over'); }
    });
    document.addEventListener('drop', function (event) {
      var zone = dropzoneOf(event);
      var files = event.dataTransfer ? event.dataTransfer.files : null;
      if (zone) {
        event.preventDefault();
        zone.classList.remove('over');
        var input = zone.querySelector('input[type="file"]');
        if (input && files && files.length) {
          input.files = files;
          summarizeFiles(input);
          return;
        }
      }
      var sources = sourcesOf(event);
      if (!sources) { return; }
      event.preventDefault();
      var link = droppedLink(event.dataTransfer);
      if (!link) { return; }
      var tab = document.getElementById('src-url');
      if (tab) { tab.checked = true; }
      var form = sources.closest('form');
      var field = form ? form.querySelector('input[name="url"]') : null;
      if (!field) { return; }
      field.value = link;
      /* The field's own hx-trigger is `input changed`, so this is what
         starts the preview - the same path a paste takes. */
      field.dispatchEvent(new Event('input', { bubbles: true }));
      field.focus();
    });

    /*
      The episode list a feed or channel turns into (see _url_panel.html).
      Three conveniences over a form that posts correctly without them: the
      filter, All shown / None, and the live count with the cap. All three
      are delegated to document like every listener here, and every check
      below is a property (hidden, checked) or an attribute (data-search),
      never a :checked / [hidden] selector - the Node harness the tests run
      in models neither, and a stub that answers "no" to a question it did
      not understand is how a broken script gets through.
    */
    function episodesPanel(el) {
      return el && el.closest ? el.closest('[data-panel="url"]') : null;
    }
    function episodeRows(panel) {
      return panel ? panel.querySelectorAll('.episode') : [];
    }
    function episodeBox(row) {
      return row.querySelector('input[type="checkbox"]');
    }
    function recount(panel) {
      if (!panel) { return; }
      var out = panel.querySelector('[data-episodes-count]');
      var submit = panel.querySelector('[data-episodes-submit]');
      if (!out) { return; }
      var ticked = 0;
      episodeRows(panel).forEach(function (row) {
        var box = episodeBox(row);
        if (box && Boolean(box.checked)) { ticked += 1; }
      });
      var max = Number(out.getAttribute('data-max')) || 0;
      var over = max > 0 && ticked > max;
      out.textContent = over
        ? ticked + ' selected - at most ' + max + ' in one go'
        : ticked + ' selected';
      if (over) { out.classList.add('over'); } else { out.classList.remove('over'); }
      if (submit) { submit.disabled = over; }
    }
    document.addEventListener('input', function (event) {
      var el = event.target;
      if (!el || typeof el.matches !== 'function') { return; }
      if (el.matches('[data-episode-filter]')) {
        var needle = String(el.value || '').trim().toLowerCase();
        var panel = episodesPanel(el);
        episodeRows(panel).forEach(function (row) {
          var hay = String(row.getAttribute('data-search') || '');
          row.hidden = needle !== '' && hay.indexOf(needle) === -1;
        });
        recount(panel);
      } else if (el.matches('input[type="checkbox"][name="entry"]')) {
        recount(episodesPanel(el));
      }
    });
    document.addEventListener('click', function (event) {
      var el = event.target;
      if (!el || typeof el.closest !== 'function') { return; }
      var all = el.closest('[data-episodes-all]');
      var none = el.closest('[data-episodes-none]');
      if (!all && !none) { return; }
      var panel = episodesPanel(el);
      episodeRows(panel).forEach(function (row) {
        var box = episodeBox(row);
        if (!box) { return; }
        if (none) { box.checked = false; } else if (!row.hidden) { box.checked = true; }
      });
      recount(panel);
    });

    document.addEventListener('change', function (event) {
      var el = event.target;
      if (!el || typeof el.matches !== 'function') { return; }
      if (el.matches('[data-dropzone] input[type="file"]')) {
        summarizeFiles(el);
      } else if (el.matches('[data-toggle-directory]')) {
        var zone = el.closest('[data-dropzone]');
        var input = zone ? zone.querySelector('input[type="file"]') : null;
        if (input) { input.toggleAttribute('webkitdirectory', el.checked); }
      } else if (el.matches('[data-enables]')) {
        var governed = document.querySelector(el.getAttribute('data-enables'));
        if (governed) { governed.disabled = !el.checked; }
      }
    });

    /*
      Enter in a text field submits the form, and the browser presses the
      *first* submit button in it - "Add file" - whichever field the cursor
      was in. So Enter after a pasted link posted /transcribe/path and
      complained about a path nobody had typed.

      Each row that carries a button of its own is marked data-submit-row, and
      Enter inside one presses that row's button. Reordering the buttons would
      only move the bug to the other field; a field outside such a row is left
      to the browser, which is right for the one submit that does work.
    */
    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter') { return; }
      if (event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) { return; }
      var field = event.target;
      if (!field || typeof field.closest !== 'function') { return; }
      /* Scope first, and only then which button. Marking fields one at a time
         was the wrong primitive: it covered the two the template's comment
         happened to name and missed the three speaker-hint inputs, because
         opting out has to be remembered and opting in does not. Inside a
         [data-enter-scope] the key never reaches the browser - which matters
         because the browser presses the first submit in the form, "Add file",
         from whichever field the cursor is in. Every other form in the app is
         outside any scope and keeps the browser's behaviour, which is right
         where there is one submit that means something. */
      var scope = field.closest('[data-enter-scope]');
      if (!scope) { return; }
      event.preventDefault();
      var named = field.getAttribute ? field.getAttribute('data-enter') : null;
      var button = named
        ? scope.querySelector(named)
        : (function () {
            var row = field.closest('[data-submit-row]');
            return row ? row.querySelector('button[type="submit"]') : null;
          })();
      if (button) { button.click(); }
    });

    document.addEventListener('click', function (event) {
      var pick = event.target.closest ? event.target.closest('[data-pick-path]') : null;
      if (!pick) { return; }
      var form = pick.closest('form');
      var field = form ? form.querySelector('input[name="path"]') : null;
      if (field) {
        field.value = pick.getAttribute('data-pick-path');
        field.focus();
      }
    });

    document.body.addEventListener('htmx:xhr:progress', function (event) {
      var detail = event.detail || {};
      var form = detail.elt && detail.elt.closest ? detail.elt.closest('form') : null;
      var bar = form ? form.querySelector('progress[data-upload-progress]') : null;
      if (!bar || !detail.lengthComputable) { return; }
      bar.hidden = false;
      bar.max = detail.total;
      bar.value = detail.loaded;
    });
  }

  /* ---- the transcript page -------------------------------------------------- */

  /*
    The transcript page pairs #player (a native <audio> in the bar at the
    bottom) with #transcript (the words). The markup carries every number
    this needs - see _transcript_panel.html - so the work here is wiring:

      click on a.ts or span.w    seek to its data-start / data-s
      timeupdate                 find the word for the time (binary search
                                 over a Float64Array of word starts), move
                                 .current to it and scroll it into view -
                                 unless the reader scrolled by hand in the
                                 last eight seconds
      #t=<sec> in the URL        seek on load, and again on hashchange
      no #t=                     resume where this recording was left, two
                                 seconds back, from localStorage
      the speed buttons          playbackRate, pitch preserved
      the timestamp toggle       body.hide-ts, persisted in localStorage
      the find box               every match highlighted through the CSS
                                 Custom Highlight API, or wrapped in <mark>
                                 where the browser has none; Enter walks
                                 the matches
      click on h3.speaker        the rename form from the panel's <template>
                                 opens under it, aimed at data-rename-url
      shift-click on span.w      closes a range from the last plain click
                                 (the anchor) to this word and shows the
                                 assign toolbar with from_idx/to_idx set

    The panel around the words re-fetches itself after a rail action, and is
    swapped whole by a rename or a reassignment, so the word index is rebuilt
    after every htmx swap rather than kept - and nothing chosen for a
    reassignment outlives the swap that applied it.
  */
  var SCROLL_SUSPEND_MS = 8000;
  var PROGRAMMATIC_SCROLL_MS = 1000;
  var RESUME_REWIND_SECONDS = 2;
  var SAVE_POSITION_EVERY_MS = 2000;
  /* The band the followed word is allowed to sit in before the page scrolls,
     as a fraction of the viewport. Wide on purpose: the text should stay
     still while the highlight travels down it, and only move when the word
     would otherwise leave. */
  var FOLLOW_BAND_TOP = 0.20;
  var FOLLOW_BAND_BOTTOM = 0.80;
  var SEARCH_DEBOUNCE_MS = 150;
  var SEARCH_MIN_CHARS = 2;
  var SEARCH_HIGHLIGHT = 'transcript-search';

  /* localStorage throws in a private window or with site data blocked; a
     player that cannot remember its position still has to play. */
  function readStore(key) {
    try { return window.localStorage.getItem(key); } catch (err) { return null; }
  }
  function writeStore(key, value) {
    try {
      if (value == null) { window.localStorage.removeItem(key); } else { window.localStorage.setItem(key, value); }
    } catch (err) { /* nothing to do: the position is a convenience */ }
  }

  /*
    The two readings of one recording (TASK-026): the transcript as it was
    heard, and the cleaned version beside it. Switching is a local matter -
    both are already on the page - so this is a class toggle and not a request,
    and the transcript is one click away whichever is showing.

    Delegated to document like everything else here, because the panel is
    replaced wholesale by htmx (hx-swap="outerHTML") after a rename or a
    reassignment, and a listener bound to the button would go with it.

    Every check below is a property (hidden) or an attribute, never a
    :checked or [hidden] selector: the Node harness the tests run in models
    neither, and a stub that answers "no" to a question it did not understand
    is how a broken script gets through.
  */
  function wireReadingSwitch() {
    document.addEventListener('click', function (event) {
      var button = event.target && event.target.closest
        ? event.target.closest('[data-reading-toggle]')
        : null;
      if (!button) { return; }

      var words = document.getElementById('transcript');
      var clean = document.getElementById('clean-reading');
      if (!words || !clean) { return; }

      var showClean = clean.hidden;   /* hidden now means we are about to show it */
      clean.hidden = !showClean;
      words.hidden = showClean;
      button.setAttribute('aria-pressed', showClean ? 'true' : 'false');
      button.textContent = showClean ? 'Show the transcript' : 'Show the cleaned reading';

      var says = document.querySelector('[data-reading-says]');
      if (says) {
        says.textContent = showClean
          ? 'Showing the cleaned reading. The transcript is unchanged.'
          : 'Showing the transcript as it was heard.';
      }

      /* The search lives in wireTranscript's scope and searches whichever
         reading is on screen, so it has to be told the screen changed. An
         event rather than a shared variable: these two are wired separately
         and a page without a player has no search to re-run. */
      document.body.dispatchEvent(new CustomEvent('reading-changed'));
    });
  }

  function wireTranscript() {
    var audio = document.getElementById('player');
    if (!audio) { return; }
    var resumeKey = audio.getAttribute('data-resume-key');

    var words = [];                    /* span.w, in transcript order */
    var starts = new Float64Array(0);  /* their data-s, for the search */
    var current = -1;
    var suspendedUntil = 0;            /* after a manual scroll: no auto-scroll */
    var programmaticUntil = 0;         /* our own scrollIntoView is not manual */
    var lastSave = 0;

    /* ---- the word under the playhead ---- */

    function index() {
      words = Array.prototype.slice.call(document.querySelectorAll('#transcript .w'));
      starts = new Float64Array(words.length);
      words.forEach(function (w, i) { starts[i] = Number(w.getAttribute('data-s')) || 0; });
      current = -1;
      highlight(audio.currentTime, false);
    }

    /* The last word that has started by t: the greatest i with starts[i] <= t,
       or -1 before the first word. Staying on the last started word through a
       pause between words reads better than a gap with nothing lit. */
    function wordAt(t) {
      var lo = 0, hi = starts.length - 1, found = -1;
      while (lo <= hi) {
        var mid = (lo + hi) >> 1;
        if (starts[mid] <= t) { found = mid; lo = mid + 1; } else { hi = mid - 1; }
      }
      return found;
    }

    /* Move .current to the word at t. `follow` scrolls it into view - what
       playback and a deep link want, and what a click on a word the reader
       is already looking at does not. */
    function highlight(t, follow) {
      var i = wordAt(t);
      if (i === current) { return; }
      if (current >= 0 && words[current]) { words[current].classList.remove('current'); }
      current = i;
      if (i < 0) { return; }
      words[i].classList.add('current');
      if (follow && Date.now() >= suspendedUntil && offScreen(words[i])) {
        programmaticUntil = Date.now() + PROGRAMMATIC_SCROLL_MS;
        words[i].scrollIntoView({ block: 'center', behavior: 'smooth' });
      }
    }

    /* Is this word outside the band the reader is looking at?

       The check is new because the sampling rate is. At four samples a second
       the highlight only moved a few times a second and re-centring on every
       move was fine; at sixty it moves once per word - ten times a second in
       fast speech - and a smooth scroll restarted ten times a second never
       arrives anywhere. So the page only scrolls when the word has actually
       left the comfortable middle, which is also what a reader wants: the
       text stays put while the highlight travels down it. */
    function offScreen(el) {
      var box = el.getBoundingClientRect();
      var height = window.innerHeight || document.documentElement.clientHeight;
      return box.top < height * FOLLOW_BAND_TOP || box.bottom > height * FOLLOW_BAND_BOTTOM;
    }

    /* Only a scroll that is not ours counts as the reader taking over. */
    window.addEventListener('scroll', function () {
      if (Date.now() > programmaticUntil) { suspendedUntil = Date.now() + SCROLL_SUSPEND_MS; }
    }, { passive: true });

    /* ---- seeking ---- */

    function whenReady(fn) {
      if (audio.readyState >= 1) { fn(); return; }
      audio.addEventListener('loadedmetadata', fn, { once: true });
    }

    function seek(t, follow) {
      if (!isFinite(t) || t < 0) { return; }
      whenReady(function () { audio.currentTime = t; });
      highlight(t, follow);
    }

    function hashTime() {
      var found = /^#t=([0-9]+(?:\.[0-9]+)?)$/.exec(window.location.hash || '');
      return found ? Number(found[1]) : NaN;
    }

    /* A #t= (a search hit, a shared link) or a remembered position is
       somewhere down the page; the reader should land there. */
    function seekFromUrlOrResume() {
      var t = hashTime();
      if (isFinite(t)) { seek(t, true); return; }
      var saved = Number(readStore(resumeKey));
      if (resumeKey && saved > RESUME_REWIND_SECONDS) { seek(saved - RESUME_REWIND_SECONDS, true); }
    }
    window.addEventListener('hashchange', function () {
      var t = hashTime();
      if (isFinite(t)) { seek(t, true); }
    });

    /* ---- following along ----

       `timeupdate` fires about four times a second - the HTML spec allows
       anywhere from 15ms to 250ms and browsers pick the slow end - and
       `wordAt` returns the LAST word started by t. So when several words
       begin between two events the highlight jumps over the ones between and
       they are never lit at all.

       That is most of a recording, not an edge case. Measured on media 20
       (run 146, 12,108 words): the median gap between word starts is 0.240s,
       and 52.4% of words begin within 250ms of the one before.

       So while the audio is playing the highlight is driven by
       requestAnimationFrame instead - sixty samples a second, which is finer
       than any word. `timeupdate` stays wired for two reasons: it keeps the
       resume position saved, and a backgrounded tab gets no animation frames
       while the audio plays on, so it is also the fallback that keeps the
       highlight roughly right until the tab comes back. Both call the same
       function, and `highlight` returns early when the word has not changed,
       so the pair cannot fight. */
    var following = 0;

    function followFrame() {
      following = window.requestAnimationFrame(followFrame);
      highlight(audio.currentTime, true);
      movePlayhead();
    }

    function startFollowing() {
      if (!following) { followFrame(); }
    }

    function stopFollowing() {
      if (following) { window.cancelAnimationFrame(following); following = 0; }
    }

    audio.addEventListener('play', startFollowing);
    audio.addEventListener('playing', startFollowing);
    audio.addEventListener('pause', stopFollowing);
    audio.addEventListener('ended', stopFollowing);
    if (!audio.paused) { startFollowing(); }

    audio.addEventListener('timeupdate', function () {
      highlight(audio.currentTime, true);
      movePlayhead();
      var now = Date.now();
      if (resumeKey && now - lastSave >= SAVE_POSITION_EVERY_MS) {
        lastSave = now;
        writeStore(resumeKey, String(audio.currentTime));
      }
    });
    audio.addEventListener('pause', function () {
      if (resumeKey) { writeStore(resumeKey, String(audio.currentTime)); }
    });
    audio.addEventListener('ended', function () {
      if (resumeKey) { writeStore(resumeKey, null); }
    });

    /* ---- speed ---- */

    if ('preservesPitch' in audio) { audio.preservesPitch = true; }

    /* Which button is lit is read from the audio element, never remembered:
       these buttons are not the only way to change the rate. The player's own
       overflow menu is browser chrome this app cannot remove, and a rate
       chosen there used to leave 1x reading aria-pressed="true" while the
       audio ran at 2x - a control lying about the thing it controls. */
    function showSpeed() {
      var rate = audio.playbackRate;
      document.querySelectorAll('[data-speed]').forEach(function (button) {
        var mine = Number(button.getAttribute('data-speed')) === rate;
        button.setAttribute('aria-pressed', mine ? 'true' : 'false');
      });
    }

    function setSpeed(rate) {
      if (!isFinite(rate) || rate <= 0) { return; }
      audio.playbackRate = rate;   /* fires ratechange, which calls showSpeed */
    }

    /* ---- the structure ribbon (TASK-053.07) ----

       A band per paragraph, placed by the server. This only moves the
       playhead and turns a click into a seek: every position is a percentage
       of the duration, so nothing here measures a box - which matters because
       `content-visibility: auto` makes off-screen paragraphs unreliable to
       measure, and the ones you want to jump to are always off screen. */
    var ribbon = document.querySelector('[data-ribbon]');
    var playhead = ribbon ? ribbon.querySelector('[data-playhead]') : null;

    function movePlayhead() {
      if (!playhead) { return; }
      var span = Number(ribbon.getAttribute('data-duration')) || audio.duration || 0;
      if (!span) { return; }
      playhead.style.left = Math.min(100, Math.max(0, audio.currentTime / span * 100)) + '%';
    }

    if (ribbon) {
      ribbon.addEventListener('click', function (event) {
        var band = event.target.closest ? event.target.closest('.band') : null;
        if (!band) { return; }
        seek(Number(band.getAttribute('data-at')), true);
      });
      movePlayhead();
    }

    audio.addEventListener('ratechange', showSpeed);
    showSpeed();   /* and once now, in case the element opens at a rate we did not set */

    /* ---- the timestamp toggle ---- */

    function applyTimestamps(button, hidden) {
      document.body.classList.toggle('hide-ts', hidden);
      var key = button ? button.getAttribute('data-storage-key') : null;
      if (key) { writeStore(key, hidden ? '1' : null); }
      document.querySelectorAll('[data-toggle-ts]').forEach(function (b) {
        b.setAttribute('aria-pressed', hidden ? 'true' : 'false');
        b.textContent = hidden ? 'Show timestamps' : 'Hide timestamps';
      });
    }

    function restoreTimestamps() {
      var button = document.querySelector('[data-toggle-ts]');
      var key = button ? button.getAttribute('data-storage-key') : null;
      if (key && readStore(key) === '1') { applyTimestamps(button, true); }
    }

    /* ---- speakers: rename under the heading, reassign a range ---- */

    /*
      A heading is the speaker's name. Clicking it opens the rename form the
      panel carries as a <template>, pointed at the heading's data-rename-url
      and handed to htmx.process so its post swaps the panel like any other.

      A range of words for reassignment is chosen with the mouse: a plain
      click (which seeks, as before) marks the anchor, a shift-click closes
      the range from the anchor to that word - either way round - and the
      assign toolbar appears with the idx bounds filled in. Escape or Clear
      lets go of it. Nothing here is computed from the transcript: the idx
      and the times come off the spans' attributes.
    */
    var anchor = -1;

    function assignBar() { return document.querySelector('[data-assign-toolbar]'); }

    function closeRename() {
      document.querySelectorAll('#transcript form.rename-speaker').forEach(function (form) {
        var heading = form.previousElementSibling;
        if (heading) { heading.hidden = false; }
        form.parentNode.removeChild(form);
      });
    }

    function openRename(heading) {
      var template = document.querySelector('template[data-rename-template]');
      if (!template || !template.content || !window.htmx) { return; }
      closeRename();
      var form = template.content.firstElementChild.cloneNode(true);
      var url = heading.getAttribute('data-rename-url');
      form.setAttribute('action', url);
      form.setAttribute('hx-post', url);
      var input = form.querySelector('input[name="display_name"]');
      input.value = heading.textContent.trim();
      heading.insertAdjacentElement('afterend', form);
      heading.hidden = true;
      window.htmx.process(form);
      input.focus();
      input.select();
    }

    /* ---- words: type over a misheard one ---- */

    function closeCorrect() {
      document.querySelectorAll('#transcript form.correct-word').forEach(function (form) {
        form.parentNode.removeChild(form);
      });
      document.querySelectorAll('#transcript .w.editing').forEach(function (w) {
        w.classList.remove('editing');
      });
    }

    /* A double-click (or a right-click) on a word opens a one-field form
       right after it, prefilled with the word's core - not its leading space
       or trailing comma, which the server keeps. Enter saves through htmx and
       the panel comes back with the correction over the word and, when other
       words say the same, an offer to fix those too. */
    function openCorrect(word) {
      var template = document.querySelector('template[data-correct-template]');
      var transcript = document.getElementById('transcript');
      if (!template || !template.content || !window.htmx || !transcript) { return; }
      closeCorrect();
      closeRename();
      var base = transcript.getAttribute('data-correct-url') || '';
      var url = base + String(Number(word.getAttribute('data-i'))) + '/correct';
      var form = template.content.firstElementChild.cloneNode(true);
      form.setAttribute('action', url);
      form.setAttribute('hx-post', url);
      var input = form.querySelector('input[name="text"]');
      input.value = word.textContent.replace(/^[\s"'“”‘’(\[{«]+|[\s"'“”‘’.,;:!?…)\]}»\-–—]+$/g, '');
      word.insertAdjacentElement('afterend', form);
      word.classList.add('editing');
      window.htmx.process(form);
      input.focus();
      input.select();
    }

    document.addEventListener('dblclick', function (event) {
      var target = event.target;
      if (!target || typeof target.closest !== 'function') { return; }
      var word = target.closest('#transcript .w');
      if (!word) { return; }
      event.preventDefault();
      if (window.getSelection) { window.getSelection().removeAllRanges(); }
      openCorrect(word);
    });

    document.addEventListener('contextmenu', function (event) {
      var target = event.target;
      if (!target || typeof target.closest !== 'function') { return; }
      var word = target.closest('#transcript .w');
      if (!word) { return; }
      event.preventDefault();
      openCorrect(word);
    });

    function clearRange() {
      anchor = -1;
      document.querySelectorAll('#transcript .w.sel, #transcript .w.anchor').forEach(function (w) {
        w.classList.remove('sel');
        w.classList.remove('anchor');
      });
      var bar = assignBar();
      if (bar) { bar.hidden = true; }
    }

    function setAnchor(word) {
      clearRange();
      anchor = Number(word.getAttribute('data-i'));
      word.classList.add('anchor');
    }

    function showRange(from, to) {
      var first = null, last = null;
      words.forEach(function (w) {
        var i = Number(w.getAttribute('data-i'));
        var inside = i >= from && i <= to;
        w.classList.toggle('sel', inside);
        w.classList.remove('anchor');
        if (inside && !first) { first = w; }
        if (inside) { last = w; }
      });
      var bar = assignBar();
      if (!bar || !first) { return; }
      bar.querySelector('input[name="from_idx"]').value = String(from);
      bar.querySelector('input[name="to_idx"]').value = String(to);
      var label = bar.querySelector('[data-assign-range]');
      if (label) {
        label.textContent = (to - from + 1) + (to === from ? ' word, ' : ' words, ')
          + formatClock(Number(first.getAttribute('data-s'))) + '–'
          + formatClock(Number(last.getAttribute('data-e')));
      }
      bar.hidden = false;
    }

    function pickWord(event, word) {
      var i = Number(word.getAttribute('data-i'));
      if (!event.shiftKey) {
        seek(Number(word.getAttribute('data-s')), false);
        setAnchor(word);
        return;
      }
      /* A shift-click would also extend the browser's text selection; this
         one is ours. */
      event.preventDefault();
      if (window.getSelection) { window.getSelection().removeAllRanges(); }
      if (anchor < 0) { anchor = i; }
      showRange(Math.min(anchor, i), Math.max(anchor, i));
    }

    /* "New speaker…" needs a name; any other choice does not. */
    document.addEventListener('change', function (event) {
      var select = event.target;
      if (!select || typeof select.matches !== 'function' || !select.matches('[data-assign-speaker]')) { return; }
      var bar = select.closest('form');
      var name = bar ? bar.querySelector('[data-assign-name]') : null;
      if (!name) { return; }
      var fresh = select.value === bar.getAttribute('data-new-speaker');
      name.hidden = !fresh;
      name.required = fresh;
      if (fresh) { name.focus(); }
    });

    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape') { return; }
      var el = event.target;
      if (el && typeof el.matches === 'function' && el.matches('[data-transcript-search]')) { return; }
      if (el && typeof el.closest === 'function' && el.closest('form.rename-speaker')) { closeRename(); return; }
      if (el && typeof el.closest === 'function' && el.closest('form.correct-word')) { closeCorrect(); return; }
      var bar = assignBar();
      if (anchor >= 0 || (bar && !bar.hidden)) { clearRange(); }
    });

    /* ---- clicks: timestamps, words, headings, speeds, the toggle ---- */

    document.addEventListener('click', function (event) {
      var target = event.target;
      if (!target || typeof target.closest !== 'function') { return; }
      /* a.ts anywhere on a page that has a player, not only inside
         #transcript: an AI answer's chapter marks and an action item's
         evidence carry the same data-start, and a citation should seek
         rather than reload the page it is already on. Every such link also
         carries a #t= href, so the same markup navigates correctly from a
         page with no player (the chat page) and with scripting off. */
      var stamp = target.closest('a.ts[data-start]');
      if (stamp) {
        event.preventDefault();
        seek(Number(stamp.getAttribute('data-start')), false);
        /* Keep the address bar's #t= honest without a hashchange (which
           would seek a second time) and without a scroll. */
        window.history.replaceState(null, '', stamp.getAttribute('href'));
        return;
      }
      var heading = target.closest('#transcript h3.speaker');
      if (heading) { openRename(heading); return; }
      if (target.closest('[data-cancel-rename]')) { closeRename(); return; }
      if (target.closest('[data-cancel-correct]')) { closeCorrect(); return; }
      if (target.closest('[data-dismiss-offer]')) {
        var offer = target.closest('.word-offer');
        if (offer) { offer.parentNode.removeChild(offer); }
        return;
      }
      if (target.closest('[data-assign-clear]')) { clearRange(); return; }
      var word = target.closest('#transcript span.w');
      if (word) { pickWord(event, word); return; }
      var speed = target.closest('[data-speed]');
      if (speed) { setSpeed(Number(speed.getAttribute('data-speed'))); return; }
      var toggle = target.closest('[data-toggle-ts]');
      if (toggle) { applyTimestamps(toggle, !document.body.classList.contains('hide-ts')); }
    });

    /* ---- find in transcript ---- */

    /*
      A match may cross word spans (" the" + " towel"), so each paragraph is
      searched as one string with a map back to its text nodes: pieces of
      {node, base} where base is the node's offset in the joined text. A match
      is kept as (pieces, from, to) and turned into a Range, or into <mark>
      wrappers, only when it is shown.
    */
    var useHighlightAPI = typeof window.Highlight === 'function'
      && window.CSS && window.CSS.highlights && typeof window.CSS.highlights.set === 'function';
    var matches = [];
    var matchIndex = -1;
    var searchTimer = null;

    /* The blocks the search may look in: whichever reading is on screen.
       Search used to walk `#transcript .para` unconditionally, so with the
       cleaned reading showing it counted matches in hidden words, highlighted
       them where nobody could see, and scrolled to them - lying in exactly
       the mode a reader picks when they want to read rather than verify. */
    function searchBlocks() {
      var clean = document.getElementById('clean-reading');
      if (clean && !clean.hidden) {
        var pre = clean.querySelector('.clean-text');
        return pre ? [pre] : [];
      }
      return Array.prototype.slice.call(document.querySelectorAll('#transcript .para'));
    }

    /* One block's text nodes, and the offset each starts at in the joined
       string. The words reading puts one node per `.w`; the cleaned reading
       is a <pre> whose text is its own child, so a block with no `.w`
       contributes its own nodes instead. */
    function piecesOf(block) {
      var pieces = [];
      var text = '';
      function take(node) {
        if (!node || node.nodeType !== 3) { return; }
        var lower = node.nodeValue.toLowerCase();
        /* A few characters change length when lower-cased; keep the offsets
           honest for those words at the price of case-sensitivity there. */
        pieces.push({ node: node, base: text.length });
        text += lower.length === node.nodeValue.length ? lower : node.nodeValue;
      }
      var words = block.querySelectorAll('.w');
      if (words.length) {
        words.forEach(function (w) { take(w.firstChild); });
      } else {
        Array.prototype.forEach.call(block.childNodes, take);
      }
      return { pieces: pieces, text: text };
    }

    /* The last piece starting at or before offset. */
    function pieceAt(pieces, offset) {
      var lo = 0, hi = pieces.length - 1;
      while (lo < hi) {
        var mid = (lo + hi + 1) >> 1;
        if (pieces[mid].base <= offset) { lo = mid; } else { hi = mid - 1; }
      }
      return lo;
    }

    function rangeOf(match) {
      var first = match.pieces[pieceAt(match.pieces, match.from)];
      var last = match.pieces[pieceAt(match.pieces, match.to - 1)];
      var range = document.createRange();
      range.setStart(first.node, match.from - first.base);
      range.setEnd(last.node, match.to - last.base);
      return range;
    }

    function clearSearch() {
      matches = [];
      matchIndex = -1;
      if (useHighlightAPI) { window.CSS.highlights.delete(SEARCH_HIGHLIGHT); return; }
      /* Both readings: a mark left behind in the one that is off screen comes
         back the moment the reader switches. */
      document.querySelectorAll('#transcript mark.find, #clean-reading mark.find').forEach(function (mark) {
        var parent = mark.parentNode;
        parent.replaceChild(document.createTextNode(mark.textContent), mark);
        parent.normalize();
      });
    }

    /* Wrapping splits text nodes, so the matches go in from the back: a
       split behind an earlier match leaves that match's offsets untouched. */
    function markMatches() {
      for (var m = matches.length - 1; m >= 0; m--) {
        var match = matches[m];
        var first = pieceAt(match.pieces, match.from);
        var last = pieceAt(match.pieces, match.to - 1);
        for (var p = last; p >= first; p--) {
          var piece = match.pieces[p];
          var range = document.createRange();
          range.setStart(piece.node, Math.max(0, match.from - piece.base));
          range.setEnd(piece.node, Math.min(piece.node.length, match.to - piece.base));
          var mark = document.createElement('mark');
          mark.className = 'find';
          range.surroundContents(mark);
        }
      }
    }

    function sayCount() {
      var out = document.querySelector('[data-search-count]');
      if (!out) { return; }
      if (!matches.length) { out.textContent = ''; return; }
      var total = matches.length + (matches.length === 1 ? ' match' : ' matches');
      out.textContent = matchIndex >= 0 ? (matchIndex + 1) + ' of ' + total : total;
    }

    function runSearch(query) {
      clearSearch();
      var needle = query.trim().toLowerCase();
      if (needle.length < SEARCH_MIN_CHARS) { sayCount(); return; }
      searchBlocks().forEach(function (paragraph) {
        var joined = piecesOf(paragraph);
        var at = joined.text.indexOf(needle);
        while (at !== -1) {
          matches.push({ pieces: joined.pieces, from: at, to: at + needle.length });
          at = joined.text.indexOf(needle, at + needle.length);
        }
      });
      if (useHighlightAPI) {
        var highlightSet = new window.Highlight();
        matches.forEach(function (match) { highlightSet.add(rangeOf(match)); });
        window.CSS.highlights.set(SEARCH_HIGHLIGHT, highlightSet);
      } else {
        markMatches();
      }
      sayCount();
    }

    function nextMatch(step) {
      if (!matches.length) { return; }
      matchIndex = (matchIndex + step + matches.length) % matches.length;
      var match = matches[matchIndex];
      var el = match.pieces[pieceAt(match.pieces, match.from)].node.parentElement;
      if (el) {
        suspendedUntil = Date.now() + SCROLL_SUSPEND_MS;
        el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      }
      sayCount();
    }

    document.addEventListener('input', function (event) {
      var box = event.target;
      if (!box || typeof box.matches !== 'function' || !box.matches('[data-transcript-search]')) { return; }
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(function () { runSearch(box.value); }, SEARCH_DEBOUNCE_MS);
    });
    document.addEventListener('keydown', function (event) {
      var box = event.target;
      if (!box || typeof box.matches !== 'function' || !box.matches('[data-transcript-search]')) { return; }
      if (event.key === 'Enter') {
        event.preventDefault();
        nextMatch(event.shiftKey ? -1 : 1);
      } else if (event.key === 'Escape') {
        box.value = '';
        runSearch('');
      }
    });

    /* Switching reading re-runs the search against what is now on screen,
       so the count and the highlights describe the text the reader can see. */
    document.body.addEventListener('reading-changed', function () {
      var box = document.querySelector('[data-transcript-search]');
      runSearch(box ? box.value : '');
    });

    /* Put the correction offer beside the word it is about.

       The server renders it at the top of the main column, which is where it
       works with scripting off. On a 64-minute recording that can be 25,000px
       from the word just corrected, so the person who triggered it never saw
       the offer to fix the same mistake everywhere - the feature's whole
       payoff, reported to an empty screen (TASK-053.06).

       The offer names its word's index, so this is one move and no geometry:
       nothing here measures anything, which matters because
       `content-visibility: auto` on .para makes off-screen boxes unreliable.
       Focus follows it, because the swap dropped focus to <body> and the next
       thing a reader wants is the button. */
    function placeWordOffer() {
      var offer = document.querySelector('[data-word-offer]');
      if (!offer) { return; }
      var word = document.querySelector('#transcript .w[data-i="' + offer.getAttribute('data-word-offer') + '"]');
      if (!word || !word.parentNode) { return; }
      word.insertAdjacentElement('afterend', offer);
      var button = offer.querySelector('button[type="submit"]');
      if (button) { button.focus({ preventScroll: true }); }
    }

    /* ---- the panel re-fetches itself; the words are new nodes ---- */

    document.body.addEventListener('htmx:afterSwap', function (event) {
      var panel = document.getElementById('transcript-panel');
      if (!panel) { return; }
      /* Only when the panel itself was replaced (TASK-068). The AI region sits
         outside it and every one of its cards polls every two seconds, each
         swap reaching this listener; asking merely whether a panel *existed*
         wiped the reader's search and the shift-click anchor on every poll.
         A swap whose target is neither the panel nor something holding it
         left these words in place, so their state is still good. No target
         information at all keeps the old, conservative reset. */
      var swapped = event && event.detail ? event.detail.target : null;
      if (swapped && swapped !== panel && !(swapped.contains && swapped.contains(panel))) { return; }
      clearSearch();
      anchor = -1;  /* the new panel's words carry no selection */
      index();
      restoreTimestamps();
      placeWordOffer();
    });

    index();
    restoreTimestamps();
    placeWordOffer();
    seekFromUrlOrResume();
  }


  /* ---- the AI region: a menu, tabs, and one panel (TASK-053.04) ----

    Nine answers share one bounded panel. Every card stays in the document,
    because `hx-trigger="every 2s"` only fires for an element that is in it -
    a card removed to save markup would stop polling and its answer would
    never arrive. Hidden is not absent.

    Everything here is delegated from `document`, like the rest of this file,
    because the region is replaced wholesale when the pin is toggled.

    `el.hidden` and attributes, never `:checked` or `[hidden]` selectors: the
    tests here parse the source rather than run it, so a CSS-state check would
    pass the suite and fail in Chrome (see wireReadingSwitch).
  */
  var TAB_KEY_PREFIX = 'scribe:ai-tab:';
  var OPEN_KEY_PREFIX = 'scribe:ai-open:';

  function openPanel(region, open) {
    if (!region) { return; }
    region.setAttribute('data-open', open ? 'true' : 'false');
    var toggle = region.querySelector('[data-ai-toggle]');
    if (toggle) { toggle.setAttribute('aria-expanded', open ? 'true' : 'false'); }
    var media = region.getAttribute('data-media');
    if (media) { writeStore(OPEN_KEY_PREFIX + media, open ? '1' : '0'); }
  }

  function showTab(kind, open) {
    var region = document.querySelector('[data-ai-region]');
    if (!region || !kind) { return; }
    region.querySelectorAll('.ai-tab').forEach(function (tab) {
      var mine = tab.getAttribute('data-tab') === kind;
      tab.setAttribute('aria-selected', mine ? 'true' : 'false');
      tab.setAttribute('tabindex', mine ? '0' : '-1');
    });
    region.querySelectorAll('[data-slot]').forEach(function (slot) {
      slot.hidden = slot.getAttribute('data-slot') !== kind;
    });
    var media = region.getAttribute('data-media');
    if (media) { writeStore(TAB_KEY_PREFIX + media, kind); }
    if (open) { openPanel(region, true); }
  }

  function wireAiTabs() {
    document.addEventListener('click', function (event) {
      var target = event.target;
      if (!target || typeof target.closest !== 'function') { return; }

      var toggle = target.closest('[data-ai-toggle]');
      if (toggle) {
        var region = toggle.closest('[data-ai-region]');
        openPanel(region, region.getAttribute('data-open') !== 'true');
        return;
      }

      /* A tab opens the panel: clicking "Chapters" to read nothing would be
         a control that looks broken. */
      var tab = target.closest('.ai-tab');
      if (tab) { showTab(tab.getAttribute('data-tab'), true); return; }

      /* An Ask button switches to its own tab BEFORE the request leaves.
         htmx's swap lands in #ai-<kind>, and a swap into a hidden container
         is invisible - the same bug as an answer thirty thousand pixels down,
         one layer up. */
      var ask = target.closest('[data-asks]');
      if (ask) { showTab(ask.getAttribute('data-asks'), true); }
    });

    /* Left and right move along the strip, which is what a tablist owes a
       keyboard: the tabs carry roving tabindex, so Tab enters the strip once
       and the arrows walk it. */
    document.addEventListener('keydown', function (event) {
      var tab = event.target && event.target.closest
        ? event.target.closest('.ai-tab')
        : null;
      if (!tab || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) { return; }
      var tabs = Array.prototype.slice.call(
        tab.parentElement.querySelectorAll('.ai-tab')
      );
      var next = tabs[(tabs.indexOf(tab) + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
      if (!next) { return; }
      event.preventDefault();
      showTab(next.getAttribute('data-tab'), false);
      next.focus();
    });

    /* What was open last time for THIS recording. The server picked a sane
       tab already - a running job, else the newest answer - so this only
       overrides it when a choice was actually made and that card is still
       there. */
    function restoreTab() {
      var region = document.querySelector('[data-ai-region]');
      if (!region) { return; }
      var media = region.getAttribute('data-media');
      var kind = media ? readStore(TAB_KEY_PREFIX + media) : null;
      if (kind && region.querySelector('[data-slot="' + kind + '"]')) { showTab(kind, false); }
      /* Only an explicit choice overrides the server, which already opened
         the panel if something is being written. */
      var was = media ? readStore(OPEN_KEY_PREFIX + media) : null;
      if (was === '1' || was === '0') { openPanel(region, was === '1'); }
    }

    document.body.addEventListener('htmx:afterSwap', restoreTab);
    restoreTab();
  }

  function start() {
    wireForms();
    wireSelectAll();
    wireHtmxErrors();
    wireDialogs();
    wireTranscribeDialog();
    wireReadingSwitch();
    wireAiTabs();
    wireExportDialog();
    wireJobLog();
    wireTranscript();
    tickElapsed();
    window.setInterval(tickElapsed, 1000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();

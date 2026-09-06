/*
  MyScribe - the recorder behind the Record dialog.

  A file of its own rather than a section of app.js because it is the only
  script here that asks the browser for a device, and because it does nothing
  at all on a page without [data-recorder] - which is every page but the one
  with the Record dialog open. Loaded from base.html, never from the
  fragment: htmx runs with allowScriptTags off, so a <script> inside the
  swapped-in dialog would be stripped and this would silently never run.

  What it does, and the reasoning behind the parts that look odd:

  * getUserMedia is asked for audio with echoCancellation, noiseSuppression
    and autoGainControl all OFF. Those three are tuned for a phone call: they
    gate quiet speech, duck a second voice, and pump the gain between
    sentences. Every one of those is a transcription error waiting to happen,
    and the model would rather have the room than a cleaned-up version of it.
  * MediaRecorder is started with start(5000), so a Blob lands every five
    seconds and is POSTed straight to the server. That is the whole durability
    story: a closed tab, a crash or a flat battery costs the last few seconds
    and not the session. Nothing is accumulated in memory.
  * The uploads are chained through one promise, so chunk 7 cannot overtake
    chunk 6 on a slow disk, and the finish waits for the chain rather than
    guessing.
  * The scope and the level meter read an AnalyserNode's time-domain data -
    the actual signal - and the device line names the microphone the stream
    came from. A muted or wrong input is a flat trace, an amber sentence and
    a device name, and the user finds out now rather than after an hour of
    silence.
  * A running recording claims the dialog it is in, with data-busy on the
    block: app.js will not close a dialog that holds one. The recording is not
    in the markup - it is a MediaRecorder, a stream and a session id, none of
    them nodes - so closing the dialog used to leave it running, posting a
    chunk every five seconds, with no control on screen and no way back but a
    reload, which abandons the session directory under WORK_DIR. For the same
    reason `reveal` repaints the live state onto whatever fragment arrives,
    rather than trusting the fragment's idle markup.

  Everything written into the page goes through textContent (an error message
  can be any text at all), and the finish posts the dialog's own form body, so
  the options the user chose travel with the recording exactly as they do for
  an upload.
*/
(function () {
  'use strict';

  /* Five seconds: short enough that a crash costs almost nothing, long
     enough that an hour of recording is 720 requests and not 36,000. */
  var CHUNK_MS = 5000;
  /* The microphone the user picked, by deviceId, across recordings. Absent
     means the browser's default - which is also what an id that no longer
     exists (a headset unplugged) falls back to, with a word to say so. */
  var MIC_KEY = 'myscribe-mic';
  var MIME = 'audio/webm;codecs=opus';

  var HINT_LIVE = 'Recording. The audio is saved to this machine every five seconds.';
  var HINT_SAVING = 'Saving the recording…';

  /* What app.js shows in the dialog's flash line when it is asked to close
     while this is running. The sentence lives here because this is the part
     that is busy, and the only part that can say why. */
  var BUSY_LIVE = 'A recording is still open here. Stop and transcribe it, or discard it, first.';

  /* The one recording in flight, or null. A second one cannot start: the
     Record button is hidden while this is set. */
  var live = null;
  /* True from the moment `begin` is entered until `live` is set or the start
     has failed. `live` alone was the guard, and `live` is only assigned after
     getUserMedia and POST /record/start have both answered - a second `begin`
     in that window (a swap that re-ran the autostart, a second click) got
     through, opened a second session and a second MediaRecorder on the same
     microphone, and both recorders then posted every chunk to whichever
     session `live` named. The server assembled each five seconds twice, and
     the recording played back over itself. Measured on 2026-09-06: two
     POST /record/start, thirteen chunk POSTs for a 32-second recording, 62 s
     of audio in a file stamped 32 s. */
  var starting = false;

  function panel() {
    return document.querySelector('[data-recorder]');
  }

  /*
    data-busy is the whole of the recorder's claim on the dialog around it:
    app.js refuses to close a dialog that contains one, and shows its value.

    It mirrors `live`, so it is set and cleared where `live` is - not where
    the block is repainted. A marker owned by the repaint would survive a
    discard and leave the dialog shut for good, which is the bug this fixes
    with the sign flipped.
  */
  function busy(reason) {
    var root = panel();
    if (!root) { return; }
    if (reason) { root.setAttribute('data-busy', reason); } else { root.removeAttribute('data-busy'); }
  }

  function part(name) {
    var root = panel();
    return root ? root.querySelector('[data-record-' + name + ']') : null;
  }

  function say(text) {
    var hint = part('status');
    if (hint) { hint.textContent = text; }
  }

  function show(name, visible) {
    var el = part(name);
    if (el) { el.hidden = !visible; }
  }

  function controls(recording, paused) {
    show('start', !recording);
    show('pause', recording);
    show('finish', recording);
    show('cancel', recording);
    var pause = part('pause');
    if (pause) { pause.textContent = paused ? 'Resume' : 'Pause'; }
  }

  function clock(seconds) {
    var s = Math.max(0, Math.floor(seconds));
    var m = Math.floor(s / 60);
    var rest = s % 60;
    if (m < 60) { return m + ':' + (rest < 10 ? '0' : '') + rest; }
    var h = Math.floor(m / 60);
    var mm = m % 60;
    return h + ':' + (mm < 10 ? '0' : '') + mm + ':' + (rest < 10 ? '0' : '') + rest;
  }

  /* ---- talking to the server --------------------------------------------- */

  function post(url, options) {
    var opts = options || {};
    opts.method = 'POST';
    opts.credentials = 'same-origin';
    return fetch(url, opts).then(function (response) {
      if (response.ok) { return response; }
      return response.text().then(function (text) {
        var detail = 'HTTP ' + response.status;
        try {
          var body = JSON.parse(text);
          if (body && typeof body.detail === 'string') { detail = body.detail; }
        } catch (err) { /* a non-JSON error body is the status and nothing more */ }
        throw new Error(detail);
      });
    });
  }

  function sendChunk(session, blob) {
    return post('/record/' + session + '/chunk', {
      body: blob,
      headers: { 'Content-Type': blob.type || MIME }
    });
  }

  /* ---- the level meter ---------------------------------------------------- */

  /*
    The scope: what the microphone is hearing, drawn while it is heard.

    The <meter> before it answered one question - is anything coming in right
    now - and answered it in a way nobody looked at. A recording made this
    evening was 8 seconds of -91 dB, and the pipeline dutifully transcribed the
    silence; the meter had been at zero the whole time. A picture of the audio
    is looked at, because it is the thing on the screen that moves.

    Two panes on one canvas, both drawn from the samples of the stream the
    MediaRecorder is encoding - the analyser sits on the same
    MediaStreamSource, so what is painted is what is recorded.

    The left two thirds are the last 20 seconds as a waveform: for every
    column (one per 40 ms) the true minimum and maximum of the samples that
    fell in it, drawn as a bar from one to the other around the zero line.
    That is what an audio editor shows, and it is asymmetric the way a voice
    is; a mirrored "peak" bar would have been a picture of a number, not of
    the sound. A time axis with a tick per second says how long the window is.
    The analyser hands over 1024 samples (21 ms at 48 kHz) each frame at ~60
    frames a second, so the columns see nearly every sample. The right third is
    the live trace of the current window - the shape of the sound now.

    And the case the meter failed on gets words. Below `SILENT_LEVEL` for
    `SILENT_AFTER_MS` while recording, the envelope stops being painted in
    the accent and the canvas says, in the warning colour, that nothing is
    reaching the microphone. That is a sentence a person acts on; a thin
    green bar at zero is not.

    Colours are read from the custom properties on the canvas, so the scope
    follows the theme switch like everything else. The analyser feeds the
    scope only - nothing here touches the MediaRecorder's stream.
  */
  var SILENT_LEVEL = 2 / 128;   /* peak deviation below this is "nothing" */
  var SILENT_AFTER_MS = 2000;
  var COLUMN_MS = 40;
  var WINDOW_MS = 20000;   /* what the left pane shows: the last 20 seconds */

  function meterFrom(stream) {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) { return null; }

    var ctx = new Ctx();
    if (ctx.resume) { ctx.resume(); }
    var analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0;
    ctx.createMediaStreamSource(stream).connect(analyser);
    var samples = new Uint8Array(analyser.fftSize);
    var running = true;

    /* One [min, max] per column, oldest first; `columns` follows the width. */
    var history = [];
    var columnMin = 0;
    var columnMax = 0;
    var columnStarted = performance.now();
    var floats = analyser.getFloatTimeDomainData ? new Float32Array(analyser.fftSize) : null;
    var lastSoundAt = performance.now();
    var heardAnything = false;

    /* A token's value is `light-dark(#a, #b)`, which the canvas cannot parse:
       getPropertyValue hands back the declaration, not a colour. Assigning it
       to an element's `color` and reading that back makes the browser resolve
       it for the theme in force - the same thing it does for every rule in
       app.css. One probe, re-read every frame, so a theme switch mid-recording
       recolours the trace on the next tick. */
    var probe = null;
    var tones = {};
    var tonesAt = 0;
    var TONES_TTL_MS = 1000;
    /* Resolved once a second, not once a frame: five forced style recalcs
       per frame was 300 a second for colours that change only on a theme
       switch, and a switch mid-recording still shows within a second. */
    function tone(canvas, name, fallback) {
      var now = performance.now();
      if (now - tonesAt > TONES_TTL_MS) { tones = {}; tonesAt = now; }
      if (tones[name]) { return tones[name]; }
      if (!probe || !probe.isConnected) {
        probe = document.createElement('span');
        probe.hidden = true;
        probe.setAttribute('data-record-probe', '');
        (canvas.parentNode || document.body).appendChild(probe);
      }
      probe.style.color = 'var(' + name + ', ' + fallback + ')';
      var value = window.getComputedStyle(probe).color || fallback;
      tones[name] = value;
      return value;
    }

    /* The state in words, on change only. */
    var saidState = null;
    function announce(state) {
      if (state === saidState) { return; }
      saidState = state;
      var live = part('scope-say');
      if (!live) { return; }
      live.textContent = state === 'silent'
        ? 'No sound is reaching the microphone. Check the input device.'
        : (state === 'sound' ? 'Sound is reaching the microphone.'
          : (state === 'paused' ? 'Paused.' : ''));
    }

    /* Reduced motion: the scope still shows the state - silence detection
       depends on it - but repaints a few times a second instead of sixty. */
    var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var lastPaint = 0;

    function paint(canvas, peak, paused) {
      var dpr = window.devicePixelRatio || 1;
      var w = Math.max(1, Math.round(canvas.clientWidth * dpr));
      var h = Math.max(1, Math.round(canvas.clientHeight * dpr));
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
      var g = canvas.getContext('2d');
      if (!g) { return; }

      var accent = tone(canvas, '--accent', '#2563eb');
      var accent2 = tone(canvas, '--accent-2', '#7c3aed');
      var muted = tone(canvas, '--fg-muted', '#5b6b82');
      var warn = tone(canvas, '--warn', '#a16207');
      var line = tone(canvas, '--line-soft', '#e2e8f0');

      var now = performance.now();
      var silent = heardAnything
        ? (now - lastSoundAt) > SILENT_AFTER_MS
        : (now - columnStarted) > SILENT_AFTER_MS && history.length > 0;
      var state = paused ? 'paused' : (silent ? 'silent' : (heardAnything ? 'sound' : 'waiting'));
      canvas.setAttribute('data-record-scope-state', state);
      announce(state);
      if (reduceMotion && now - lastPaint < 250) { return; }
      lastPaint = now;

      g.clearRect(0, 0, w, h);
      var split = Math.round(w * 0.68);
      var mid = h / 2;

      /* Centre line across both panes: the zero a silent input sits on. */
      g.strokeStyle = line;
      g.lineWidth = 1 * dpr;
      g.beginPath(); g.moveTo(0, mid); g.lineTo(w, mid); g.stroke();
      g.beginPath(); g.moveTo(split, 0); g.lineTo(split, h); g.stroke();

      /* Left: the waveform. WINDOW_MS of columns fit the pane exactly, so
         the time axis is honest: a tick per second, newest at the split. */
      var columns = Math.round(WINDOW_MS / COLUMN_MS);
      var colW = split / columns;
      while (history.length > columns) { history.shift(); }
      var perSecond = 1000 / COLUMN_MS;
      g.strokeStyle = line;
      g.fillStyle = muted;
      g.font = (9 * dpr) + 'px system-ui, sans-serif';
      g.textBaseline = 'bottom';
      for (var sec = 1; sec * perSecond <= columns; sec++) {
        var tx = split - sec * perSecond * colW;
        g.beginPath(); g.moveTo(tx, h - 6 * dpr); g.lineTo(tx, h); g.stroke();
        if (sec % 5 === 0) { g.fillText('-' + sec + 's', tx + 2 * dpr, h - 1 * dpr); }
      }
      var grad = g.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0, accent2);
      grad.addColorStop(0.5, accent);
      grad.addColorStop(1, accent2);
      g.fillStyle = silent ? muted : grad;
      var scale = mid - 3 * dpr;
      for (var i = 0; i < history.length; i++) {
        var x = split - (history.length - i) * colW;
        var top = mid - history[i][1] * scale;
        var bottom = mid - history[i][0] * scale;
        if (bottom - top < 1 * dpr) { top = mid - 0.5 * dpr; bottom = mid + 0.5 * dpr; }
        g.fillRect(x, top, Math.max(1, colW - 1 * dpr), bottom - top);
      }

      /* Right: the trace of the current window. */
      g.strokeStyle = silent ? muted : accent;
      g.lineWidth = 1.5 * dpr;
      g.beginPath();
      var span = w - split;
      for (var s = 0; s < samples.length; s++) {
        var px = split + (s / (samples.length - 1)) * span;
        var py = mid + ((samples[s] - 128) / 128) * (mid - 2 * dpr);
        if (s === 0) { g.moveTo(px, py); } else { g.lineTo(px, py); }
      }
      g.stroke();

      /* Words for the two states a picture cannot carry on its own. */
      var label = null;
      if (paused) { label = 'Paused'; }
      else if (silent) { label = 'No sound is reaching the microphone - check the input device'; }
      if (label) {
        g.font = (12 * dpr) + 'px system-ui, sans-serif';
        g.fillStyle = paused ? muted : warn;
        g.textBaseline = 'top';
        g.fillText(label, 8 * dpr, 6 * dpr);
      }
    }

    function tick() {
      if (!running) { return; }
      analyser.getByteTimeDomainData(samples);
      /* The float data is the signal itself in -1..1; the byte copy above is
         what the right-hand trace draws. Min and max, not RMS: a scope is for
         spotting a dead microphone, and an extreme says that a beat sooner. */
      var lo = 0, hi = 0;
      if (floats) {
        analyser.getFloatTimeDomainData(floats);
        for (var i = 0; i < floats.length; i++) {
          if (floats[i] < lo) { lo = floats[i]; }
          if (floats[i] > hi) { hi = floats[i]; }
        }
      } else {
        for (var j = 0; j < samples.length; j++) {
          var v = (samples[j] - 128) / 128;
          if (v < lo) { lo = v; }
          if (v > hi) { hi = v; }
        }
      }
      var level = Math.min(1, Math.max(-lo, hi));
      var paused = !!(live && live.recorder && live.recorder.state === 'paused');
      var now = performance.now();
      if (level >= SILENT_LEVEL) { lastSoundAt = now; heardAnything = true; }

      /* The history advances in wall-clock columns, and not while paused:
         a pause is a gap in the recording and should read as one. */
      if (!paused) {
        /* A hidden tab suspends animation frames but not the recorder. The
           first frame back would push one column per 40 ms of the whole
           absence - an hour is 90,000 - for a window that only ever shows
           the last 500. Nothing on screen is salvageable; start over. */
        if (now - columnStarted > WINDOW_MS) {
          history.length = 0;
          columnStarted = now;
          columnMin = 0;
          columnMax = 0;
        }
        if (lo < columnMin) { columnMin = lo; }
        if (hi > columnMax) { columnMax = hi; }
        /* A frame that took longer than a column (a busy tab) still advances
           the axis by the time that passed, so the window stays 20 seconds
           of wall clock and not 20 seconds of frames. */
        while (now - columnStarted >= COLUMN_MS) {
          history.push([columnMin, columnMax]);
          columnMin = 0;
          columnMax = 0;
          columnStarted += COLUMN_MS;
        }
      }

      /* Looked up every frame rather than closed over: a swap replaces the
         block, and the nodes this started with are then detached and unseen.
         Two querySelectors at 60 Hz cost nothing. */
      var meter = part('level');
      if (meter) { meter.value = level; }
      var canvas = part('scope');
      if (canvas) { paint(canvas, level, paused); }
      window.requestAnimationFrame(tick);
    }
    window.requestAnimationFrame(tick);

    return function stop() {
      running = false;
      var meter = part('level');
      if (meter) { meter.value = 0; }
      var canvas = part('scope');
      if (canvas) {
        var g = canvas.getContext('2d');
        if (g) { g.clearRect(0, 0, canvas.width, canvas.height); }
        canvas.removeAttribute('data-record-scope-state');
      }
      if (ctx.close) { ctx.close(); }
    };
  }

  /* ---- the recording itself ------------------------------------------------ */

  function warnBeforeLeaving(event) {
    event.preventDefault();
    /* Chrome ignores the string and shows its own; Firefox wants returnValue
       set. Both need something non-empty to show anything at all. */
    event.returnValue = 'A recording is still running.';
    return event.returnValue;
  }

  function tickElapsed() {
    var out = part('elapsed');
    if (out && live) { out.textContent = clock((Date.now() - live.started) / 1000); }
  }

  function release() {
    if (!live) { return; }
    window.clearInterval(live.timer);
    window.removeEventListener('beforeunload', warnBeforeLeaving);
    if (live.stopMeter) { live.stopMeter(); }
    live.stream.getTracks().forEach(function (track) { track.stop(); });
    live = null;
    busy(null);
    controls(false, false);
  }

  function savedInput() {
    try { return window.localStorage.getItem(MIC_KEY) || ''; } catch (err) { return ''; }
  }

  function rememberInput(id) {
    try {
      if (id) { window.localStorage.setItem(MIC_KEY, id); }
      else { window.localStorage.removeItem(MIC_KEY); }
    } catch (err) { /* the choice still applies to this recording */ }
  }

  /* Call-tuned DSP is wrong for transcription: see the file header. The
     device is `exact` when one was chosen, so a missing one fails loudly
     (OverconstrainedError) instead of silently becoming the default. */
  function constraintsFor(deviceId) {
    var audio = { echoCancellation: false, noiseSuppression: false, autoGainControl: false };
    if (deviceId) { audio.deviceId = { exact: deviceId }; }
    return { audio: audio };
  }

  /*
    Which device the stream came from, in words, on the page. `label` is
    empty until permission has been granted once, which is why this runs on
    the stream and not on the device list. The settings are what the browser
    actually negotiated - a 16 kHz mono stream from a "48 kHz" device is worth
    seeing.
  */
  function describeInput(stream) {
    /* Naming the device is a courtesy, and a courtesy must not be able to
       end the recording: a stream with no describable track (a test double,
       an odd browser) is recorded from anyway. */
    var tracks = stream && stream.getAudioTracks ? stream.getAudioTracks() : [];
    var track = tracks[0];
    var line = part('device');
    if (!track || !line) { return ''; }
    var s = track.getSettings ? track.getSettings() : {};
    var bits = ['Recording from: ' + (track.label || 'the default microphone')];
    if (s.sampleRate) { bits.push(Math.round(s.sampleRate / 1000) + ' kHz'); }
    if (s.channelCount) { bits.push(s.channelCount === 1 ? 'mono' : s.channelCount + ' channels'); }
    line.textContent = bits.join(' \u00b7 ');
    line.setAttribute('data-record-device-id', s.deviceId || '');
    if (window.console && console.info) { console.info('[recorder] ' + line.textContent, s); }
    return s.deviceId || '';
  }

  /*
    The microphone list. enumerateDevices gives labels only after a
    permission grant, so this is called once a stream is open and again when
    the block is repainted. The select keeps the device in use selected, so
    the list says what is true and not what was asked for.
  */
  function listInputs(currentId) {
    var select = part('input');
    if (!select || !navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) { return; }
    navigator.mediaDevices.enumerateDevices().then(function (devices) {
      var fresh = part('input');
      if (!fresh) { return; }
      while (fresh.options.length > 1) { fresh.remove(1); }
      devices.forEach(function (d) {
        if (d.kind !== 'audioinput' || !d.deviceId || d.deviceId === 'default') { return; }
        var opt = document.createElement('option');
        opt.value = d.deviceId;
        opt.textContent = d.label || ('Microphone ' + (fresh.options.length));
        fresh.appendChild(opt);
      });
      fresh.value = currentId || '';
      if (fresh.value !== (currentId || '')) { fresh.value = ''; }
    }).catch(function () { /* the select stays as it was */ });
  }

  /*
    Switching microphones restarts the capture: a MediaRecorder is bound to
    its stream, and the server's session is one file. Under five seconds in,
    that costs nothing worth asking about; past it the user is asked, because
    a minute of audio is theirs to throw away and not this script's.
  */
  function switchInput(deviceId) {
    rememberInput(deviceId);
    if (!live) { return; }
    var elapsed = Date.now() - live.started;
    if (elapsed > 5000 && !window.confirm(
      'Switching microphones restarts the recording and discards the ' +
      clock(Math.round(elapsed / 1000)) + ' recorded so far. Switch?'
    )) {
      var select = part('input');
      if (select) { select.value = live.deviceId || ''; }
      rememberInput(live.deviceId || '');
      return;
    }
    var session = live.session;
    drain().then(function () {
      release();
      return post('/record/' + session + '/cancel');
    }).then(function () {
      begin();
    }).catch(function (err) {
      say('Could not switch: ' + (err && err.message ? err.message : 'the server refused it'));
    });
  }

  function begin() {
    if (live || starting || !navigator.mediaDevices || !window.MediaRecorder) { return; }
    starting = true;
    say('Waiting for the microphone…');

    var wanted = savedInput();
    navigator.mediaDevices.getUserMedia(constraintsFor(wanted)).catch(function (err) {
      /* The remembered microphone is gone (unplugged, renamed): record from
         the default rather than from nothing, and say which happened. */
      if (wanted && err && (err.name === 'OverconstrainedError' || err.name === 'NotFoundError')) {
        rememberInput('');
        say('The remembered microphone is not connected; using the default.');
        return navigator.mediaDevices.getUserMedia(constraintsFor(''));
      }
      throw err;
    }).then(function (stream) {
      return post('/record/start').then(function (response) {
        return response.json();
      }).then(function (body) {
        return { stream: stream, session: body.session };
      }).catch(function (err) {
        stream.getTracks().forEach(function (track) { track.stop(); });
        throw err;
      });
    }).then(function (opened) {
      var supported = window.MediaRecorder.isTypeSupported;
      var recorder = new window.MediaRecorder(
        opened.stream,
        supported && !supported(MIME) ? {} : { mimeType: MIME }
      );

      starting = false;
      live = {
        session: opened.session,
        stream: opened.stream,
        recorder: recorder,
        started: Date.now(),
        uploads: Promise.resolve(),
        failed: null,
        /* True from the moment `finish` starts draining until it either
           navigates away or fails: the recorder is inactive then but the
           session is not over, and the two look identical otherwise. */
        saving: false,
        timer: window.setInterval(tickElapsed, 1000),
        stopMeter: meterFrom(opened.stream),
        deviceId: ''
      };
      live.deviceId = describeInput(opened.stream) || '';
      listInputs(live.deviceId);
      busy(BUSY_LIVE);

      /* Bound here, not read off `live` when the chunk arrives: a chunk
         belongs to the session it was recorded under, and a recorder that
         is not the live one - which cannot happen now, and did - is not
         allowed to feed the live session. */
      var session = opened.session;
      recorder.ondataavailable = function (event) {
        if (!live || live.recorder !== this || !event.data || !event.data.size) { return; }
        live.uploads = live.uploads.then(function () {
          return sendChunk(session, event.data);
        }).catch(function (err) {
            /* Remembered rather than thrown away: the recording carries on -
               losing five seconds is better than stopping - and the finish
               reports it. */
          if (live) { live.failed = err; }
          say('A chunk did not reach the server: ' + err.message);
        });
      };

      recorder.start(CHUNK_MS);
      controls(true, false);
      tickElapsed();
      say(HINT_LIVE);
      window.addEventListener('beforeunload', warnBeforeLeaving);
    }).catch(function (err) {
      /* `live` is assigned before recorder.start(), so a start that throws
         (a stream whose tracks have already ended, a codec the device will
         not do) leaves a session that looks alive - timer counting, data-busy
         set, a Record button on screen that `begin` refuses because `live` is
         set. Found by switching microphones in a test harness that handed back
         a stopped stream. Tear it down and let the server forget the session,
         so the button that is shown is a button that works. */
      var session = live ? live.session : null;
      if (live) { release(); }
      starting = false;
      if (session) { post('/record/' + session + '/cancel').catch(function () { /* nothing to add */ }); }
      say('Could not start recording: ' + (err && err.message ? err.message : 'the microphone was refused'));
      controls(false, false);
    });
  }

  /* The last chunk arrives after stop(), so everything that ends a recording
     waits for `onstop` and then for the upload chain to drain. */
  function drain() {
    return new Promise(function (resolve) {
      if (!live) { resolve(); return; }
      var recorder = live.recorder;
      if (recorder.state === 'inactive') { resolve(); return; }
      recorder.onstop = function () { resolve(); };
      recorder.stop();
    }).then(function () {
      return live ? live.uploads : null;
    });
  }

  function finish() {
    if (!live) { return; }
    var session = live.session;
    var form = panel() ? panel().closest('form') : null;
    if (!form) { return; }

    live.saving = true;
    busy(HINT_SAVING);
    controls(false, false);
    show('start', false);   /* nothing to press until this settles */
    say(HINT_SAVING);
    drain().then(function () {
      /* A chunk that never arrived is a gap in the audio, not a reason to
         throw the rest away - so the user is told and asked, rather than
         having the decision made for them. */
      if (live && live.failed && !window.confirm(
        'Some audio never reached the server (' + live.failed.message +
        '). Transcribe what did arrive?'
      )) {
        throw new Error('the recording was not saved; the chunks are still on this machine');
      }
      return post('/record/' + session + '/finish', {
        /* The dialog's own fields, urlencoded - the same body the form would
           send - so language, tier, speakers, folder and the name travel with
           the recording. Files are left out: this is not an upload. */
        body: bodyOf(form),
        headers: { 'Accept': 'text/html, application/json' }
      });
    }).then(function (response) {
      release();
      /* The route answers a plain (non-htmx) request with a redirect to the
         library, which is where the new row and its queued job are. Following
         it is what app.js's own forms do when they have no region to refresh. */
      window.location.assign(response.redirected ? response.url : '/');
    }).catch(function (err) {
      /* The session is still open and its chunks are still on disk, so the
         controls come back and the dialog stays claimed: the user can fix the
         form and press stop again rather than lose the recording. */
      if (live) { live.saving = false; }
      busy(BUSY_LIVE);
      controls(true, false);
      say('Could not save the recording: ' + (err && err.message ? err.message : 'the server refused it'));
    });
  }

  function discard() {
    if (!live) { return; }
    if (!window.confirm('Throw this recording away? It has not been transcribed.')) { return; }
    var session = live.session;
    drain().then(function () {
      release();
      return post('/record/' + session + '/cancel');
    }).then(function () {
      say('Recording discarded.');
    }).catch(function (err) {
      say('Could not discard it: ' + (err && err.message ? err.message : 'the server refused it'));
    });
  }

  function bodyOf(form) {
    var data = new FormData(form);
    data.delete('files');
    return new URLSearchParams(data);
  }

  function togglePause() {
    if (!live) { return; }
    var recorder = live.recorder;
    if (recorder.state === 'recording') {
      recorder.pause();
      live.pausedAt = Date.now();
      controls(true, true);
      say('Paused.');
    } else if (recorder.state === 'paused') {
      /* The elapsed clock counts recorded time, not wall time, so the start
         is moved forward by however long the pause lasted. */
      live.started += Date.now() - live.pausedAt;
      recorder.resume();
      controls(true, false);
      say('Recording.');
    }
  }

  /* ---- wiring -------------------------------------------------------------- */

  /*
    Delegated from the document, like everything in app.js: the dialog's
    markup arrives through an htmx swap, so there is nothing to bind to when
    this file runs. The same swap is what reveals the block, if the browser
    can record at all.

    And it may not be a first look. `live` holds a MediaRecorder, a stream and
    a session id - none of them nodes - so it outlives the markup it was
    started from. Reopening the dialog runs hx-get /transcribe with
    hx-swap="innerHTML", and a fresh, idle copy of this block then arrives
    while the microphone is still running and still posting a chunk every five
    seconds. Painting that copy idle put a Record button on screen that
    `begin` refuses (it returns at once when `live` is set) beside a clock that
    kept counting, and took away the only two controls that could have ended
    the session.

    So this repaints what is true rather than what a fresh fragment says, and
    it is idempotent: a swap anywhere else on the page runs it too.
  */
  function reveal() {
    var root = panel();
    if (!root) { return; }
    if (!navigator.mediaDevices || !window.MediaRecorder) { return; }
    root.hidden = false;

    if (!live) {
      busy(null);
      controls(false, false);
      /* Opened by the toolbar's microphone: start without waiting for a
         press. The attribute is cleared first, so a later swap - the block
         arriving again while this very recording runs - cannot start a
         second one. `begin` refuses that anyway; this makes it not arise. */
      if (root.hasAttribute('data-record-autostart')) {
        root.removeAttribute('data-record-autostart');
        begin();
      }
      return;
    }
    if (live.saving) {
      busy(HINT_SAVING);
      controls(false, false);
      show('start', false);
      say(HINT_SAVING);
      return;
    }
    busy(BUSY_LIVE);
    describeInput(live.stream);
    listInputs(live.deviceId);
    /* `inactive` here is a finish that failed: the chunks are still on disk
       and Stop can be pressed again, so the controls come back and whatever
       the failure said is left standing in the status line. */
    var state = live.recorder.state;
    controls(true, state === 'paused');
    tickElapsed();
    if (state === 'recording') { say(HINT_LIVE); }
    else if (state === 'paused') { say('Paused.'); }
  }

  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!target || !target.closest) { return; }
    if (target.closest('[data-record-start]')) { begin(); }
    else if (target.closest('[data-record-pause]')) { togglePause(); }
    else if (target.closest('[data-record-finish]')) { finish(); }
    else if (target.closest('[data-record-cancel]')) { discard(); }
  });

  document.addEventListener('change', function (event) {
    var select = event.target.closest ? event.target.closest('[data-record-input]') : null;
    if (select) { switchInput(select.value); }
  });

  document.body.addEventListener('htmx:afterSwap', reveal);
  document.addEventListener('DOMContentLoaded', reveal);
  reveal();
})();
